"""Patient card issuance.

Answers the question a cashier actually asks at the counter: "does THIS patient
owe a card fee right now?" -- which "was the patient record created today?"
cannot answer for a replacement, a waiver, or a migrated record.

One row per issuance event. The row, not the patient, is the billing subject,
which is what allows a replacement card to raise a second charge legitimately
while a retried request never does.
"""
from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError

G_MANAGER = "hospital_management.group_hospital_manager"
G_ADMIN = "hospital_management.group_hospital_system_administrator"
G_EMERGENCY_AUTHORIZER = "yoya_reception_bridge.group_hospital_emergency_authorizer"

WAIVER_GROUPS = (G_MANAGER, G_ADMIN)
DEFERRAL_GROUPS = (G_EMERGENCY_AUTHORIZER, G_MANAGER, G_ADMIN)

# States in which a first card already exists as a commitment. A patient holding
# any of these must never be charged a second first-card fee.
SETTLED_STATES = ("charged", "paid", "issued", "waived", "deferred")

# The billing engine keys every charge on source_model:res_id:line:event. A
# distinct event per reason lets a replacement bill separately from the original
# while a retry of the same issuance stays idempotent.
ISSUE_REASON_EVENTS = {
    "first": "card_new",
    "replacement": "card_replacement",
    "lost": "card_lost",
    "damaged": "card_damaged",
    "upgrade": "card_upgrade",
}

# Commercial identity. Frozen once the issuance leaves draft.
IMMUTABLE_AFTER_DRAFT = frozenset(
    {"patient_id", "encounter_id", "issue_reason", "service_id", "company_id"}
)

# Set only by the workflow methods on this model, never by a direct write.
SYSTEM_STAMPED = frozenset(
    {
        "name",
        "charge_line_id",
        "issued_by_id",
        "issued_at",
        "waived_by_id",
        "state",
    }
)


