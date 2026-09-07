from odoo import api, fields, models
from odoo.exceptions import UserError


class HospitalStockMovement(models.Model):
    _name = "hospital.stock.movement"
    _description = "Hospital Stock Movement"
    _inherit = ["mail.thread"]
    _order = "movement_date desc, id desc"

    name = fields.Char(
        readonly=True,
        copy=False,
        default="New",
        tracking=True,
    )
    movement_date = fields.Datetime(
        string="Movement Date",
        default=fields.Datetime.now,
        readonly=True,
    )
    movement_type = fields.Selection(
        [
            ("receipt", "Receipt"),
            ("issue", "Issue"),
            ("return", "Return"),
            ("transfer", "Transfer"),
            ("adjustment", "Adjustment"),
            ("consumption", "Consumption"),
        ],
        string="Movement Type",
        required=True,
        readonly=True,
    )
    item_id = fields.Many2one(
        "hospital.inventory.item",
        string="Item",
        required=True,
        ondelete="restrict",
        readonly=True,
    )
    batch_id = fields.Many2one(
        "hospital.inventory.batch",
        string="Batch / Lot",
        ondelete="restrict",
        readonly=True,
    )
    from_location_id = fields.Many2one(
        "hospital.inventory.location",
        string="From Location",
        ondelete="set null",
        readonly=True,
    )
    to_location_id = fields.Many2one(
        "hospital.inventory.location",
        string="To Location",
        ondelete="set null",
        readonly=True,
    )
    from_department_id = fields.Many2one(
        "hospital.department",
        string="From Department",
        ondelete="set null",
        readonly=True,
    )
    to_department_id = fields.Many2one(
        "hospital.department",
        string="To Department",
        ondelete="set null",
        readonly=True,
    )
    quantity = fields.Float(string="Quantity", required=True, readonly=True)
    unit_cost = fields.Monetary(string="Unit Cost", currency_field="currency_id", readonly=True)
    currency_id = fields.Many2one(
        "res.currency",
        string="Currency",
        default=lambda self: self.env.company.currency_id,
        readonly=True,
    )
    movement_value = fields.Monetary(
        compute="_compute_movement_value",
        string="Movement Value",
        currency_field="currency_id",
        store=True,
        help="quantity × unit cost. Informational valuation only — no "
        "accounting entry is posted.",
    )
    inventory_accounting_state = fields.Selection(
        [
            ("not_applicable", "Not Applicable"),
            ("pending", "Pending"),
            ("ready", "Ready"),
            ("posted", "Posted"),
            ("error", "Error"),
            ("reversed", "Reversed"),
        ],
        string="Accounting State",
        default="not_applicable",
        required=True,
        readonly=True,
        help="Accounting-readiness flag for a future inventory/accounting "
        "bridge. No journal entry is posted in this module.",
    )
    accounting_note = fields.Text(string="Accounting Note", readonly=True)
    related_request_id = fields.Many2one(
        "hospital.stock.request",
        string="Related Request",
        ondelete="set null",
        readonly=True,
    )
    consumption_id = fields.Many2one(
        "hospital.stock.consumption",
        string="Consumption",
        ondelete="set null",
        readonly=True,
    )
    patient_id = fields.Many2one(
        "hospital.patient",
        string="Patient",
        ondelete="set null",
        readonly=True,
    )
    admission_id = fields.Many2one(
        "hospital.admission",
        string="Admission",
        ondelete="set null",
        readonly=True,
    )
    procedure_id = fields.Many2one(
        "hospital.procedure.request",
        string="Procedure",
        ondelete="set null",
        readonly=True,
    )
    nursing_round_id = fields.Many2one(
        "hospital.nursing.round",
        string="Nursing Round",
        ondelete="set null",
        readonly=True,
    )
    performed_by = fields.Many2one(
        "res.users",
        string="Performed By",
        default=lambda self: self.env.user,
        readonly=True,
    )
    notes = fields.Text()
    active = fields.Boolean(default=True)

    # ------------------------------------------------------------------
    # Computed
    # ------------------------------------------------------------------
    @api.depends("quantity", "unit_cost")
    def _compute_movement_value(self):
        for rec in self:
            rec.movement_value = (rec.quantity or 0.0) * (rec.unit_cost or 0.0)

    # ------------------------------------------------------------------
    # ORM overrides + audit
    # ------------------------------------------------------------------
    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if not vals.get("name") or vals.get("name") == "New":
                vals["name"] = (
                    self.env["ir.sequence"].next_by_code("hospital.stock.movement.sequence")
                    or "New"
                )
        records = super().create(vals_list)
        for record in records:
            record._create_audit_log(
                action_type="create",
                description="Stock movement recorded.",
                patient_id=record.patient_id.id if record.patient_id else False,
                new_value=record._audit_summary(
                    ["name", "movement_type", "item_id", "batch_id", "quantity"]
                ),
            )
        return records

    def unlink(self):
        if not self.env.user.has_group("hospital_management.group_hospital_system_administrator"):
            for rec in self:
                rec._create_audit_log(
                    action_type="delete_attempt",
                    description="Deletion of stock movement blocked.",
                    patient_id=rec.patient_id.id if rec.patient_id else False,
                    old_value=rec._audit_summary(["name", "movement_type", "item_id", "quantity"]),
                )
            raise UserError(
                "Stock movements are an immutable audit trail and cannot be deleted."
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

    def _create_audit_log(self, action_type, description, patient_id=False, old_value=False, new_value=False):
        try:
            audit_log = self.env["hospital.audit.log"]
        except KeyError:
            return
        for rec in self:
            audit_log.with_context(audit_user_id=self.env.user.id).sudo().create_log(
                patient_id=patient_id,
                model_name=rec._name,
                record_id=rec.id,
                action_type=action_type,
                description=description,
                old_value=old_value,
                new_value=new_value,
            )
