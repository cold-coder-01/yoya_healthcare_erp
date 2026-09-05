from odoo import api, fields, models
from odoo.exceptions import UserError


class HospitalProcedureRequest(models.Model):
    _name = "hospital.procedure.request"
    _description = "Procedure / Clinical Service Request"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "request_datetime desc, id desc"

    # Clinical fields that must not be modified once a procedure record is
    # completed (done) or cancelled. Workflow/system fields (state, chatter,
    # write_uid/write_date, display_name) are intentionally excluded so the
    # workflow and mail threading keep working.
    PROTECTED_PROCEDURE_FIELDS = {
        "patient_id",
        "admission_id",
        "appointment_id",
        "physician_id",
        "procedure_type_id",
        "request_datetime",
        "scheduled_datetime",
        "start_datetime",
        "performed_datetime",
        "performed_by",
        "clinical_indication",
        "procedure_notes",
        "outcome",
        "complications",
        "notes",
        "active",
    }

    name = fields.Char(
        readonly=True,
        copy=False,
        default="New",
        tracking=True,
    )
    patient_id = fields.Many2one(
        "hospital.patient",
        string="Patient",
        required=True,
        ondelete="restrict",
        tracking=True,
    )
    admission_id = fields.Many2one(
        "hospital.admission",
        string="Admission",
        ondelete="restrict",
        domain="[('patient_id', '=', patient_id)]",
        tracking=True,
    )
    appointment_id = fields.Many2one(
        "hospital.appointment",
        string="Appointment",
        ondelete="set null",
    )
    physician_id = fields.Many2one(
        "hospital.doctor",
        string="Physician",
    )
    requested_by = fields.Many2one(
        "res.users",
        string="Requested By",
        default=lambda self: self.env.user,
    )
    procedure_type_id = fields.Many2one(
        "hospital.procedure.type",
        string="Procedure Type",
        required=True,
        ondelete="restrict",
        tracking=True,
    )
    category = fields.Selection(
        related="procedure_type_id.category",
        string="Category",
        store=True,
        readonly=True,
    )
    request_datetime = fields.Datetime(
        string="Request Date/Time",
        default=fields.Datetime.now,
    )
    scheduled_datetime = fields.Datetime(string="Scheduled Date/Time", tracking=True)
    start_datetime = fields.Datetime(string="Start Date/Time")
    performed_datetime = fields.Datetime(string="Performed Date/Time", tracking=True)
    performed_by = fields.Many2one(
        "res.users",
        string="Performed By",
        tracking=True,
    )
    clinical_indication = fields.Text(string="Clinical Indication")
    procedure_notes = fields.Text(string="Procedure Notes")
    outcome = fields.Text(string="Outcome")
    complications = fields.Text(string="Complications")
    default_price = fields.Monetary(
        string="Price Reference",
        related="procedure_type_id.default_price",
        currency_field="currency_id",
        store=True,
        readonly=True,
    )
    currency_id = fields.Many2one(
        "res.currency",
        string="Currency",
        default=lambda self: self.env.company.currency_id,
    )

    # ── Billing linkage (Task 28B) ──────────────────────────────────
    bill_id = fields.Many2one(
        "hospital.patient.bill",
        string="Patient Bill",
        readonly=True,
        copy=False,
        tracking=True,
    )
    bill_count = fields.Integer(
        compute="_compute_bill_count",
        string="Bills",
    )
    billing_state = fields.Selection(
        [
            ("not_billed", "Not Billed"),
            ("billed", "Billed"),
            ("partially_paid", "Partially Paid"),
            ("paid", "Paid"),
        ],
        compute="_compute_billing_state",
        string="Billing Status",
        store=True,
    )
    billed_amount = fields.Monetary(
        string="Billed Amount",
        compute="_compute_billing_amounts",
        currency_field="currency_id",
    )
    amount_paid = fields.Monetary(
        string="Amount Paid",
        compute="_compute_billing_amounts",
        currency_field="currency_id",
    )
    amount_due = fields.Monetary(
        string="Amount Due",
        compute="_compute_billing_amounts",
        currency_field="currency_id",
    )

    state = fields.Selection(
        [
            ("draft", "Draft"),
            ("requested", "Requested"),
            ("scheduled", "Scheduled"),
            ("in_progress", "In Progress"),
            ("done", "Done"),
            ("cancelled", "Cancelled"),
        ],
        default="draft",
        required=True,
        tracking=True,
    )
    active = fields.Boolean(default=True)
    notes = fields.Text(string="Notes")

    # ------------------------------------------------------------------
    # Display name
    # ------------------------------------------------------------------
    @api.depends("name", "patient_id", "procedure_type_id")
    def _compute_display_name(self):
        for rec in self:
            if rec.name and rec.name != "New":
                rec.display_name = rec.name
            elif rec.patient_id:
                rec.display_name = f"New - {rec.patient_id.display_name}"
            else:
                rec.display_name = "New"

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------
    @api.constrains("start_datetime", "request_datetime")
    def _check_start_datetime(self):
        for rec in self:
            if rec.start_datetime and rec.request_datetime and rec.start_datetime < rec.request_datetime:
                raise UserError("Start date/time cannot be before the request date/time.")

    @api.constrains("performed_datetime", "request_datetime")
    def _check_performed_datetime(self):
        for rec in self:
            if rec.performed_datetime and rec.request_datetime and rec.performed_datetime < rec.request_datetime:
                raise UserError("Performed date/time cannot be before the request date/time.")

    def _check_admission_requirement(self):
        for rec in self:
            if rec.procedure_type_id.requires_admission and not rec.admission_id:
                raise UserError(
                    "This procedure type requires an admission. "
                    "Please set the admission before continuing."
                )

    def _check_doctor_requirement(self):
        for rec in self:
            if rec.procedure_type_id.requires_doctor_approval and not rec.physician_id:
                raise UserError(
                    "This procedure type requires doctor approval. "
                    "Please set the physician before marking it done."
                )

    # ------------------------------------------------------------------
    # Workflow
    # ------------------------------------------------------------------
    def action_submit_request(self):
        for rec in self.filtered(lambda r: r.state == "draft"):
            rec._check_admission_requirement()
            old_state = rec.state
            rec.write({"state": "requested"})
            rec._create_audit_log(
                action_type="state_change",
                description="Procedure request submitted.",
                old_value=f"State: {old_state}",
                new_value="State: requested",
            )

    def action_schedule(self):
        for rec in self.filtered(lambda r: r.state == "requested"):
            rec._check_admission_requirement()
            old_state = rec.state
            rec.write({"state": "scheduled"})
            rec._create_audit_log(
                action_type="state_change",
                description="Procedure scheduled.",
                old_value=f"State: {old_state}",
                new_value="State: scheduled",
            )

    def action_start_procedure(self):
        for rec in self.filtered(lambda r: r.state in ("requested", "scheduled")):
            rec._check_admission_requirement()
            old_state = rec.state
            vals = {"state": "in_progress"}
            if not rec.start_datetime:
                vals["start_datetime"] = fields.Datetime.now()
            rec.write(vals)
            rec._create_audit_log(
                action_type="state_change",
                description="Procedure started.",
                old_value=f"State: {old_state}",
                new_value="State: in_progress",
            )

    def action_mark_done(self):
        for rec in self.filtered(lambda r: r.state in ("requested", "scheduled", "in_progress")):
            rec._check_admission_requirement()
            rec._check_doctor_requirement()
            old_state = rec.state
            vals = {"state": "done"}
            if not rec.performed_datetime:
                vals["performed_datetime"] = fields.Datetime.now()
            if not rec.performed_by:
                vals["performed_by"] = self.env.user.id
            rec.write(vals)
            rec._create_audit_log(
                action_type="state_change",
                description="Procedure marked done.",
                old_value=f"State: {old_state}",
                new_value="State: done",
            )

    def action_cancel(self):
        for rec in self.filtered(lambda r: r.state in ("draft", "requested", "scheduled", "in_progress")):
            old_state = rec.state
            rec.write({"state": "cancelled"})
            rec._create_audit_log(
                action_type="state_change",
                description="Procedure cancelled.",
                old_value=f"State: {old_state}",
                new_value="State: cancelled",
            )

    def action_reset_to_draft(self):
        for rec in self.filtered(lambda r: r.state == "cancelled"):
            rec.write({"state": "draft"})
            rec._create_audit_log(
                action_type="state_change",
                description="Procedure reset to draft.",
                old_value="State: cancelled",
                new_value="State: draft",
            )

    # ------------------------------------------------------------------
    # Billing (Task 28B)
    # ------------------------------------------------------------------
    def _compute_bill_count(self):
        for rec in self:
            rec.bill_count = 1 if rec.bill_id else 0

    @api.depends(
        "bill_id",
        "bill_id.state",
        "bill_id.amount_paid",
        "bill_id.amount_total",
        "bill_id.amount_due",
    )
    def _compute_billing_state(self):
        for rec in self:
            bill = rec.bill_id
            if not bill:
                rec.billing_state = "not_billed"
            elif bill.state == "paid" or (
                bill.amount_due <= 0 and bill.amount_total > 0
            ):
                rec.billing_state = "paid"
            elif bill.state == "partially_paid" or bill.amount_paid > 0:
                rec.billing_state = "partially_paid"
            else:
                rec.billing_state = "billed"

    @api.depends(
        "bill_id",
        "bill_id.amount_total",
        "bill_id.amount_paid",
        "bill_id.amount_due",
    )
    def _compute_billing_amounts(self):
        for rec in self:
            bill = rec.bill_id
            rec.billed_amount = bill.amount_total if bill else 0.0
            rec.amount_paid = bill.amount_paid if bill else 0.0
            rec.amount_due = bill.amount_due if bill else 0.0

    def action_generate_procedure_bill(self):
        self.ensure_one()
        if self.state != "done":
            raise UserError(
                "Procedure bill can only be generated after the procedure is completed."
            )
        if self.bill_id:
            raise UserError("This procedure has already been billed.")
        if not self.patient_id:
            raise UserError("Patient is required to generate a bill.")
        if self.procedure_type_id.default_price <= 0:
            raise UserError(
                "Procedure price is zero. Set a default price before billing."
            )

        bill_line = (0, 0, {
            "description": f"{self.procedure_type_id.name} - {self.name}",
            "source_type": "procedure",
            "quantity": 1.0,
            "unit_price": self.procedure_type_id.default_price,
            "source_model": self._name,
            "source_record_id": self.id,
            "sequence": 10,
        })

        bill = self.env["hospital.patient.bill"].create({
            "patient_id": self.patient_id.id,
            "physician_id": self.physician_id.id if self.physician_id else False,
            "appointment_id": self.appointment_id.id if self.appointment_id else False,
            "bill_date": fields.Date.context_today(self),
            "cashier_id": self.env.user.id,
            "currency_id": (
                self.currency_id.id or self.env.company.currency_id.id
            ),
            "notes": f"Generated from procedure {self.name}",
            "line_ids": [bill_line],
        })

        self.write({"bill_id": bill.id})

        self._create_audit_log(
            action_type="update",
            description=f"Procedure bill generated: {bill.name} for procedure {self.name}",
        )

        return {
            "type": "ir.actions.act_window",
            "name": "Patient Bill",
            "res_model": "hospital.patient.bill",
            "res_id": bill.id,
            "view_mode": "form",
            "target": "current",
        }

    def action_view_procedure_bill(self):
        self.ensure_one()
        if not self.bill_id:
            raise UserError("No bill has been generated for this procedure yet.")
        return {
            "type": "ir.actions.act_window",
            "name": "Patient Bill",
            "res_model": "hospital.patient.bill",
            "res_id": self.bill_id.id,
            "view_mode": "form",
            "target": "current",
        }

    # ------------------------------------------------------------------
    # ORM overrides
    # ------------------------------------------------------------------
    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if not vals.get("name") or vals.get("name") == "New":
                vals["name"] = (
                    self.env["ir.sequence"].next_by_code("hospital.procedure.request.sequence") or "New"
                )
        records = super().create(vals_list)
        for record in records:
            record._create_audit_log(
                action_type="create",
                description="Procedure request created.",
                new_value=record._audit_summary(["name", "patient_id", "procedure_type_id", "request_datetime"]),
            )
        return records

    def write(self, vals):
        protected_changed = self.PROTECTED_PROCEDURE_FIELDS.intersection(vals.keys())
        if protected_changed:
            for rec in self:
                if rec.state in ("done", "cancelled"):
                    rec._create_audit_log(
                        action_type="update",
                        description="Locked edit attempt on completed/cancelled procedure.",
                        old_value=rec._audit_summary(["name", "state"]),
                    )
                    raise UserError(
                        "Completed or cancelled procedure records are locked. "
                        "Reset to Draft before making corrections."
                    )
        tracked = {k: v for k, v in vals.items() if k not in ("write_date", "write_uid", "display_name")}
        old_values = {rec.id: rec._audit_summary(tracked.keys()) for rec in self}
        result = super().write(vals)
        if tracked and "state" not in tracked:
            for rec in self:
                rec._create_audit_log(
                    action_type="update",
                    description="Procedure request updated.",
                    old_value=old_values.get(rec.id),
                    new_value=rec._audit_summary(tracked.keys()),
                )
        return result

    def unlink(self):
        if not self.env.user.has_group("hospital_management.group_hospital_system_administrator"):
            for rec in self:
                rec._create_audit_log(
                    action_type="delete_attempt",
                    description="Deletion of procedure record blocked.",
                    old_value=rec._audit_summary(["name", "state"]),
                )
            raise UserError(
                "Procedure records are sensitive clinical data. "
                "Cancel or archive instead of deleting."
            )
        return super().unlink()

    # ------------------------------------------------------------------
    # Audit helpers
    # ------------------------------------------------------------------
    def _audit_summary(self, field_names):
        values = []
        for field_name in field_names:
            if field_name in self._fields:
                value = self[field_name]
                if hasattr(value, "mapped"):
                    value = ", ".join(value.mapped("display_name"))
                values.append(f"{field_name}: {value}")
        return "; ".join(values)

    def _create_audit_log(self, action_type, description, old_value=False, new_value=False):
        try:
            audit_log = self.env["hospital.audit.log"]
        except KeyError:
            return
        for rec in self:
            audit_log.with_context(audit_user_id=self.env.user.id).sudo().create_log(
                patient_id=rec.patient_id.id if rec.patient_id else False,
                model_name=rec._name,
                record_id=rec.id,
                action_type=action_type,
                description=description,
                old_value=old_value,
                new_value=new_value,
            )
