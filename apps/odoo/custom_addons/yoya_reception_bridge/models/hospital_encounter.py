"""Emergency bypass becomes an authorized, audited act.

Before this module, hospital.encounter accepted emergency_bypass=True from any
user holding write access -- which the ACL grants to receptionists and nurses.
Because hospital.billing.engine.check_financial_clearance short-circuits on the
bypass flag first, that single boolean silently cleared every payment gate on
the encounter.

The guard runs in create() and write(), before super(), and decides purely on
res.groups membership. There is no context flag to forge.
"""
from odoo import api, fields, models
from odoo.exceptions import AccessError, ValidationError

from .reception_capability import has_reception_workflow_capability

G_MANAGER = "hospital_management.group_hospital_manager"
G_ADMIN = "hospital_management.group_hospital_system_administrator"
G_EMERGENCY_AUTHORIZER = "yoya_reception_bridge.group_hospital_emergency_authorizer"
G_FRONT_DESK_NURSE = "yoya_reception_bridge.group_hospital_front_desk_nurse"
G_RECEPTIONIST = "hospital_management.group_hospital_receptionist"
G_DOCTOR = "hospital_management.group_hospital_doctor"

EMERGENCY_AUTHORIZER_GROUPS = (G_EMERGENCY_AUTHORIZER, G_MANAGER, G_ADMIN)
ENCOUNTER_CREATE_ALLOWED_GROUPS = (G_RECEPTIONIST, G_DOCTOR, G_MANAGER, G_ADMIN)

# Every field that asserts a bypass or records who authorised it. Writing any of
# them -- enabling, disabling, editing the reason, or forging the attribution --
# requires an authorised role.
BYPASS_GUARDED_FIELDS = frozenset(
    {
        "emergency_bypass",
        "emergency_bypass_reason",
        "emergency_bypass_authorized_by",
        "emergency_bypass_at",
    }
)


