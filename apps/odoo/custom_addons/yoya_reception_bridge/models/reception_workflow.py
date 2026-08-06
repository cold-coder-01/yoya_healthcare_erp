"""Authoritative reception workflow service.

Every method here composes EXISTING authoritative methods. Nothing writes a
workflow state directly, nothing raises a charge outside the billing engine,
and nothing elevates privileges -- if the caller may not do it, it does not
happen.

Charge responsibility is split deliberately:
    consultation -> appointment.action_confirm() (hospital_billing owns it)
    patient card -> hospital.patient.card.issue.action_create_charge()
The consultation charge is never raised here; doing so would duplicate
_ensure_consultation_billing.
"""
import logging

from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError

from .reception_capability import reception_workflow_capability

_logger = logging.getLogger(__name__)

G_RECEPTIONIST = "hospital_management.group_hospital_receptionist"
G_MANAGER = "hospital_management.group_hospital_manager"
G_ADMIN = "hospital_management.group_hospital_system_administrator"

REGISTRATION_GROUPS = (G_RECEPTIONIST, G_MANAGER, G_ADMIN)
MIGRATION_GROUPS = (G_MANAGER, G_ADMIN)

# Only these may be supplied when registering a patient. identification_code is
# absent on purpose: the MRN is sequence-assigned, never client-chosen.
PATIENT_REGISTRATION_FIELDS = frozenset(
    {
        "name",
        "title",
        "date_of_birth",
        "gender",
        "phone",
        "mobile",
        "email",
        "address",
        "city",
        "state",
        "zip_code",
        "country",
        "nationality",
        "government_identity",
        "passport_number",
        "marital_status",
        "occupation",
        "religion",
        "blood_group",
        "emergency_contact_name",
        "emergency_contact_phone",
        "primary_care_doctor_id",
    }
)


