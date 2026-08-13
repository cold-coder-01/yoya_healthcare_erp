"""The hospital <-> payer agreement, and its version chain.

THE OBJECT THAT DID NOT EXIST
-----------------------------
Before this, a sponsored visit pointed at a bare res.partner. There was no
contract, no validity window, no ceiling and no terms -- so nothing could say
whether a payer was allowed to cover a visit, or up to what.

WHY VERSIONING IS STRUCTURAL AND NOT A NICETY
---------------------------------------------
Terms are frozen the moment an agreement activates. Amending a live contract
creates a NEW VERSION and supersedes the old one; it never edits it in place.
That is what stops a limit raised next year from silently rewriting the
responsibility split of a visit that happened last year.

This mirrors the discipline hospital.charge.line already applies to its
commercial snapshot (see FROZEN_PRICING_FIELDS in charge_line.py, and the
CHRG000063 incident documented in billing_engine.create_or_update_charge): a
snapshot that can be rewritten is not a snapshot.

PHASE 1 SCOPE
-------------
This module creates the master data and its lifecycle ONLY. It changes no live
behaviour: nothing reads an agreement yet, allocate_payer() is still
unimplemented, and check_financial_clearance() is untouched. Bounded limits
cannot be activated at all until the Phase 4 utilization ledger exists -- see
_check_activation_ready().
"""

from datetime import timedelta

from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError

# Imported rather than restated. charge_line.py is the single source of truth for
# these xmlids; three independent copies of a group tuple is exactly how the
# standalone Cashier came to be locked out of the payment path.
from .charge_line import G_ACCOUNTANT, G_ADMIN, G_MANAGER

# Declared in this module's own security/hospital_billing_groups.xml. Named here
# rather than imported from a downstream module for the usual reason: the guard
# has to be resolvable from inside hospital_billing.
G_INSURANCE_OFFICER = "hospital_billing.group_hospital_insurance_officer"

# May draft and edit an agreement, but NOT bring one into force.
DRAFTING_GROUPS = (G_INSURANCE_OFFICER, G_ACCOUNTANT, G_MANAGER, G_ADMIN)

# THE authorization boundary for every state change. Activating, suspending,
# expiring, superseding and terminating an agreement all commit the hospital to
# (or release it from) a commercial position, so they are manager-and-above.
# The Insurance/Credit Officer is deliberately ABSENT: preparing terms and
# committing to them are separable duties.
ACTIVATION_GROUPS = (G_MANAGER, G_ADMIN)

LIMIT_SCOPES = [
    ("unlimited", "Unlimited"),
    ("agreement", "Agreement-wide Pool"),
    ("member", "Per Member"),
    ("visit", "Per Visit Cap"),
]

AGREEMENT_STATES = [
    ("draft", "Draft"),
    ("active", "Active"),
    ("suspended", "Suspended"),
    ("expired", "Expired"),
    ("superseded", "Superseded"),
    ("terminated", "Terminated"),
]

LIVE_STATES = ("active", "suspended")

# Exactly the columns _supersede_source() writes. Flushed explicitly during an
# amendment activation so PostgreSQL sees the predecessor leave 'active' BEFORE
# the successor enters it. state and effective_to are what the EXCLUDE
# constraint reads, so this set is sufficient as well as minimal.
SUPERSESSION_FLUSH_FIELDS = ("state", "effective_to", "superseded_by_id")

# Every term that could change a historical responsibility split. Frozen the
# moment the agreement leaves draft, on EVERY write path -- there is no context
# key that lifts this, deliberately. Changing any of them is an amendment.
#
# effective_to is absent because it has a stricter rule of its own: it may be
# SHORTENED but never extended (see _assert_effective_to_shortening).
FROZEN_TERM_FIELDS = frozenset(
    {
        "payer_id",
        "agreement_number",
        "version",
        "supersedes_id",
        "company_id",
        "effective_from",
        "limit_scope",
        "limit_amount",
        "authorization_required",
        "guarantee_required",
        "tariff_mode",
    }
)

# The only state changes that exist. Anything else is refused rather than
# silently accepted -- an unlisted transition is a bug, not a business case.
ALLOWED_TRANSITIONS = {
    ("draft", "active"),
    ("active", "suspended"),
    ("suspended", "active"),
    ("active", "expired"),
    ("suspended", "expired"),
    ("active", "superseded"),
    ("active", "terminated"),
    ("suspended", "terminated"),
    ("expired", "terminated"),
    ("superseded", "terminated"),
}

