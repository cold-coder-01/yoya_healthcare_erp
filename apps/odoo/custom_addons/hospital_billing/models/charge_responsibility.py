"""Authorized SPONSOR responsibility for one charge.

WHAT THIS IS
------------
A record that some share of a charge is carried by a sponsor rather than by the
patient. It is a RECORDED AND AUTHORIZED decision, not a calculated one: this
repository has no coverage percentage, copay rate, benefit schedule or exclusion
list anywhere (hospital.payer.agreement carries limit_scope, limit_amount,
authorization_required, guarantee_required, payment_terms_days and tariff_mode --
none of which can produce a split). Inventing a split from rules that do not
exist would be a guess wearing the costume of a calculation.

WHY THERE IS NO PATIENT ROW
---------------------------
Patient responsibility is a RESIDUAL, never a stored figure:

    patient_responsibility = max(0, charge.amount_estimated - Sum(authorized sponsor))

Storing it too would create a second writable number that could disagree with
the first, which is precisely the class of bug Phase 3A removed from
billing_account.payer_type. A residual cannot drift from its own definition.
`responsibility_type` exists as a field, constrained to 'sponsor', so the model
is self-describing and a future second party has somewhere to go.

WHY amount_estimated IS THE BASIS
---------------------------------
It is what the cash gate already divides -- amount_due_for_clearance is
max(0, amount_estimated - cash_in_hand) -- so the residual reconciles with the
gate by construction. amount_eligible (delivered value) is zero until delivery,
and a pre-service split cannot divide zero. See charge_line._compute_outstanding.

DELIBERATELY ABSENT, AND DEFERRED
---------------------------------
  * No coverage/copay/benefit fields. See above.
  * No limit or utilization consumption against the agreement ceiling.
  * No sponsor invoice, claim, receivable or settlement. A sponsor amount here
    is an OPERATIONAL responsibility, not an accounting receivable.
  * No post-invoice adjustment path. Once anything is invoiced, or once patient
    cash has been taken, this phase refuses the edit rather than inventing the
    credit/debit accounting that would make it safe.
"""

from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError

from .charge_line import (
    AMOUNT_TOLERANCE,
    G_ADMIN,
    G_MANAGER,
    OPERATIONAL_MONEY_READ,
)
from .payer_agreement import G_INSURANCE_OFFICER

# Creating, amending, authorizing and cancelling a sponsor share are all the
# same duty: deciding who owes what. It is the Insurance/Credit Officer's job.
#
# The ACCOUNTANT IS DELIBERATELY ABSENT. They hold read access on this model for
# downstream finance work, but making them an operational authorizer would put
# the party who books the receivable in charge of creating it.
RESPONSIBILITY_AUTHORITY = (G_INSURANCE_OFFICER, G_MANAGER, G_ADMIN)

RESPONSIBILITY_STATES = [
    ("draft", "Draft"),
    ("authorized", "Authorized"),
    ("cancelled", "Cancelled"),
]

# Only an AUTHORIZED row reduces what the patient must pay. A draft is a
# proposal and buys the patient nothing at the cashier's window.
LIVE_RESPONSIBILITY_STATES = ("draft", "authorized")

# Frozen the moment the row is authorized. A correction is cancel + re-create,
# so the superseded decision stays readable with its original authorization
# stamp -- the same discipline patient_payer.VERIFIED_FIELDS applies.
AUTHORIZED_FROZEN_FIELDS = frozenset(
    {
        "charge_id",
        "responsibility_type",
        "amount",
        "patient_payer_id",
        "payer_id",
        "agreement_id",
        "member_reference_snapshot",
        "source",
        "request_token",
        "authorization_reference",
        "authorization_date",
        "authorized_by_id",
    }
)


