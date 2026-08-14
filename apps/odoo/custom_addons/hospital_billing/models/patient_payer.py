"""Patient eligibility under a specific payer agreement.

Phase 2A is master-data foundation only.  Nothing in the live billing engine,
reception workflow, financial-clearance path, or responsibility allocation
reads this model yet.
"""

from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError

from .charge_line import G_ADMIN, G_MANAGER
from .payer_agreement import G_INSURANCE_OFFICER, PAYER_COMMERCIAL_READ


ELIGIBILITY_STATES = [
    ("draft", "Draft"),
    ("active", "Active"),
    ("suspended", "Suspended"),
    ("expired", "Expired"),
    ("cancelled", "Cancelled"),
]

RELATIONSHIPS = [
    ("self", "Self"),
    ("spouse", "Spouse"),
    ("child", "Child"),
    ("parent", "Parent"),
    ("employee", "Employee"),
    ("dependent", "Dependent"),
    ("other", "Other"),
]

ELIGIBILITY_OPERATORS = (G_INSURANCE_OFFICER, G_MANAGER, G_ADMIN)

ALLOWED_TRANSITIONS = {
    ("draft", "active"),
    ("active", "suspended"),
    ("suspended", "active"),
    ("active", "expired"),
    ("suspended", "expired"),
    ("draft", "cancelled"),
    ("active", "cancelled"),
    ("suspended", "cancelled"),
}

# Once verified, eligibility identity and boundaries are historical facts.  A
# suspended record may be resumed, but changing its agreement/member/window in
# place would make the verification stamp describe different data.
VERIFIED_FIELDS = frozenset(
    {
        "patient_id",
        "agreement_id",
        "member_reference",
        "membership_number",
        "policy_number",
        "employee_id_number",
        "principal_member_name",
        "relationship_to_principal",
        "effective_from",
        "effective_to",
        "member_limit_amount",
    }
)


def _member_limit_is_empty(value):
    """Report whether a caller supplied no member ceiling at all.

    The Odoo web client materialises an untouched Monetary input as 0.0 rather
    than omitting the key, so a create payload from the eligibility form always
    carries member_limit_amount even while the Member Limit group is invisible.
    False, None, 0 and 0.0 are therefore all the empty value on the wire; only
    member scope keeps the NULL-vs-zero distinction that makes an explicit 0.0
    a meaningful ceiling.
    """
    if value is False or value is None:
        return True
    return isinstance(value, (int, float)) and float(value) == 0.0


