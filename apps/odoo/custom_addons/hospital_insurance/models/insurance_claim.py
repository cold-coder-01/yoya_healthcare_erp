from odoo import api, fields, models
from odoo.exceptions import UserError


class HospitalInsuranceClaim(models.Model):
    _name = "hospital.insurance.claim"
    _description = "Insurance / Payer Claim"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "claim_date desc, name desc"

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
    )
    bill_id = fields.Many2one(
        "hospital.patient.bill",
        string="Patient Bill",
        required=True,
        ondelete="restrict",
        tracking=True,
    )
    coverage_id = fields.Many2one(
        "hospital.patient.coverage",
        string="Coverage",
        ondelete="set null",
    )
    guarantee_id = fields.Many2one(
        "hospital.guarantee.letter",
        string="Guarantee Letter",
        ondelete="set null",
    )
    provider_id = fields.Many2one(
        "hospital.insurance.provider",
        string="Payer",
        required=True,
        ondelete="restrict",
        tracking=True,
    )
    claim_date = fields.Datetime(default=fields.Datetime.now, tracking=True)
    submitted_date = fields.Datetime(readonly=True)
    approved_date = fields.Datetime(readonly=True)
    rejected_date = fields.Datetime(readonly=True)
    paid_date = fields.Datetime(readonly=True)
    claim_amount = fields.Monetary(
        compute="_compute_claim_amount",
        store=True,
        tracking=True,
    )
    approved_amount = fields.Monetary(tracking=True)
    rejected_amount = fields.Monetary(
        compute="_compute_rejected_amount",
        store=True,
    )
    patient_responsibility_amount = fields.Monetary()
    payer_responsibility_amount = fields.Monetary()
    amount_paid_by_payer = fields.Monetary(tracking=True)
    amount_due_from_payer = fields.Monetary(
        compute="_compute_amount_due_from_payer",
        store=True,
    )
    currency_id = fields.Many2one(
        "res.currency",
        default=lambda self: self.env.company.currency_id,
    )
    state = fields.Selection(
        [
            ("draft", "Draft"),
            ("submitted", "Submitted"),
            ("under_review", "Under Review"),
            ("approved", "Approved"),
            ("partially_approved", "Partially Approved"),
            ("rejected", "Rejected"),
            ("paid", "Paid"),
            ("cancelled", "Cancelled"),
        ],
        default="draft",
        required=True,
        tracking=True,
    )
    line_ids = fields.One2many(
        "hospital.insurance.claim.line",
        "claim_id",
        string="Claim Lines",
    )
    rejection_reason = fields.Text()
    notes = fields.Text()
    active = fields.Boolean(default=True)

    @api.depends("line_ids.claimed_amount")
    def _compute_claim_amount(self):
        for claim in self:
            claim.claim_amount = sum(claim.line_ids.mapped("claimed_amount"))

    @api.depends("claim_amount", "approved_amount")
    def _compute_rejected_amount(self):
        for claim in self:
            claim.rejected_amount = max(
                0.0, claim.claim_amount - claim.approved_amount
            )

    @api.depends("approved_amount", "amount_paid_by_payer")
    def _compute_amount_due_from_payer(self):
        for claim in self:
            claim.amount_due_from_payer = max(
                0.0, claim.approved_amount - claim.amount_paid_by_payer
            )

    @api.model_create_multi
    def create(self, vals_list):
        sequence = self.env["ir.sequence"]
        for vals in vals_list:
            if not vals.get("name") or vals.get("name") == "New":
                vals["name"] = (
                    sequence.next_by_code("hospital.insurance.claim") or "New"
                )
        return super().create(vals_list)

    def unlink(self):
        if not self.env.user.has_group(
            "hospital_management.group_hospital_system_administrator"
        ):
            for claim in self:
                if claim.state in ("approved", "partially_approved", "paid"):
                    raise UserError(
                        "Approved or paid claims cannot be deleted. "
                        "Cancel or archive them instead."
                    )
        return super().unlink()

    def _sync_bill_insurance_state(self):
        """Reflect the claim state onto the originating bill (informational)."""
        state_map = {
            "draft": "ready_to_claim",
            "submitted": "claim_submitted",
            "under_review": "claim_submitted",
            "approved": "claim_approved",
            "partially_approved": "claim_approved",
            "rejected": "claim_rejected",
            "paid": "claim_paid",
        }
        for claim in self:
            bill = claim.bill_id
            if not bill:
                continue
            new_state = state_map.get(claim.state)
            if new_state and bill.insurance_state != new_state:
                bill.insurance_state = new_state

    def action_submit(self):
        for claim in self:
            if claim.state != "draft":
                raise UserError("Only draft claims can be submitted.")
            if not (claim.bill_id and claim.provider_id and claim.patient_id):
                raise UserError(
                    "A claim requires a bill, payer, and patient before "
                    "submission."
                )
            claim.write(
                {
                    "state": "submitted",
                    "submitted_date": fields.Datetime.now(),
                }
            )
        self._sync_bill_insurance_state()

    def action_mark_under_review(self):
        for claim in self:
            if claim.state != "submitted":
                raise UserError(
                    "Only submitted claims can be marked under review."
                )
            claim.state = "under_review"
        self._sync_bill_insurance_state()

    def action_approve(self):
        for claim in self:
            if claim.state not in ("submitted", "under_review"):
                raise UserError(
                    "Only submitted or under-review claims can be approved."
                )
            # Default the approved amount to the full claim when unset.
            if not claim.approved_amount:
                claim.approved_amount = claim.claim_amount
                for line in claim.line_ids:
                    if not line.approved_amount:
                        line.approved_amount = line.claimed_amount
            if claim.approved_amount > claim.claim_amount:
                raise UserError(
                    "Approved amount cannot exceed the claim amount."
                )
            if claim.approved_amount < claim.claim_amount:
                new_state = "partially_approved"
            else:
                new_state = "approved"
            claim.write(
                {
                    "state": new_state,
                    "approved_date": fields.Datetime.now(),
                }
            )
        self._sync_bill_insurance_state()

    def action_reject(self):
        for claim in self:
            if claim.state not in ("submitted", "under_review"):
                raise UserError(
                    "Only submitted or under-review claims can be rejected."
                )
            claim.write(
                {
                    "state": "rejected",
                    "approved_amount": 0.0,
                    "rejected_date": fields.Datetime.now(),
                }
            )
        self._sync_bill_insurance_state()

    def action_register_payer_payment(self):
        for claim in self:
            if claim.state not in ("approved", "partially_approved"):
                raise UserError(
                    "Only approved claims can register a payer payment."
                )
            if claim.approved_amount <= 0:
                raise UserError(
                    "The approved amount must be positive before registering "
                    "a payment."
                )
            # Foundation: assume the payer settles the full approved amount.
            if not claim.amount_paid_by_payer:
                claim.amount_paid_by_payer = claim.approved_amount
            if claim.amount_paid_by_payer >= claim.approved_amount:
                claim.write(
                    {
                        "state": "paid",
                        "paid_date": fields.Datetime.now(),
                    }
                )
        self._sync_bill_insurance_state()

    def action_cancel(self):
        for claim in self:
            if claim.state not in ("draft", "submitted", "under_review"):
                raise UserError(
                    "Only draft, submitted, or under-review claims can be "
                    "cancelled."
                )
            claim.state = "cancelled"
        self._sync_bill_insurance_state()

    def action_reset_to_draft(self):
        for claim in self:
            if claim.state not in ("rejected", "cancelled"):
                raise UserError(
                    "Only rejected or cancelled claims can be reset to draft."
                )
            claim.write(
                {
                    "state": "draft",
                    "submitted_date": False,
                    "approved_date": False,
                    "rejected_date": False,
                    "paid_date": False,
                }
            )
        self._sync_bill_insurance_state()