class HospitalChargeResponsibility(models.Model):
    _name = "hospital.charge.responsibility"
    _description = "Sponsor Responsibility For A Charge"
    _order = "charge_id, id"

    name = fields.Char(required=True, readonly=True, copy=False, default="New")

    charge_id = fields.Many2one(
        "hospital.charge.line",
        required=True,
        ondelete="restrict",
        index=True,
        string="Charge",
    )

    # Context mirrors, stored so the constraints below are cheap and the rows are
    # searchable per encounter/account. Exactly the shape
    # hospital.charge.receipt.allocation uses.
    encounter_id = fields.Many2one(
        related="charge_id.encounter_id", store=True, readonly=True, index=True,
    )
    billing_account_id = fields.Many2one(
        related="charge_id.billing_account_id", store=True, readonly=True, index=True,
    )
    patient_id = fields.Many2one(
        related="charge_id.patient_id", store=True, readonly=True, index=True,
    )
    company_id = fields.Many2one(
        related="charge_id.company_id", store=True, readonly=True, index=True,
    )
    currency_id = fields.Many2one(
        related="charge_id.currency_id", store=True, readonly=True,
    )

    responsibility_type = fields.Selection(
        [("sponsor", "Sponsor")],
        required=True,
        default="sponsor",
        help="Only 'sponsor' exists. Patient responsibility is the residual of "
        "the charge amount less the authorized sponsor amount, and is never "
        "stored as a row.",
    )

    amount = fields.Float(
        required=True,
        digits=(16, 2),
        help="The sponsor's share of this charge, in the charge's currency. "
        "Never a percentage: no coverage rule exists to apply one to.",
    )

    state = fields.Selection(
        RESPONSIBILITY_STATES,
        required=True,
        default="draft",
        copy=False,
        index=True,
        tracking=False,
    )

    # ------------------------------------------------------------------
    # THE ELIGIBILITY SNAPSHOT.
    #
    # patient_payer_id is a live link (the row must belong to the eligibility
    # the visit was presented under, and that is checked on every write), but
    # payer_id, agreement_id and member_reference_snapshot are COPIES taken at
    # creation. An eligibility can later be superseded, an agreement can be
    # renegotiated, and a member number can be corrected -- none of which may
    # retroactively rewrite who authorized what, on which contract, for this
    # charge. Related fields would do exactly that.
    # ------------------------------------------------------------------
    patient_payer_id = fields.Many2one(
        "hospital.patient.payer",
        required=True,
        ondelete="restrict",
        index=True,
        string="Patient Eligibility",
    )
    payer_id = fields.Many2one(
        "hospital.payer", readonly=True, ondelete="restrict", index=True,
    )
    agreement_id = fields.Many2one(
        "hospital.payer.agreement", readonly=True, ondelete="restrict", index=True,
    )
    member_reference_snapshot = fields.Char(
        readonly=True,
        help="The member/policy identity as it read when this share was "
        "recorded. Identity only -- never a commercial term.",
    )

    source = fields.Selection(
        [("manual", "Manually Recorded")],
        required=True,
        default="manual",
        help="Only manual entry exists in this phase. A rule-derived source "
        "requires coverage master data the system does not have.",
    )
    reason = fields.Text(
        help="Why the sponsor carries this share. Required to authorize.",
    )

    authorization_reference = fields.Char(
        readonly=True,
        help="Guarantee letter or payer authorization number, as supplied.",
    )
    authorization_date = fields.Datetime(readonly=True, copy=False)
    authorized_by_id = fields.Many2one("res.users", readonly=True, copy=False)
    cancel_reason = fields.Text(readonly=True, copy=False)

    # Idempotency identity of the REQUEST, not of the row. A retry carrying the
    # same token returns the existing row instead of creating a second one --
    # the pattern hospital.charge.receipt.intake_token already establishes.
    request_token = fields.Char(readonly=True, copy=False, index=True)

    active = fields.Boolean(default=True)

    _sql_constraints = [
        (
            "responsibility_amount_non_negative",
            "CHECK(amount >= 0)",
            "A sponsor responsibility amount cannot be negative.",
        ),
        (
            "responsibility_request_token_unique",
            "unique(request_token)",
            "This responsibility request has already been recorded.",
        ),
        (
            "responsibility_name_company_unique",
            "unique(name, company_id)",
            "A responsibility record with this reference already exists for this company.",
        ),
    ]

    # ------------------------------------------------------------------
    # Authorization primitive
    # ------------------------------------------------------------------
    @api.model
    def _assert_authority(self, action):
        """Real res.groups membership. sudo() passes; a forged context does not."""
        if self.env.su:
            return
        if not any(
            self.env.user.has_group(group) for group in RESPONSIBILITY_AUTHORITY
        ):
            raise AccessError(
                "You are not authorized to %s. Sponsor responsibility decides how "
                "much cash the patient is asked for, so it is restricted to the "
                "Insurance/Credit Officer, Hospital Manager and System "
                "Administrator roles." % action
            )

    # ------------------------------------------------------------------
    # Concurrency
    # ------------------------------------------------------------------
    def _lock_scope(self):
        """Serialize every actor that can move money on this billing account.

        The SAME key is taken by hospital.charge.receipt.allocation.create(), so
        a cashier taking payment and an officer changing the split cannot
        interleave and compute a patient figure from a state neither of them
        ever saw. Advisory, transaction-scoped, released at commit or rollback:
        the pattern payer_agreement and patient_payer already use.
        """
        self.ensure_one()
        self.env["hospital.billing.account"]._lock_responsibility_scope(
            self.billing_account_id.id or self.charge_id.billing_account_id.id
        )

    # ------------------------------------------------------------------
    # Constraints
    # ------------------------------------------------------------------
    @api.constrains("amount")
    def _check_amount_non_negative(self):
        for record in self:
            if record.amount < -AMOUNT_TOLERANCE:
                raise ValidationError(
                    "Responsibility %s: a sponsor amount cannot be negative."
                    % (record.name or "New")
                )

    @api.constrains("amount", "charge_id", "state")
    def _check_amount_within_charge(self):
        """A sponsor may never carry more than the charge is worth.

        Enforced for DRAFT as well as AUTHORIZED, deliberately. A draft that
        already exceeds the charge is not a harmless proposal: it is the number
        an authorizing officer is shown, and letting it exist invites a
        one-click authorization of an impossible split.
        """
        for record in self:
            if record.state == "cancelled":
                continue
            # sudo for READING only: the officer holds no OPERATIONAL_MONEY_READ,
            # so charge.amount_estimated would raise AccessError here and mask
            # the ValidationError the caller must actually see. Grants nothing.
            basis = record.charge_id.sudo().amount_estimated
            if record.amount > basis + AMOUNT_TOLERANCE:
                raise ValidationError(
                    "Responsibility %s: the sponsor share %.2f exceeds charge %s, "
                    "which is worth %.2f. A sponsor cannot carry more than the "
                    "charge."
                    % (
                        record.name or "New",
                        record.amount,
                        record.charge_id.name,
                        basis,
                    )
                )

    @api.constrains("charge_id", "state")
    def _check_single_live_allocation(self):
        """At most ONE draft-or-authorized row per charge.

        Backed by a partial unique index (db_constraints), which is the real
        guard against a race. This Python check exists so the ordinary path
        fails with a readable message instead of an IntegrityError.
        """
        for record in self:
            if record.state not in LIVE_RESPONSIBILITY_STATES:
                continue
            clash = self.sudo().search(
                [
                    ("id", "!=", record.id),
                    ("charge_id", "=", record.charge_id.id),
                    ("state", "in", list(LIVE_RESPONSIBILITY_STATES)),
                ],
                limit=1,
            )
            if clash:
                raise ValidationError(
                    "Charge %s already carries sponsor responsibility %s (%s). "
                    "Cancel it before recording a different share."
                    % (record.charge_id.name, clash.name, clash.state)
                )

    @api.constrains("patient_payer_id", "charge_id", "state")
    def _check_eligibility_matches_the_visit(self):
        """The share must belong to the eligibility the VISIT is presented under.

        Not merely 'an eligibility of this patient': attaching a share to an
        eligibility the encounter is not using would bill a sponsor who was
        never selected for this visit.
        """
        for record in self:
            if record.state == "cancelled":
                continue
            eligibility = record.patient_payer_id.sudo()
            encounter = record.charge_id.sudo().encounter_id
            if not encounter:
                raise ValidationError(
                    "Responsibility %s: charge %s has no encounter."
                    % (record.name or "New", record.charge_id.name)
                )
            if not encounter.patient_payer_id:
                raise ValidationError(
                    "Encounter %s has no payer eligibility selected, so no "
                    "sponsor can carry any part of its charges. Record the "
                    "payer identity at the front desk first."
                    % encounter.name
                )
            if encounter.patient_payer_id != eligibility:
                raise ValidationError(
                    "Responsibility %s names eligibility %s, but encounter %s is "
                    "presented under %s."
                    % (
                        record.name or "New",
                        eligibility.display_name,
                        encounter.name,
                        encounter.patient_payer_id.sudo().display_name,
                    )
                )
            if eligibility.patient_id != encounter.patient_id:
                raise ValidationError(
                    "Responsibility %s: eligibility %s belongs to %s, not to %s."
                    % (
                        record.name or "New",
                        eligibility.display_name,
                        eligibility.patient_id.display_name,
                        encounter.patient_id.display_name,
                    )
                )
            if eligibility.company_id != record.company_id:
                raise ValidationError(
                    "Responsibility %s: eligibility %s belongs to company %s, "
                    "not %s."
                    % (
                        record.name or "New",
                        eligibility.display_name,
                        eligibility.company_id.display_name,
                        record.company_id.display_name,
                    )
                )

    # ------------------------------------------------------------------
    # Freeze
    # ------------------------------------------------------------------
    def _payment_freeze_reason(self):
        """Why this share may no longer move, or None.

        A CONFIRMED receipt against the encounter is the authoritative 'patient
        cash has been taken' relation -- the same one
        encounter_payer._payer_identity_freeze_reason() uses, and for the same
        reason: it is the payment EVENT, it is reachable without
        OPERATIONAL_MONEY_READ, and it does not depend on any amount being
        non-zero.

        Changing the sponsor share underneath a receipt would silently restate
        what the patient owed AFTER they paid it, producing either a credit the
        system never books or a shortfall nobody is asked for. Resolving that
        needs the refund/credit path this phase does not build, so the edit is
        refused rather than approximated.
        """
        self.ensure_one()
        encounter = self.charge_id.sudo().encounter_id
        if not encounter:
            return None
        receipt = self.env["hospital.charge.receipt"].sudo().search(
            [("encounter_id", "=", encounter.id), ("state", "=", "confirmed")],
            limit=1,
        )
        if receipt:
            return (
                "patient payment %s has already been received against encounter %s"
                % (receipt.name, encounter.name)
            )
        if self.charge_id.sudo().invoice_state != "not_invoiced":
            return "charge %s has already been invoiced" % self.charge_id.name
        return None

    def _assert_not_frozen(self, action):
        for record in self:
            reason = record._payment_freeze_reason()
            if reason:
                raise UserError(
                    "Cannot %s: %s.\n\nSponsor responsibility is frozen once "
                    "patient cash has been taken or the charge has been "
                    "invoiced. Correcting it from here requires the refund / "
                    "credit-note path, which is not implemented in this phase."
                    % (action, reason)
                )

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------
    @api.model_create_multi
    def create(self, vals_list):
        self._assert_authority("record sponsor responsibility")
        for vals in vals_list:
            if vals.get("state", "draft") != "draft":
                raise UserError(
                    "Sponsor responsibility is always recorded as Draft. Use "
                    "action_authorize after review."
                )
            for stamped in ("authorization_date", "authorized_by_id"):
                if vals.get(stamped):
                    raise UserError(
                        "Authorization stamps are set only by the authorization "
                        "workflow."
                    )
            charge = self.env["hospital.charge.line"].sudo().browse(
                vals.get("charge_id")
            )
            if charge.charge_state in ("cancelled", "reversed"):
                raise UserError(
                    "Charge %s is %s and cannot carry sponsor responsibility."
                    % (charge.name, charge.charge_state)
                )
            # Snapshot the commercial identity ONCE, from the eligibility, and
            # never from the caller: an RPC client must not be able to attribute
            # a share to a payer or agreement the eligibility does not name.
            eligibility = self.env["hospital.patient.payer"].sudo().browse(
                vals.get("patient_payer_id")
            )
            if eligibility:
                vals["payer_id"] = eligibility.payer_id.id or False
                vals["agreement_id"] = eligibility.agreement_id.id or False
                vals["member_reference_snapshot"] = (
                    eligibility.member_reference
                    or eligibility.membership_number
                    or eligibility.policy_number
                    or False
                )
            # PRE-CHECK the one-live-row rule, before the INSERT.
            #
            # The partial unique index is the authoritative race guard, but it
            # fires during the flush -- ahead of @api.constrains -- so relying on
            # the Python constraint alone hands the ordinary caller a raw
            # IntegrityError instead of a readable message. Flush first so a
            # cancellation made earlier in this same transaction is visible.
            self.env["hospital.charge.responsibility"].flush_model()
            clash = self.sudo().search(
                [
                    ("charge_id", "=", charge.id),
                    ("state", "in", list(LIVE_RESPONSIBILITY_STATES)),
                ],
                limit=1,
            )
            if clash:
                raise ValidationError(
                    "Charge %s already carries sponsor responsibility %s (%s). "
                    "Cancel it before recording a different share."
                    % (charge.name, clash.name, clash.state)
                )
            vals["name"] = (
                self.env["ir.sequence"].next_by_code("hospital.charge.responsibility")
                or "New"
            )
        records = super().create(vals_list)
        for record in records:
            record._assert_not_frozen(
                "record sponsor responsibility on charge %s" % record.charge_id.name
            )
            record._log_audit(
                "create",
                "Sponsor responsibility %s drafted: %.2f of charge %s under %s."
                % (
                    record.name,
                    record.amount,
                    record.charge_id.name,
                    record.patient_payer_id.sudo().display_name,
                ),
            )
        return records

    def write(self, vals):
        self._assert_authority("amend sponsor responsibility")
        if "name" in vals:
            raise UserError(
                "Responsibility references are generated and cannot be changed."
            )
        # The workflow methods below set state/stamps through super(); an
        # ordinary caller may not.
        workflow_only = {
            "state",
            "authorization_date",
            "authorized_by_id",
            "authorization_reference",
            "cancel_reason",
        } & set(vals)
        if workflow_only and not self.env.context.get(
            "hospital_responsibility_workflow"
        ):
            raise UserError(
                "Sponsor responsibility state and authorization stamps are "
                "managed by action_authorize / action_cancel: %s."
                % ", ".join(sorted(workflow_only))
            )

        for record in self:
            if record.state == "cancelled":
                blocked = set(vals) - {"active"}
                if blocked:
                    raise UserError(
                        "Responsibility %s is cancelled and cannot be modified: %s."
                        % (record.name, ", ".join(sorted(blocked)))
                    )
            elif record.state == "authorized":
                blocked = AUTHORIZED_FROZEN_FIELDS & set(vals)
                if blocked:
                    raise UserError(
                        "Responsibility %s is authorized. %s cannot be changed in "
                        "place -- cancel it and record the corrected share, so the "
                        "superseded authorization stays readable."
                        % (record.name, ", ".join(sorted(blocked)))
                    )
            if not workflow_only:
                record._assert_not_frozen(
                    "amend sponsor responsibility %s" % record.name
                )
        return super().write(vals)

    def unlink(self):
        for record in self:
            if record.state != "draft":
                raise UserError(
                    "Responsibility %s is %s and cannot be deleted. Cancel it "
                    "instead, so the decision stays auditable."
                    % (record.name, record.state)
                )
            record._assert_authority("delete sponsor responsibility")
        return super().unlink()

    # ------------------------------------------------------------------
    # Workflow
    # ------------------------------------------------------------------
    def action_authorize(self, authorization_reference=None):
        """Make this share authoritative for the patient's cash requirement."""
        for record in self:
            record._assert_authority(
                "authorize sponsor responsibility %s" % record.name
            )
            record._lock_scope()
            if record.state == "authorized":
                # Idempotent: re-authorizing an authorized row is a no-op, not
                # a second authorization with a fresh timestamp.
                continue
            if record.state == "cancelled":
                raise UserError(
                    "Responsibility %s is cancelled and cannot be authorized."
                    % record.name
                )
            record._assert_not_frozen(
                "authorize sponsor responsibility %s" % record.name
            )
            if not (record.reason or "").strip():
                raise UserError(
                    "Authorizing responsibility %s requires a documented reason: "
                    "it decides how much cash the patient is asked for."
                    % record.name
                )
            encounter = record.charge_id.sudo().encounter_id
            if not encounter.patient_payer_id:
                raise UserError(
                    "Encounter %s has no payer eligibility selected. Sponsor "
                    "responsibility cannot be authorized without one."
                    % encounter.name
                )
            if not record.patient_payer_id.sudo().is_valid_today:
                raise UserError(
                    "Eligibility %s is not valid today, so no sponsor share may "
                    "be authorized under it."
                    % record.patient_payer_id.sudo().display_name
                )
            record.with_context(
                hospital_responsibility_workflow=True
            ).sudo().write(
                {
                    "state": "authorized",
                    "authorization_date": fields.Datetime.now(),
                    "authorized_by_id": self.env.user.id,
                    "authorization_reference": authorization_reference
                    or record.authorization_reference
                    or False,
                }
            )
            record._log_audit(
                "state_change",
                "Sponsor responsibility %s AUTHORIZED: %.2f of charge %s by %s%s."
                % (
                    record.name,
                    record.amount,
                    record.charge_id.name,
                    self.env.user.display_name,
                    " (ref %s)" % authorization_reference
                    if authorization_reference
                    else "",
                ),
            )
        return True

    def action_cancel(self, reason=None):
        """Withdraw the share. The patient residual returns to the full amount."""
        for record in self:
            record._assert_authority(
                "cancel sponsor responsibility %s" % record.name
            )
            record._lock_scope()
            if record.state == "cancelled":
                continue
            record._assert_not_frozen(
                "cancel sponsor responsibility %s" % record.name
            )
            if not (reason or record.cancel_reason or "").strip():
                raise UserError(
                    "Cancelling responsibility %s requires a documented reason: "
                    "it increases what the patient is asked to pay."
                    % record.name
                )
            record.with_context(
                hospital_responsibility_workflow=True
            ).sudo().write(
                {
                    "state": "cancelled",
                    "cancel_reason": reason or record.cancel_reason,
                }
            )
            # Land the state change before anything tries to insert a
            # replacement share: the partial unique index counts rows in the
            # database, not rows in the ORM cache.
            record.flush_recordset()
            record._log_audit(
                "state_change",
                "Sponsor responsibility %s CANCELLED by %s. Reason: %s"
                % (
                    record.name,
                    self.env.user.display_name,
                    (reason or record.cancel_reason or "n/a").strip(),
                ),
            )
        return True

    def _log_audit(self, action_type, description):
        self.ensure_one()
        self.env["hospital.audit.log"].sudo().create_log(
            patient_id=self.patient_id.id,
            model_name=self._name,
            record_id=self.id,
            action_type=action_type,
            description=description,
        )