class HospitalEncounter(models.Model):
    _inherit = "hospital.encounter"

    danger_sign_ids = fields.Many2many(
        "hospital.emergency.danger.sign",
        "hospital_encounter_danger_sign_rel",
        "encounter_id",
        "danger_sign_id",
        string="Danger Signs",
        help="Danger signs identified during rapid emergency screening.",
    )
    emergency_screened_by_id = fields.Many2one(
        "res.users",
        string="Screened By",
        readonly=True,
        copy=False,
    )
    emergency_screened_at = fields.Datetime(
        string="Screened At",
        readonly=True,
        copy=False,
    )
    highest_danger_severity = fields.Selection(
        [
            ("low", "Low"),
            ("medium", "Medium"),
            ("high", "High"),
            ("critical", "Critical"),
        ],
        compute="_compute_highest_danger_severity",
        string="Highest Danger Severity",
    )

    # ------------------------------------------------------------------
    # Reception clearance (LIVE, encounter-wide)
    # ------------------------------------------------------------------
    #
    # hospital.billing.account.financial_clearance_state is a STORED MIRROR that
    # only changes when someone calls check_financial_clearance(persist=True).
    # That happens exactly once in the whole codebase -- inside
    # action_start_consultation -- so between registration and consultation it
    # still reads its default, 'not_required'. That is why ENC01225 displays
    # "Not Required" while 1,500 ETB is genuinely outstanding.
    #
    # These fields are the live view. They call the authoritative engine with
    # persist=False and NO charge filter, so every pre-service charge on the
    # account participates: consultation AND patient card. Nothing here writes
    # financial_clearance_state.
    #
    reception_required_amount = fields.Float(
        string="Required Before Service",
        compute="_compute_reception_clearance",
        compute_sudo=True,
        digits=(16, 2),
    )
    reception_paid_amount = fields.Float(
        string="Received",
        compute="_compute_reception_clearance",
        compute_sudo=True,
        digits=(16, 2),
    )
    reception_outstanding_amount = fields.Float(
        string="Outstanding",
        compute="_compute_reception_clearance",
        compute_sudo=True,
        digits=(16, 2),
    )
    reception_clearance_ok = fields.Boolean(
        string="Cleared For Triage",
        compute="_compute_reception_clearance",
        compute_sudo=True,
    )
    reception_clearance_state = fields.Selection(
        # Same vocabulary as hospital_billing's FINANCIAL_CLEARANCE_STATES,
        # redeclared rather than imported so a refactor there cannot silently
        # change this field's meaning.
        [
            ("not_required", "Not Required"),
            ("pending", "Pending"),
            ("cleared", "Cleared"),
            ("credit_authorized", "Credit Authorized"),
            # Phase 3. Must be listed here or the compute below raises the
            # moment the engine returns it: this vocabulary is redeclared rather
            # than imported (see the comment above), which keeps the two from
            # drifting silently but means an addition has to be made twice.
            ("sponsor_cleared", "Sponsor Cleared"),
            ("emergency_bypass", "Emergency Bypass"),
        ],
        string="Live Clearance",
        compute="_compute_reception_clearance",
        compute_sudo=True,
    )
    reception_clearance_message = fields.Char(
        string="Clearance Reason",
        compute="_compute_reception_clearance",
        compute_sudo=True,
    )

    def _compute_reception_clearance(self):
        engine = self.env["hospital.billing.engine"]
        for encounter in self:
            summary = encounter._reception_clearance_summary(engine=engine)
            encounter.reception_required_amount = summary["required"]
            encounter.reception_paid_amount = summary["paid"]
            encounter.reception_outstanding_amount = summary["outstanding"]
            encounter.reception_clearance_ok = summary["cleared"]
            encounter.reception_clearance_state = summary["state"]
            encounter.reception_clearance_message = summary["reason"]

    def _reception_clearance_summary(self, engine=None):
        """Encounter-wide pre-service clearance.

        Deliberately calls check_financial_clearance WITHOUT the ``charges``
        argument. hospital_billing scopes the appointment's own
        billing_blocked/billing_clearance_message to the CONSULTATION charge
        only -- correct for gating a consultation, wrong for gating reception,
        where the patient card must be paid too.
        """
        self.ensure_one()
        engine = engine or self.env["hospital.billing.engine"]

        result = engine.check_financial_clearance(self)

        account = self.billing_account_id
        pre_service = account.charge_line_ids.filtered(
            lambda line: line.charge_state in ("draft", "active")
            and line.billing_basis == "prepaid"
        ) if account else self.env["hospital.charge.line"]

        # WHAT THE PATIENT IS ASKED FOR, which under 'enforce' is their share
        # rather than the whole charge. Sourced from the charge's own helper so
        # the desk quotes the same number the cashier will collect; under 'off'
        # and 'shadow' the helper returns amount_estimated and this is unchanged.
        def _required(line):
            return (
                line.amount_patient_responsibility
                if line._responsibility_mode() == "enforce"
                else line.amount_estimated
            )

        return {
            "cleared": bool(result.get("cleared")),
            "state": result.get("state") or "not_required",
            "reason": result.get("reason") or "",
            "required": sum(_required(line) for line in pre_service),
            "paid": sum(pre_service.mapped("amount_received")),
            "outstanding": result.get("amount_due", 0.0),
            "lines": [
                {
                    "charge_id": line.id,
                    "name": line.name,
                    "description": line.description,
                    "required": _required(line),
                    "received": line.amount_received,
                    "outstanding": line.amount_due_for_clearance,
                }
                for line in pre_service
            ],
        }

    # ------------------------------------------------------------------
    # Authorization
    # ------------------------------------------------------------------
    @api.model
    def _is_emergency_authorizer(self):
        """Pure group membership. No sudo, no context, nothing forgeable.

        Note: code running as the superuser (any .sudo() path) passes this, as
        the superuser holds every group. That is inherent to sudo and is why the
        reception workflow service never elevates.
        """
        user = self.env.user
        return any(user.has_group(group) for group in EMERGENCY_AUTHORIZER_GROUPS)

    @api.model
    def _assert_emergency_authorizer(self, action):
        if self._is_emergency_authorizer():
            return
        raise AccessError(
            "Only a Hospital Emergency Authorizer, Hospital Manager or Hospital "
            "System Administrator may %s. An emergency bypass allows care to be "
            "delivered before payment and must be authorized by name." % action
        )

    @api.model
    def _assert_bypass_reason(self, reason):
        if not (reason or "").strip():
            raise ValidationError(
                "An emergency bypass requires a documented reason naming the "
                "clinical justification."
            )

    @api.model
    def _is_front_desk_direct_create_blocked(self):
        if self.env.su or has_reception_workflow_capability():
            return False
        user = self.env.user
        if not user.has_group(G_FRONT_DESK_NURSE):
            return False
        return not any(user.has_group(group) for group in ENCOUNTER_CREATE_ALLOWED_GROUPS)

    # ------------------------------------------------------------------
    # ONE ACTIVE EPISODE PER PATIENT
    #
    # Manual UAT showed the front desk opening a second visit for a patient
    # whose first was still awaiting the cashier. Two live episodes for one
    # person means two consultation charges, two cashier liabilities, two draws
    # against the same corporate benefit and two triage flows for one body.
    #
    # WHY THE GUARD IS ON THE ENCOUNTER AND NOT ON create_visit()
    # The encounter IS the episode of care. Every route to a new one passes
    # through this create() -- the reception workflow, action_confirm, a direct
    # ORM call, a second front end, a double-clicked button. A guard in the
    # workflow service would be the one place a determined caller can go round.
    #
    # WHY STATE AND NOT TIME. Repeated vitals, several lab requests and a
    # cashier visit all belong to ONE episode and stay perfectly legal; what is
    # refused is opening a SECOND episode while the first is unfinished. A "not
    # within an hour" rule would forbid the legitimate case and permit the
    # illegitimate one an hour later.
    # ------------------------------------------------------------------
    #
    # Terminal from the episode's point of view: the visit is over and a new one
    # is a genuinely new attendance. Everything else is still in progress.
    EPISODE_CLOSED_STATES = ("completed", "closed", "cancelled")

    def _lock_patient_episode(self, patient_id, company_id):
        """Serialize episode creation for one patient in one company.

        A SELECT-then-INSERT cannot stop two concurrent registrations: both
        transactions read "no active episode" before either writes. The advisory
        lock makes the check and the insert one critical section, and it is
        transaction-scoped so it releases on commit or rollback without cleanup.
        Same mechanism hospital.billing.account and hospital.patient.payer use.
        """
        self.env.cr.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
            ["hospital.encounter.episode:%s:%s" % (patient_id, company_id)],
        )

    @api.model
    def assert_patient_has_no_active_episode(self, patient, company=None):
        """PRE-FLIGHT form, callable before anything has been created.

        The model-level guard in create() fires too late to be the only check:
        by the time an encounter is being created, hospital.reception.workflow
        has already created the appointment, and an API decorator that catches
        the ValidationError and returns JSON lets Odoo commit that appointment.
        The result was an orphan visit in the queue behind a "duplicate" error.

        So the workflow calls THIS first, before its first write. Same lock,
        same key, same query as the create() guard -- one implementation, so
        the two can never disagree about what an active episode is.
        """
        if not patient:
            return
        self._assert_no_active_episode(
            {
                "patient_id": patient.id,
                "company_id": (company or self.env.company).id,
            }
        )

    @api.model
    def _assert_no_active_episode(self, vals):
        """Refuse a second live episode for the same patient and company."""
        patient_id = vals.get("patient_id")
        if not patient_id:
            return
        company_id = vals.get("company_id") or self.env.company.id

        self._lock_patient_episode(patient_id, company_id)

        # sudo: the guard must see an episode the caller may not read -- a front
        # desk nurse and a doctor have different row visibility, and a duplicate
        # is a duplicate either way. It only ever refuses; it grants nothing.
        existing = self.sudo().search(
            [
                ("patient_id", "=", patient_id),
                ("company_id", "=", company_id),
                ("state", "not in", list(self.EPISODE_CLOSED_STATES)),
            ],
            order="id desc",
            limit=1,
        )
        if not existing:
            return

        # Name the visit so the desk can find it rather than guess. The
        # appointment code is what staff actually search on; nothing clinical
        # is disclosed.
        reference = (
            existing.appointment_id.appointment_code
            or existing.name
            or "an existing visit"
        )
        raise ValidationError(
            "%s already has an active visit (%s). Complete or cancel it before "
            "registering another."
            % (existing.patient_id.display_name, reference)
        )

    # ------------------------------------------------------------------
    # CRUD guards
    # ------------------------------------------------------------------
    @api.model_create_multi
    def create(self, vals_list):
        if self._is_front_desk_direct_create_blocked():
            raise AccessError(
                "Front Desk Nurses may open encounters only through the "
                "authoritative reception workflow."
            )

        # Two passes, and the second is not redundant.
        #
        # _assert_no_active_episode reads the DATABASE, so in a single
        # create([a, b]) for one patient neither row exists yet when the other
        # is checked and BOTH pass. The batch itself has to be de-duplicated
        # separately -- an ORM caller can hand us the duplicate in one call
        # rather than two transactions.
        seen = set()
        for vals in vals_list:
            patient_id = vals.get("patient_id")
            if patient_id:
                key = (patient_id, vals.get("company_id") or self.env.company.id)
                if key in seen:
                    raise ValidationError(
                        "This request would open two visits at once for the "
                        "same patient. A patient may have only one active "
                        "visit."
                    )
                seen.add(key)
            self._assert_no_active_episode(vals)

        for vals in vals_list:
            touched = BYPASS_GUARDED_FIELDS.intersection(vals)
            if not touched:
                continue
            # Only an actual assertion of bypass (or an attempt to plant its
            # provenance) needs authorization; a stray False changes nothing.
            asserting = any(vals.get(name) for name in touched)
            if not asserting:
                continue

            self._assert_emergency_authorizer(
                "open an encounter with an emergency bypass"
            )
            if vals.get("emergency_bypass"):
                self._assert_bypass_reason(vals.get("emergency_bypass_reason"))

            # Attribution is stamped from the session, never accepted from the
            # caller. Set before super() so the parent's setdefault sees it.
            vals["emergency_bypass_authorized_by"] = self.env.user.id
            vals["emergency_bypass_at"] = fields.Datetime.now()

        encounters = super().create(vals_list)
        for encounter in encounters:
            if encounter.emergency_bypass:
                encounter._log_bypass_audit("granted")
        return encounters

    def write(self, vals):
        touched = BYPASS_GUARDED_FIELDS.intersection(vals)
        if touched:
            # Disabling or amending an existing bypass is just as sensitive as
            # granting one, so the whole field group is guarded uniformly.
            self._assert_emergency_authorizer(
                "change the emergency bypass on an encounter"
            )

            enabling = bool(vals.get("emergency_bypass"))
            if "emergency_bypass" in vals and enabling:
                for encounter in self:
                    reason = vals.get(
                        "emergency_bypass_reason", encounter.emergency_bypass_reason
                    )
                    self._assert_bypass_reason(reason)

            if enabling or "emergency_bypass_reason" in vals:
                vals["emergency_bypass_authorized_by"] = self.env.user.id
                vals["emergency_bypass_at"] = fields.Datetime.now()

        previous = {
            encounter.id: encounter.emergency_bypass for encounter in self
        } if touched else {}

        result = super().write(vals)

        if touched:
            for encounter in self:
                was = previous.get(encounter.id)
                now = encounter.emergency_bypass
                if was and not now:
                    encounter._log_bypass_audit("revoked")
                elif now and not was:
                    encounter._log_bypass_audit("granted")
                elif now and was:
                    encounter._log_bypass_audit("amended")
        return result

    def _log_bypass_audit(self, verb):
        """Reuse the encounter's own audit hook so the trail stays in one table."""
        for encounter in self:
            encounter._log_audit(
                "state_change",
                "Emergency bypass %s on encounter %s by %s. Reason: %s"
                % (
                    verb,
                    encounter.name,
                    self.env.user.display_name,
                    (encounter.emergency_bypass_reason or "n/a").strip(),
                ),
            )

    # ------------------------------------------------------------------
    # Screening
    # ------------------------------------------------------------------
    @api.depends("danger_sign_ids.severity")
    def _compute_highest_danger_severity(self):
        order = {"low": 1, "medium": 2, "high": 3, "critical": 4}
        for encounter in self:
            severities = [
                sign.severity for sign in encounter.danger_sign_ids if sign.severity
            ]
            if not severities:
                encounter.highest_danger_severity = False
                continue
            encounter.highest_danger_severity = max(
                severities, key=lambda value: order.get(value, 0)
            )

    def action_record_emergency_screening(self):
        """Stamp who performed the rapid danger screening, and when.

        Screening itself is a clinical observation, not an authorization, so it
        is deliberately open to any user who may write the encounter.
        """
        now = fields.Datetime.now()
        for encounter in self:
            encounter.write(
                {
                    "emergency_screened_by_id": self.env.user.id,
                    "emergency_screened_at": now,
                }
            )
        return True

    # ------------------------------------------------------------------
    # Locked-encounter compatibility
    # ------------------------------------------------------------------
    @api.model
    def _get_locked_writable_fields(self):
        """Keep screening metadata writable on a closed encounter.

        The bypass fields are deliberately NOT added here: once an encounter is
        closed or cancelled the parent's lock stands.
        """
        fields_set = super()._get_locked_writable_fields()
        return fields_set | {"emergency_screened_by_id", "emergency_screened_at"}