BOUNDED_LIMIT_BLOCKED = (
    "Bounded payer limits require the utilization ledger.\n"
    "Only Unlimited agreements can be activated at this time."
)


class HospitalPayerAgreement(models.Model):
    _name = "hospital.payer.agreement"
    _description = "Hospital ↔ Payer Agreement"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "payer_id, version desc, effective_from desc"

    name = fields.Char(
        required=True,
        readonly=True,
        copy=False,
        default="New",
    )

    # --- Identity -----------------------------------------------------
    payer_id = fields.Many2one(
        "hospital.payer",
        required=True,
        ondelete="restrict",
        index=True,
        tracking=True,
    )
    agreement_number = fields.Char(
        required=True,
        tracking=True,
        help="The contract reference as the payer's own paperwork states it. "
        "STABLE ACROSS VERSIONS -- an amendment keeps the number and increments "
        "the version.",
    )
    version = fields.Integer(
        required=True,
        default=1,
        readonly=True,
        copy=False,
    )
    supersedes_id = fields.Many2one(
        "hospital.payer.agreement",
        string="Amends",
        ondelete="restrict",
        readonly=True,
        copy=False,
        index=True,
    )
    superseded_by_id = fields.Many2one(
        "hospital.payer.agreement",
        string="Superseded By",
        ondelete="restrict",
        readonly=True,
        copy=False,
        index=True,
        help="Written in the same transaction that activates the successor. Not "
        "an inverse compute: supersession is a transactional fact, and deriving "
        "it would let a half-finished amendment look complete.",
    )

    company_id = fields.Many2one(
        "res.company",
        required=True,
        default=lambda self: self.env.company,
        index=True,
    )
    currency_id = fields.Many2one(
        "res.currency",
        related="company_id.currency_id",
        store=True,
        readonly=True,
    )

    # --- Validity -----------------------------------------------------
    effective_from = fields.Date(required=True, tracking=True)
    effective_to = fields.Date(
        tracking=True,
        help="Empty means OPEN-ENDED. No renewal period is assumed or implied: "
        "the contract's duration is data, never a hardcoded interval.",
    )
    state = fields.Selection(
        AGREEMENT_STATES,
        required=True,
        default="draft",
        index=True,
        copy=False,
        tracking=True,
    )
    is_valid_today = fields.Boolean(
        string="Valid Today",
        compute="_compute_is_valid_today",
        help="Active, and today falls inside the effective window.",
    )

    # --- Limit terms --------------------------------------------------
    #
    # NO DEFAULT ON limit_scope, and that is the single most deliberate choice in
    # this model. The hospital has not yet confirmed what its payer limits are
    # actually attached to. Defaulting the field would encode a guess as data and
    # make it invisible; leaving it required-with-no-default forces a Manager to
    # state the answer explicitly, per agreement, on the record.
    limit_scope = fields.Selection(
        LIMIT_SCOPES,
        required=True,
        tracking=True,
        help="What the monetary ceiling is attached to. Deliberately has no "
        "default: it must be chosen explicitly for every agreement.",
    )
    limit_amount = fields.Monetary(
        currency_field="currency_id",
        tracking=True,
        help="The ceiling. Required and positive for any scope other than "
        "Unlimited.",
    )

    # --- Authorization terms -----------------------------------------
    authorization_required = fields.Boolean(
        default=False,
        tracking=True,
        help="Visits under this agreement need an explicit payer authorization "
        "before service.",
    )
    guarantee_required = fields.Boolean(
        default=False,
        tracking=True,
        help="Visits under this agreement need a guarantee letter on file.",
    )

    # --- Commercial terms --------------------------------------------
    payment_terms_days = fields.Integer(
        string="Payment Terms (Days)",
        default=30,
        tracking=True,
    )
    tariff_mode = fields.Selection(
        [
            ("list_price", "Hospital List Price"),
            ("agreement_price", "Negotiated Agreement Price"),
        ],
        required=True,
        default="list_price",
        tracking=True,
        help="Negotiated pricing has no per-service tariff table yet, so an "
        "agreement set to Agreement Price cannot be activated. Selecting it "
        "records the commercial intent without pretending the behaviour exists.",
    )

    # --- History ------------------------------------------------------
    activated_by_id = fields.Many2one("res.users", readonly=True, copy=False)
    activated_at = fields.Datetime(readonly=True, copy=False)
    suspension_reason = fields.Text(readonly=True, copy=False)
    terminated_by_id = fields.Many2one("res.users", readonly=True, copy=False)
    terminated_at = fields.Datetime(readonly=True, copy=False)
    termination_reason = fields.Text(readonly=True, copy=False)

    notes = fields.Text()
    active = fields.Boolean(
        default=True,
        help="ADMINISTRATIVE ARCHIVE ONLY. Suspended, expired, superseded and "
        "terminated agreements stay active=True so their history remains "
        "readable; no workflow ever archives a record.",
    )
    eligibility_ids = fields.One2many(
        "hospital.patient.payer",
        "agreement_id",
        string="Patient Eligibilities",
    )

    _sql_constraints = [
        (
            "agreement_name_company_unique",
            "unique(name, company_id)",
            "An agreement with this reference already exists for this company.",
        ),
        (
            "agreement_number_version_company_unique",
            "unique(agreement_number, version, company_id)",
            "This agreement number already has a version with that number for "
            "this company.",
        ),
        ("agreement_version_positive", "CHECK(version >= 1)", "Version must be 1 or greater."),
        (
            "agreement_limit_amount_non_negative",
            "CHECK(limit_amount >= 0)",
            "The limit amount cannot be negative.",
        ),
        (
            "agreement_payment_terms_non_negative",
            "CHECK(payment_terms_days >= 0)",
            "Payment terms (days) cannot be negative.",
        ),
        (
            "agreement_effective_window_ordered",
            "CHECK(effective_to IS NULL OR effective_to >= effective_from)",
            "The effective end date cannot precede the effective start date.",
        ),
    ]

    # ------------------------------------------------------------------
    # Computes
    # ------------------------------------------------------------------
    @api.depends("payer_id", "agreement_number", "version")
    def _compute_display_name(self):
        for agreement in self:
            agreement.display_name = "%s v%s" % (
                agreement.agreement_number or agreement.name or "New",
                agreement.version or 1,
            )

    @api.depends("state", "effective_from", "effective_to")
    def _compute_is_valid_today(self):
        today = fields.Date.context_today(self)
        for agreement in self:
            valid = agreement.state == "active"
            if valid and agreement.effective_from and agreement.effective_from > today:
                valid = False
            if valid and agreement.effective_to and agreement.effective_to < today:
                valid = False
            agreement.is_valid_today = valid

    # ------------------------------------------------------------------
    # Constraints
    # ------------------------------------------------------------------
    @api.constrains("limit_scope", "limit_amount")
    def _check_limit_amount(self):
        for agreement in self:
            if agreement.limit_scope == "unlimited" and agreement.limit_amount:
                raise ValidationError(
                    "Agreement %s is Unlimited, so it must not carry a limit "
                    "amount." % agreement.display_name
                )

    @api.constrains("payer_id", "company_id")
    def _check_payer_company(self):
        for agreement in self:
            if agreement.payer_id.company_id != agreement.company_id:
                raise ValidationError(
                    "Agreement %s is for company %s but payer %s belongs to %s."
                    % (
                        agreement.display_name,
                        agreement.company_id.display_name,
                        agreement.payer_id.display_name,
                        agreement.payer_id.company_id.display_name,
                    )
                )

    @api.constrains("supersedes_id")
    def _check_supersedes(self):
        for agreement in self:
            source = agreement.supersedes_id
            if not source:
                continue
            if source == agreement:
                raise ValidationError("An agreement cannot supersede itself.")
            if source.agreement_number != agreement.agreement_number:
                raise ValidationError(
                    "An amendment must keep the same agreement number as the "
                    "version it amends (%s != %s)."
                    % (agreement.agreement_number, source.agreement_number)
                )
            if source.payer_id != agreement.payer_id:
                raise ValidationError("An amendment must keep the same payer.")
            if agreement.version <= source.version:
                raise ValidationError(
                    "Amendment version %s must be greater than the version it "
                    "amends (%s)." % (agreement.version, source.version)
                )

    # ------------------------------------------------------------------
    # Authorization + overlap primitives
    # ------------------------------------------------------------------
    def _assert_group(self, groups, action):
        """Real res.groups membership. The only authorization primitive here.

        sudo() passes deliberately: server-side system code must be able to act.
        What must never pass is an ordinary RPC user lacking the group, whatever
        context they supply.
        """
        if self.env.su:
            return
        if not any(self.env.user.has_group(group) for group in groups):
            raise AccessError(
                "You are not authorized to %s. Required: one of %s."
                % (action, ", ".join(g.split(".")[-1] for g in groups))
            )

    def _lock_overlap_scope(self):
        """Serialise everyone competing for the same payer's active window.

        Same primitive hospital_billing_accounting already uses for invoice
        batching. Without it, two managers activating overlapping agreements
        concurrently would both pass a pre-lock overlap check and both commit.
        """
        self.ensure_one()
        self.env.cr.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
            [
                "hospital.payer.agreement.overlap:%s:%s"
                % (self.company_id.id, self.payer_id.id)
            ],
        )

    def _assert_no_active_overlap(self, ignore=None):
        """Re-check overlap UNDER the advisory lock. Never before it.

        ``ignore`` excludes the predecessor an amendment is about to supersede.
        Passing it is what lets the whole check run BEFORE any record is
        mutated: the predecessor overlaps by definition, so without this the
        only way to pass would be to close it first and validate afterwards --
        which would leave it closed if a later check then failed.
        """
        self.ensure_one()
        ignored_ids = ignore.ids if ignore else []
        domain = [
            ("id", "not in", [self.id] + ignored_ids),
            ("payer_id", "=", self.payer_id.id),
            ("company_id", "=", self.company_id.id),
            ("state", "=", "active"),
            "|",
            ("effective_to", "=", False),
            ("effective_to", ">=", self.effective_from),
        ]
        if self.effective_to:
            domain.append(("effective_from", "<=", self.effective_to))
        # active_test=False: an ADMINISTRATIVELY ARCHIVED agreement can still be
        # in state 'active' and still conflict. Hiding it from this search would
        # let an archive silently create an overlap.
        clash = (
            self.sudo()
            .with_context(active_test=False)
            .search(domain, limit=1)
        )
        if clash:
            raise UserError(
                "Agreement %s overlaps active agreement %s (%s to %s) for payer "
                "%s. Two active agreements for one payer may not cover the same "
                "dates. Amend the existing agreement instead."
                % (
                    self.display_name,
                    clash.display_name,
                    clash.effective_from,
                    clash.effective_to or "open-ended",
                    self.payer_id.display_name,
                )
            )

    def _check_activation_ready(self):
        self.ensure_one()
        if not self.payer_id.partner_id:
            raise UserError(
                "Payer %s has no commercial partner, so nothing could ever be "
                "invoiced to it." % self.payer_id.display_name
            )
        if not self.limit_scope:
            raise UserError(
                "Agreement %s has no limit scope. It must be chosen explicitly "
                "before activation." % self.display_name
            )
        if self.limit_scope != "unlimited":
            if self.limit_amount <= 0:
                raise UserError(
                    "Agreement %s uses limit scope '%s' and therefore needs a "
                    "positive limit amount."
                    % (self.display_name, self.limit_scope)
                )
            # THE PHASE GATE. Bounded limits are meaningless without the
            # reservation/utilization ledger: allowing one to activate now would
            # display a ceiling that nothing enforces, which is worse than having
            # no ceiling at all because staff would trust it.
            raise UserError(BOUNDED_LIMIT_BLOCKED)
        if self.tariff_mode != "list_price":
            raise UserError(
                "Agreement %s is set to negotiated pricing, but no per-service "
                "tariff table exists yet. Set it back to Hospital List Price to "
                "activate." % self.display_name
            )
        if not self.effective_from:
            raise UserError("An effective start date is required before activation.")
        if self.effective_to and self.effective_to < self.effective_from:
            raise UserError("The effective end date cannot precede the start date.")

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------
    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if not vals.get("name") or vals["name"] == "New":
                vals["name"] = (
                    self.env["ir.sequence"].next_by_code("hospital.payer.agreement")
                    or "New"
                )
            if vals.get("state") and vals["state"] != "draft":
                raise UserError(
                    "An agreement is always created in Draft. Use the workflow "
                    "buttons to bring it into force."
                )
        return super().create(vals_list)

    def _assert_state_transition(self, new_state, vals):
        """Every state change, on every path, passes through here.

        Group membership is checked HERE rather than only inside the action
        methods, so a raw ORM or RPC write cannot activate an agreement by
        setting a field. The action methods are convenience wrappers with better
        error messages; this is the boundary.
        """
        self.ensure_one()
        old_state = self.state
        if new_state == old_state:
            return
        if (old_state, new_state) not in ALLOWED_TRANSITIONS:
            raise UserError(
                "Agreement %s cannot move from '%s' to '%s'."
                % (self.display_name, old_state, new_state)
            )
        self._assert_group(
            ACTIVATION_GROUPS,
            "move agreement %s to '%s'" % (self.display_name, new_state),
        )
        if new_state == "suspended" and not (
            vals.get("suspension_reason") or self.suspension_reason or ""
        ).strip():
            raise UserError("Suspending an agreement requires a documented reason.")
        if new_state == "terminated" and not (
            vals.get("termination_reason") or self.termination_reason or ""
        ).strip():
            raise UserError("Terminating an agreement requires a documented reason.")

    def _assert_effective_to_shortening(self, new_value):
        """An active agreement's window may shrink, never grow.

        Extending a live contract in place would retroactively legitimise visits
        that fell outside it. Extension is an amendment.
        """
        self.ensure_one()
        new_date = fields.Date.to_date(new_value) if new_value else False
        if not new_date:
            raise UserError(
                "Agreement %s is %s and cannot be re-opened to an unlimited end "
                "date. Create an amendment instead."
                % (self.display_name, self.state)
            )
        if self.effective_to and new_date > self.effective_to:
            raise UserError(
                "Agreement %s is %s; its end date may only be brought forward, "
                "not extended from %s to %s. Create an amendment instead."
                % (self.display_name, self.state, self.effective_to, new_date)
            )
        if new_date < self.effective_from:
            raise UserError(
                "Agreement %s: the end date cannot precede the start date (%s)."
                % (self.display_name, self.effective_from)
            )

    def _assert_eligibilities_fit_new_end(self, new_value):
        """Do not let a parent edit invalidate an already nested child window."""
        self.ensure_one()
        new_date = fields.Date.to_date(new_value) if new_value else False
        if not new_date:
            return
        clash = self.env["hospital.patient.payer"].with_context(
            active_test=False
        ).search(
            [
                ("agreement_id", "=", self.id),
                "|",
                ("effective_from", ">", new_date),
                ("effective_to", ">", new_date),
            ],
            limit=1,
        )
        if clash:
            raise ValidationError(
                "Agreement %s cannot end on %s because eligibility %s has a "
                "window extending beyond that date."
                % (self.display_name, new_date, clash.display_name)
            )

    def write(self, vals):
        for agreement in self:
            if "effective_to" in vals:
                agreement._assert_eligibilities_fit_new_end(vals["effective_to"])
            if "state" in vals:
                agreement._assert_state_transition(vals["state"], vals)
            if agreement.state == "draft":
                continue
            blocked = FROZEN_TERM_FIELDS & set(vals)
            if blocked:
                raise UserError(
                    "Agreement %s is %s. Its commercial terms are frozen and "
                    "cannot change: %s.\n\n"
                    "Use 'Create Amendment' to issue a new version instead."
                    % (agreement.display_name, agreement.state, ", ".join(sorted(blocked)))
                )
            if "effective_to" in vals:
                agreement._assert_effective_to_shortening(vals["effective_to"])
        return super().write(vals)

    def unlink(self):
        for agreement in self:
            if agreement.state != "draft":
                raise UserError(
                    "Agreement %s is %s and cannot be deleted. Its history must "
                    "stay readable." % (agreement.display_name, agreement.state)
                )
            if agreement.superseded_by_id:
                raise UserError(
                    "Agreement %s is referenced by amendment %s and cannot be "
                    "deleted." % (agreement.display_name, agreement.superseded_by_id.display_name)
                )
            agreement._assert_group(
                DRAFTING_GROUPS, "delete draft agreement %s" % agreement.display_name
            )
        return super().unlink()

    # ------------------------------------------------------------------
    # Workflow
    # ------------------------------------------------------------------
    def action_activate(self):
        for agreement in self:
            if agreement.state != "draft":
                raise UserError(
                    "Only a draft agreement can be activated. %s is %s."
                    % (agreement.display_name, agreement.state)
                )
            agreement._assert_group(
                ACTIVATION_GROUPS, "activate agreement %s" % agreement.display_name
            )
            agreement._check_activation_ready()

            # ORDER IS THE ATOMICITY GUARANTEE:
            #   lock -> validate EVERYTHING -> then mutate.
            #
            # Every check runs before the first write, so a failure leaves the
            # predecessor exactly as it was. That matters because a bare Python
            # call is not wrapped in a savepoint the way an RPC request is: if
            # this method closed v1 and only then discovered a conflict, the
            # caller would be left with v1 closed and v2 still draft.
            # The advisory lock is taken OUTSIDE the savepoint below. It is
            # transaction-scoped, and acquiring it inside a subtransaction that
            # later rolls back would release it early.
            agreement._lock_overlap_scope()
            agreement._assert_no_active_overlap(ignore=agreement.supersedes_id)

            if not agreement.supersedes_id:
                # Plain activation: one write, nothing to order or unwind.
                agreement._write_activation()
                continue

            # AMENDMENT: two records move, and the ORDER THEY REACH POSTGRES
            # IS LOAD-BEARING.
            #
            # hospital_payer_agreement_no_active_overlap is a non-deferrable
            # EXCLUDE constraint checked per statement. Between v1 ceasing to be
            # active and v2 becoming active there is a moment when both rows
            # would satisfy `WHERE state = 'active'` over overlapping windows.
            # If v2's UPDATE reached the database first, that moment would be a
            # real constraint violation on a perfectly legitimate amendment.
            #
            # Odoo emits these as two separate UPDATE statements (the two
            # records have different field sets in the towrite buffer), and
            # nothing in the ORM contract guarantees which is issued first.
            # Relying on the buffer's insertion order happens to work and is
            # not something correctness should rest on.
            #
            # So the predecessor is flushed explicitly before the successor is
            # written, and both are wrapped in one savepoint.
            with agreement.env.cr.savepoint():
                agreement._supersede_source()
                # Narrow by design: exactly the three columns _supersede_source
                # writes. Everything the constraint reads (state, effective_to)
                # is in this set, so the database has the predecessor's final
                # shape before the next statement runs.
                agreement.supersedes_id.flush_recordset(SUPERSESSION_FLUSH_FIELDS)

                agreement._write_activation()
                # Force the successor's UPDATE too, so the exclusion constraint
                # is evaluated INSIDE this savepoint. A violation therefore
                # unwinds the supersession with it, instead of surfacing later
                # at an outer flush with v1 already closed.
                agreement.flush_recordset(["state"])
        return True

    def _write_activation(self):
        """The successor's own state change. Split out so the amendment path can
        order it explicitly against the predecessor's flush, and so a test can
        prove the savepoint unwinds a failure here."""
        self.ensure_one()
        return self.write(
            {
                "state": "active",
                "activated_by_id": self.env.user.id,
                "activated_at": fields.Datetime.now(),
            }
        )

    def _supersede_source(self):
        """Close the predecessor's window and mark it superseded.

        Runs inside action_activate's transaction. If anything after it raises,
        the whole thing rolls back and the predecessor is untouched -- there is
        no intermediate state in which v1 is closed and v2 is not active.
        """
        self.ensure_one()
        source = self.supersedes_id
        if source.state != "active":
            raise UserError(
                "Amendment %s cannot supersede %s because it is %s, not active."
                % (self.display_name, source.display_name, source.state)
            )
        self.env.cr.execute(
            "SELECT id FROM hospital_payer_agreement WHERE id = %s FOR UPDATE",
            [source.id],
        )
        source.invalidate_recordset()

        boundary = self.effective_from - timedelta(days=1)
        if boundary < source.effective_from:
            raise UserError(
                "Amendment %s starts on %s, which would close %s before it "
                "began (%s)."
                % (
                    self.display_name,
                    self.effective_from,
                    source.display_name,
                    source.effective_from,
                )
            )
        values = {"state": "superseded", "superseded_by_id": self.id}
        if not source.effective_to or source.effective_to > boundary:
            values["effective_to"] = boundary
        source.write(values)
        return source

    def action_suspend(self, reason=None):
        for agreement in self:
            if agreement.state != "active":
                raise UserError(
                    "Only an active agreement can be suspended. %s is %s."
                    % (agreement.display_name, agreement.state)
                )
            agreement.write(
                {
                    "state": "suspended",
                    "suspension_reason": reason or agreement.suspension_reason,
                }
            )
        return True

    def action_resume(self):
        for agreement in self:
            if agreement.state != "suspended":
                raise UserError(
                    "Only a suspended agreement can be resumed. %s is %s."
                    % (agreement.display_name, agreement.state)
                )
            agreement._assert_group(
                ACTIVATION_GROUPS, "resume agreement %s" % agreement.display_name
            )
            # Re-validate: the world moved while it was suspended. Another
            # agreement may have been activated over the same window.
            agreement._check_activation_ready()
            agreement._lock_overlap_scope()
            agreement._assert_no_active_overlap()
            agreement.write({"state": "active"})
        return True

    def action_expire(self):
        """Manual for now. A scheduled expiry belongs with the Phase 4 work that
        introduces a cron for capacity, not bolted on here."""
        for agreement in self:
            if agreement.state not in LIVE_STATES:
                raise UserError(
                    "Only an active or suspended agreement can expire. %s is %s."
                    % (agreement.display_name, agreement.state)
                )
            agreement.write({"state": "expired"})
        return True

    def action_terminate(self, reason=None):
        for agreement in self:
            if agreement.state == "draft":
                raise UserError(
                    "A draft agreement is deleted, not terminated (%s)."
                    % agreement.display_name
                )
            agreement.write(
                {
                    "state": "terminated",
                    "termination_reason": reason or agreement.termination_reason,
                    "terminated_by_id": self.env.user.id,
                    "terminated_at": fields.Datetime.now(),
                }
            )
        return True

    # ------------------------------------------------------------------
    # Amendment / versioning
    # ------------------------------------------------------------------
    def _create_amendment(self, effective_from=None):
        """Produce the next DRAFT version. The source is NOT touched here.

        Supersession happens only when the amendment is activated, so an
        abandoned draft leaves the live agreement exactly as it was.
        """
        self.ensure_one()
        self._assert_group(
            ACTIVATION_GROUPS, "amend agreement %s" % self.display_name
        )
        if self.state != "active":
            raise UserError(
                "Only an active agreement can be amended. %s is %s."
                % (self.display_name, self.state)
            )
        if self.superseded_by_id:
            raise UserError(
                "Agreement %s has already been amended by %s."
                % (self.display_name, self.superseded_by_id.display_name)
            )

        self.env.cr.execute(
            "SELECT id FROM hospital_payer_agreement WHERE id = %s FOR UPDATE",
            [self.id],
        )
        self.invalidate_recordset()

        existing_draft = self.search(
            [("supersedes_id", "=", self.id), ("state", "=", "draft")], limit=1
        )
        if existing_draft:
            raise UserError(
                "A draft amendment (%s) already exists for %s. Finish or delete "
                "it before creating another."
                % (existing_draft.display_name, self.display_name)
            )

        start = fields.Date.to_date(effective_from) if effective_from else None
        if start is None:
            today = fields.Date.context_today(self)
            start = max(today, self.effective_from + timedelta(days=1))
        if start <= self.effective_from:
            raise UserError(
                "An amendment must start after the version it amends (%s starts "
                "on %s)." % (self.display_name, self.effective_from)
            )

        return self.copy(
            {
                "name": "New",
                "version": self.version + 1,
                "supersedes_id": self.id,
                "superseded_by_id": False,
                "state": "draft",
                "effective_from": start,
                "effective_to": False,
                "activated_by_id": False,
                "activated_at": False,
                "suspension_reason": False,
                "terminated_by_id": False,
                "terminated_at": False,
                "termination_reason": False,
            }
        )

    def action_create_amendment(self):
        """Button entry point. Opens the new draft so the Manager sets its terms.

        effective_from defaults to the earliest date that does not overlap the
        source; it stays fully editable while the amendment is draft, so the
        default is a starting point rather than a silent decision.
        """
        self.ensure_one()
        amendment = self._create_amendment()
        return {
            "type": "ir.actions.act_window",
            "name": "Agreement Amendment",
            "res_model": "hospital.payer.agreement",
            "view_mode": "form",
            "res_id": amendment.id,
            "target": "current",
        }

    def action_view_version_history(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": "Version History",
            "res_model": "hospital.payer.agreement",
            "view_mode": "list,form",
            "domain": [
                ("agreement_number", "=", self.agreement_number),
                ("payer_id", "=", self.payer_id.id),
                ("company_id", "=", self.company_id.id),
            ],
            "context": {"active_test": False},
        }