class HospitalReceptionWorkflow(models.AbstractModel):
    _name = "hospital.reception.workflow"
    _description = "Hospital Reception Workflow Service"

    # ------------------------------------------------------------------
    # Guards
    # ------------------------------------------------------------------
    @api.model
    def _assert_group(self, groups, action):
        user = self.env.user
        if any(user.has_group(group) for group in groups):
            return
        raise AccessError("You are not allowed to %s." % action)

    # ------------------------------------------------------------------
    # Preview
    # ------------------------------------------------------------------
    @api.model
    def preview_visit(self, patient=None, visit_type="routine", department=None, doctor=None):
        """Quote a visit before anything is created.

        Resolves both services so a misconfiguration surfaces at the counter as
        a clear message, rather than half-way through create_visit().
        """
        Service = self.env["hospital.billing.service"]
        CardIssue = self.env["hospital.patient.card.issue"]
        company = self.env.company

        consultation_service = Service.get_default_consultation_service(company)

        if patient:
            requirement = CardIssue.card_requirement_for(patient)
        else:
            requirement = {
                "required": True,
                "reason": "New patient; a first card will be issued.",
                "existing_issue_id": None,
            }

        card_service = None
        card_price = 0.0
        if requirement["required"]:
            card_service = Service.get_default_card_service(company)
            card_price = card_service.default_price

        consultation_price = consultation_service.default_price
        return {
            "visit_type": visit_type,
            "patient": {"id": patient.id, "name": patient.display_name} if patient else None,
            "department": {"id": department.id, "name": department.display_name}
            if department
            else None,
            "doctor": {"id": doctor.id, "name": doctor.display_name} if doctor else None,
            "card": {
                "required": requirement["required"],
                "reason": requirement["reason"],
                "existing_issue_id": requirement["existing_issue_id"],
                "service": {"id": card_service.id, "name": card_service.display_name}
                if card_service
                else None,
                "price": card_price,
            },
            "consultation": {
                "service": {
                    "id": consultation_service.id,
                    "name": consultation_service.display_name,
                },
                "price": consultation_price,
            },
            "total": card_price + consultation_price,
            "currency": company.currency_id.name,
        }

    # ------------------------------------------------------------------
    # Patient registration
    # ------------------------------------------------------------------
    @api.model
    def register_new_patient(self, values):
        """Create a patient from an allowlisted payload. MRN is sequence-assigned."""
        self._assert_group(REGISTRATION_GROUPS, "register a patient")

        if not isinstance(values, dict):
            raise UserError("Patient registration values must be a mapping.")

        unknown = set(values) - PATIENT_REGISTRATION_FIELDS
        if unknown:
            raise UserError(
                "These fields cannot be set during registration: %s."
                % ", ".join(sorted(unknown))
            )
        if not (values.get("name") or "").strip():
            raise UserError("A patient name is required.")

        with reception_workflow_capability():
            return self.env["hospital.patient"].create(dict(values))

    # ------------------------------------------------------------------
    # Visit creation
    # ------------------------------------------------------------------
    @api.model
    def create_visit(
        self,
        patient=None,
        patient_values=None,
        visit_type="routine",
        department=None,
        doctor=None,
        appointment_date=None,
        reason=None,
        triage_destination=None,
        issue_card=None,
    ):
        """Register a visit end to end, in one transaction.

        Orchestrates, in order:
            patient (reused or created)
            appointment
            action_confirm()  -> encounter + billing account + consultation charge
            first-card issuance and its charge, only when actually required

        Any exception rolls the whole thing back: no orphan appointment without
        an encounter, no card charge without a card issuance.
        """
        self._assert_group(REGISTRATION_GROUPS, "register a visit")

        if not patient and not patient_values:
            raise UserError("Provide an existing patient or values to register one.")
        if patient and patient_values:
            raise UserError(
                "Provide either an existing patient or registration values, not both."
            )

        if not patient:
            patient = self.register_new_patient(patient_values)

        now = fields.Datetime.now()
        appointment_values = {
            "patient_id": patient.id,
            "appointment_date": appointment_date or now,
            "visit_type": visit_type,
            "registered_by_id": self.env.user.id,
            "registered_at": now,
            # The ONLY place this is ever set. It opts the visit into the
            # nursing-triage gate on action_start_consultation; legacy
            # appointments stay False and behave exactly as before.
            "reception_workflow_managed": True,
        }
        if doctor:
            appointment_values["doctor_id"] = doctor.id
        if department:
            appointment_values["department_id"] = department.id
        if reason:
            appointment_values["reason"] = reason
        if triage_destination:
            appointment_values["triage_destination_id"] = triage_destination.id
        elif department:
            appointment_values["triage_destination_id"] = department.id

        with reception_workflow_capability():
            appointment = self.env["hospital.appointment"].create(appointment_values)

        # THE check-in event. hospital_billing hooks this to open the encounter,
        # the billing account and the consultation charge. Never duplicated here.
        appointment.action_confirm()

        encounter = appointment.encounter_id
        if not encounter:
            raise UserError(
                "Appointment %s was confirmed but no encounter was opened. "
                "Check that hospital_billing is installed and configured."
                % appointment.appointment_code
            )
        if visit_type == "emergency" and encounter.encounter_type != "emergency":
            encounter.write({"encounter_type": "emergency"})

        card_issue = self._maybe_issue_first_card(patient, encounter, issue_card)

        # The card charge was raised AFTER action_confirm ran its clearance
        # bookkeeping, so the account's stored financial_clearance_state still
        # reads its 'not_required' default. Re-run the authoritative engine with
        # persist=True now that every pre-service charge exists. This is the
        # only sanctioned way to move that field -- never a direct write.
        self.env["hospital.billing.engine"].check_financial_clearance(
            encounter, persist=True
        )

        # Registration is complete, so the patient is physically present and
        # checked in. This is independent of payment: action_check_in only moves
        # planned -> checked_in and never starts the encounter or the
        # consultation. Guarded so a retried create_visit is idempotent.
        if encounter.state == "planned":
            encounter.action_check_in()

        return {
            "patient": patient,
            "appointment": appointment,
            "encounter": encounter,
            "card_issue": card_issue,
            "consultation_charge": appointment.consultation_charge_id,
            "clearance": encounter._reception_clearance_summary(),
        }

    @api.model
    def _maybe_issue_first_card(self, patient, encounter, issue_card=None):
        """Create and charge a first card only when one is genuinely owed."""
        CardIssue = self.env["hospital.patient.card.issue"]
        requirement = CardIssue.card_requirement_for(patient)

        if issue_card is False:
            return CardIssue.browse()
        if not requirement["required"] and not issue_card:
            return CardIssue.browse()

        with reception_workflow_capability():
            card_issue = CardIssue.create(
                {
                    "patient_id": patient.id,
                    "encounter_id": encounter.id,
                    "issue_reason": "first",
                }
            )
        card_issue.action_create_charge()
        return card_issue

    # ------------------------------------------------------------------
    # Triage handoff
    # ------------------------------------------------------------------
    @api.model
    def send_to_triage(self, appointment, triage_destination=None):
        """Route a checked-in patient to a triage department.

        The queue stage is derived, so there is no stage to write: this records
        the destination and validates that the patient is actually checked in.
        """
        self._assert_group(REGISTRATION_GROUPS, "send a patient to triage")
        appointment.ensure_one()

        if appointment.state != "confirmed":
            raise UserError(
                "Appointment %s is %s. Only a confirmed (checked-in) appointment can "
                "be sent to triage." % (appointment.appointment_code, appointment.state)
            )

        destination = triage_destination or appointment.triage_destination_id or appointment.department_id
        if not destination:
            raise UserError(
                "No triage destination is set for appointment %s."
                % appointment.appointment_code
            )

        encounter = appointment.encounter_id
        if not encounter:
            raise UserError(
                "Appointment %s has no encounter; it cannot be sent to triage."
                % appointment.appointment_code
            )

        # Opportunistic, verified self-heal: promote charged -> paid for any card
        # whose charge the billing layer now reports as funded. It cannot lie --
        # action_mark_paid re-reads charge.payment_state itself.
        self._sync_card_payment_states(appointment.patient_id, encounter)

        # Encounter-wide clearance. NOT appointment.billing_blocked, which
        # hospital_billing deliberately scopes to the consultation charge alone
        # and would let a patient through having paid 300 of 1,500.
        clearance = encounter._reception_clearance_summary()
        if not clearance["cleared"]:
            raise UserError(
                "%s cannot be sent to triage yet.\n\n"
                "Required before service: %.2f\n"
                "Received: %.2f\n"
                "Outstanding: %.2f\n\n"
                "%s\n\n"
                "Take the outstanding payment, record a payer authorization, or "
                "authorize an emergency bypass on encounter %s."
                % (
                    appointment.patient_id.display_name,
                    clearance["required"],
                    clearance["paid"],
                    clearance["outstanding"],
                    clearance["reason"],
                    encounter.name,
                )
            )

        if destination != appointment.triage_destination_id:
            appointment.write({"triage_destination_id": destination.id})

        return {
            "appointment": appointment,
            "encounter": encounter,
            "triage_destination": destination,
            "clearance": clearance,
            "queue_stage": appointment.clinical_queue_stage,
        }

    @api.model
    def _sync_card_payment_states(self, patient, encounter):
        """Promote card issuances whose charge is now verifiably funded.

        hospital_billing exposes no hook on receipt confirmation, and patching
        it is out of scope, so the promotion happens at the next reception
        touchpoint instead of by observation. Silent when nothing qualifies.
        """
        cards = self.env["hospital.patient.card.issue"].search(
            [
                ("patient_id", "=", patient.id),
                ("encounter_id", "=", encounter.id),
                ("state", "=", "charged"),
            ]
        )
        promoted = self.env["hospital.patient.card.issue"]
        for card in cards:
            charge = card.charge_line_id
            if not charge:
                continue
            funded = charge.payment_state == "paid"
            authorized = (
                charge.authorization_state == "authorized"
                and encounter.payer_type != "self_pay"
            )
            if funded or authorized:
                card.action_mark_paid()
                promoted |= card
        return promoted

    # ------------------------------------------------------------------
    # Emergency
    # ------------------------------------------------------------------
    @api.model
    def emergency_bypass_and_send(
        self,
        appointment,
        reason,
        danger_signs=None,
        triage_destination=None,
        defer_card=True,
    ):
        """Authorize an emergency bypass and route straight to triage.

        The authorization itself is enforced by hospital.encounter.write(); this
        method does not decide it and does not elevate. A caller without the
        Emergency Authorizer role gets an AccessError from the encounter.
        """
        appointment.ensure_one()

        encounter = appointment.encounter_id
        if not encounter:
            raise UserError(
                "Appointment %s has no encounter yet; confirm it first."
                % appointment.appointment_code
            )

        encounter_values = {
            "emergency_bypass": True,
            "emergency_bypass_reason": reason,
            "encounter_type": "emergency",
        }
        if danger_signs:
            encounter_values["danger_sign_ids"] = [(6, 0, danger_signs.ids)]
        encounter.write(encounter_values)
        encounter.action_record_emergency_screening()

        if appointment.visit_type != "emergency":
            appointment.write({"visit_type": "emergency"})

        # The card fee is not forgiven, only postponed: it stays outstanding and
        # visible on the account.
        deferred_card = self.env["hospital.patient.card.issue"].browse()
        if defer_card:
            deferred_card = self.env["hospital.patient.card.issue"].search(
                [
                    ("patient_id", "=", appointment.patient_id.id),
                    ("state", "in", ("draft", "charged")),
                ],
                limit=1,
            )
            if deferred_card:
                deferred_card.action_defer()

        destination = (
            triage_destination
            or appointment.triage_destination_id
            or appointment.department_id
        )
        if destination and destination != appointment.triage_destination_id:
            appointment.write({"triage_destination_id": destination.id})

        return {
            "appointment": appointment,
            "encounter": encounter,
            "deferred_card_issue": deferred_card,
            "triage_destination": destination,
            "queue_stage": appointment.clinical_queue_stage,
        }

    # ------------------------------------------------------------------
    # Migration -- never runs automatically
    # ------------------------------------------------------------------
    @api.model
    def backfill_historical_card_issues(self, mode="issued", limit=None, dry_run=True):
        """Give pre-existing patients a historical first-card record.

        NOT wired to a post-init hook. It must be invoked explicitly, and it
        defaults to dry_run=True so an accidental call reports instead of writes.

        mode='issued'  the patient is treated as already holding a card (no fee,
                       nothing owed) -- the safe choice for a live database.
        mode='waived'  records an explicit fee waiver instead; requires that a
                       waiver reason be acceptable to Finance.

        Never raises a charge: no historical patient is billed retroactively.
        """
        self._assert_group(MIGRATION_GROUPS, "backfill historical patient cards")
        if mode not in ("issued", "waived"):
            raise UserError("mode must be 'issued' or 'waived'.")

        CardIssue = self.env["hospital.patient.card.issue"]
        already = CardIssue.with_context(active_test=False).search(
            [("issue_reason", "=", "first"), ("state", "!=", "cancelled")]
        )
        patients = self.env["hospital.patient"].search(
            [("id", "not in", already.mapped("patient_id").ids)],
            limit=limit or None,
            order="id asc",
        )

        if dry_run:
            return {
                "dry_run": True,
                "mode": mode,
                "patients_without_card": len(patients),
                "patient_ids": patients.ids[:50],
                "note": "Re-run with dry_run=False to write these records.",
            }

        created = CardIssue.browse()
        now = fields.Datetime.now()
        for patient in patients:
            card = CardIssue.create(
                {
                    "patient_id": patient.id,
                    "issue_reason": "first",
                    "waiver_reason": "Historical patient migrated before card "
                    "issuance tracking existed."
                    if mode == "waived"
                    else False,
                }
            )
            # No charge is ever raised: _workflow_write moves it straight to a
            # settled state so card_requirement_for() reports nothing owed.
            values = {"state": mode}
            if mode == "issued":
                values.update({"issued_by_id": self.env.user.id, "issued_at": now})
            else:
                values["waived_by_id"] = self.env.user.id
            card._workflow_write(values)
            created |= card

        _logger.info(
            "yoya_reception_bridge: backfilled %s historical card issuances as '%s'.",
            len(created),
            mode,
        )
        return {
            "dry_run": False,
            "mode": mode,
            "created": len(created),
            "card_issue_ids": created.ids,
        }