class HospitalPatientCardIssue(models.Model):
    _name = "hospital.patient.card.issue"
    _description = "Hospital Patient Card Issuance"
    _order = "create_date desc, id desc"

    name = fields.Char(required=True, readonly=True, copy=False, default="New")
    patient_id = fields.Many2one(
        "hospital.patient",
        required=True,
        ondelete="restrict",
        index=True,
    )
    encounter_id = fields.Many2one(
        "hospital.encounter",
        ondelete="restrict",
        index=True,
        help="Encounter the card fee is billed against. Required to raise a charge.",
    )
    issue_reason = fields.Selection(
        [
            ("first", "First Issue"),
            ("replacement", "Replacement"),
            ("lost", "Lost"),
            ("damaged", "Damaged"),
            ("upgrade", "Upgrade"),
        ],
        required=True,
        default="first",
        index=True,
    )
    state = fields.Selection(
        [
            ("draft", "Draft"),
            ("charged", "Charged"),
            ("paid", "Paid"),
            ("issued", "Issued"),
            ("waived", "Waived"),
            ("deferred", "Deferred"),
            ("cancelled", "Cancelled"),
        ],
        required=True,
        default="draft",
        index=True,
        copy=False,
    )

    card_number = fields.Char(copy=False)
    service_id = fields.Many2one("hospital.billing.service", string="Card Service")
    charge_line_id = fields.Many2one(
        "hospital.charge.line",
        string="Card Charge",
        readonly=True,
        copy=False,
        ondelete="restrict",
    )

    registered_by_id = fields.Many2one(
        "res.users",
        string="Registered By",
        readonly=True,
        copy=False,
        default=lambda self: self.env.user,
    )
    issued_by_id = fields.Many2one("res.users", string="Issued By", readonly=True, copy=False)
    issued_at = fields.Datetime(readonly=True, copy=False)
    waived_by_id = fields.Many2one("res.users", string="Waived By", readonly=True, copy=False)
    waiver_reason = fields.Text()
    deferred_until_encounter_id = fields.Many2one(
        "hospital.encounter",
        string="Deferred Until Encounter",
        ondelete="set null",
        help="Emergency deferral: the fee remains owed and visible, to be settled "
        "on or after this encounter.",
    )

    company_id = fields.Many2one(
        "res.company",
        required=True,
        default=lambda self: self.env.company,
    )
    currency_id = fields.Many2one(
        "res.currency", related="company_id.currency_id", readonly=True
    )
    charge_amount = fields.Float(
        related="charge_line_id.amount_estimated", readonly=True, string="Charge Amount"
    )
    charge_payment_state = fields.Selection(
        related="charge_line_id.payment_state", readonly=True, string="Funding"
    )
    active = fields.Boolean(default=True)

    _sql_constraints = [
        (
            "card_issue_card_number_unique",
            "unique(card_number)",
            "This card number is already in use.",
        ),
    ]

    def init(self):
        """Partial unique index: at most one live FIRST issuance per patient.

        _sql_constraints cannot express a WHERE clause, and a plain unique index
        would wrongly block legitimate replacement issuances.
        """
        self.env.cr.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS
                hospital_patient_card_issue_first_live_uniq
            ON hospital_patient_card_issue (patient_id)
            WHERE issue_reason = 'first' AND state != 'cancelled'
            """
        )

    # ------------------------------------------------------------------
    # Display / constraints
    # ------------------------------------------------------------------
    @api.depends("name", "patient_id")
    def _compute_display_name(self):
        for card in self:
            patient = card.patient_id.display_name or "Patient"
            card.display_name = (
                "%s - %s" % (card.name, patient) if card.name != "New" else patient
            )

    @api.constrains("patient_id", "issue_reason", "state")
    def _check_single_live_first_issue(self):
        for card in self:
            if card.issue_reason != "first" or card.state == "cancelled":
                continue
            clash = self.with_context(active_test=False).search(
                [
                    ("patient_id", "=", card.patient_id.id),
                    ("issue_reason", "=", "first"),
                    ("state", "!=", "cancelled"),
                    ("id", "!=", card.id),
                ],
                limit=1,
            )
            if clash:
                raise ValidationError(
                    "%s already has a first patient-card issuance (%s, %s). "
                    "Raise a replacement instead."
                    % (card.patient_id.display_name, clash.name, clash.state)
                )

    @api.constrains("encounter_id", "patient_id")
    def _check_encounter_patient(self):
        for card in self:
            if card.encounter_id and card.encounter_id.patient_id != card.patient_id:
                raise ValidationError(
                    "Card issuance %s is for %s but encounter %s belongs to %s."
                    % (
                        card.display_name,
                        card.patient_id.display_name,
                        card.encounter_id.name,
                        card.encounter_id.patient_id.display_name,
                    )
                )

    @api.constrains("state", "waiver_reason")
    def _check_waiver_reason(self):
        for card in self:
            if card.state == "waived" and not (card.waiver_reason or "").strip():
                raise ValidationError(
                    "A waived card fee requires a documented waiver reason."
                )

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------
    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if not vals.get("name") or vals["name"] == "New":
                vals["name"] = (
                    self.env["ir.sequence"].next_by_code(
                        "hospital.patient.card.issue.sequence"
                    )
                    or "New"
                )
            # Provenance is stamped from the session, never accepted from callers.
            vals["registered_by_id"] = self.env.user.id
            for stamped in ("charge_line_id", "issued_by_id", "issued_at", "waived_by_id"):
                vals.pop(stamped, None)
            vals.setdefault("state", "draft")
        cards = super().create(vals_list)
        for card in cards:
            card._log_audit("create", "Patient card issuance %s created." % card.name)
        return cards

    def write(self, vals):
        stamped = SYSTEM_STAMPED.intersection(vals)
        if stamped:
            raise UserError(
                "These fields are maintained by the card issuance workflow and "
                "cannot be written directly: %s. Use the issuance buttons."
                % ", ".join(sorted(stamped))
            )

        frozen = IMMUTABLE_AFTER_DRAFT.intersection(vals)
        if frozen:
            for card in self:
                if card.state != "draft":
                    raise UserError(
                        "Card issuance %s is %s; its commercial identity is frozen. "
                        "Blocked fields: %s."
                        % (card.name, card.state, ", ".join(sorted(frozen)))
                    )
        return super().write(vals)

    def _workflow_write(self, vals):
        """Write path reserved for this model's own action methods.

        Skips the user-facing guard in write() deliberately: the guard exists to
        stop hand edits, not to stop the workflow that owns these fields.
        """
        return super().write(vals)

    def unlink(self):
        for card in self:
            if card.state != "draft":
                raise UserError(
                    "Card issuance %s is %s and cannot be deleted. Cancel it instead."
                    % (card.name, card.state)
                )
        return super().unlink()

    def _log_audit(self, action_type, description):
        for card in self:
            self.env["hospital.audit.log"].create_log(
                patient_id=card.patient_id.id,
                model_name=card._name,
                record_id=card.id,
                action_type=action_type,
                description=description,
            )

    def _assert_group(self, groups, action):
        user = self.env.user
        if any(user.has_group(group) for group in groups):
            return
        raise AccessError("You are not allowed to %s." % action)

    # ------------------------------------------------------------------
    # Requirement resolution
    # ------------------------------------------------------------------
    @api.model
    def card_requirement_for(self, patient):
        """Does this patient owe a first-card fee?

        Deliberately independent of the patient's creation date: it looks only at
        issuance history, so migrated patients, waivers, emergency deferrals and
        replacements all behave correctly.
        """
        patient.ensure_one()
        existing = self.with_context(active_test=False).search(
            [
                ("patient_id", "=", patient.id),
                ("issue_reason", "=", "first"),
                ("state", "in", SETTLED_STATES),
            ],
            limit=1,
        )
        if existing:
            return {
                "required": False,
                "reason": "A first patient card already exists (%s, %s)."
                % (existing.name, existing.state),
                "existing_issue_id": existing.id,
            }
        return {
            "required": True,
            "reason": "No first patient card has been issued for this patient.",
            "existing_issue_id": None,
        }

    # ------------------------------------------------------------------
    # Workflow
    # ------------------------------------------------------------------
    def action_create_charge(self):
        """Raise the card fee through the billing engine. Idempotent."""
        engine = self.env["hospital.billing.engine"]
        for card in self:
            if card.state not in ("draft",):
                raise UserError(
                    "Card issuance %s is %s; a charge can only be raised from Draft."
                    % (card.name, card.state)
                )
            if not card.encounter_id:
                raise UserError(
                    "Card issuance %s has no encounter; a charge cannot be billed."
                    % card.name
                )

            service = card.service_id or self.env[
                "hospital.billing.service"
            ].get_default_card_service(card.company_id)

            charge = engine.create_or_update_charge(
                card.encounter_id,
                card._name,
                card.id,
                ISSUE_REASON_EVENTS[card.issue_reason],
                service.name,
                service=service,
                qty_requested=1.0,
            )
            # Registration is a real commitment the moment reception raises it,
            # matching how a confirmed appointment activates its consultation.
            engine.activate_charge(charge)

            card._workflow_write(
                {
                    "charge_line_id": charge.id,
                    "service_id": service.id,
                    "state": "charged",
                }
            )
            card._log_audit(
                "update",
                "Card fee charged on %s via %s." % (card.name, charge.name),
            )
        return True

    def action_mark_paid(self):
        """Promote to Paid only on verified billing state.

        The decision is read from the charge line, which is maintained by the
        receipt/allocation layer. No caller-supplied amount is trusted.
        """
        for card in self:
            if card.state != "charged":
                raise UserError(
                    "Card issuance %s is %s; only a Charged issuance can be marked paid."
                    % (card.name, card.state)
                )
            charge = card.charge_line_id
            if not charge:
                raise UserError(
                    "Card issuance %s has no charge to verify." % card.name
                )
            funded = charge.payment_state == "paid"
            payer_authorized = (
                charge.authorization_state == "authorized"
                and card.encounter_id.payer_type != "self_pay"
            )
            if not (funded or payer_authorized):
                raise UserError(
                    "Charge %s is not funded (%s) and carries no payer authorization. "
                    "Record the payment first."
                    % (charge.name, charge.payment_state)
                )
            card._workflow_write({"state": "paid"})
            card._log_audit("update", "Card fee verified as funded on %s." % card.name)
        return True

    def action_issue(self):
        for card in self:
            if card.state not in ("paid", "waived", "deferred"):
                raise UserError(
                    "Card issuance %s is %s; a card can only be handed over once it is "
                    "Paid, Waived or Deferred." % (card.name, card.state)
                )
            card._workflow_write(
                {
                    "state": "issued",
                    "issued_by_id": self.env.user.id,
                    "issued_at": fields.Datetime.now(),
                }
            )
            card._log_audit("update", "Patient card issued (%s)." % card.name)
        return True

    def action_waive(self):
        """Manager/administrator only: forgo the fee entirely."""
        self._assert_group(WAIVER_GROUPS, "waive a patient card fee")
        engine = self.env["hospital.billing.engine"]
        for card in self:
            if card.state not in ("draft", "charged"):
                raise UserError(
                    "Card issuance %s is %s and can no longer be waived."
                    % (card.name, card.state)
                )
            if not (card.waiver_reason or "").strip():
                raise UserError(
                    "Record a waiver reason on %s before waiving the fee." % card.name
                )
            if card.charge_line_id:
                engine.cancel_charge(
                    card.charge_line_id,
                    reason="Patient card fee waived (%s)" % card.name,
                )
            card._workflow_write(
                {"state": "waived", "waived_by_id": self.env.user.id}
            )
            card._log_audit(
                "update",
                "Card fee waived on %s by %s. Reason: %s"
                % (card.name, self.env.user.display_name, card.waiver_reason.strip()),
            )
        return True

    def action_defer(self):
        """Emergency deferral: the fee stays owed and visible, care proceeds."""
        self._assert_group(DEFERRAL_GROUPS, "defer a patient card fee")
        for card in self:
            if card.state not in ("draft", "charged"):
                raise UserError(
                    "Card issuance %s is %s and can no longer be deferred."
                    % (card.name, card.state)
                )
            card._workflow_write(
                {
                    "state": "deferred",
                    "deferred_until_encounter_id": card.encounter_id.id or False,
                }
            )
            card._log_audit(
                "update",
                "Card fee deferred on %s by %s; the charge remains outstanding."
                % (card.name, self.env.user.display_name),
            )
        return True

    def action_cancel(self):
        engine = self.env["hospital.billing.engine"]
        for card in self:
            if card.state in ("issued", "cancelled"):
                raise UserError(
                    "Card issuance %s is %s and cannot be cancelled."
                    % (card.name, card.state)
                )
            if card.charge_line_id:
                engine.cancel_charge(
                    card.charge_line_id,
                    reason="Patient card issuance %s cancelled" % card.name,
                )
            card._workflow_write({"state": "cancelled"})
            card._log_audit("state_change", "Card issuance %s cancelled." % card.name)
        return True