class HospitalPatientPayer(models.Model):
    _name = "hospital.patient.payer"
    _description = "Patient Eligibility Under a Payer Agreement"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "patient_id, effective_from desc"

    name = fields.Char(
        required=True,
        readonly=True,
        copy=False,
        default="New",
    )

    patient_id = fields.Many2one(
        "hospital.patient",
        required=True,
        ondelete="restrict",
        index=True,
        tracking=True,
    )
    agreement_id = fields.Many2one(
        "hospital.payer.agreement",
        required=True,
        ondelete="restrict",
        index=True,
        tracking=True,
    )
    payer_id = fields.Many2one(
        "hospital.payer",
        related="agreement_id.payer_id",
        store=True,
        readonly=True,
        index=True,
    )
    company_id = fields.Many2one(
        "res.company",
        related="agreement_id.company_id",
        store=True,
        readonly=True,
        index=True,
    )
    currency_id = fields.Many2one(
        "res.currency",
        related="agreement_id.currency_id",
        store=True,
        readonly=True,
    )

    member_reference = fields.Char(tracking=True)
    membership_number = fields.Char(tracking=True)
    policy_number = fields.Char(tracking=True)
    employee_id_number = fields.Char(tracking=True)
    principal_member_name = fields.Char(tracking=True)
    relationship_to_principal = fields.Selection(
        RELATIONSHIPS,
        required=True,
        default="self",
        tracking=True,
    )

    effective_from = fields.Date(
        required=True,
        default=fields.Date.context_today,
        tracking=True,
    )
    effective_to = fields.Date(tracking=True)
    state = fields.Selection(
        ELIGIBILITY_STATES,
        required=True,
        default="draft",
        copy=False,
        index=True,
        tracking=True,
    )
    # The per-member ceiling. Group-protected for the same reason as
    # hospital.payer.agreement.limit_amount: the Front Desk Nurse holds a read
    # ACL on this model in order to SELECT an eligibility identity, and a record
    # rule cannot hide a column. See PAYER_COMMERCIAL_READ in payer_agreement.py.
    member_limit_amount = fields.Monetary(
        currency_field="currency_id",
        tracking=True,
        groups=PAYER_COMMERCIAL_READ,
    )
    is_valid_today = fields.Boolean(
        compute="_compute_is_valid_today",
        help="Active eligibility whose own window and agreement window both include today.",
    )

    verified_by_id = fields.Many2one(
        "res.users",
        readonly=True,
        copy=False,
    )
    verified_at = fields.Datetime(readonly=True, copy=False)
    notes = fields.Text(groups=PAYER_COMMERCIAL_READ)
    active = fields.Boolean(default=True)

    _sql_constraints = [
        (
            "patient_payer_patient_agreement_start_unique",
            "unique(patient_id, agreement_id, effective_from)",
            "A patient already has eligibility for this agreement starting on this date.",
        ),
        (
            "patient_payer_effective_window_ordered",
            "CHECK(effective_to IS NULL OR effective_to >= effective_from)",
            "The eligibility end date cannot precede its start date.",
        ),
        (
            "patient_payer_member_limit_non_negative",
            "CHECK(member_limit_amount IS NULL OR member_limit_amount >= 0)",
            "The member limit amount cannot be negative.",
        ),
    ]

    @api.depends(
        "state",
        "effective_from",
        "effective_to",
        "agreement_id.state",
        "agreement_id.effective_from",
        "agreement_id.effective_to",
    )
    def _compute_is_valid_today(self):
        today = fields.Date.context_today(self)
        for eligibility in self:
            agreement = eligibility.agreement_id
            eligibility.is_valid_today = bool(
                eligibility.state == "active"
                and eligibility.effective_from
                and eligibility.effective_from <= today
                and (not eligibility.effective_to or eligibility.effective_to >= today)
                and agreement.state == "active"
                and agreement.effective_from
                and agreement.effective_from <= today
                and (not agreement.effective_to or agreement.effective_to >= today)
            )

    def _assert_operator(self, action):
        if self.env.su:
            return
        if not any(self.env.user.has_group(group) for group in ELIGIBILITY_OPERATORS):
            raise AccessError(
                "You are not authorized to %s. Required: Insurance/Credit Officer, "
                "Hospital Manager, or System Administrator." % action
            )

    @api.constrains("agreement_id", "effective_from", "effective_to")
    def _check_agreement_window(self):
        for eligibility in self:
            eligibility._assert_window_inside_agreement()

    def _assert_window_inside_agreement(self):
        self.ensure_one()
        agreement = self.agreement_id
        if not agreement or not self.effective_from:
            return
        if self.effective_from < agreement.effective_from:
            raise ValidationError(
                "Eligibility %s starts on %s, before agreement %s starts on %s."
                % (
                    self.name or "New",
                    self.effective_from,
                    agreement.display_name,
                    agreement.effective_from,
                )
            )
        if agreement.effective_to and self.effective_from > agreement.effective_to:
            raise ValidationError(
                "Eligibility %s starts on %s, after agreement %s ended on %s."
                % (
                    self.name or "New",
                    self.effective_from,
                    agreement.display_name,
                    agreement.effective_to,
                )
            )
        if (
            agreement.effective_to
            and self.effective_to
            and self.effective_to > agreement.effective_to
        ):
            raise ValidationError(
                "Eligibility %s ends on %s, after agreement %s ends on %s."
                % (
                    self.name or "New",
                    self.effective_to,
                    agreement.display_name,
                    agreement.effective_to,
                )
            )
        # An empty eligibility end follows a finite agreement boundary for
        # validity.  It stays NULL: copying the agreement date would turn a
        # derived boundary into duplicated data.

    @api.constrains("agreement_id", "member_limit_amount")
    def _check_member_limit_scope(self):
        for eligibility in self:
            # Read the amount through sudo(). member_limit_amount is now
            # group-protected, and this constraint fires for the Front Desk
            # Nurse, who still holds the draft-eligibility ACL but not
            # PAYER_COMMERCIAL_READ. Without sudo the ORM would raise AccessError
            # here instead of the ValidationError the caller must actually see.
            # This grants nothing: the value is only compared, never returned.
            record = eligibility.sudo()
            scope = record.agreement_id.limit_scope
            amount = record.member_limit_amount
            if scope != "member" and amount:
                raise ValidationError(
                    "Eligibility %s must not carry a member limit because agreement %s "
                    "uses limit scope '%s'."
                    % (
                        eligibility.name or "New",
                        eligibility.agreement_id.display_name,
                        scope or "unset",
                    )
                )

    @api.model
    def _assert_member_limit_input(self, agreement, vals, existing=None):
        """Preserve the NULL-vs-zero distinction that Monetary cache values lose.

        Under member scope zero is a valid explicit ceiling, while NULL means no
        value was supplied.  Once read into the ORM both can look falsey, so
        create/write validate the caller's value before conversion.

        Under every other scope there is no ceiling to express and the web client
        cannot omit the Monetary field its form is hiding, so a supplied zero is
        the empty value rather than a zero ceiling.  Returns True when the caller
        supplied such an empty value, which create/write then clear rather than
        persist verbatim.
        """
        supplied = "member_limit_amount" in vals
        value = vals.get("member_limit_amount") if supplied else None
        if agreement.limit_scope == "member":
            if supplied and (value is False or value is None):
                raise ValidationError(
                    "A per-member agreement requires an explicit member limit amount."
                )
            if existing is None and not supplied:
                raise ValidationError(
                    "A per-member agreement requires an explicit member limit amount."
                )
            if existing is not None and "agreement_id" in vals and not supplied:
                raise ValidationError(
                    "Selecting a per-member agreement requires an explicit member limit amount."
                )
            return False
        if supplied:
            if not _member_limit_is_empty(value):
                raise ValidationError(
                    "Member limit amount must be empty unless the agreement limit scope is Member."
                )
            return True
        if existing is not None and "agreement_id" in vals:
            # Changing away from a member agreement must explicitly clear even a
            # zero ceiling; zero and SQL NULL are indistinguishable in cache.
            raise ValidationError(
                "Changing to a non-member agreement requires explicitly clearing "
                "the member limit amount."
            )
        return False

    def _lock_overlap_scope(self):
        self.ensure_one()
        self.env.cr.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
            [
                "hospital.patient.payer.overlap:%s:%s"
                % (self.patient_id.id, self.agreement_id.id)
            ],
        )

    def _assert_no_active_overlap(self):
        self.ensure_one()
        domain = [
            ("id", "!=", self.id),
            ("patient_id", "=", self.patient_id.id),
            ("agreement_id", "=", self.agreement_id.id),
            ("state", "=", "active"),
            "|",
            ("effective_to", "=", False),
            ("effective_to", ">=", self.effective_from),
        ]
        if self.effective_to:
            domain.append(("effective_from", "<=", self.effective_to))
        clash = self.sudo().with_context(active_test=False).search(domain, limit=1)
        if clash:
            raise UserError(
                "Eligibility %s overlaps active eligibility %s for patient %s and "
                "agreement %s. Active eligibility date windows are inclusive."
                % (
                    self.display_name,
                    clash.display_name,
                    self.patient_id.display_name,
                    self.agreement_id.display_name,
                )
            )

    def _assert_activation_ready(self):
        self.ensure_one()
        self._assert_window_inside_agreement()
        self._check_member_limit_scope()
        if self.agreement_id.state != "active":
            raise UserError(
                "Eligibility %s cannot activate because agreement %s is %s, not active."
                % (self.display_name, self.agreement_id.display_name, self.agreement_id.state)
            )

    def _assert_transition(self, new_state):
        self.ensure_one()
        if new_state == self.state:
            return
        if (self.state, new_state) not in ALLOWED_TRANSITIONS:
            raise UserError(
                "Eligibility %s cannot move from '%s' to '%s'."
                % (self.display_name, self.state, new_state)
            )
        self._assert_operator("move eligibility %s to '%s'" % (self.display_name, new_state))

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            # company_id is derived exclusively from the required agreement.
            # Discard RPC/import input so callers cannot seed a conflicting
            # stored-related cache value before its dependency is recomputed.
            vals.pop("company_id", None)
            if vals.get("state", "draft") != "draft":
                raise UserError(
                    "Patient eligibility is always created in Draft. Use Activate after review."
                )
            if vals.get("verified_by_id") or vals.get("verified_at"):
                raise UserError("Verification stamps are set only by eligibility activation.")
            agreement = self.env["hospital.payer.agreement"].browse(vals.get("agreement_id"))
            if agreement and self._assert_member_limit_input(agreement, vals):
                # Drop the key instead of setting False.  Monetary's column
                # conversion coerces False to 0.0, so only an absent key leaves
                # the column NULL.  A non-member eligibility saved from the form
                # then stores exactly what one created with the field omitted
                # stores, and the IS NULL branch of the CHECK constraint keeps
                # meaning what it says.
                vals.pop("member_limit_amount")
            vals["name"] = (
                self.env["ir.sequence"].next_by_code("hospital.patient.payer") or "New"
            )
        return super().create(vals_list)

    def write(self, vals):
        if "name" in vals:
            raise UserError("Eligibility references are generated and cannot be changed.")
        if "company_id" in vals:
            raise UserError(
                "Eligibility company is derived from its payer agreement and "
                "cannot be set independently."
            )
        if "verified_by_id" in vals or "verified_at" in vals:
            raise UserError("Verification stamps are managed by the eligibility workflow.")

        new_state = vals.get("state")
        # Scope is a per-record property, so one RPC may need the supplied zero
        # cleared on a non-member row and kept on a member row.  Track which
        # rows normalise instead of rewriting the shared vals dict.
        cleared_ids = set()
        for eligibility in self:
            agreement = (
                self.env["hospital.payer.agreement"].browse(vals["agreement_id"])
                if vals.get("agreement_id")
                else eligibility.agreement_id
            )
            if "agreement_id" in vals or "member_limit_amount" in vals:
                if self._assert_member_limit_input(agreement, vals, existing=eligibility):
                    cleared_ids.add(eligibility.id)
            if new_state:
                eligibility._assert_transition(new_state)
            if eligibility.state != "draft":
                eligibility._assert_operator("edit non-draft eligibility %s" % eligibility.display_name)
                blocked = VERIFIED_FIELDS & set(vals)
                if blocked:
                    raise UserError(
                        "Eligibility %s is %s. Verified identity, agreement, limit, and "
                        "validity fields are frozen: %s. Cancel it and create a new draft."
                        % (
                            eligibility.display_name,
                            eligibility.state,
                            ", ".join(sorted(blocked)),
                        )
                    )
            if new_state == "active" and eligibility.state != "active":
                eligibility._assert_activation_ready()
                eligibility._lock_overlap_scope()
                eligibility._assert_no_active_overlap()

        if new_state == "active" or cleared_ids:
            # A multi-record RPC may activate drafts and resume suspended rows in
            # one call.  Stamp only first verification, never a resume.
            for eligibility in self:
                record_vals = dict(vals)
                if eligibility.id in cleared_ids:
                    # An UPDATE has no "leave it NULL" value to send, so False is
                    # the clear Monetary can express.  It stores 0.00, which is
                    # falsey and satisfies _check_member_limit_scope exactly as
                    # NULL does; dropping the key instead would silently keep a
                    # stale ceiling the caller asked to remove.
                    record_vals["member_limit_amount"] = False
                if new_state == "active" and eligibility.state == "draft":
                    record_vals.update(
                        {
                            "verified_by_id": self.env.user.id,
                            "verified_at": fields.Datetime.now(),
                        }
                    )
                super(HospitalPatientPayer, eligibility).write(record_vals)
            return True
        return super().write(vals)

    def unlink(self):
        for eligibility in self:
            eligibility._assert_operator("delete draft eligibility %s" % eligibility.display_name)
            if eligibility.state != "draft":
                raise UserError(
                    "Eligibility %s is %s and cannot be deleted. Cancel or archive it instead."
                    % (eligibility.display_name, eligibility.state)
                )
        return super().unlink()

    def action_activate(self):
        return self.write({"state": "active"})

    def action_suspend(self):
        return self.write({"state": "suspended"})

    def action_resume(self):
        return self.write({"state": "active"})

    def action_expire(self):
        return self.write({"state": "expired"})

    def action_cancel(self):
        return self.write({"state": "cancelled"})
