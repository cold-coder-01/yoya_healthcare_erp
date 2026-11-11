from odoo import api, fields, models
from odoo.exceptions import UserError


class HospitalPatientBillLine(models.Model):
    _inherit = "hospital.patient.bill.line"

    # Add "surgery" so theatre charges are traceable and the accounting
    # bridge can map them to a Surgery Revenue account at posting time.
    source_type = fields.Selection(
        selection_add=[("surgery", "Surgery")],
        ondelete={"surgery": "set null"},
    )


class HospitalSurgeryRequest(models.Model):
    _inherit = "hospital.surgery.request"

    # States at which a surgery may be billed.
    BILLABLE_STATES = ("operation_done", "postop_done", "completed")

    # ── Material cost review / billing options (Task 32A) ──────────────
    include_material_cost_in_bill = fields.Boolean(
        string="Include Material Cost in Bill",
        default=False,
        help="When set, the actual consumed material cost is added to the "
        "patient bill as a separate Surgery Materials line.",
    )
    material_cost_reviewed = fields.Boolean(
        string="Material Cost Reviewed",
        default=False,
        help="Tick once the consumed material cost has been reviewed against "
        "the estimated plan.",
    )
    material_cost_review_note = fields.Text(string="Material Cost Review Note")
    billing_note = fields.Text(string="Billing Note")
    material_cost_difference = fields.Monetary(
        compute="_compute_material_cost_difference",
        string="Material Cost Difference",
        currency_field="currency_id",
        help="Actual material cost minus estimated material cost.",
    )
    billable_amount = fields.Monetary(
        compute="_compute_billable_amount",
        string="Billable Amount",
        currency_field="currency_id",
        help="Surgery service price plus material cost when it is billed.",
    )

    billing_state = fields.Selection(
        [
            ("not_billed", "Not Billed"),
            ("billed", "Billed"),
            ("partial", "Partially Paid"),
            ("paid", "Paid"),
            ("cancelled", "Cancelled"),
        ],
        string="Billing Status",
        compute="_compute_billing_state",
        store=True,
        tracking=True,
    )

    # ------------------------------------------------------------------
    # Computed
    # ------------------------------------------------------------------
    @api.depends("actual_material_cost", "estimated_material_cost")
    def _compute_material_cost_difference(self):
        for rec in self:
            rec.material_cost_difference = (
                (rec.actual_material_cost or 0.0) - (rec.estimated_material_cost or 0.0)
            )

    @api.depends(
        "default_price",
        "include_material_cost_in_bill",
        "actual_material_cost",
    )
    def _compute_billable_amount(self):
        for rec in self:
            amount = rec.default_price or 0.0
            if rec.include_material_cost_in_bill:
                amount += rec.actual_material_cost or 0.0
            rec.billable_amount = amount

    @api.depends(
        "bill_id",
        "bill_id.state",
        "bill_id.amount_total",
        "bill_id.amount_paid",
        "bill_id.amount_due",
    )
    def _compute_billing_state(self):
        for rec in self:
            bill = rec.bill_id
            if not bill:
                rec.billing_state = "not_billed"
            elif bill.state == "cancelled":
                rec.billing_state = "cancelled"
            elif bill.state == "paid" or (
                bill.amount_due <= 0 and bill.amount_total > 0
            ):
                rec.billing_state = "paid"
            elif bill.state == "partially_paid" or bill.amount_paid > 0:
                rec.billing_state = "partial"
            else:
                rec.billing_state = "billed"

    # ------------------------------------------------------------------
    # Billing actions
    # ------------------------------------------------------------------
    def action_generate_surgery_bill(self):
        self.ensure_one()
        if self.bill_id:
            raise UserError("This surgery already has a patient bill.")
        if self.state not in self.BILLABLE_STATES:
            raise UserError(
                "A surgery bill can only be generated once the operation is "
                "completed (Operation Done, Post-op Done or Completed)."
            )
        if not self.patient_id:
            raise UserError("A patient is required to generate a bill.")
        if not self.surgery_type_id:
            raise UserError("A surgery type is required to generate a bill.")

        service_price = self.default_price or 0.0
        if service_price <= 0:
            raise UserError(
                "Please set a surgery price before generating the bill."
            )

        if self.include_material_cost_in_bill and (self.actual_material_cost or 0.0) <= 0:
            raise UserError(
                "Cannot add material cost to the bill because no consumed "
                "material cost has been recorded."
            )

        lines = [
            (0, 0, {
                "description": f"Surgery - {self.surgery_type_id.name} - {self.name}",
                "source_type": "surgery",
                "quantity": 1.0,
                "unit_price": service_price,
                "discount": 0.0,
                "source_model": self._name,
                "source_record_id": self.id,
                "sequence": 10,
            })
        ]
        if self.include_material_cost_in_bill:
            lines.append((0, 0, {
                "description": f"Surgery Materials - {self.name}",
                "source_type": "surgery",
                "quantity": 1.0,
                "unit_price": self.actual_material_cost,
                "discount": 0.0,
                "source_model": self._name,
                "source_record_id": self.id,
                "sequence": 20,
            }))

        bill = self.env["hospital.patient.bill"].create({
            "patient_id": self.patient_id.id,
            "physician_id": self._get_lead_surgeon_doctor().id or False,
            "bill_date": fields.Date.context_today(self),
            "cashier_id": self.env.user.id,
            "currency_id": self.currency_id.id or self.env.company.currency_id.id,
            "notes": (self.billing_note or "")
            + (f"\nGenerated from surgery {self.name}" if self.billing_note
               else f"Generated from surgery {self.name}"),
            "line_ids": lines,
        })

        self.write({"bill_id": bill.id})
        self._create_audit_log(
            action_type="update",
            description=f"Surgery bill generated: {bill.name} for surgery {self.name}.",
        )
        return self._open_record_action(
            "Patient Bill", "hospital.patient.bill", bill.id
        )

    def _get_lead_surgeon_doctor(self):
        """Return the lead surgeon's doctor record if one is assigned."""
        self.ensure_one()
        lead = self.team_member_ids.filtered(
            lambda m: m.role == "lead_surgeon" and m.physician_id
        )[:1]
        return lead.physician_id if lead else self.env["hospital.doctor"]

    def action_view_patient_bill(self):
        self.ensure_one()
        if not self.bill_id:
            raise UserError("No patient bill is linked to this surgery yet.")
        return self._open_record_action(
            "Patient Bill", "hospital.patient.bill", self.bill_id.id
        )
