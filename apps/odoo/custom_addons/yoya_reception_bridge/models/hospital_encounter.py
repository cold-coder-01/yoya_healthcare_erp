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

G_MANAGER = "hospital_management.group_hospital_manager"
G_ADMIN = "hospital_management.group_hospital_system_administrator"
G_EMERGENCY_AUTHORIZER = "yoya_reception_bridge.group_hospital_emergency_authorizer"

EMERGENCY_AUTHORIZER_GROUPS = (G_EMERGENCY_AUTHORIZER, G_MANAGER, G_ADMIN)

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

        return {
            "cleared": bool(result.get("cleared")),
            "state": result.get("state") or "not_required",
            "reason": result.get("reason") or "",
            "required": sum(pre_service.mapped("amount_estimated")),
            "paid": sum(pre_service.mapped("amount_received")),
            "outstanding": result.get("amount_due", 0.0),
            "lines": [
                {
                    "charge_id": line.id,
                    "name": line.name,
                    "description": line.description,
                    "required": line.amount_estimated,
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

    # ------------------------------------------------------------------
    # CRUD guards
    # ------------------------------------------------------------------
    @api.model_create_multi
    def create(self, vals_list):
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
