"""Slice 4: completing a consultation.

WHAT THESE TESTS ARE FOR
------------------------
Completion is the first IRREVERSIBLE act on the Doctor Desk. There is no
amendment workflow and no reopen path, so five properties carry this slice and
each has a class below.

  1. THE CLINICAL MINIMUM IS TWO RULES, NOT FIVE. An assessment, and an active
     primary diagnosis. Every additional required field is a field a clinician
     will fabricate rather than be blocked by, and fabricated clinical content
     is worse than absent clinical content. Certainty is deliberately NOT
     required to be `final`: demanding it would force doctors to overstate
     certainty on exactly the visits where laboratory work is still pending.

  2. COMPLETION IS DELEGATED, NOT REIMPLEMENTED.
     hospital.consultation.action_complete() writes two columns and then calls
     appointment.action_done(). Everything else -- the appointment transition,
     the consultation charge delivery, the encounter move -- stays where
     hospital_billing already put it. The tests assert the whole chain fires,
     precisely so nobody is tempted to copy it.

  3. THE TRANSITION IS ONE UNIT. Four records move together. A failure while
     BUILDING THE RESPONSE must roll back all four, or a doctor is told
     completion failed while looking at a note that is now frozen.

  4. PENDING WORK DOES NOT BLOCK COMPLETION, AND COMPLETION DOES NOT UNBLOCK
     PENDING WORK. A doctor may legitimately finish while laboratory results
     are outstanding; an unpaid test stays ungated by the lab's own rules.

  5. THE PATIENT DOES NOT VANISH FROM THE CASHIER. This is the Defect A
     carry-forward, and it is the single most dangerous regression this slice
     could introduce: completing a consultation moves the visit to `done`, and
     before the lane was widened that made an unpaid patient disappear from
     every cashier queue at the exact moment their doctor signed off.
"""
import json
import uuid
from unittest.mock import patch

from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import tagged
from odoo.tools import mute_logger

from .test_doctor_laboratory_api import LaboratoryCase

COMPLETE = "/yoya-emr/api/v1/doctor/visits/%s/consultation/complete"
CONSULTATION = "/yoya-emr/api/v1/doctor/visits/%s/consultation"
SAVE = "/yoya-emr/api/v1/doctor/visits/%s/consultation/save"
DIAGNOSES = "/yoya-emr/api/v1/doctor/visits/%s/diagnoses"
LAB_ORDERS = "/yoya-emr/api/v1/doctor/visits/%s/orders/laboratory"

COMPLETE_SERIALIZER_TARGET = (
    "odoo.addons.yoya_emr_api.controllers.doctor.serialize_visit_detail"
)

# Billing / accounting vocabulary that has no business in a clinical payload.
FORBIDDEN_COMPLETION_KEYS = (
    "charge_line", "charge_id", "billing_account", "receipt", "allocation",
    "sponsor", "payer_id", "agreement", "tariff", "invoice", "responsibility_state",
    "amount_estimated", "amount_received", "accounting",
)


class CompletionCase(LaboratoryCase):
    """A visit in consultation, with the clinical minimum on hand."""

    def _assessment(self, appointment, text="Migraine, no red flags."):
        """Save an assessment through the REAL endpoint, as the doctor.

        Deliberately not a sudo() write: the version chaining and the freeze are
        part of what completion interacts with, and a fixture that bypassed them
        would test a shape the desk never produces.
        """
        _r, payload = self._get(CONSULTATION % appointment.id)
        version = payload["data"]["consultation"]["version"]
        response, body = self._post_body(
            SAVE % appointment.id, {"version": version, "assessment": text}
        )
        self.assertTrue(body["success"], body)
        return response, body

    def _primary(self, appointment, certainty=None):
        extra = {"certainty": certainty} if certainty else {}
        response, payload = self._add(
            appointment, diagnosis_type="primary", **extra
        )
        self.assertTrue(payload["success"], payload)
        return payload

    def _completable_visit(self, doctor=None, certainty=None):
        """in_consultation + assessment + active primary diagnosis."""
        appointment, encounter = self._in_consultation_visit(doctor=doctor)
        self._assessment(appointment)
        self._primary(appointment, certainty=certainty)
        return appointment, encounter

    def _version(self, appointment):
        _r, payload = self._get(CONSULTATION % appointment.id)
        return payload["data"]["consultation"]["version"]

    def _complete(self, appointment, version=None, user=None, password=None):
        return self._post_body(
            COMPLETE % appointment.id,
            {"version": version or self._version(appointment)},
            user=user,
            password=password,
        )

    def _envelope(self, appointment):
        _r, payload = self._get(CONSULTATION % appointment.id)
        return payload["data"]


