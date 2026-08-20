"""Diagnosis in the Doctor Consultation: ownership, the primary slot, the freeze.

WHAT THESE TESTS ARE FOR
------------------------
Five properties carry this slice, and each has a class below.

  1. OWNERSHIP IS DERIVED. Patient, encounter, appointment and physician all
     come from the consultation the caller already resolved through their own
     scope. The API rejects every one of them by name.

  2. ONE PRIMARY PER CONSULTATION, and the refusal is a decision the doctor can
     act on rather than a silent demotion of somebody else's judgement. The
     guarantee is a PARTIAL UNIQUE INDEX, so a race cannot produce two.

  3. THE FREEZE IS ALREADY LIVE. Nothing in this slice can complete a
     consultation, but a completed one's diagnoses must already be immutable --
     otherwise the freeze is a rule only the code arriving later obeys.

  4. SCOPE IS REAL NOW. hospital_management shipped this model with ACLs and NO
     record rules, so every doctor saw every diagnosis in the hospital. These
     tests pin that a pure doctor now reaches only their own.

  5. THE CONFIDENTIALITY BOUNDARY HOLDS. A diagnosis may later justify a claim;
     that does not put money in the clinical payload.

Legacy rows -- diagnoses with no consultation, which is every historical row --
get their own tests, because the whole design depends on them staying valid.
"""
import json
import uuid
from unittest.mock import patch

from psycopg2 import IntegrityError

from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import tagged
from odoo.tools import mute_logger

from odoo.addons.yoya_clinical_bridge.models.patient_diagnosis import (
    DiagnosisPrimaryConflict,
)

from .test_doctor_consultation_api import ConsultationCase

CATALOGUE = "/yoya-emr/api/v1/doctor/catalogue/diseases"
DIAGNOSES = "/yoya-emr/api/v1/doctor/visits/%s/diagnoses"
UPDATE = "/yoya-emr/api/v1/doctor/visits/%s/diagnoses/%s/update"
REMOVE = "/yoya-emr/api/v1/doctor/visits/%s/diagnoses/%s/remove"

# Where the CONTROLLER looks the serializer up, not where it is defined.
DIAGNOSIS_SERIALIZER_TARGET = (
    "odoo.addons.yoya_emr_api.controllers.doctor.serialize_diagnosis_list"
)

EXPECTED_ROW_KEYS = {
    "id", "disease", "diagnosis_type", "certainty", "severity", "status",
    "notes", "diagnosis_date", "editable",
}

FORBIDDEN_KEYS = (
    "amount", "balance", "outstanding", "paid", "receipt", "sponsor",
    "agreement", "membership", "policy_number", "payer", "tariff", "price",
    "invoice", "charge", "credit_limit", "coverage",
)


