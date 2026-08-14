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
from odoo.exceptions import AccessError, UserError, ValidationError

from odoo.addons.hospital_billing.models.encounter_payer import (
    PAYER_IDENTITY_AUTHORITY,
    payer_identity_capability,
)

from .reception_capability import reception_workflow_capability

_logger = logging.getLogger(__name__)

G_RECEPTIONIST = "hospital_management.group_hospital_receptionist"
G_MANAGER = "hospital_management.group_hospital_manager"
G_ADMIN = "hospital_management.group_hospital_system_administrator"

# The entrance is staffed by nurses at this hospital, so the intake workflow has
# to be callable by a front-desk nurse. Declared in this module's own
# security/yoya_reception_groups.xml. It implies Hospital Nurse (triage rights)
# and NOTHING financial -- see that file for exactly what it grants.
G_FRONT_DESK_NURSE = "yoya_reception_bridge.group_hospital_front_desk_nurse"

REGISTRATION_GROUPS = (G_FRONT_DESK_NURSE, G_RECEPTIONIST, G_MANAGER, G_ADMIN)
MIGRATION_GROUPS = (G_MANAGER, G_ADMIN)

# Who may call set_visit_payer at all: the intake roles that select a payer at
# registration, PLUS the eligibility-authority roles that correct one after the
# front-desk freeze.
#
# DERIVED from both tuples rather than restated, so the union cannot drift from
# either half. Manager and admin appear in both; dict.fromkeys dedupes while
# keeping a stable order for the error message.
#
# The bug this fixes: the method asserted REGISTRATION_GROUPS alone, so an
# Insurance/Credit Officer -- the role the freeze explicitly names as the one
# that MAY correct a frozen payer -- was rejected before the freeze logic was
# ever reached. It went unnoticed because the after-freeze test used a manager,
# who is in both tuples.
VISIT_PAYER_GROUPS = tuple(
    dict.fromkeys(REGISTRATION_GROUPS + PAYER_IDENTITY_AUTHORITY)
)

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
        patient_payer=None,
    ):
        """Register a visit end to end, in one transaction.

        Orchestrates, in order:
            patient (reused or created)
            appointment
            action_confirm()  -> encounter + billing account + consultation charge
            payer identity capture, when one was supplied
            first-card issuance and its charge, only when actually required

        Any exception rolls the whole thing back: no orphan appointment without
        an encounter, no card charge without a card issuance, and no visit left
        standing with an eligibility that turned out not to be selectable.

        ``patient_payer`` is optional and identity-only. Supplying one does NOT
        make the visit sponsored in any financial sense: payer_type is untouched
        and stays 'self_pay', so the cash gate is exactly what it would have been
        without it. See hospital_billing.models.encounter_payer for why.
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

        # Payer identity, captured in THIS transaction.
        #
        # Placed after the encounter resolves and before the card issuance and
        # the clearance re-run below, so an eligibility that fails validation
        # raises while every artefact create_visit made is still inside the same
        # transaction. Nothing here commits or rolls back by hand: the raise
        # propagates and Odoo unwinds the request, which is what makes an
        # invalid payer leave no half-registered visit behind.
        if patient_payer:
            self._apply_visit_payer(encounter, patient_payer)

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
            "patient_payer": encounter.patient_payer_id,
        }

    # ------------------------------------------------------------------
    # Payer identity
    # ------------------------------------------------------------------
    @api.model
    def _apply_visit_payer(self, encounter, patient_payer):
        """THE single write path for hospital.encounter.patient_payer_id.

        The capability block is what lets this write succeed at all:
        hospital.encounter.write() refuses patient_payer_id outside it, for every
        user including the Front Desk Nurse who holds raw write on the model. So
        the role check and the freeze check above this line are not advisory --
        there is no second route to the field.

        Deliberately writes ONE key. payer_type and payer_id are not touched, and
        no clearance is recomputed or persisted: recording who is responsible is
        not a financial event in this phase.
        """
        eligibility = patient_payer or self.env["hospital.patient.payer"]
        encounter.ensure_one()

        # Validate before entering the capability block, so a bad eligibility
        # fails on its own terms rather than inside a privileged section. The
        # encounter's @api.constrains re-checks it after the write regardless.
        self.env["hospital.encounter"]._assert_eligibility_selectable(
            eligibility, encounter.patient_id, encounter.company_id
        )

        with payer_identity_capability():
            encounter.write({"patient_payer_id": eligibility.id or False})
        return encounter

    @api.model
    def set_visit_payer(self, appointment, patient_payer=None):
        """Select, change or clear the payer identity of a registered visit.

        ``patient_payer=None`` clears it: the visit carries no sponsorship
        identity and is a plain self-pay presentation. That is a legitimate
        outcome, not an error, so it takes the same path and the same guards.

        WHAT THIS DOES NOT DO, and none of it is an oversight:
          - it does not write payer_type or payer_id, so
            check_financial_clearance behaves identically before and after;
          - it does not recompute or persist clearance;
          - it does not touch the queue stage, which is derived and never
            written;
          - it does not consume a limit or compute a responsibility split.
        """
        self._assert_group(VISIT_PAYER_GROUPS, "set the payer on a visit")

        # Both answers are taken from the REAL calling user, once, before any
        # elevation below can make every has_group() return True.
        user = self.env.user
        is_registration = any(
            user.has_group(group) for group in REGISTRATION_GROUPS
        )
        has_payer_authority = any(
            user.has_group(group) for group in PAYER_IDENTITY_AUTHORITY
        )

        # RECORD ACCESS FOR A PAYER-AUTHORITY CORRECTION.
        #
        # group_hospital_insurance_officer "deliberately implies no other role"
        # (hospital_billing/security/hospital_billing_groups.xml) and holds ACLs
        # on hospital.payer, hospital.payer.agreement and hospital.patient.payer
        # only. A standalone officer can therefore neither read
        # hospital.appointment nor write hospital.encounter, so authorizing them
        # above without this would simply move the AccessError from the guard to
        # the ORM two lines later.
        #
        # The alternative was granting the officer standing ACL + record rules on
        # appointments and encounters. That was rejected: it would hand a
        # master-data role permanent read/write over every clinical episode, to
        # support a correction path used rarely. This elevation is narrower --
        # it exists only inside this method and confers no ambient access, so the
        # officer still cannot browse or edit an encounter through the Odoo UI or
        # a raw RPC call.
        #
        # ORDER IS THE GUARANTEE, and it is the same ordering
        # hospital.charge.payment.wizard.action_confirm documents: assert real
        # group membership as the CALLING user, THEN elevate. A registration
        # caller is never elevated at all and keeps passing through their own
        # record rules exactly as before.
        visit = appointment if (self.env.su or is_registration) else appointment.sudo()
        visit.ensure_one()

        encounter = visit.encounter_id
        if not encounter:
            raise UserError(
                "Appointment %s has no encounter yet; its payer cannot be set."
                % (visit.appointment_code or visit.id)
            )
        encounter.ensure_one()

        eligibility = patient_payer or self.env["hospital.patient.payer"]
        if eligibility and encounter.patient_payer_id == eligibility:
            # Idempotent: re-selecting what is already selected is not a change,
            # so it must not trip the freeze on a visit that has already paid.
            return self._visit_payer_result(visit, encounter)

        # THE FREEZE. Front desk staff may correct a payer identity right up to
        # the moment money is taken or the consultation starts; after that a
        # correction is an eligibility-authority act.
        #
        # Decided from has_payer_authority, NOT from
        # encounter._has_payer_identity_authority(): the encounter may be a
        # sudo() recordset by now, and the superuser holds every group, so asking
        # it would answer True for anyone who reached this line.
        if not has_payer_authority:
            encounter._assert_payer_identity_changeable()

        self._apply_visit_payer(encounter, eligibility)
        return self._visit_payer_result(visit, encounter)

    @api.model
    def _visit_payer_result(self, appointment, encounter):
        """Bounded result. Records, never a money figure."""
        return {
            "appointment": appointment,
            "encounter": encounter,
            "patient_payer": encounter.patient_payer_id,
            "payer_type": encounter.payer_type,
            "front_desk_stage": appointment.front_desk_stage,
        }

    @api.model
    def selectable_eligibilities(self, patient, company=None):
        """Eligibilities this patient could be presented under TODAY.

        The window is narrowed in SQL to what could possibly qualify, then
        is_valid_today makes the final call -- it is a non-stored compute (it
        depends on today) and therefore cannot appear in a domain. Its rules are
        not restated here; that is the whole point of calling it.
        """
        patient.ensure_one()
        company = company or self.env.company
        today = fields.Date.context_today(self)
        candidates = self.env["hospital.patient.payer"].search(
            [
                ("patient_id", "=", patient.id),
                ("company_id", "=", company.id),
                ("state", "=", "active"),
                ("effective_from", "<=", today),
                "|",
                ("effective_to", "=", False),
                ("effective_to", ">=", today),
            ],
            order="effective_from desc, id desc",
        )
        return candidates.filtered(lambda record: record.is_valid_today)

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
    # Doctor assignment
    # ------------------------------------------------------------------
    @api.model
    def assign_doctor(self, appointment, doctor):
        """Assign or change the doctor for an already-registered visit.

        WHY THIS EXISTS. The doctor is recorded in three places:

            hospital.appointment.doctor_id            routing + the consultation
                                                      authorization check
            hospital.encounter.primary_doctor_id      the clinical episode
            hospital.patient.evaluation.physician_id  the triage document

        hospital.encounter only copies the doctor across in
        _onchange_appointment_id, which is an @api.onchange and therefore never
        fires on an ORM write. get_or_create_encounter stamps primary_doctor_id
        ONCE, at creation, from whatever appointment.doctor_id held at that
        moment. B2.1 registers visits with no doctor, so every one of them has
        an empty primary_doctor_id that a later write to appointment.doctor_id
        would silently leave behind.

        THE SYNCHRONIZATION RULE, and there is exactly one:

            appointment.doctor_id is the source of truth.
            It is propagated to encounter.primary_doctor_id always, and to
            evaluation.physician_id ONLY while that evaluation is still draft.

        A completed evaluation is deliberately left alone. physician_id is in
        LOCKED_CLINICAL_FIELDS, so writing it would raise; more importantly a
        finished triage is a clinical document that records who was responsible
        AT COMPLETION, and re-pointing the visit at a different doctor
        afterwards is an operational routing decision that must not rewrite it.

        Financially inert by construction. It writes two relational fields and
        nothing else: no charge is raised or re-priced (the consultation service
        is resolved per COMPANY in _ensure_consultation_billing, never per
        doctor), no clearance is recomputed or persisted, and no consultation is
        started.
        """
        self._assert_group(REGISTRATION_GROUPS, "assign a doctor to a visit")
        appointment.ensure_one()
        doctor.ensure_one()

        if appointment.state in ("cancelled", "done"):
            raise UserError(
                "Appointment %s is %s; its doctor can no longer be changed."
                % (appointment.appointment_code or appointment.id, appointment.state)
            )

        # Same rule the reception API already enforces at registration
        # (yoya_emr_api/controllers/reception.py::_validate_doctor_department).
        # A doctor with no department is unconstrained, exactly as there.
        department = appointment.department_id
        if department and doctor.department_id and doctor.department_id != department:
            raise ValidationError(
                "%s belongs to %s, not %s."
                % (
                    doctor.display_name,
                    doctor.department_id.display_name,
                    department.display_name,
                )
            )

        # The department is NEVER changed here, even though
        # hospital.appointment.create infers one from the doctor when it is
        # missing. Re-routing a checked-in patient to another department is a
        # different decision with its own consequences (triage destination,
        # nurse scoping, doctor eligibility) and does not belong in this method.
        if appointment.doctor_id != doctor:
            appointment.write({"doctor_id": doctor.id})

        encounter = appointment.encounter_id
        if encounter and encounter.primary_doctor_id != doctor:
            encounter.write({"primary_doctor_id": doctor.id})

        evaluation = appointment._latest_evaluation()
        if (
            evaluation
            and evaluation.state == "draft"
            and evaluation.physician_id != doctor
        ):
            evaluation.write({"physician_id": doctor.id})

        return {
            "appointment": appointment,
            "encounter": encounter,
            "evaluation": evaluation,
            "doctor": doctor,
        }

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

        # NO FINANCIAL GATE HERE, deliberately.
        #
        # The people at this hospital's entrance are nurses. They triage on
        # arrival, and the patient walks to the cashier AFTER the nursing
        # evaluation. Blocking triage on payment inverted that and does not match
        # how the hospital works.
        #
        # Financial clearance is NOT removed from the system -- it moved to the
        # gate that belongs to it. hospital_billing's
        # action_start_consultation() still calls
        # hospital.billing.engine.check_financial_clearance(persist=True) before
        # care commences, and hospital_appointment._assert_triage_completed()
        # still requires a completed evaluation. An unpaid patient can therefore
        # be triaged but still cannot see the doctor.
        #
        # The clearance summary is returned below for information, so the desk
        # can tell the patient what to pay -- it no longer decides anything here.
        clearance = encounter._reception_clearance_summary()

        if destination != appointment.triage_destination_id:
            appointment.write({"triage_destination_id": destination.id})

        return {
            "appointment": appointment,
            "encounter": encounter,
            "triage_destination": destination,
            "clearance": clearance,
            "queue_stage": appointment.clinical_queue_stage,
            "front_desk_stage": appointment.front_desk_stage,
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
