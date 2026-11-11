from odoo import api, fields, models
from odoo.exceptions import UserError


class HospitalGuaranteeLetter(models.Model):
    _name = "hospital.guarantee.letter"
    _description = "Payer Guarantee / Authorization Letter"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "name desc"

    name = fields.Char(
        readonly=True,
        copy=False,
        default="New",
    )
    patient_id = fields.Many2one(
        "hospital.patient",
        required=True,
        ondelete="restrict",
        tracking=True,
    )
    admission_id = fields.Many2one(
        "hospital.admission",
        ondelete="set null",
        tracking=True,
    )
    coverage_id = fields.Many2one(
        "hospital.patient.coverage",
        string="Coverage",
        ondelete="set null",
        tracking=True,
    )
    provider_id = fields.Many2one(
        "hospital.insurance.provider",
        string="Payer",
        tracking=True,
    )
    guarantee_reference = fields.Char(string="Guarantee Reference")
    issue_date = fields.Date(default=fields.Date.context_today)
    expiry_date = fields.Date()
    authorized_amount = fields.Monetary()
    used_amount = fields.Monetary(
        compute="_compute_used_amount",
        store=True,
    )
    remaining_amount = fields.Monetary(
        compute="_compute_used_amount",
        store=True,
    )
    coverage_percent = fields.Float(string="Coverage %", default=0.0)
    authorized_services = fields.Text()
    exclusions = fields.Text()
    claim_ids = fields.One2many(
        "hospital.insurance.claim",
        "guarantee_id",
        string="Claims",
    )
    state = fields.Selection(
        [
            ("draft", "Draft"),
            ("submitted", "Submitted"),
            ("approved", "Approved"),
            ("rejected", "Rejected"),
            ("expired", "Expired"),
            ("cancelled", "Cancelled"),
        ],
        default="draft",
        required=True,
        tracking=True,
    )
    approved_by = fields.Many2one("res.users", string="Approved By", readonly=True)
    approved_date = fields.Datetime(readonly=True)
    rejection_reason = fields.Text()
    currency_id = fields.Many2one(
        "res.currency",
        default=lambda self: self.env.company.currency_id,
    )
    notes = fields.Text()
    active = fields.Boolean(default=True)

    @api.depends(
        "authorized_amount",
        "claim_ids.claim_amount",
        "claim_ids.approved_amount",
        "claim_ids.state",
        "claim_ids.active",
    )
    def _compute_used_amount(self):
        for letter in self:
            used = 0.0
            for claim in letter.claim_ids:
                if not claim.active or claim.state == "cancelled":
                    continue
                # Prefer the payer-approved figure once available.
                used += claim.approved_amount or claim.claim_amount
            letter.used_amount = used
            letter.remaining_amount = letter.authorized_amount - used

    @api.onchange("coverage_id")
    def _onchange_coverage_id(self):
        if self.coverage_id:
            self.provider_id = self.coverage_id.provider_id
            if not self.coverage_percent:
                self.coverage_percent = self.coverage_id.coverage_percent

    @api.model_create_multi
    def create(self, vals_list):
        sequence = self.env["ir.sequence"]
        for vals in vals_list:
            if not vals.get("name") or vals.get("name") == "New":
                vals["name"] = (
                    sequence.next_by_code("hospital.guarantee.letter") or "New"
                )
        return super().create(vals_list)

    def unlink(self):
        if not self.env.user.has_group(
            "hospital_management.group_hospital_system_administrator"
        ):
            for letter in self:
                if letter.state == "approved":
                    raise UserError(
                        "Approved guarantee letters cannot be deleted. "
                        "Cancel or archive them instead."
                    )
        return super().unlink()

    def action_submit(self):
        for letter in self:
            if letter.state != "draft":
                raise UserError("Only draft guarantee letters can be submitted.")
            letter.state = "submitted"

    def action_approve(self):
        for letter in self:
            if letter.state != "submitted":
                raise UserError(
                    "Only submitted guarantee letters can be approved."
                )
            if not letter.provider_id:
                raise UserError("A payer is required before approval.")
            if letter.authorized_amount <= 0:
                raise UserError(
                    "An authorized amount is required before approval."
                )
            letter.write(
                {
                    "state": "approved",
                    "approved_by": self.env.user.id,
                    "approved_date": fields.Datetime.now(),
                }
            )

    def action_reject(self):
        for letter in self:
            if letter.state != "submitted":
                raise UserError(
                    "Only submitted guarantee letters can be rejected."
                )
            letter.state = "rejected"

    def action_expire(self):
        for letter in self:
            if letter.state not in ("submitted", "approved"):
                raise UserError(
                    "Only submitted or approved guarantee letters can expire."
                )
            letter.state = "expired"

    def action_cancel(self):
        for letter in self:
            if letter.state not in ("draft", "submitted", "approved"):
                raise UserError(
                    "Only draft, submitted, or approved guarantee letters can "
                    "be cancelled."
                )
            letter.state = "cancelled"

    def action_reset_to_draft(self):
        for letter in self:
            if letter.state not in ("rejected", "cancelled"):
                raise UserError(
                    "Only rejected or cancelled guarantee letters can be reset "
                    "to draft."
                )
            letter.write(
                {
                    "state": "draft",
                    "approved_by": False,
                    "approved_date": False,
                }
            )

    def _is_usable_for_claim(self):
        """A guarantee is usable only while approved and not expired."""
        self.ensure_one()
        if self.state != "approved":
            return False
        today = fields.Date.context_today(self)
        if self.expiry_date and self.expiry_date < today:
            return False
        return True