# ======================================================================
# 1. THE CLINICAL MINIMUM
# ======================================================================
@tagged("post_install", "-at_install", "doctor_consultation_complete")
class TestCompletionMinimumData(CompletionCase):

    def test_assessment_is_required(self):
        appointment, _e = self._in_consultation_visit()
        self._primary(appointment)

        response, payload = self._complete(appointment)
        self.assertEqual(response.status_code, 422)
        self.assertEqual(payload["error"]["code"], "consultation_incomplete")
        self.assertIn("assessment", json.dumps(payload).lower())

        appointment.invalidate_recordset()
        self.assertEqual(
            appointment.state, "in_consultation",
            "A refused completion transitions nothing.",
        )

    def test_a_diagnosis_is_required(self):
        appointment, _e = self._in_consultation_visit()
        self._assessment(appointment)

        response, payload = self._complete(appointment)
        self.assertEqual(response.status_code, 422)
        blockers = self._envelope(appointment)["completion_blockers"]
        self.assertIn(
            "no_diagnosis", [blocker["code"] for blocker in blockers]
        )

    def test_a_primary_diagnosis_is_required(self):
        """A secondary diagnosis is not a substitute, and the message says so.

        The two blockers are deliberately DIFFERENT sentences: "record a
        diagnosis" and "mark one of the diagnoses you already recorded as
        primary" are different actions, and one sentence covering both would
        send half the doctors who read it to the wrong control.
        """
        appointment, _e = self._in_consultation_visit()
        self._assessment(appointment)
        _r, payload = self._add(appointment, diagnosis_type="secondary")
        self.assertTrue(payload["success"], payload)

        response, _body = self._complete(appointment)
        self.assertEqual(response.status_code, 422)

        blockers = self._envelope(appointment)["completion_blockers"]
        codes = [blocker["code"] for blocker in blockers]
        self.assertIn("no_primary_diagnosis", codes)
        self.assertNotIn("no_diagnosis", codes)

    def test_a_provisional_primary_is_enough(self):
        """THE RULE THIS SLICE MOST EASILY GETS WRONG.

        Requiring `final` would force a doctor to overstate certainty on
        exactly the visits where laboratory work is still pending -- the
        dangerous direction. hospital.patient.diagnosis.certainty has no
        default for the same reason.
        """
        appointment, _e = self._completable_visit(certainty="provisional")
        response, payload = self._complete(appointment)
        self.assertEqual(response.status_code, 200, payload)
        self.assertEqual(payload["data"]["consultation"]["state"], "completed")

    def test_a_final_primary_is_also_enough(self):
        appointment, _e = self._completable_visit(certainty="final")
        response, payload = self._complete(appointment)
        self.assertEqual(response.status_code, 200, payload)

    def test_plan_is_not_required(self):
        """`plan` is legitimately empty when the plan IS the orders placed."""
        appointment, _e = self._completable_visit()
        consultation = self._consultation_for(appointment)
        self.assertFalse(consultation.plan)

        response, _payload = self._complete(appointment)
        self.assertEqual(response.status_code, 200)

    def test_the_other_narrative_fields_are_not_required(self):
        """Only `assessment` is required. Five of the six stay optional.

        presenting_complaint is deliberately absent from the rule for a reason
        the other four do not share: it is SEEDED from the triage chief
        complaint, so requiring it would be a rule that passes automatically on
        almost every visit -- the appearance of a check with none of the effect.
        """
        appointment, _e = self._completable_visit()
        consultation = self._consultation_for(appointment)
        for field in (
            "history_of_presenting_illness",
            "review_of_systems",
            "examination_findings",
            "plan",
        ):
            self.assertFalse(consultation[field], field)

        response, _payload = self._complete(appointment)
        self.assertEqual(response.status_code, 200)

    def test_whitespace_is_not_an_assessment(self):
        """Fabricated content is worse than absent content, and a space is the
        cheapest fabrication available."""
        appointment, _e = self._in_consultation_visit()
        self._primary(appointment)
        version = self._version(appointment)
        self._post_body(
            SAVE % appointment.id, {"version": version, "assessment": "      "}
        )

        response, _payload = self._complete(appointment)
        self.assertEqual(response.status_code, 422)

    def test_can_complete_tracks_the_blockers_live(self):
        """The button and the rule are ONE implementation, consulted twice."""
        appointment, _e = self._in_consultation_visit()

        envelope = self._envelope(appointment)
        self.assertFalse(envelope["can_complete"])
        self.assertTrue(envelope["completion_blockers"])

        self._assessment(appointment)
        self.assertFalse(self._envelope(appointment)["can_complete"])

        self._primary(appointment)
        envelope = self._envelope(appointment)
        self.assertTrue(envelope["can_complete"])
        self.assertEqual(envelope["completion_blockers"], [])

    def test_a_removed_primary_reopens_the_blocker(self):
        """Archiving the primary must put the blocker back, or the desk would
        offer a completion the server then refuses."""
        appointment, _e = self._completable_visit()
        self.assertTrue(self._envelope(appointment)["can_complete"])

        consultation = self._consultation_for(appointment)
        diagnosis = self._diagnoses_of(consultation)[0]
        self._post_body(
            "/yoya-emr/api/v1/doctor/visits/%s/diagnoses/%s/remove"
            % (appointment.id, diagnosis.id),
            {},
        )

        envelope = self._envelope(appointment)
        self.assertFalse(envelope["can_complete"])
        self.assertIn(
            "no_diagnosis",
            [blocker["code"] for blocker in envelope["completion_blockers"]],
        )


