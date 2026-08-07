from odoo import api, fields, models
from odoo.exceptions import UserError, ValidationError

LOCKED_STATES = ("closed", "cancelled")


class HospitalEncounter(models.Model):
    _name = "hospital.encounter"
    _description = "Hospital Encounter"
    _order = "opened_at desc, id desc"

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
    )
    appointment_id = fields.Many2one(
        "hospital.appointment",
        ondelete="restrict",
        copy=False,
        help="Source appointment. At most one encounter may reference a given appointment.",
    )
    encounter_type = fields.Selection(
        [
            ("outpatient", "Outpatient"),
            ("inpatient", "Inpatient"),
            ("emergency", "Emergency"),
            ("daycare", "Day Care"),
            ("telemedicine", "Telemedicine"),
            ("other", "Other"),
        ],
        required=True,
        default="outpatient",
    )
    department_id = fields.Many2one("hospital.department", ondelete="restrict")
    primary_doctor_id = fields.Many2one("hospital.doctor", ondelete="restrict")

    opened_at = fields.Datetime(default=fields.Datetime.now, copy=False)
    completed_at = fields.Datetime(readonly=True, copy=False)
    closed_at = fields.Datetime(readonly=True, copy=False)

    state = fields.Selection(
        [
            ("planned", "Planned"),
            ("checked_in", "Checked In"),
            ("active", "Active"),
            ("completed", "Completed"),
            ("closed", "Closed"),
            ("cancelled", "Cancelled"),
        ],
        default="planned",
        required=True,
        copy=False,
        index=True,
    )

    payer_type = fields.Selection(
        [
            ("self_pay", "Self Pay"),
            ("insurance", "Insurance"),
            ("corporate", "Corporate"),
            ("government", "Government"),
            ("ngo", "NGO / Charity"),
            ("other", "Other"),
        ],
        default="self_pay",
        required=True,
    )
    payer_id = fields.Many2one(
        "res.partner",
        string="Payer",
        ondelete="restrict",
        help="Generic payer party. Insurance-specific linkage is added by hospital_insurance.",
    )

    emergency_bypass = fields.Boolean(
        help="Allows clinical service delivery before financial clearance.",
    )
    emergency_bypass_reason = fields.Text()
    emergency_bypass_authorized_by = fields.Many2one("res.users", readonly=True, copy=False)
    emergency_bypass_at = fields.Datetime(readonly=True, copy=False)

    cancel_reason = fields.Text(copy=False)

    company_id = fields.Many2one(
        "res.company",
        required=True,
        default=lambda self: self.env.company,
    )
    currency_id = fields.Many2one(
        "res.currency",
        related="company_id.currency_id",
        store=True,
        readonly=True,
    )
    active = fields.Boolean(default=True)

    _sql_constraints = [
        (
            "encounter_appointment_unique",
            "unique(appointment_id)",
            "An encounter already exists for this appointment.",
        ),
        (
            "encounter_name_company_unique",
            "unique(name, company_id)",
            "An encounter with this reference already exists for this company.",
        ),
    ]

    # ------------------------------------------------------------------
    # Extension hooks (overridden by downstream modules)
    # ------------------------------------------------------------------
    @api.model
    def _get_locked_writable_fields(self):
        """Fields still writable once the encounter is closed or cancelled."""
        return {"active", "state", "closed_at", "cancel_reason"}

    def _check_can_close(self):
        """Raise if the encounter must not be closed yet.

        hospital_billing overrides this to block closing an encounter whose
        billing account still has an outstanding balance.
        """
        return True

    # ------------------------------------------------------------------
    # Constraints
    # ------------------------------------------------------------------
    @api.constrains("opened_at", "completed_at", "closed_at")
    def _check_timeline(self):
        for encounter in self:
            if encounter.completed_at and encounter.opened_at and encounter.completed_at < encounter.opened_at:
                raise ValidationError("Completion time cannot precede the encounter opening time.")
            if encounter.closed_at and encounter.completed_at and encounter.closed_at < encounter.completed_at:
                raise ValidationError("Closing time cannot precede the completion time.")

    @api.constrains("emergency_bypass", "emergency_bypass_reason")
    def _check_emergency_bypass_reason(self):
        for encounter in self:
            if encounter.emergency_bypass and not (encounter.emergency_bypass_reason or "").strip():
                raise ValidationError("An emergency bypass requires a documented reason.")

    @api.constrains("appointment_id", "patient_id")
    def _check_appointment_patient(self):
        for encounter in self:
            appointment = encounter.appointment_id
            if appointment and appointment.patient_id != encounter.patient_id:
                raise ValidationError(
                    "The linked appointment belongs to a different patient than this encounter."
                )

    @api.constrains("payer_type", "payer_id")
    def _check_payer(self):
        for encounter in self:
            if encounter.payer_type != "self_pay" and not encounter.payer_id:
                raise ValidationError("A payer is required when the payer type is not Self Pay.")

    # ------------------------------------------------------------------
    # Onchange
    # ------------------------------------------------------------------
    @api.onchange("appointment_id")
    def _onchange_appointment_id(self):
        appointment = self.appointment_id
        if appointment:
            self.patient_id = appointment.patient_id
            if appointment.doctor_id:
                self.primary_doctor_id = appointment.doctor_id

    @api.onchange("payer_type")
    def _onchange_payer_type(self):
        if self.payer_type == "self_pay":
            self.payer_id = False

    @api.onchange("emergency_bypass")
    def _onchange_emergency_bypass(self):
        if not self.emergency_bypass:
            self.emergency_bypass_reason = False

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------
    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if not vals.get("name") or vals["name"] == "New":
                vals["name"] = self.env["ir.sequence"].next_by_code("hospital.encounter.sequence") or "New"
            if vals.get("emergency_bypass"):
                vals.setdefault("emergency_bypass_authorized_by", self.env.user.id)
                vals.setdefault("emergency_bypass_at", fields.Datetime.now())
        encounters = super().create(vals_list)
        for encounter in encounters:
            encounter._log_audit("create", f"Encounter {encounter.name} created.")
        return encounters

    def write(self, vals):
        allowed = self._get_locked_writable_fields()
        for encounter in self:
            if encounter.state in LOCKED_STATES:
                blocked = set(vals) - allowed
                if blocked:
                    raise UserError(
                        f"Encounter {encounter.name} is {encounter.state} and cannot be modified. "
                        f"Blocked fields: {', '.join(sorted(blocked))}."
                    )
        if vals.get("emergency_bypass"):
            vals.setdefault("emergency_bypass_authorized_by", self.env.user.id)
            vals.setdefault("emergency_bypass_at", fields.Datetime.now())
        result = super().write(vals)
        if "state" in vals:
            for encounter in self:
                encounter._log_audit("state_change", f"Encounter {encounter.name} moved to {vals['state']}.")
        return result

    def unlink(self):
        if not self.env.user.has_group("hospital_management.group_hospital_system_administrator"):
            for encounter in self:
                encounter._log_audit("delete_attempt", f"Blocked deletion of encounter {encounter.name}.")
            raise UserError("Only Hospital System Administrators can delete encounters.")
        for encounter in self:
            if encounter.state not in ("planned", "cancelled"):
                raise UserError(
                    f"Encounter {encounter.name} is {encounter.state} and cannot be deleted. "
                    "Cancel it instead."
                )
        return super().unlink()

    def _log_audit(self, action_type, description):
        self.ensure_one()
        self.env["hospital.audit.log"].create_log(
            patient_id=self.patient_id.id,
            model_name=self._name,
            record_id=self.id,
            action_type=action_type,
            description=description,
        )

    # ------------------------------------------------------------------
    # Workflow
    # ------------------------------------------------------------------
    def _require_state(self, allowed_states, action_label):
        for encounter in self:
            if encounter.state not in allowed_states:
                raise UserError(
                    f"Cannot {action_label} encounter {encounter.name} from state '{encounter.state}'."
                )

    def action_check_in(self):
        self._require_state(("planned",), "check in")
        return self.write({"state": "checked_in"})

    def action_start(self):
        self._require_state(("planned", "checked_in"), "start")
        return self.write({"state": "active", "opened_at": fields.Datetime.now()})

    def action_complete(self):
        self._require_state(("active",), "complete")
        return self.write({"state": "completed", "completed_at": fields.Datetime.now()})

    def action_close(self):
        self._require_state(("completed",), "close")
        for encounter in self:
            encounter._check_can_close()
        return self.write({"state": "closed", "closed_at": fields.Datetime.now()})

    def action_cancel(self):
        self._require_state(("planned", "checked_in", "active"), "cancel")
        return self.write({"state": "cancelled"})

    def action_reset_to_planned(self):
        for encounter in self:
            if encounter.state != "cancelled":
                raise UserError(f"Only cancelled encounters can be reset. {encounter.name} is {encounter.state}.")
        return self.write(
            {
                "state": "planned",
                "completed_at": False,
                "closed_at": False,
            }
        )


class HospitalPatientEncounter(models.Model):
    _inherit = "hospital.patient"

    encounter_ids = fields.One2many("hospital.encounter", "patient_id", string="Encounters")
    encounter_count = fields.Integer(compute="_compute_encounter_count")

    @api.depends("encounter_ids")
    def _compute_encounter_count(self):
        for patient in self:
            patient.encounter_count = len(patient.encounter_ids)

    def action_view_patient_encounters(self):
        self.ensure_one()
        action = {
            "type": "ir.actions.act_window",
            "name": "Encounters",
            "res_model": "hospital.encounter",
            "domain": [("patient_id", "=", self.id)],
            "context": {"default_patient_id": self.id},
        }
        encounter = self.env["hospital.encounter"].search(
            [("patient_id", "=", self.id)], order="opened_at desc, id desc", limit=1
        )
        if encounter:
            action.update({"view_mode": "form", "res_id": encounter.id})
        else:
            action["view_mode"] = "list,form"
        return action