class DiagnosisCase(ConsultationCase):
    """ConsultationCase plus a small disease catalogue."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        tag = uuid.uuid4().hex[:6]
        cls.category = cls.env["hospital.disease.category"].sudo().create(
            {"name": "Neuro %s" % tag, "code": "NEU%s" % tag.upper()}
        )
        cls.disease = cls._make_disease("Migraine without aura %s" % tag, "G43.0")
        cls.other_disease = cls._make_disease("Tension headache %s" % tag, "G44.2")
        cls.third_disease = cls._make_disease("Cluster headache %s" % tag, "G44.0")

    @classmethod
    def _make_disease(cls, name, code):
        return cls.env["hospital.disease"].sudo().create(
            {"name": name, "code": code, "category_id": cls.category.id}
        )

    # ------------------------------------------------------------------
    def _add(self, appointment, disease=None, diagnosis_type="primary", **extra):
        body = {
            "disease_id": (disease or self.disease).id,
            "diagnosis_type": diagnosis_type,
        }
        body.update(extra)
        return self._post_body(DIAGNOSES % appointment.id, body)

    def _rows(self, payload):
        return payload["data"]["diagnoses"]

    def _diagnoses_of(self, consultation, active_test=True):
        model = self.env["hospital.patient.diagnosis"].sudo()
        if not active_test:
            model = model.with_context(active_test=False)
        return model.search([("consultation_id", "=", consultation.id)])


@tagged("post_install", "-at_install", "doctor_diagnosis")
class TestDiagnosisCatalogue(DiagnosisCase):
    """The disease picker: bounded, searchable, read-only."""

    def test_search_matches_name_and_code(self):
        response, payload = self._get(CATALOGUE, q="Migraine")
        self.assertEqual(response.status_code, 200)
        names = [row["name"] for row in payload["data"]["diseases"]]
        self.assertIn(self.disease.name, names)

        _r, by_code = self._get(CATALOGUE, q="G44.2")
        self.assertIn(
            self.other_disease.name,
            [row["name"] for row in by_code["data"]["diseases"]],
        )

    def test_the_limit_is_clamped_server_side(self):
        """A client cannot opt out of the cap and pull the whole table."""
        _r, payload = self._get(CATALOGUE, limit=9999)
        self.assertLessEqual(payload["data"]["limit"], 50)
        self.assertLessEqual(len(payload["data"]["diseases"]), 50)

    def test_an_empty_query_is_still_bounded(self):
        _r, payload = self._get(CATALOGUE)
        self.assertLessEqual(len(payload["data"]["diseases"]), payload["data"]["limit"])

    def test_catalogue_rows_carry_no_commercial_field(self):
        _r, payload = self._get(CATALOGUE, q="Migraine")
        serialized = json.dumps(payload).lower()
        for forbidden in FORBIDDEN_KEYS:
            self.assertNotIn(forbidden, serialized, forbidden)

    def test_non_doctor_roles_are_denied_the_catalogue(self):
        for user, password in (
            (self.nurse, self.nurse_password),
            (self.cashier, self.cashier_password),
            (self.receptionist, self.receptionist_password),
        ):
            response, payload = self._get(CATALOGUE, user=user, password=password)
            self.assertEqual(response.status_code, 403, user.login)
            self.assertEqual(payload["error"]["code"], "access_denied", user.login)


@tagged("post_install", "-at_install", "doctor_diagnosis")
class TestDiagnosisRecording(DiagnosisCase):
    """Adding, listing and the ownership the client never gets to choose."""

    def test_an_empty_consultation_lists_nothing(self):
        appointment, _encounter = self._in_consultation_visit()
        response, payload = self._get(DIAGNOSES % appointment.id)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(self._rows(payload), [])
        self.assertTrue(payload["data"]["editable"])
        self.assertFalse(payload["data"]["has_primary"])

    def test_adding_a_diagnosis_derives_every_ownership_field(self):
        appointment, encounter = self._in_consultation_visit()
        consultation = self._consultation_for(appointment)

        response, payload = self._add(appointment)

        self.assertEqual(response.status_code, 200)
        rows = self._rows(payload)
        self.assertEqual(len(rows), 1)
        self.assertEqual(set(rows[0]), EXPECTED_ROW_KEYS)

        stored = self._diagnoses_of(consultation)
        self.assertEqual(len(stored), 1)
        self.assertEqual(stored.patient_id, appointment.patient_id)
        self.assertEqual(stored.encounter_id, encounter)
        self.assertEqual(stored.appointment_id, appointment)
        self.assertEqual(stored.consultation_id, consultation)
        self.assertEqual(stored.physician_id, consultation.doctor_id)
        self.assertTrue(stored.active)

    def test_the_api_rejects_client_supplied_ownership(self):
        appointment, _encounter = self._in_consultation_visit()

        for field in (
            "patient_id", "encounter_id", "consultation_id", "appointment_id",
            "physician_id", "active", "diagnosis_date",
        ):
            response, payload = self._add(appointment, **{field: 1})
            self.assertEqual(response.status_code, 400, field)
            self.assertEqual(payload["error"]["code"], "protected_field", field)

    def test_unknown_fields_are_rejected(self):
        appointment, _encounter = self._in_consultation_visit()
        response, payload = self._add(appointment, icd_code="G43")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(payload["error"]["code"], "unknown_field")

    def test_a_diagnosis_type_is_required(self):
        appointment, _encounter = self._in_consultation_visit()
        response, payload = self._post_body(
            DIAGNOSES % appointment.id, {"disease_id": self.disease.id}
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(payload["error"]["code"], "invalid_field")

    def test_an_unknown_disease_is_a_404(self):
        appointment, _encounter = self._in_consultation_visit()
        response, payload = self._post_body(
            DIAGNOSES % appointment.id,
            {"disease_id": 99999999, "diagnosis_type": "secondary"},
        )
        self.assertEqual(response.status_code, 404)
        self.assertEqual(payload["error"]["code"], "disease_not_found")

    def test_multiple_diagnoses_are_supported(self):
        appointment, _encounter = self._in_consultation_visit()

        self._add(appointment, self.disease, "primary")
        self._add(appointment, self.other_disease, "secondary")
        _r, payload = self._add(appointment, self.third_disease, "differential")

        rows = self._rows(payload)
        self.assertEqual(len(rows), 3)
        self.assertEqual(
            sorted(row["diagnosis_type"] for row in rows),
            ["differential", "primary", "secondary"],
        )

    def test_certainty_and_status_are_independent(self):
        """Provisional+Active, Final+Active and Final+Resolved must all persist."""
        appointment, _encounter = self._in_consultation_visit()

        combinations = [
            ("provisional", "active", "primary", self.disease),
            ("final", "active", "secondary", self.other_disease),
            ("final", "resolved", "differential", self.third_disease),
        ]
        for certainty, status, kind, disease in combinations:
            _r, payload = self._add(
                appointment, disease, kind, certainty=certainty, status=status
            )

        rows = {row["disease"]["id"]: row for row in self._rows(payload)}
        for certainty, status, _kind, disease in combinations:
            row = rows[disease.id]
            self.assertEqual(row["certainty"], certainty, disease.name)
            self.assertEqual(row["status"], status, disease.name)

    def test_certainty_is_never_inferred_from_type(self):
        appointment, _encounter = self._in_consultation_visit()

        _r, payload = self._add(
            appointment, self.disease, "differential", certainty="final"
        )

        # A differential diagnosis marked Final is clinically unusual but
        # legitimate, and nothing may quietly rewrite it to provisional.
        self.assertEqual(self._rows(payload)[0]["certainty"], "final")

    def test_notes_round_trip_including_line_breaks(self):
        appointment, _encounter = self._in_consultation_visit()
        text = "Onset 3 days ago.\n\nWorse in the morning."

        _r, payload = self._add(appointment, notes=text)

        self.assertEqual(self._rows(payload)[0]["notes"], text)

    def test_a_bad_selection_value_is_refused(self):
        appointment, _encounter = self._in_consultation_visit()
        response, _payload = self._add(appointment, certainty="maybe")
        self.assertEqual(response.status_code, 400)

    def test_diagnoses_cannot_be_added_before_the_visit_starts(self):
        appointment, _encounter = self._ready_visit()
        response, payload = self._add(appointment)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(payload["error"]["code"], "consultation_not_available")


@tagged("post_install", "-at_install", "doctor_diagnosis")
class TestDiagnosisPrimary(DiagnosisCase):
    """At most one active primary per consultation."""

    def test_a_second_primary_is_refused_with_a_stable_code(self):
        appointment, _encounter = self._in_consultation_visit()
        self._add(appointment, self.disease, "primary")

        response, payload = self._add(appointment, self.other_disease, "primary")

        self.assertEqual(response.status_code, 409)
        self.assertEqual(payload["error"]["code"], "diagnosis_primary_exists")
        # The refusal NAMES the diagnosis holding the slot, so the doctor knows
        # which one to demote rather than being told only that they cannot.
        self.assertIn(self.disease.name, payload["error"]["message"])

    def test_the_refusal_does_not_demote_the_existing_primary(self):
        """NO SILENT RECLASSIFICATION. The first primary must be untouched."""
        appointment, _encounter = self._in_consultation_visit()
        self._add(appointment, self.disease, "primary")
        consultation = self._consultation_for(appointment)

        self._add(appointment, self.other_disease, "primary")

        stored = self._diagnoses_of(consultation)
        self.assertEqual(len(stored), 1)
        self.assertEqual(stored.disease_id, self.disease)
        self.assertEqual(stored.diagnosis_type, "primary")

    def test_promoting_a_second_diagnosis_to_primary_is_refused(self):
        appointment, _encounter = self._in_consultation_visit()
        self._add(appointment, self.disease, "primary")
        _r, payload = self._add(appointment, self.other_disease, "secondary")
        secondary_id = [
            row["id"] for row in self._rows(payload)
            if row["diagnosis_type"] == "secondary"
        ][0]

        response, body = self._post_body(
            UPDATE % (appointment.id, secondary_id), {"diagnosis_type": "primary"}
        )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(body["error"]["code"], "diagnosis_primary_exists")

    def test_demoting_the_primary_frees_the_slot(self):
        appointment, _encounter = self._in_consultation_visit()
        _r, payload = self._add(appointment, self.disease, "primary")
        first_id = self._rows(payload)[0]["id"]

        self._post_body(
            UPDATE % (appointment.id, first_id), {"diagnosis_type": "secondary"}
        )
        response, body = self._add(appointment, self.other_disease, "primary")

        self.assertEqual(response.status_code, 200)
        self.assertTrue(body["data"]["has_primary"])

    def test_removing_the_primary_frees_the_slot(self):
        """The unique index is partial on `active`, which is what makes a
        corrected mistake replaceable rather than permanently blocking."""
        appointment, _encounter = self._in_consultation_visit()
        _r, payload = self._add(appointment, self.disease, "primary")
        first_id = self._rows(payload)[0]["id"]

        self._post_body(REMOVE % (appointment.id, first_id), {})
        response, body = self._add(appointment, self.other_disease, "primary")

        self.assertEqual(response.status_code, 200)
        self.assertTrue(body["data"]["has_primary"])

    def test_the_database_refuses_a_second_primary_even_past_the_model(self):
        """THE guarantee. The advisory lock produces the friendly refusal; this
        index is what holds when something races past it or skips the method.

        Written with raw SQL precisely because it bypasses every Python check,
        which is the only way to prove the constraint is real.
        """
        appointment, _encounter = self._in_consultation_visit()
        consultation = self._consultation_for(appointment)
        self._add(appointment, self.disease, "primary")
        existing = self._diagnoses_of(consultation)

        with self.assertRaises(IntegrityError), mute_logger("odoo.sql_db"):
            with self.cr.savepoint():
                self.env.cr.execute(
                    """
                    INSERT INTO hospital_patient_diagnosis
                        (patient_id, disease_id, consultation_id, encounter_id,
                         diagnosis_type, active, create_uid, write_uid,
                         create_date, write_date)
                    VALUES (%s, %s, %s, %s, 'primary', true, 1, 1, now(), now())
                    """,
                    (
                        existing.patient_id.id,
                        self.other_disease.id,
                        consultation.id,
                        existing.encounter_id.id,
                    ),
                )

    def test_two_consultations_may_each_have_their_own_primary(self):
        """The invariant is per CONSULTATION, not global."""
        first, _e1 = self._in_consultation_visit()
        second, _e2 = self._in_consultation_visit()

        one, _p1 = self._add(first, self.disease, "primary")
        two, _p2 = self._add(second, self.disease, "primary")

        self.assertEqual(one.status_code, 200)
        self.assertEqual(two.status_code, 200)

    def test_legacy_rows_without_a_consultation_are_unconstrained(self):
        """Historical primaries must never collide with each other on NULL."""
        patient = self.env["hospital.patient"].sudo().create(
            {"name": "Legacy %s" % uuid.uuid4().hex[:6]}
        )
        model = self.env["hospital.patient.diagnosis"].sudo()

        first = model.create(
            {
                "patient_id": patient.id,
                "disease_id": self.disease.id,
                "diagnosis_type": "primary",
            }
        )
        second = model.create(
            {
                "patient_id": patient.id,
                "disease_id": self.other_disease.id,
                "diagnosis_type": "primary",
            }
        )

        self.assertTrue(first.exists() and second.exists())
        self.assertFalse(first.consultation_id)
        self.assertFalse(second.certainty)


@tagged("post_install", "-at_install", "doctor_diagnosis")
class TestDiagnosisEditAndRemove(DiagnosisCase):
    """Edit and remove while open; frozen once completed."""

    def _one(self, appointment):
        _r, payload = self._add(appointment, self.disease, "primary")
        return self._rows(payload)[0]["id"]

    def test_editing_updates_only_the_clinical_fields(self):
        appointment, _encounter = self._in_consultation_visit()
        consultation = self._consultation_for(appointment)
        diagnosis_id = self._one(appointment)

        response, payload = self._post_body(
            UPDATE % (appointment.id, diagnosis_id),
            {
                "certainty": "final",
                "severity": "moderate",
                "status": "chronic",
                "notes": "Confirmed on review.",
            },
        )

        self.assertEqual(response.status_code, 200)
        row = self._rows(payload)[0]
        self.assertEqual(row["certainty"], "final")
        self.assertEqual(row["severity"], "moderate")
        self.assertEqual(row["status"], "chronic")
        self.assertEqual(row["notes"], "Confirmed on review.")
        # Ownership is untouched by an edit.
        stored = self._diagnoses_of(consultation)
        self.assertEqual(stored.consultation_id, consultation)
        self.assertEqual(stored.patient_id, appointment.patient_id)

    def test_removing_archives_rather_than_deletes(self):
        """The longitudinal record and its audit trail survive the removal."""
        appointment, _encounter = self._in_consultation_visit()
        consultation = self._consultation_for(appointment)
        diagnosis_id = self._one(appointment)

        response, payload = self._post_body(REMOVE % (appointment.id, diagnosis_id), {})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(self._rows(payload), [])
        # Gone from the consultation list...
        self.assertFalse(self._diagnoses_of(consultation))
        # ...but still on the record.
        archived = self._diagnoses_of(consultation, active_test=False)
        self.assertEqual(len(archived), 1)
        self.assertFalse(archived.active)

    def test_a_diagnosis_from_another_visit_is_not_reachable(self):
        """The consultation filter, not just the record rule.

        Both visits belong to the SAME doctor, so the record rule admits both
        rows. Only the consultation check stops one visit's URL editing the
        other visit's diagnosis.
        """
        first, _e1 = self._in_consultation_visit()
        second, _e2 = self._in_consultation_visit()
        foreign_id = self._one(second)

        response, payload = self._post_body(
            UPDATE % (first.id, foreign_id), {"certainty": "final"}
        )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(payload["error"]["code"], "diagnosis_not_found")

    # ------------------------------------------------------------------
    # The freeze
    # ------------------------------------------------------------------
    def _complete(self, appointment):
        consultation = self._consultation_for(appointment)
        consultation.write({"state": "completed"})
        return consultation

    def test_a_completed_consultation_refuses_new_diagnoses(self):
        appointment, _encounter = self._in_consultation_visit()
        self._complete(appointment)

        response, payload = self._add(appointment)

        self.assertEqual(response.status_code, 409)
        self.assertEqual(payload["error"]["code"], "consultation_completed")

    def test_a_completed_consultation_refuses_edits(self):
        appointment, _encounter = self._in_consultation_visit()
        diagnosis_id = self._one(appointment)
        self._complete(appointment)

        response, payload = self._post_body(
            UPDATE % (appointment.id, diagnosis_id), {"certainty": "final"}
        )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(payload["error"]["code"], "consultation_completed")

    def test_a_completed_consultation_refuses_removal(self):
        appointment, _encounter = self._in_consultation_visit()
        consultation = self._consultation_for(appointment)
        diagnosis_id = self._one(appointment)
        self._complete(appointment)

        response, _payload = self._post_body(
            REMOVE % (appointment.id, diagnosis_id), {}
        )

        self.assertEqual(response.status_code, 409)
        self.assertTrue(self._diagnoses_of(consultation))

    def test_the_freeze_holds_at_the_model_for_every_channel(self):
        appointment, _encounter = self._in_consultation_visit()
        self._one(appointment)
        consultation = self._complete(appointment)
        diagnosis = self._diagnoses_of(consultation)

        with self.assertRaisesRegex(UserError, "locked"):
            diagnosis.write({"certainty": "final"})
        with self.assertRaisesRegex(UserError, "locked"):
            diagnosis.write({"active": False})
        with self.assertRaises(UserError):
            diagnosis.unlink()

    def test_a_completed_consultation_is_still_readable(self):
        appointment, _encounter = self._in_consultation_visit()
        self._one(appointment)
        self._complete(appointment)

        response, payload = self._get(DIAGNOSES % appointment.id)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(self._rows(payload)), 1)
        self.assertFalse(payload["data"]["editable"])
        self.assertFalse(self._rows(payload)[0]["editable"])

    def test_diagnoses_survive_the_visit_moving_to_done(self):
        """THE regression this read contract exists for.

        The list used to key on appointment.state == 'in_consultation', so the
        moment the visit finished, every diagnosis recorded during it vanished
        from the desk -- which is exactly when a clinician re-reads them. The
        answer now depends on the CONSULTATION, not on the appointment state.
        """
        appointment, _encounter = self._in_consultation_visit()
        self._add(appointment, self.disease, "primary")
        self._add(appointment, self.other_disease, "secondary")
        self._complete(appointment)
        appointment.sudo().action_done()
        appointment.invalidate_recordset()
        self.assertEqual(appointment.state, "done")

        response, payload = self._get(DIAGNOSES % appointment.id)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(self._rows(payload)), 2)
        self.assertTrue(payload["data"]["has_primary"])
        # Read-only, and every row says so.
        self.assertFalse(payload["data"]["editable"])
        for row in self._rows(payload):
            self.assertFalse(row["editable"])

    def test_a_finished_visit_still_refuses_every_mutation(self):
        """Readability must not have loosened a single write gate."""
        appointment, _encounter = self._in_consultation_visit()
        consultation = self._consultation_for(appointment)
        diagnosis_id = self._one(appointment)
        self._complete(appointment)
        appointment.sudo().action_done()
        appointment.invalidate_recordset()

        add, _a = self._add(appointment, self.other_disease, "secondary")
        update, _u = self._post_body(
            UPDATE % (appointment.id, diagnosis_id), {"certainty": "final"}
        )
        remove, _r = self._post_body(REMOVE % (appointment.id, diagnosis_id), {})

        for response in (add, update, remove):
            self.assertEqual(response.status_code, 409)
        self.assertEqual(len(self._diagnoses_of(consultation)), 1)

    def test_a_pre_consultation_visit_lists_nothing_and_creates_nothing(self):
        appointment, encounter = self._ready_visit()

        response, payload = self._get(DIAGNOSES % appointment.id)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(self._rows(payload), [])
        self.assertFalse(payload["data"]["editable"])
        self.assertFalse(payload["data"]["has_primary"])
        # A pure read opens no consultation.
        self.assertFalse(
            self.env["hospital.consultation"]
            .sudo()
            .search([("encounter_id", "=", encounter.id)])
        )


@tagged("post_install", "-at_install", "doctor_diagnosis")
class TestDiagnosisIdempotency(DiagnosisCase):
    """A retried submission must not file the same diagnosis twice."""

    def test_the_same_request_token_returns_the_first_diagnosis(self):
        appointment, _encounter = self._in_consultation_visit()
        consultation = self._consultation_for(appointment)
        token = uuid.uuid4().hex

        first, first_body = self._add(appointment, request_token=token)
        second, second_body = self._add(appointment, request_token=token)

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(len(self._rows(second_body)), 1)
        self.assertEqual(
            self._rows(first_body)[0]["id"], self._rows(second_body)[0]["id"]
        )
        self.assertEqual(len(self._diagnoses_of(consultation)), 1)

    def test_a_repeated_primary_submission_is_not_a_conflict(self):
        """THE case the token exists for.

        Without it, a double-clicked Add of a PRIMARY diagnosis would create the
        first row and then answer the retry with 409 primary-already-exists --
        blaming the doctor for the browser's second request.
        """
        appointment, _encounter = self._in_consultation_visit()
        token = uuid.uuid4().hex

        self._add(appointment, self.disease, "primary", request_token=token)
        response, _payload = self._add(
            appointment, self.disease, "primary", request_token=token
        )

        self.assertEqual(response.status_code, 200)

    def test_different_tokens_record_different_diagnoses(self):
        """The token identifies the SUBMISSION, not the content: the same
        disease may legitimately be recorded twice for different reasons."""
        appointment, _encounter = self._in_consultation_visit()
        consultation = self._consultation_for(appointment)

        self._add(appointment, self.disease, "secondary", request_token=uuid.uuid4().hex)
        self._add(appointment, self.disease, "differential", request_token=uuid.uuid4().hex)

        self.assertEqual(len(self._diagnoses_of(consultation)), 2)

    def test_the_database_refuses_a_duplicate_token_in_one_consultation(self):
        """The race backstop, now scoped to (consultation, token).

        Written with raw SQL because it must bypass add_to_consultation's
        friendly lookup: the point is to prove the DATABASE refuses it.
        """
        appointment, _encounter = self._in_consultation_visit()
        consultation = self._consultation_for(appointment)
        token = uuid.uuid4().hex
        self._add(appointment, request_token=token)

        with self.assertRaises(IntegrityError), mute_logger("odoo.sql_db"):
            with self.cr.savepoint():
                self.env.cr.execute(
                    """
                    INSERT INTO hospital_patient_diagnosis
                        (patient_id, disease_id, consultation_id, encounter_id,
                         appointment_id, diagnosis_type, request_token, active,
                         create_uid, write_uid, create_date, write_date)
                    VALUES (%s, %s, %s, %s, %s, 'secondary', %s, true,
                            1, 1, now(), now())
                    """,
                    (
                        consultation.patient_id.id,
                        self.other_disease.id,
                        consultation.id,
                        consultation.encounter_id.id,
                        consultation.appointment_id.id,
                        token,
                    ),
                )

    def test_the_same_token_in_two_consultations_never_crosses_over(self):
        """A token is opaque and client-minted; the server cannot assume it is
        globally unique.

        With a GLOBAL token index the second consultation's submission would
        either be refused by the database for a row it has nothing to do with,
        or -- worse -- the lookup would hand back the FIRST patient's diagnosis
        as though this request had created it.
        """
        first, _e1 = self._in_consultation_visit()
        second, _e2 = self._in_consultation_visit()
        token = uuid.uuid4().hex

        one, first_body = self._add(first, self.disease, "primary", request_token=token)
        two, second_body = self._add(
            second, self.other_disease, "primary", request_token=token
        )

        self.assertEqual(one.status_code, 200)
        self.assertEqual(two.status_code, 200, "the shared token was refused")

        first_rows = self._rows(first_body)
        second_rows = self._rows(second_body)
        self.assertEqual(len(first_rows), 1)
        self.assertEqual(len(second_rows), 1)
        # Two DISTINCT rows, each holding its own consultation's disease.
        self.assertNotEqual(first_rows[0]["id"], second_rows[0]["id"])
        self.assertEqual(first_rows[0]["disease"]["id"], self.disease.id)
        self.assertEqual(second_rows[0]["disease"]["id"], self.other_disease.id)

        # And neither consultation gained the other's row.
        self.assertEqual(len(self._diagnoses_of(self._consultation_for(first))), 1)
        self.assertEqual(len(self._diagnoses_of(self._consultation_for(second))), 1)

    def test_a_replay_resolves_within_its_own_consultation_only(self):
        first, _e1 = self._in_consultation_visit()
        second, _e2 = self._in_consultation_visit()
        token = uuid.uuid4().hex

        _r1, original = self._add(first, self.disease, "primary", request_token=token)
        self._add(second, self.other_disease, "primary", request_token=token)
        # Replaying against the FIRST consultation returns the FIRST row.
        _r3, replay = self._add(first, self.disease, "primary", request_token=token)

        self.assertEqual(self._rows(replay)[0]["id"], self._rows(original)[0]["id"])
        self.assertEqual(len(self._diagnoses_of(self._consultation_for(first))), 1)


@tagged("post_install", "-at_install", "doctor_diagnosis")
class TestDiagnosisIntegrity(DiagnosisCase):
    """Cross-patient and cross-encounter rows are unrepresentable."""

    def test_a_cross_patient_diagnosis_is_refused(self):
        appointment, _encounter = self._in_consultation_visit()
        consultation = self._consultation_for(appointment)
        stranger = self.env["hospital.patient"].sudo().create(
            {"name": "Stranger %s" % uuid.uuid4().hex[:6]}
        )

        with self.assertRaisesRegex(ValidationError, "different patient"):
            self.env["hospital.patient.diagnosis"].sudo().create(
                {
                    "patient_id": stranger.id,
                    "disease_id": self.disease.id,
                    "consultation_id": consultation.id,
                    "encounter_id": consultation.encounter_id.id,
                    "diagnosis_type": "secondary",
                }
            )

    def test_a_consultation_diagnosis_must_carry_an_encounter(self):
        appointment, _encounter = self._in_consultation_visit()
        consultation = self._consultation_for(appointment)

        with self.assertRaisesRegex(ValidationError, "no encounter"):
            self.env["hospital.patient.diagnosis"].sudo().create(
                {
                    "patient_id": consultation.patient_id.id,
                    "disease_id": self.disease.id,
                    "consultation_id": consultation.id,
                    "diagnosis_type": "secondary",
                }
            )

    def test_a_mismatched_encounter_is_refused(self):
        first, _e1 = self._in_consultation_visit()
        second, other_encounter = self._in_consultation_visit()
        consultation = self._consultation_for(first)

        with self.assertRaisesRegex(ValidationError, "encounter"):
            self.env["hospital.patient.diagnosis"].sudo().create(
                {
                    "patient_id": consultation.patient_id.id,
                    "disease_id": self.disease.id,
                    "consultation_id": consultation.id,
                    "encounter_id": other_encounter.id,
                    "diagnosis_type": "secondary",
                }
            )

    def test_a_consultation_diagnosis_must_carry_the_consultation_visit(self):
        """THE missing-value case the earlier constraint let through.

        It required BOTH sides to be set before comparing, so a diagnosis with
        no appointment passed silently even when its consultation documented
        one. appointment_id is what the doctor record rule traverses, so an
        unlinked row is invisible to the rule and absent from the visit it was
        actually made in.
        """
        appointment, _encounter = self._in_consultation_visit()
        consultation = self._consultation_for(appointment)
        self.assertTrue(consultation.appointment_id)

        with self.assertRaisesRegex(ValidationError, "exactly the consultation"):
            self.env["hospital.patient.diagnosis"].sudo().create(
                {
                    "patient_id": consultation.patient_id.id,
                    "disease_id": self.disease.id,
                    "consultation_id": consultation.id,
                    "encounter_id": consultation.encounter_id.id,
                    # appointment_id deliberately omitted.
                    "diagnosis_type": "secondary",
                }
            )

    def test_a_consultation_diagnosis_cannot_name_another_visit(self):
        first, _e1 = self._in_consultation_visit()
        second, _e2 = self._in_consultation_visit()
        consultation = self._consultation_for(first)

        with self.assertRaisesRegex(ValidationError, "exactly the consultation"):
            self.env["hospital.patient.diagnosis"].sudo().create(
                {
                    "patient_id": consultation.patient_id.id,
                    "disease_id": self.disease.id,
                    "consultation_id": consultation.id,
                    "encounter_id": consultation.encounter_id.id,
                    "appointment_id": second.id,
                    "diagnosis_type": "secondary",
                }
            )

    def test_the_normal_add_path_satisfies_the_appointment_constraint(self):
        """Guards against the constraint being tightened past what the service
        method actually writes."""
        appointment, _encounter = self._in_consultation_visit()
        response, _payload = self._add(appointment)
        self.assertEqual(response.status_code, 200)
        stored = self._diagnoses_of(self._consultation_for(appointment))
        self.assertEqual(stored.appointment_id, appointment)

    def test_a_legacy_diagnosis_needs_no_consultation(self):
        """Every historical row is this shape and must stay valid forever."""
        patient = self.env["hospital.patient"].sudo().create(
            {"name": "Historic %s" % uuid.uuid4().hex[:6]}
        )

        diagnosis = self.env["hospital.patient.diagnosis"].sudo().create(
            {
                "patient_id": patient.id,
                "disease_id": self.disease.id,
                "diagnosis_type": "history",
                "status": "chronic",
            }
        )

        self.assertTrue(diagnosis.exists())
        self.assertFalse(diagnosis.consultation_id)
        self.assertFalse(diagnosis.encounter_id)
        self.assertFalse(diagnosis.certainty)
        # And it is still freely editable: no consultation means no freeze.
        diagnosis.write({"status": "resolved"})
        self.assertEqual(diagnosis.status, "resolved")


@tagged("post_install", "-at_install", "doctor_diagnosis")
class TestDiagnosisAccess(DiagnosisCase):
    """Scope, which this model shipped without."""

    def test_a_pure_doctor_cannot_reach_another_doctors_diagnoses(self):
        appointment, _encounter = self._in_consultation_visit(doctor=self.other_doctor)

        response, payload = self._get(
            DIAGNOSES % appointment.id,
            user=self.doctor_user,
            password=self.doctor_password,
        )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(payload["error"]["code"], "visit_not_found")

    def test_the_record_rule_hides_another_doctors_diagnosis_at_the_orm(self):
        """Independent of the API. Before this slice there was NO rule at all
        on this model, so this returned the row."""
        appointment, _encounter = self._in_consultation_visit(doctor=self.other_doctor)
        self._auth(self.other_user, self.other_password)
        self._post_body(
            DIAGNOSES % appointment.id,
            {"disease_id": self.disease.id, "diagnosis_type": "primary"},
            user=self.other_user,
            password=self.other_password,
        )
        consultation = self._consultation_for(appointment)
        diagnosis = self._diagnoses_of(consultation)
        self.assertTrue(diagnosis)

        visible = (
            self.env["hospital.patient.diagnosis"]
            .with_user(self.doctor_user)
            .search([("id", "=", diagnosis.id)])
        )

        self.assertFalse(visible)

    def test_the_authoring_doctor_reaches_their_own_diagnosis(self):
        appointment, _encounter = self._in_consultation_visit(doctor=self.doctor)
        self._add(appointment)
        diagnosis = self._diagnoses_of(self._consultation_for(appointment))

        visible = (
            self.env["hospital.patient.diagnosis"]
            .with_user(self.doctor_user)
            .search([("id", "=", diagnosis.id)])
        )

        self.assertEqual(visible, diagnosis)

    def test_a_nurse_can_neither_read_nor_create_a_diagnosis(self):
        """hospital_management shipped Hospital Nurse with perm_create=1 and no
        record rule. Both are corrected here."""
        appointment, _encounter = self._in_consultation_visit()
        self._add(appointment)
        diagnosis = self._diagnoses_of(self._consultation_for(appointment))

        with self.assertRaises(AccessError):
            diagnosis.with_user(self.nurse).read(["notes"])
        with self.assertRaises(AccessError):
            self.env["hospital.patient.diagnosis"].with_user(self.nurse).create(
                {
                    "patient_id": appointment.patient_id.id,
                    "disease_id": self.disease.id,
                    "diagnosis_type": "primary",
                }
            )

    def test_a_front_desk_nurse_gains_nothing_from_a_permitted_department(self):
        """The Slice 1 fragility, not repeated. The clamp is [(0,'=',1)], which
        no configuration change can undo."""
        appointment, _encounter = self._in_consultation_visit()
        self._add(appointment)
        diagnosis = self._diagnoses_of(self._consultation_for(appointment))
        self.front_desk.sudo().write(
            {"yoya_permitted_department_ids": [(6, 0, [self.department.id])]}
        )

        with self.assertRaises(AccessError):
            diagnosis.with_user(self.front_desk).read(["notes"])

    def test_non_clinical_roles_are_denied_the_endpoints(self):
        appointment, _encounter = self._in_consultation_visit()

        for user, password in (
            (self.nurse, self.nurse_password),
            (self.front_desk, self.fd_password),
            (self.cashier, self.cashier_password),
            (self.receptionist, self.receptionist_password),
            (self.accountant, self.accountant_password),
        ):
            response, payload = self._get(
                DIAGNOSES % appointment.id, user=user, password=password
            )
            self.assertEqual(response.status_code, 403, user.login)
            self.assertEqual(payload["error"]["code"], "access_denied", user.login)

            response, _payload = self._post_body(
                DIAGNOSES % appointment.id,
                {"disease_id": self.disease.id, "diagnosis_type": "primary"},
                user=user,
                password=password,
            )
            self.assertEqual(response.status_code, 403, user.login)

    def test_non_clinical_roles_hold_no_orm_access(self):
        appointment, _encounter = self._in_consultation_visit()
        self._add(appointment)
        diagnosis = self._diagnoses_of(self._consultation_for(appointment))

        for user in (self.cashier, self.receptionist, self.accountant, self.nurse):
            with self.assertRaises(AccessError, msg=user.login):
                diagnosis.with_user(user).read(["notes"])


@tagged("post_install", "-at_install", "doctor_diagnosis")
class TestDiagnosisTransactionBoundary(DiagnosisCase):
    """A recorded diagnosis must never survive a failed response."""

    def test_response_failure_after_add_rolls_the_write_back(self):
        appointment, _encounter = self._in_consultation_visit()
        consultation = self._consultation_for(appointment)

        with patch(
            DIAGNOSIS_SERIALIZER_TARGET,
            side_effect=AccessError("simulated post-write failure"),
        ):
            response, payload = self._add(appointment)

        self.assertEqual(response.status_code, 500)
        self.assertEqual(payload["error"]["code"], "diagnosis_response_failed")
        self.assertNotEqual(payload["error"]["code"], "access_denied")
        serialized = json.dumps(payload)
        self.assertNotIn("Traceback", serialized)
        self.assertNotIn("simulated post-write failure", serialized)

        self.assertFalse(
            self._diagnoses_of(consultation, active_test=False),
            "the diagnosis survived a rolled-back add",
        )

    def test_the_retry_after_a_rollback_succeeds(self):
        appointment, _encounter = self._in_consultation_visit()
        consultation = self._consultation_for(appointment)

        with patch(
            DIAGNOSIS_SERIALIZER_TARGET,
            side_effect=AccessError("simulated post-write failure"),
        ):
            failed, _ = self._add(appointment)
        self.assertEqual(failed.status_code, 500)

        response, payload = self._add(appointment)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(self._rows(payload)), 1)
        self.assertEqual(len(self._diagnoses_of(consultation)), 1)

    def test_a_rolled_back_add_does_not_consume_the_request_token(self):
        """Otherwise the retry would return "already recorded" for a diagnosis
        that was rolled back and does not exist."""
        appointment, _encounter = self._in_consultation_visit()
        token = uuid.uuid4().hex

        with patch(
            DIAGNOSIS_SERIALIZER_TARGET,
            side_effect=AccessError("simulated post-write failure"),
        ):
            self._add(appointment, request_token=token)

        response, payload = self._add(appointment, request_token=token)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(self._rows(payload)), 1)

    def test_response_failure_after_remove_rolls_the_removal_back(self):
        appointment, _encounter = self._in_consultation_visit()
        consultation = self._consultation_for(appointment)
        _r, payload = self._add(appointment)
        diagnosis_id = self._rows(payload)[0]["id"]

        with patch(
            DIAGNOSIS_SERIALIZER_TARGET,
            side_effect=AccessError("simulated post-write failure"),
        ):
            response, _body = self._post_body(
                REMOVE % (appointment.id, diagnosis_id), {}
            )

        self.assertEqual(response.status_code, 500)
        # Still active: the archive was rolled back.
        self.assertEqual(len(self._diagnoses_of(consultation)), 1)


@tagged("post_install", "-at_install", "doctor_diagnosis")
class TestDiagnosisConfidentiality(DiagnosisCase):
    """No money, ever, however the diagnosis may later be billed."""

    def test_the_diagnosis_payload_carries_no_commercial_field(self):
        appointment, _encounter = self._in_consultation_visit()
        self._add(appointment, notes="Recorded during consultation.")

        _response, payload = self._get(DIAGNOSES % appointment.id)

        serialized = json.dumps(payload).lower()
        for forbidden in FORBIDDEN_KEYS:
            self.assertNotIn(
                forbidden, serialized,
                "'%s' leaked into the diagnosis payload" % forbidden,
            )

    def test_the_payload_key_set_is_exactly_the_agreed_contract(self):
        appointment, _encounter = self._in_consultation_visit()
        self._add(appointment)

        _response, payload = self._get(DIAGNOSES % appointment.id)

        self.assertEqual(
            set(payload["data"]), {"diagnoses", "editable", "has_primary"}
        )
        self.assertEqual(set(payload["data"]["diagnoses"][0]), EXPECTED_ROW_KEYS)
        self.assertEqual(
            set(payload["data"]["diagnoses"][0]["disease"]),
            {"id", "name", "code", "category"},
        )

    def test_the_model_refuses_a_primary_clash_independently_of_the_api(self):
        appointment, _encounter = self._in_consultation_visit()
        consultation = self._consultation_for(appointment)
        model = self.env["hospital.patient.diagnosis"].sudo()
        model.add_to_consultation(
            consultation, self.disease, {"diagnosis_type": "primary"}
        )

        with self.assertRaises(DiagnosisPrimaryConflict):
            model.add_to_consultation(
                consultation, self.other_disease, {"diagnosis_type": "primary"}
            )