# ======================================================================
# 2. THE TRANSITION
# ======================================================================
@tagged("post_install", "-at_install", "doctor_consultation_complete")
class TestCompletionTransition(CompletionCase):

    def test_the_whole_chain_fires_from_one_call(self):
        """FOUR RECORDS MOVE, and only two of them are written here.

        action_complete() writes consultation.state and completed_at, then
        delegates to appointment.action_done(). The appointment transition, the
        charge delivery and the encounter move all belong to hospital_billing's
        existing override -- asserted here precisely so nobody reimplements
        them inside the consultation.
        """
        appointment, encounter = self._completable_visit()
        charge = appointment.sudo()._consultation_charge()
        self.assertEqual(encounter.sudo().state, "active")
        self.assertNotEqual(charge.delivery_state, "delivered")

        response, payload = self._complete(appointment)
        self.assertEqual(response.status_code, 200, payload)

        appointment.invalidate_recordset()
        encounter.invalidate_recordset()
        charge.invalidate_recordset()
        consultation = self._consultation_for(appointment)

        self.assertEqual(consultation.state, "completed")
        self.assertTrue(consultation.completed_at)
        self.assertEqual(appointment.state, "done")
        self.assertEqual(encounter.state, "completed")
        self.assertEqual(charge.delivery_state, "delivered")

    def test_the_encounter_is_completed_but_never_closed(self):
        """COMPLETED, NOT CLOSED, and the difference is load-bearing.

        hospital.billing.engine refuses new charges on a CLOSED encounter
        (LOCKED_ENCOUNTER_STATES), and hospital.encounter refuses most writes on
        one. 'completed' is in neither set, which is exactly what lets a
        laboratory result, a late radiology charge or a pharmacy dispense land
        against a visit whose consultation is over. Closing here would break
        every downstream workflow this hospital has.
        """
        appointment, encounter = self._completable_visit()
        self._complete(appointment)

        encounter.invalidate_recordset()
        self.assertEqual(encounter.sudo().state, "completed")
        self.assertNotIn(encounter.sudo().state, ("closed", "cancelled"))

    def test_the_response_carries_the_post_completion_picture(self):
        appointment, _e = self._completable_visit()
        _r, payload = self._complete(appointment)
        data = payload["data"]

        self.assertFalse(data["consultation"]["editable"])
        self.assertEqual(data["consultation"]["state"], "completed")
        self.assertFalse(data["can_complete"])
        self.assertEqual(data["visit_detail"]["visit"]["state"], "done")
        self.assertEqual(
            data["bucket"], "finished",
            "The queue re-buckets from this payload, not from a guess.",
        )

    def test_completing_twice_is_refused(self):
        appointment, _e = self._completable_visit()
        self._complete(appointment)

        response, payload = self._complete(appointment)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(
            payload["error"]["code"], "consultation_not_available",
            "A done visit has nothing left to complete.",
        )

    def test_completion_writes_no_clinical_content(self):
        """Narrative on the completion body is REJECTED BY NAME.

        Silently dropping it would let a client believe an unsaved paragraph
        had been signed. This is the server half of the Doctor Desk refusing to
        complete while the note is dirty.
        """
        appointment, _e = self._completable_visit()
        response, payload = self._post_body(
            COMPLETE % appointment.id,
            {"version": self._version(appointment), "assessment": "sneaky edit"},
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(payload["error"]["code"], "unknown_field")

        appointment.invalidate_recordset()
        self.assertEqual(appointment.state, "in_consultation")
        self.assertNotEqual(
            self._consultation_for(appointment).assessment, "sneaky edit"
        )

    def test_a_visit_that_never_started_cannot_complete(self):
        appointment, _e = self._ready_visit()
        response, payload = self._post_body(
            COMPLETE % appointment.id, {"version": "anything"}
        )
        self.assertEqual(response.status_code, 409)
        self.assertEqual(payload["error"]["code"], "consultation_not_available")

    def test_version_is_required(self):
        appointment, _e = self._completable_visit()
        response, payload = self._post_body(COMPLETE % appointment.id, {})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(payload["error"]["code"], "missing_version")


# ======================================================================
# 3. ATOMICITY AND CONCURRENCY
# ======================================================================
@tagged("post_install", "-at_install", "doctor_consultation_complete")
class TestCompletionAtomicity(CompletionCase):

    def test_a_stale_version_is_refused_and_transitions_nothing(self):
        appointment, encounter = self._completable_visit()
        stale = self._version(appointment)

        # Somebody else writes, moving write_date and invalidating the token.
        self._assessment(appointment, text="Revised assessment.")

        response, payload = self._complete(appointment, version=stale)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(payload["error"]["code"], "consultation_conflict")

        appointment.invalidate_recordset()
        encounter.invalidate_recordset()
        self.assertEqual(appointment.state, "in_consultation")
        self.assertEqual(encounter.sudo().state, "active")
        self.assertEqual(self._consultation_for(appointment).state, "draft")

    def test_two_tabs_completing_gives_one_winner(self):
        """The second tab holds a token minted before the first tab's write."""
        appointment, _e = self._completable_visit()
        tab_one = self._version(appointment)
        tab_two = tab_one

        first, _p = self._complete(appointment, version=tab_one)
        self.assertEqual(first.status_code, 200)

        second, payload = self._complete(appointment, version=tab_two)
        self.assertEqual(second.status_code, 409)
        self.assertIn(
            payload["error"]["code"],
            ("consultation_conflict", "consultation_not_available"),
        )

        self.assertEqual(
            len(self._consultation_of(_e)), 1,
            "No second consultation was manufactured by the loser.",
        )

    @mute_logger("odoo.addons.yoya_emr_api.controllers.doctor")
    def test_a_response_failure_rolls_the_whole_transition_back(self):
        """THE TEST THIS FILE EXISTS FOR.

        doctor_endpoint catches exceptions and RETURNS a response, and Odoo's
        dispatcher commits on a normal return. Without the savepoint, a failure
        while serializing would commit the frozen consultation, the done
        appointment, the completed encounter and the delivered charge -- and
        then tell the doctor completion had failed. They would be looking at a
        locked note they believe is still open, with no amendment workflow to
        recover through.
        """
        appointment, encounter = self._completable_visit()
        charge = appointment.sudo()._consultation_charge()

        with patch(
            COMPLETE_SERIALIZER_TARGET,
            side_effect=RuntimeError("serializer exploded"),
        ):
            response, payload = self._complete(appointment)

        self.assertEqual(response.status_code, 500)
        self.assertEqual(
            payload["error"]["code"], "consultation_complete_response_failed"
        )

        appointment.invalidate_recordset()
        encounter.invalidate_recordset()
        charge.invalidate_recordset()
        consultation = self._consultation_for(appointment)

        self.assertEqual(consultation.state, "draft")
        self.assertFalse(consultation.completed_at)
        self.assertEqual(appointment.state, "in_consultation")
        self.assertEqual(encounter.state, "active")
        self.assertNotEqual(charge.delivery_state, "delivered")

    @mute_logger("odoo.addons.yoya_emr_api.controllers.doctor")
    def test_a_retry_after_a_rolled_back_failure_succeeds(self):
        """The rollback has to leave a state the doctor can actually retry from."""
        appointment, _e = self._completable_visit()

        with patch(
            COMPLETE_SERIALIZER_TARGET, side_effect=RuntimeError("boom")
        ):
            self._complete(appointment)

        response, payload = self._complete(appointment)
        self.assertEqual(response.status_code, 200, payload)
        appointment.invalidate_recordset()
        self.assertEqual(appointment.state, "done")


# ======================================================================
# 4. THE FREEZE, AND READING AFTER COMPLETION
# ======================================================================
@tagged("post_install", "-at_install", "doctor_consultation_complete")
class TestAfterCompletion(CompletionCase):

    def _completed_visit(self):
        appointment, encounter = self._completable_visit()
        response, _p = self._complete(appointment)
        self.assertEqual(response.status_code, 200)
        appointment.invalidate_recordset()
        return appointment, encounter

    # ---------------------------------------------------------- read
    def test_the_note_endpoint_still_serves_a_completed_visit(self):
        """THE BUG SLICE 4 HAD TO FIX ALONGSIDE COMPLETION.

        consultation_detail used to short-circuit on
        `state != in_consultation`, so the moment a visit was completed its note
        vanished behind "The consultation has not been started for this visit
        yet" -- told to the doctor who had just signed it.
        """
        appointment, _e = self._completed_visit()

        response, payload = self._get(CONSULTATION % appointment.id)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(payload["data"]["available"])
        self.assertIsNotNone(payload["data"]["consultation"])
        self.assertEqual(payload["data"]["consultation"]["state"], "completed")
        self.assertFalse(payload["data"]["consultation"]["editable"])
        self.assertTrue(payload["data"]["consultation"]["assessment"])

    def test_the_diagnosis_endpoint_still_serves_a_completed_visit(self):
        appointment, _e = self._completed_visit()
        response, payload = self._get(DIAGNOSES % appointment.id)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(payload["data"]["diagnoses"])
        self.assertFalse(
            payload["data"]["editable"], "Readable, and not editable."
        )

    def test_the_laboratory_endpoint_still_serves_a_completed_visit(self):
        appointment, _e = self._completable_visit()
        self._order(appointment)
        self._complete(appointment)

        response, payload = self._get(LAB_ORDERS % appointment.id)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(payload["data"]["orders"])
        self.assertFalse(payload["data"]["can_order"])

    def test_a_pre_start_visit_still_reads_as_unavailable(self):
        """The consultation-keyed rewrite must not turn "never started" into
        "available and empty"."""
        appointment, _e = self._ready_visit()
        response, payload = self._get(CONSULTATION % appointment.id)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(payload["data"]["available"])
        self.assertIsNone(payload["data"]["consultation"])
        self.assertFalse(payload["data"]["can_complete"])

    def test_the_read_creates_nothing_for_a_pre_start_visit(self):
        appointment, encounter = self._ready_visit()
        self._get(CONSULTATION % appointment.id)
        self.assertFalse(self._consultation_of(encounter))

    # ---------------------------------------------------------- freeze
    def test_the_note_cannot_be_saved_after_completion(self):
        appointment, _e = self._completed_visit()
        consultation = self._consultation_for(appointment)
        before = consultation.assessment

        response, payload = self._post_body(
            SAVE % appointment.id,
            {"version": consultation.version_token(), "assessment": "amended"},
        )
        self.assertEqual(response.status_code, 409)

        consultation.invalidate_recordset()
        self.assertEqual(consultation.assessment, before)

    def test_a_diagnosis_cannot_be_added_after_completion(self):
        appointment, _e = self._completed_visit()
        response, payload = self._add(
            appointment, disease=self.other_disease, diagnosis_type="secondary"
        )
        self.assertEqual(response.status_code, 409)
        self.assertEqual(
            payload["error"]["code"], "consultation_completed",
            "A done visit must not be reported as one that never started.",
        )

    def test_a_diagnosis_cannot_be_edited_after_completion(self):
        appointment, _e = self._completable_visit()
        consultation = self._consultation_for(appointment)
        diagnosis = self._diagnoses_of(consultation)[0]
        self._complete(appointment)

        response, _payload = self._post_body(
            "/yoya-emr/api/v1/doctor/visits/%s/diagnoses/%s/update"
            % (appointment.id, diagnosis.id),
            {"certainty": "final"},
        )
        self.assertEqual(response.status_code, 409)
        diagnosis.invalidate_recordset()
        self.assertNotEqual(diagnosis.certainty, "final")

    def test_a_diagnosis_cannot_be_removed_after_completion(self):
        appointment, _e = self._completable_visit()
        consultation = self._consultation_for(appointment)
        diagnosis = self._diagnoses_of(consultation)[0]
        self._complete(appointment)

        response, _payload = self._post_body(
            "/yoya-emr/api/v1/doctor/visits/%s/diagnoses/%s/remove"
            % (appointment.id, diagnosis.id),
            {},
        )
        self.assertEqual(response.status_code, 409)
        diagnosis.invalidate_recordset()
        self.assertTrue(diagnosis.active)

    def test_a_new_laboratory_order_is_refused_after_completion(self):
        appointment, _e = self._completed_visit()
        response, _payload = self._order(appointment)
        self.assertEqual(response.status_code, 409)

    def test_the_model_refuses_a_direct_write_too(self):
        """The freeze is a MODEL rule, not an API one."""
        appointment, _e = self._completed_visit()
        consultation = self._consultation_for(appointment)
        with self.assertRaises(UserError):
            consultation.write({"assessment": "bypassed"})


# ======================================================================
# 5. AUTHORIZATION
# ======================================================================
@tagged("post_install", "-at_install", "doctor_consultation_complete")
class TestCompletionAuthorization(CompletionCase):

    def test_the_assigned_doctor_may_complete(self):
        appointment, _e = self._completable_visit()
        response, payload = self._complete(appointment)
        self.assertEqual(response.status_code, 200, payload)

    def test_another_doctor_may_not_complete(self):
        """Scope hides the visit from a doctor it is not assigned to, so the
        refusal arrives as 404 rather than 403 -- the same answer every other
        cross-doctor path in this API gives, and deliberately so: naming the
        visit would confirm a patient the caller may not know about."""
        appointment, _e = self._completable_visit()
        version = self._version(appointment)

        response, _payload = self._complete(
            appointment,
            version=version,
            user=self.other_user,
            password=self.other_password,
        )
        self.assertIn(response.status_code, (403, 404))

        appointment.invalidate_recordset()
        self.assertEqual(appointment.state, "in_consultation")

    def test_the_model_refuses_an_unassigned_doctor_directly(self):
        """Model-layer, bypassing scope entirely -- the real control."""
        appointment, _e = self._completable_visit()
        consultation = self._consultation_for(appointment)
        version = consultation.version_token()

        with self.assertRaises(AccessError):
            consultation.with_user(self.other_user).action_complete(version)

    def test_a_nurse_may_not_complete(self):
        appointment, _e = self._completable_visit()
        response, _payload = self._complete(
            appointment, user=self.nurse, password=self.nurse_password
        )
        self.assertIn(response.status_code, (403, 404))

    def test_a_receptionist_may_not_complete(self):
        appointment, _e = self._completable_visit()
        response, _payload = self._complete(
            appointment,
            user=self.receptionist,
            password=self.receptionist_password,
        )
        self.assertIn(response.status_code, (403, 404))

    def test_a_manager_may_complete(self):
        appointment, _e = self._completable_visit()
        consultation = self._consultation_for(appointment)
        consultation.with_user(self.manager).action_complete(
            consultation.version_token()
        )
        appointment.invalidate_recordset()
        self.assertEqual(appointment.state, "done")

    def test_the_override_groups_mirror_the_start_gate(self):
        """The person who may OPEN a consultation is the person who may CLOSE
        it. A different answer at the two ends would make the clinical author of
        the episode ambiguous."""
        from odoo.addons.yoya_clinical_bridge.models.consultation import (
            COMPLETION_OVERRIDE_GROUPS,
        )
        from odoo.addons.yoya_reception_bridge.models.hospital_appointment import (
            CONSULTATION_OVERRIDE_GROUPS,
        )

        self.assertEqual(
            tuple(COMPLETION_OVERRIDE_GROUPS), tuple(CONSULTATION_OVERRIDE_GROUPS)
        )


# ======================================================================
# 6. appointment.action_done() AND action_reset_to_draft() HARDENING
# ======================================================================
@tagged("post_install", "-at_install", "doctor_consultation_complete")
class TestAppointmentHardening(CompletionCase):

    def test_a_receptionist_cannot_finish_a_visit(self):
        """hospital_management.action_done() carries no authorization at all,
        and the receptionist's record rule on hospital.appointment is
        [(1,'=',1)] with write granted. Before Slice 4 that was untidy; now
        action_done() freezes a clinical note, delivers a charge and completes
        an encounter."""
        appointment, _e = self._completable_visit()
        with self.assertRaises(AccessError):
            appointment.with_user(self.receptionist).action_done()

        appointment.invalidate_recordset()
        self.assertEqual(appointment.state, "in_consultation")

    def test_a_nurse_cannot_finish_a_visit(self):
        appointment, _e = self._completable_visit()
        with self.assertRaises(AccessError):
            appointment.with_user(self.nurse).action_done()

    def test_the_assigned_doctor_can_finish_a_visit(self):
        appointment, _e = self._completable_visit()
        appointment.with_user(self.doctor.user_id).action_done()
        appointment.invalidate_recordset()
        self.assertEqual(appointment.state, "done")

    def test_action_done_on_a_finished_visit_stays_a_no_op(self):
        """Raising at a caller whose no-op was previously harmless would be a
        behaviour change this slice has no business making."""
        appointment, _e = self._completable_visit()
        self._complete(appointment)
        appointment.invalidate_recordset()

        appointment.with_user(self.receptionist).action_done()
        appointment.invalidate_recordset()
        self.assertEqual(appointment.state, "done")

    # ------------------------------------------------------------ reset
    def test_a_non_manager_cannot_reset_to_draft(self):
        """The vendor method is protected by nothing but a view-button
        attribute, which stops nobody reaching it over RPC."""
        appointment, _e = self._completable_visit()
        appointment.sudo().action_cancel()

        with self.assertRaises(AccessError):
            appointment.with_user(self.receptionist).action_reset_to_draft()

    def test_a_manager_may_reset_an_ordinary_cancelled_visit(self):
        appointment, _e = self._ready_visit()
        appointment.sudo().action_cancel()

        appointment.with_user(self.manager).action_reset_to_draft()
        appointment.invalidate_recordset()
        self.assertEqual(appointment.state, "draft")

    def test_a_completed_consultation_blocks_reset_even_for_a_manager(self):
        """THE HAZARD THE INSPECTION FLAGGED.

        action_reset_to_draft writes state and NOTHING else -- it does not reset
        the encounter, unfreeze the consultation or reverse the delivered
        charge. Run against a completed visit it produces appointment=draft with
        encounter=completed and consultation=completed: a visit that looks
        startable and is not, because starting it would try to open a SECOND
        consultation on an encounter whose unique index already refuses one.
        """
        appointment, encounter = self._completable_visit()
        self._complete(appointment)
        appointment.invalidate_recordset()

        with self.assertRaises(UserError):
            appointment.with_user(self.manager).action_reset_to_draft()

        appointment.invalidate_recordset()
        encounter.invalidate_recordset()
        self.assertEqual(appointment.state, "done")
        self.assertEqual(encounter.sudo().state, "completed")
        self.assertEqual(self._consultation_for(appointment).state, "completed")

    def test_the_refusal_names_the_missing_workflow(self):
        appointment, _e = self._completable_visit()
        self._complete(appointment)
        appointment.invalidate_recordset()

        with self.assertRaises(UserError) as caught:
            appointment.with_user(self.manager).action_reset_to_draft()
        self.assertIn("amendment", str(caught.exception).lower())


# ======================================================================
# 7. PENDING ORDERS
# ======================================================================
@tagged("post_install", "-at_install", "doctor_consultation_complete")
class TestCompletionWithPendingOrders(CompletionCase):

    def test_a_pending_laboratory_order_does_not_block_completion(self):
        """A doctor may legitimately finish while laboratory work is pending.

        Consultation completion does not inspect downstream order states at ALL
        -- not laboratory now, and not radiology, medication or procedures when
        those arrive. The rule is generic by being absent.
        """
        appointment, _e = self._completable_visit()
        _r, payload = self._order(appointment)
        self.assertTrue(payload["success"], payload)

        response, body = self._complete(appointment)
        self.assertEqual(response.status_code, 200, body)

    def test_a_pending_order_surfaces_as_a_WARNING_not_a_blocker(self):
        appointment, _e = self._completable_visit()
        self._order(appointment)

        envelope = self._envelope(appointment)
        self.assertTrue(
            envelope["can_complete"], "A warning must never gate completion."
        )
        codes = [warning["code"] for warning in envelope["completion_warnings"]]
        self.assertIn("pending_orders", codes)

    def test_the_order_survives_completion_unchanged(self):
        appointment, _e = self._completable_visit()
        _r, payload = self._order(appointment)
        order_id = payload["data"]["orders"][0]["id"]
        request = self.env["hospital.laboratory.request"].sudo().browse(order_id)
        state_before = request.state

        self._complete(appointment)
        request.invalidate_recordset()
        self.assertEqual(request.state, state_before)

    def test_the_warnings_carry_no_billing_internals(self):
        appointment, _e = self._completable_visit()
        self._order(appointment)
        blob = json.dumps(self._envelope(appointment))
        for banned in FORBIDDEN_COMPLETION_KEYS:
            self.assertNotIn(banned, blob, banned)

    def test_the_completion_response_carries_no_billing_internals(self):
        appointment, _e = self._completable_visit()
        self._order(appointment)
        _r, payload = self._complete(appointment)
        blob = json.dumps(payload)
        for banned in FORBIDDEN_COMPLETION_KEYS:
            self.assertNotIn(banned, blob, banned)


# ======================================================================
# 8. THE DOCTOR WORKLIST BUCKETS
# ======================================================================
@tagged("post_install", "-at_install", "doctor_consultation_complete")
class TestCompletionBuckets(CompletionCase):

    def test_in_consultation_is_OPEN_and_done_is_FINISHED(self):
        """The semantic bug Slice 4 had to fix.

        FINISHED_STAGES used to be ("in_consultation", "completed"), which put
        the patient the doctor was currently examining into the Finished tab.
        Tolerable only while nothing could ever leave in_consultation.
        """
        from odoo.addons.yoya_emr_api.services.doctor_serializers import bucket_of

        self.assertEqual(
            bucket_of({"queue_stage": "in_consultation", "state": "in_consultation"}),
            "open",
        )
        self.assertEqual(
            bucket_of({"queue_stage": "completed", "state": "done"}), "finished"
        )
        self.assertEqual(
            bucket_of({"queue_stage": "ready_doctor", "state": "confirmed"}),
            "review",
        )
        self.assertEqual(
            bucket_of({"queue_stage": "awaiting_cashier", "state": "confirmed"}),
            "wait",
        )

    def test_the_frontend_mirror_declares_the_same_vocabulary(self):
        """LOCKSTEP, ASSERTED. The two implementations are separate files in
        separate languages; nothing but a test keeps them honest."""
        import os
        import re

        from odoo.addons.yoya_emr_api.services import doctor_serializers

        mirror = os.path.join(
            os.path.dirname(doctor_serializers.__file__),
            "..", "..", "..", "..", "web", "src", "lib", "doctor-format.ts",
        )
        mirror = os.path.normpath(mirror)
        if not os.path.exists(mirror):
            self.skipTest("front-end mirror not present in this checkout")

        source = open(mirror, encoding="utf-8").read()
        declared = re.search(
            r"export const DOCTOR_BUCKETS = \[(.*?)\] as const;", source, re.S
        )
        self.assertTrue(declared, "DOCTOR_BUCKETS not found in the mirror")
        keys = set(re.findall(r'key:\s*"(\w+)"', declared.group(1)))
        self.assertEqual(keys, {"all", "wait", "review", "open", "finished"})

        self.assertIn('const OPEN_STAGES: readonly DoctorQueueStage[] = ["in_consultation"]', source)
        self.assertIn('const FINISHED_STAGES: readonly DoctorQueueStage[] = ["completed"]', source)

    def test_a_completed_visit_reports_the_finished_bucket_on_the_queue(self):
        appointment, _e = self._completable_visit()
        self._complete(appointment)

        response, payload = self._get("/yoya-emr/api/v1/doctor/worklist")
        self.assertEqual(response.status_code, 200)
        rows = [
            row
            for row in payload["data"]["rows"]
            if row["appointment_id"] == appointment.id
        ]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["queue_stage"], "completed")
        self.assertIn("open", payload["data"]["counts"])
