"""Laboratory ordering from the Doctor Desk.

WHAT THESE TESTS ARE FOR
------------------------
Four properties carry this slice.

  1. THE DOCTOR DESK CREATES NO CHARGE. It creates a clinical request and calls
     the EXISTING action_confirm_request(); hospital_billing's override is what
     validates the billing configuration and raises one charge per ordered
     test. The tests below assert the charges appear -- and that they appear
     through that path, not from anything this API does.

  2. ONE DOCTOR ACTION IS ONE TRANSACTION. Request, lines, confirmation and the
     charges it raises commit together or not at all. A half-placed order that
     survived a failed response would be billed for and invisible.

  3. OWNERSHIP IS DERIVED. Patient, physician, encounter, appointment and
     consultation all come from the consultation the caller already resolved.

  4. THE PAYLOAD IS CLINICAL. A lab order is the most billing-adjacent thing a
     doctor does, which is exactly why no amount, payer or receipt may appear
     in what the desk receives.

The clinical status vocabulary is DERIVED from real backend state, so a change
to hospital.laboratory.request's workflow surfaces here rather than silently
producing an invented label.
"""
import json
import uuid
from unittest.mock import patch

from psycopg2 import IntegrityError

from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import tagged
from odoo.tools import mute_logger

from .test_doctor_diagnosis_api import DiagnosisCase

CATALOGUE = "/yoya-emr/api/v1/doctor/catalogue/laboratory-tests"
ORDERS = "/yoya-emr/api/v1/doctor/visits/%s/orders/laboratory"
CANCEL = "/yoya-emr/api/v1/doctor/visits/%s/orders/laboratory/%s/cancel"

LAB_SERIALIZER_TARGET = (
    "odoo.addons.yoya_emr_api.controllers.doctor.serialize_laboratory_orders"
)

EXPECTED_ORDER_KEYS = {
    "id", "request_code", "tests", "diagnosis", "clinical_indication",
    "priority", "status", "status_label", "ordered_at", "created_at",
    "editable", "cancellable",
}

FORBIDDEN_KEYS = (
    "amount", "balance", "outstanding", "paid", "receipt", "sponsor",
    "agreement", "membership", "payer", "tariff", "price", "invoice",
    "charge", "credit_limit", "coverage", "billing_service", "default_price",
)


class LaboratoryCase(DiagnosisCase):
    """DiagnosisCase plus a small, correctly BILLABLE laboratory catalogue."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        tag = uuid.uuid4().hex[:6]
        cls.uom = cls.env["uom.uom"].sudo().search([], limit=1)
        cls.cbc = cls._make_test("CBC %s" % tag, "CBC%s" % tag.upper(), "blood")
        cls.creatinine = cls._make_test(
            "Creatinine %s" % tag, "CRE%s" % tag.upper(), "blood"
        )
        cls.crp = cls._make_test("CRP %s" % tag, "CRP%s" % tag.upper(), "blood")
        # A test with NO billing service. Ordering it must fail the whole
        # submission, because _ensure_laboratory_billing validates the entire
        # set before raising any charge -- so the catalogue must not offer it.
        cls.unmapped = cls.env["hospital.laboratory.test"].sudo().create(
            {"name": "Unmapped %s" % tag, "code": "UNM%s" % tag.upper()}
        )
        # Archived: still a row, never orderable.
        cls.archived = cls._make_test(
            "Archived %s" % tag, "ARC%s" % tag.upper(), "blood"
        )
        cls.archived.sudo().write({"active": False})
        # Mapped, but to a service whose effective window has already closed.
        # _assert_billable refuses it, so the picker must too.
        cls.expired = cls._make_test(
            "Expired %s" % tag, "EXP%s" % tag.upper(), "blood"
        )
        cls.expired.sudo().billing_service_id.write(
            {"effective_date_end": "2000-01-01"}
        )
        # Mapped, but to an ARCHIVED billing service.
        cls.dead_service = cls._make_test(
            "DeadSvc %s" % tag, "DED%s" % tag.upper(), "blood"
        )
        cls.dead_service.sudo().billing_service_id.write({"active": False})

    @classmethod
    def _make_test(cls, name, code, sample_type):
        service = cls.env["hospital.billing.service"].sudo().create(
            {
                "name": "%s Service" % name,
                "code": "T-LAB-%s" % code,
                "service_type": "laboratory",
                "default_price": 120.0,
                "company_id": cls.env.company.id,
                "currency_id": cls.env.company.currency_id.id,
                "uom_id": cls.uom.id,
                "prepayment_required": False,
                "tax_treatment": "exempt",
            }
        )
        return cls.env["hospital.laboratory.test"].sudo().create(
            {
                "name": name,
                "code": code,
                "category": "hematology",
                "sample_type": sample_type,
                "billing_service_id": service.id,
            }
        )

    # ------------------------------------------------------------------
    def _order(self, appointment, tests=None, **extra):
        body = {"tests": [t.id for t in (tests or [self.cbc])]}
        body.update(extra)
        return self._post_body(ORDERS % appointment.id, body)

    def _orders(self, payload):
        return payload["data"]["orders"]

    def _requests_of(self, consultation):
        return (
            self.env["hospital.laboratory.request"]
            .sudo()
            .with_context(active_test=False)
            .search([("consultation_id", "=", consultation.id)])
        )


@tagged("post_install", "-at_install", "doctor_laboratory")
class TestLaboratoryCatalogue(LaboratoryCase):
    """The test picker: bounded, searchable, and priced nowhere."""

    def test_search_matches_name_and_code(self):
        response, payload = self._get(CATALOGUE, q="CBC")
        self.assertEqual(response.status_code, 200)
        self.assertIn(
            self.cbc.name, [row["name"] for row in payload["data"]["tests"]]
        )

        _r, by_code = self._get(CATALOGUE, q=self.creatinine.code)
        self.assertIn(
            self.creatinine.name,
            [row["name"] for row in by_code["data"]["tests"]],
        )

    def test_the_limit_is_clamped_server_side(self):
        _r, payload = self._get(CATALOGUE, limit=9999)
        self.assertLessEqual(payload["data"]["limit"], 50)
        self.assertLessEqual(len(payload["data"]["tests"]), 50)

    def test_an_empty_query_is_still_bounded(self):
        _r, payload = self._get(CATALOGUE)
        self.assertLessEqual(
            len(payload["data"]["tests"]), payload["data"]["limit"]
        )

    def test_the_catalogue_never_exposes_the_billing_mapping(self):
        """hospital.laboratory.test carries billing_service_id once
        hospital_billing is installed. It is the mapping that decides what a
        test costs, and it must not reach a clinician's screen."""
        self.assertTrue(self.cbc.billing_service_id)

        _r, payload = self._get(CATALOGUE, q="CBC")

        serialized = json.dumps(payload).lower()
        for forbidden in FORBIDDEN_KEYS:
            self.assertNotIn(forbidden, serialized, forbidden)
        self.assertEqual(
            set(payload["data"]["tests"][0]),
            {"id", "name", "code", "category", "sample_type"},
        )

    # ------------------------------------------------------------------
    # Eligibility: the picker must not offer what submission refuses
    # ------------------------------------------------------------------
    def _catalogue_ids(self, **params):
        _r, payload = self._get(CATALOGUE, **params)
        return {row["id"] for row in payload["data"]["tests"]}

    def test_a_mapped_active_test_is_offered(self):
        self.assertIn(self.cbc.id, self._catalogue_ids(q=self.cbc.code))

    def test_an_unmapped_test_is_never_offered(self):
        """THE regression this hardening exists for.

        Offering it would offer an action the very next step refuses, with
        nothing the doctor could fix.
        """
        self.assertFalse(self.unmapped.billing_service_id)
        self.assertNotIn(self.unmapped.id, self._catalogue_ids(limit=50))

    def test_searching_for_an_unmapped_test_by_name_returns_nothing(self):
        """Not merely absent from the default page: unreachable by search."""
        _r, payload = self._get(CATALOGUE, q=self.unmapped.name)
        self.assertEqual(payload["data"]["tests"], [])

    def test_searching_for_an_unmapped_test_by_code_returns_nothing(self):
        _r, payload = self._get(CATALOGUE, q=self.unmapped.code)
        self.assertEqual(payload["data"]["tests"], [])

    def test_an_archived_test_is_never_offered(self):
        self.assertFalse(self.archived.active)
        self.assertNotIn(self.archived.id, self._catalogue_ids(limit=50))
        _r, payload = self._get(CATALOGUE, q=self.archived.code)
        self.assertEqual(payload["data"]["tests"], [])

    def test_a_test_whose_service_is_archived_is_never_offered(self):
        """Mapped is not enough: _assert_billable refuses an archived service,
        so the picker mirrors that rather than only checking for a mapping."""
        self.assertTrue(self.dead_service.billing_service_id)
        self.assertNotIn(self.dead_service.id, self._catalogue_ids(limit=50))
        _r, payload = self._get(CATALOGUE, q=self.dead_service.code)
        self.assertEqual(payload["data"]["tests"], [])

    def test_a_test_whose_service_has_expired_is_never_offered(self):
        self.assertNotIn(self.expired.id, self._catalogue_ids(limit=50))
        _r, payload = self._get(CATALOGUE, q=self.expired.code)
        self.assertEqual(payload["data"]["tests"], [])

    def test_the_catalogue_filter_matches_the_confirmation_gate(self):
        """The picker and the gate must agree, item for item.

        Anything the catalogue offers must survive _assert_billable, and
        anything it hides must not. This is the property the domain exists to
        hold; asserting it directly means a future divergence fails here rather
        than in a clinician's hands.
        """
        model = self.env["hospital.laboratory.test"].sudo()
        offered = model.search(model.doctor_orderable_domain())

        for test in offered:
            # Must not raise.
            test._assert_billable(self.env.company)

        for hidden in (self.unmapped, self.expired, self.dead_service):
            # ValidationError subclasses UserError, so one type covers both.
            # Odoo's assertRaises override does not accept a tuple.
            with self.assertRaises(UserError, msg=hidden.name):
                hidden._assert_billable(self.env.company)

    def test_non_doctor_roles_are_denied_the_catalogue(self):
        for user, password in (
            (self.nurse, self.nurse_password),
            (self.cashier, self.cashier_password),
            (self.receptionist, self.receptionist_password),
        ):
            response, payload = self._get(CATALOGUE, user=user, password=password)
            self.assertEqual(response.status_code, 403, user.login)
            self.assertEqual(payload["error"]["code"], "access_denied", user.login)


@tagged("post_install", "-at_install", "doctor_laboratory")
class TestLaboratoryOrdering(LaboratoryCase):
    """Placing an order, and what the model does with it."""

    def test_an_empty_consultation_lists_no_orders(self):
        appointment, _e = self._in_consultation_visit()
        response, payload = self._get(ORDERS % appointment.id)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(self._orders(payload), [])
        self.assertTrue(payload["data"]["can_order"])

    def test_placing_an_order_derives_every_ownership_field(self):
        appointment, encounter = self._in_consultation_visit()
        consultation = self._consultation_for(appointment)

        response, payload = self._order(appointment)

        self.assertEqual(response.status_code, 200)
        orders = self._orders(payload)
        self.assertEqual(len(orders), 1)
        self.assertEqual(set(orders[0]), EXPECTED_ORDER_KEYS)

        stored = self._requests_of(consultation)
        self.assertEqual(len(stored), 1)
        self.assertEqual(stored.patient_id, appointment.patient_id)
        self.assertEqual(stored.physician_id, consultation.doctor_id)
        self.assertEqual(stored.encounter_id, encounter)
        self.assertEqual(stored.appointment_id, appointment)
        self.assertEqual(stored.consultation_id, consultation)

    def test_the_api_rejects_client_supplied_ownership_and_billing(self):
        appointment, _e = self._in_consultation_visit()

        for field in (
            "patient_id", "physician_id", "encounter_id", "appointment_id",
            "consultation_id", "state", "active", "request_date",
            "billing_blocked",
        ):
            response, payload = self._order(appointment, **{field: 1})
            self.assertEqual(response.status_code, 400, field)
            self.assertEqual(payload["error"]["code"], "protected_field", field)

    def test_one_request_can_carry_several_tests(self):
        """The base model supports a multi-test request, so one doctor action
        produces ONE request rather than three."""
        appointment, _e = self._in_consultation_visit()
        consultation = self._consultation_for(appointment)

        _r, payload = self._order(
            appointment, [self.cbc, self.creatinine, self.crp]
        )

        orders = self._orders(payload)
        self.assertEqual(len(orders), 1)
        self.assertEqual(len(orders[0]["tests"]), 3)
        self.assertEqual(len(self._requests_of(consultation)), 1)
        self.assertEqual(len(self._requests_of(consultation).line_ids), 3)

    def test_a_repeated_test_in_one_submission_is_ordered_once(self):
        """Two of the same test in one submission is a client mistake, and
        would otherwise raise two charges for one test."""
        appointment, _e = self._in_consultation_visit()
        consultation = self._consultation_for(appointment)

        _r, payload = self._order(appointment, [self.cbc, self.cbc, self.creatinine])

        self.assertEqual(len(self._orders(payload)[0]["tests"]), 2)
        self.assertEqual(len(self._requests_of(consultation).line_ids), 2)

    def test_the_clinical_indication_and_priority_are_stored(self):
        appointment, _e = self._in_consultation_visit()
        text = "Persistent fever.\nRule out infection."

        _r, payload = self._order(
            appointment, clinical_notes=text, priority="urgent"
        )

        order = self._orders(payload)[0]
        self.assertEqual(order["clinical_indication"], text)
        self.assertEqual(order["priority"], "urgent")

    def test_an_unknown_test_is_a_404(self):
        appointment, _e = self._in_consultation_visit()
        response, payload = self._post_body(
            ORDERS % appointment.id, {"tests": [99999999]}
        )
        self.assertEqual(response.status_code, 404)
        self.assertEqual(payload["error"]["code"], "laboratory_test_not_found")

    def test_an_empty_test_list_is_refused(self):
        appointment, _e = self._in_consultation_visit()
        response, payload = self._post_body(ORDERS % appointment.id, {"tests": []})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(payload["error"]["code"], "invalid_field")

    def test_ordering_before_the_visit_starts_is_refused(self):
        appointment, _e = self._ready_visit()
        response, payload = self._order(appointment)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(payload["error"]["code"], "consultation_not_available")

    def test_a_completed_consultation_cannot_order(self):
        appointment, _e = self._in_consultation_visit()
        consultation = self._consultation_for(appointment)
        consultation.write({"state": "completed"})

        response, payload = self._order(appointment)

        self.assertEqual(response.status_code, 409)
        self.assertEqual(payload["error"]["code"], "consultation_completed")
        self.assertFalse(self._requests_of(consultation))

    def test_orders_stay_readable_after_the_visit_finishes(self):
        appointment, _e = self._in_consultation_visit()
        self._order(appointment)
        self._consultation_for(appointment).write({"state": "completed"})
        appointment.sudo().action_done()
        appointment.invalidate_recordset()

        response, payload = self._get(ORDERS % appointment.id)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(self._orders(payload)), 1)
        self.assertFalse(payload["data"]["can_order"])


@tagged("post_install", "-at_install", "doctor_laboratory")
class TestLaboratoryBillingHandoff(LaboratoryCase):
    """The charges come from the lab model, and the Doctor Desk never makes one."""

    def test_confirmation_uses_the_authoritative_workflow(self):
        """The request must be REQUESTED, not left in draft.

        A draft request raises no charge, never reaches the bench worklist and
        can only be rescued by a manual Odoo intervention -- so `requested` is
        the state that means the doctor has really ordered the test.
        """
        appointment, _e = self._in_consultation_visit()
        consultation = self._consultation_for(appointment)

        self._order(appointment, [self.cbc, self.creatinine])

        stored = self._requests_of(consultation)
        self.assertEqual(stored.state, "requested")

    def test_confirmation_raises_one_charge_per_ordered_test(self):
        """Asserted here, produced by hospital_billing. This test proves the
        handoff happened; it does not prove -- and must not require -- that the
        Doctor API knows anything about charges."""
        appointment, _e = self._in_consultation_visit()
        consultation = self._consultation_for(appointment)

        self._order(appointment, [self.cbc, self.creatinine])

        stored = self._requests_of(consultation)
        self.assertEqual(len(stored.sudo().charge_line_ids), 2)

    def test_the_doctor_controller_never_touches_a_charge_model(self):
        """The Doctor API must never OPEN a billing model.

        A source assertion is crude, but it pins the architectural rule far
        more reliably than a behavioural test: charge creation belongs to
        hospital_billing, and the day someone reaches for env['hospital.charge.
        line'] here to "just check something", this fails.

        It matches the ACCESS IDIOM -- env["hospital.charge..."] -- rather than
        the bare model name, because these modules discuss the billing boundary
        at length in their own docstrings. Asserting on prose would make the
        test fail for explaining itself, which is how a useful guard turns into
        one people delete.
        """
        import inspect
        import re

        from odoo.addons.yoya_emr_api.controllers import doctor as controller
        from odoo.addons.yoya_emr_api.services import laboratory_serializers

        opens_billing = re.compile(
            r"""env\[\s*["']hospital\.(charge|billing)[.\w]*["']\s*\]"""
        )
        for module in (controller, laboratory_serializers):
            hits = opens_billing.findall(inspect.getsource(module))
            self.assertEqual(
                hits, [],
                "%s opens a billing model directly: %s" % (module.__name__, hits),
            )

    def test_the_doctor_api_never_calls_sudo_on_the_laboratory_path(self):
        """Elevation belongs inside hospital_billing's engine, which already
        has it. Nothing on this surface needs it, and a sudo() here would
        quietly bypass the record rules this slice just added."""
        import inspect

        from odoo.addons.yoya_emr_api.services import laboratory_serializers

        self.assertNotIn("sudo(", inspect.getsource(laboratory_serializers))

    def test_an_unmapped_test_places_no_order_at_all(self):
        """ALL-OR-NOTHING. _ensure_laboratory_billing validates the whole set
        before creating any charge, so a single unmapped test must leave no
        request and no charge behind -- not a partially charged order."""
        appointment, _e = self._in_consultation_visit()
        consultation = self._consultation_for(appointment)

        charges_before = self.env["hospital.charge.line"].sudo().search_count(
            [("encounter_id", "=", consultation.encounter_id.id)]
        )

        response, _payload = self._order(appointment, [self.cbc, self.unmapped])

        self.assertGreaterEqual(response.status_code, 400)
        self.assertFalse(
            self._requests_of(consultation),
            "a half-billable order survived",
        )
        # DEFENCE IN DEPTH. The catalogue no longer offers an unmapped test,
        # but a hand-built request, an RPC caller or a test mapping removed
        # between picking and submitting must still be refused here -- and must
        # leave no charge behind.
        charges_after = self.env["hospital.charge.line"].sudo().search_count(
            [("encounter_id", "=", consultation.encounter_id.id)]
        )
        self.assertEqual(charges_after, charges_before)

    def test_the_order_payload_carries_no_financial_field(self):
        appointment, _e = self._in_consultation_visit()
        self._order(appointment, [self.cbc, self.creatinine])

        _r, payload = self._get(ORDERS % appointment.id)

        serialized = json.dumps(payload).lower()
        for forbidden in FORBIDDEN_KEYS:
            self.assertNotIn(
                forbidden, serialized,
                "'%s' leaked into the laboratory payload" % forbidden,
            )

    def test_the_status_vocabulary_comes_from_real_backend_state(self):
        appointment, _e = self._in_consultation_visit()
        consultation = self._consultation_for(appointment)
        self._order(appointment)
        stored = self._requests_of(consultation)

        _r, payload = self._get(ORDERS % appointment.id)
        self.assertIn(
            self._orders(payload)[0]["status"],
            ("awaiting_clearance", "ready_for_collection"),
        )

        # Drive the REAL workflow forward and confirm the label follows it.
        stored.sudo().action_mark_sample_collected()
        _r2, collected = self._get(ORDERS % appointment.id)
        self.assertEqual(self._orders(collected)[0]["status"], "collected")
        self.assertEqual(
            self._orders(collected)[0]["status_label"], "Sample collected"
        )

        stored.sudo().action_mark_in_progress()
        _r3, progress = self._get(ORDERS % appointment.id)
        self.assertEqual(self._orders(progress)[0]["status"], "result_pending")


@tagged("post_install", "-at_install", "doctor_laboratory")
class TestLaboratoryDiagnosisLink(LaboratoryCase):
    """The optional indication must come from THIS consultation."""

    def _diagnosis_in(self, appointment, disease=None):
        _r, payload = self._post_body(
            "/yoya-emr/api/v1/doctor/visits/%s/diagnoses" % appointment.id,
            {
                "disease_id": (disease or self.disease).id,
                "diagnosis_type": "primary",
            },
        )
        return payload["data"]["diagnoses"][0]["id"]

    def test_a_diagnosis_from_this_consultation_is_accepted(self):
        appointment, _e = self._in_consultation_visit()
        diagnosis_id = self._diagnosis_in(appointment)

        _r, payload = self._order(appointment, diagnosis_id=diagnosis_id)

        order = self._orders(payload)[0]
        self.assertIsNotNone(order["diagnosis"])
        self.assertEqual(order["diagnosis"]["id"], diagnosis_id)

    def test_a_diagnosis_from_another_consultation_is_refused(self):
        """The base model only refuses another PATIENT's diagnosis. The same
        patient's diagnosis from an earlier visit would slip through, and would
        attribute today's order to a consultation that never made it."""
        first, _e1 = self._in_consultation_visit()
        second, _e2 = self._in_consultation_visit()
        foreign_id = self._diagnosis_in(second)
        consultation = self._consultation_for(first)

        response, _payload = self._order(first, diagnosis_id=foreign_id)

        self.assertGreaterEqual(response.status_code, 400)
        self.assertFalse(self._requests_of(consultation))

    def test_an_unknown_diagnosis_is_a_404(self):
        appointment, _e = self._in_consultation_visit()
        response, payload = self._order(appointment, diagnosis_id=99999999)
        self.assertEqual(response.status_code, 404)
        self.assertEqual(payload["error"]["code"], "diagnosis_not_found")

    def test_the_diagnosis_is_optional(self):
        appointment, _e = self._in_consultation_visit()
        _r, payload = self._order(appointment)
        self.assertIsNone(self._orders(payload)[0]["diagnosis"])

    def test_the_model_refuses_a_foreign_diagnosis_independently_of_the_api(self):
        first, _e1 = self._in_consultation_visit()
        second, _e2 = self._in_consultation_visit()
        consultation = self._consultation_for(first)
        other_consultation = self._consultation_for(second)
        foreign = self.env["hospital.patient.diagnosis"].sudo().add_to_consultation(
            other_consultation, self.disease, {"diagnosis_type": "primary"}
        )

        with self.assertRaises(ValidationError):
            self.env["hospital.laboratory.request"].sudo().create_from_consultation(
                consultation, self.cbc, {}, diagnosis=foreign
            )


@tagged("post_install", "-at_install", "doctor_laboratory")
class TestLaboratoryIdempotency(LaboratoryCase):
    """A retried submission must not order -- or bill -- the same tests twice."""

    def test_the_same_token_returns_the_first_order(self):
        appointment, _e = self._in_consultation_visit()
        consultation = self._consultation_for(appointment)
        token = uuid.uuid4().hex

        first, first_body = self._order(appointment, request_token=token)
        second, second_body = self._order(appointment, request_token=token)

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(len(self._orders(second_body)), 1)
        self.assertEqual(
            self._orders(first_body)[0]["id"], self._orders(second_body)[0]["id"]
        )
        self.assertEqual(len(self._requests_of(consultation)), 1)

    def test_a_double_click_raises_no_second_set_of_charges(self):
        """THE case the token exists for. Two requests would mean two charges
        for the same tests, on the same encounter, for the same patient."""
        appointment, _e = self._in_consultation_visit()
        consultation = self._consultation_for(appointment)
        token = uuid.uuid4().hex

        self._order(appointment, [self.cbc, self.creatinine], request_token=token)
        self._order(appointment, [self.cbc, self.creatinine], request_token=token)

        stored = self._requests_of(consultation)
        self.assertEqual(len(stored), 1)
        self.assertEqual(len(stored.sudo().charge_line_ids), 2)

    def test_the_same_token_in_two_consultations_stays_independent(self):
        first, _e1 = self._in_consultation_visit()
        second, _e2 = self._in_consultation_visit()
        token = uuid.uuid4().hex

        one, one_body = self._order(first, [self.cbc], request_token=token)
        two, two_body = self._order(second, [self.creatinine], request_token=token)

        self.assertEqual(one.status_code, 200)
        self.assertEqual(two.status_code, 200, "the shared token was refused")
        self.assertNotEqual(
            self._orders(one_body)[0]["id"], self._orders(two_body)[0]["id"]
        )
        self.assertEqual(
            self._orders(two_body)[0]["tests"][0]["test_id"], self.creatinine.id
        )

    def test_the_database_refuses_a_duplicate_token_in_one_consultation(self):
        appointment, _e = self._in_consultation_visit()
        consultation = self._consultation_for(appointment)
        token = uuid.uuid4().hex
        self._order(appointment, request_token=token)

        with self.assertRaises(IntegrityError), mute_logger("odoo.sql_db"):
            with self.cr.savepoint():
                self.env.cr.execute(
                    """
                    UPDATE hospital_laboratory_request
                    SET request_token = %s, consultation_id = %s
                    WHERE id = (
                        SELECT id FROM hospital_laboratory_request
                        WHERE consultation_id IS NULL LIMIT 1
                    )
                    """,
                    (token, consultation.id),
                )
                self.env.cr.execute(
                    """
                    INSERT INTO hospital_laboratory_request
                        (name, patient_id, physician_id, consultation_id,
                         request_token, request_date, priority, state, active,
                         create_uid, write_uid, create_date, write_date)
                    VALUES ('DUP', %s, %s, %s, %s, now(), 'routine',
                            'draft', true, 1, 1, now(), now())
                    """,
                    (
                        consultation.patient_id.id,
                        consultation.doctor_id.id,
                        consultation.id,
                        token,
                    ),
                )


@tagged("post_install", "-at_install", "doctor_laboratory")
class TestLaboratoryCancellation(LaboratoryCase):
    """Cancellation runs the model's workflow, and stops where it stops."""

    def _place(self, appointment):
        _r, payload = self._order(appointment)
        return self._orders(payload)[0]["id"]

    def test_a_requested_order_can_be_cancelled(self):
        appointment, _e = self._in_consultation_visit()
        consultation = self._consultation_for(appointment)
        order_id = self._place(appointment)

        response, payload = self._post_body(CANCEL % (appointment.id, order_id), {})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(self._orders(payload)[0]["status"], "cancelled")
        self.assertEqual(self._requests_of(consultation).state, "cancelled")

    def test_cancelling_cancels_the_charges_through_the_billing_layer(self):
        appointment, _e = self._in_consultation_visit()
        consultation = self._consultation_for(appointment)
        order_id = self._place(appointment)

        self._post_body(CANCEL % (appointment.id, order_id), {})

        charges = self._requests_of(consultation).sudo().charge_line_ids
        self.assertTrue(charges)
        for charge in charges:
            self.assertEqual(charge.charge_state, "cancelled")

    def test_cancellation_is_refused_once_the_sample_is_collected(self):
        """The base model's transition table has no sample_collected ->
        cancelled edge, and the Doctor Desk does not invent one."""
        appointment, _e = self._in_consultation_visit()
        consultation = self._consultation_for(appointment)
        order_id = self._place(appointment)
        self._requests_of(consultation).sudo().action_mark_sample_collected()

        response, _payload = self._post_body(CANCEL % (appointment.id, order_id), {})

        self.assertGreaterEqual(response.status_code, 400)
        self.assertEqual(self._requests_of(consultation).state, "sample_collected")

    def test_a_collected_order_is_not_offered_as_cancellable(self):
        appointment, _e = self._in_consultation_visit()
        consultation = self._consultation_for(appointment)
        self._place(appointment)
        self._requests_of(consultation).sudo().action_mark_sample_collected()

        _r, payload = self._get(ORDERS % appointment.id)

        self.assertFalse(self._orders(payload)[0]["cancellable"])

    def test_an_order_from_another_visit_is_not_reachable(self):
        first, _e1 = self._in_consultation_visit()
        second, _e2 = self._in_consultation_visit()
        foreign_id = self._place(second)

        response, payload = self._post_body(CANCEL % (first.id, foreign_id), {})

        self.assertEqual(response.status_code, 404)
        self.assertEqual(payload["error"]["code"], "laboratory_order_not_found")

    def test_orders_are_never_editable(self):
        """The ordered set is frozen the moment the request leaves draft, and
        the desk confirms on submission -- so it says so rather than offering a
        control the model would refuse."""
        appointment, _e = self._in_consultation_visit()
        self._place(appointment)
        _r, payload = self._get(ORDERS % appointment.id)
        self.assertFalse(self._orders(payload)[0]["editable"])


@tagged("post_install", "-at_install", "doctor_laboratory")
class TestLaboratoryAccess(LaboratoryCase):
    """Scope, which the laboratory models shipped without."""

    def test_a_pure_doctor_cannot_order_through_another_doctors_visit(self):
        appointment, _e = self._in_consultation_visit(doctor=self.other_doctor)

        response, payload = self._post_body(
            ORDERS % appointment.id,
            {"tests": [self.cbc.id]},
            user=self.doctor_user,
            password=self.doctor_password,
        )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(payload["error"]["code"], "visit_not_found")

    def test_the_record_rule_hides_another_doctors_request_at_the_orm(self):
        appointment, _e = self._in_consultation_visit(doctor=self.other_doctor)
        self._post_body(
            ORDERS % appointment.id,
            {"tests": [self.cbc.id]},
            user=self.other_user,
            password=self.other_password,
        )
        stored = self._requests_of(self._consultation_for(appointment))
        self.assertTrue(stored)

        visible = (
            self.env["hospital.laboratory.request"]
            .with_user(self.doctor_user)
            .search([("id", "=", stored.id)])
        )

        self.assertFalse(visible)

    def test_the_ordering_doctor_reaches_their_own_request(self):
        appointment, _e = self._in_consultation_visit(doctor=self.doctor)
        self._order(appointment)
        stored = self._requests_of(self._consultation_for(appointment))

        visible = (
            self.env["hospital.laboratory.request"]
            .with_user(self.doctor_user)
            .search([("id", "=", stored.id)])
        )

        self.assertEqual(visible, stored)

    def test_the_laboratory_bench_still_sees_every_request(self):
        """THE regression a doctor-scoped rule most easily causes. The lab works
        a cross-patient queue; scoping it would break the laboratory."""
        lab_tech = self._make_user(
            "lab_tech", "lab-pw-1",
            ["hospital_management.group_hospital_lab_technician"],
        )
        appointment, _e = self._in_consultation_visit(doctor=self.doctor)
        self._order(appointment)
        stored = self._requests_of(self._consultation_for(appointment))

        visible = (
            self.env["hospital.laboratory.request"]
            .with_user(lab_tech)
            .search([("id", "=", stored.id)])
        )

        self.assertEqual(visible, stored)

    def test_non_clinical_roles_are_denied_the_endpoints(self):
        appointment, _e = self._in_consultation_visit()

        for user, password in (
            (self.nurse, self.nurse_password),
            (self.front_desk, self.fd_password),
            (self.cashier, self.cashier_password),
            (self.receptionist, self.receptionist_password),
            (self.accountant, self.accountant_password),
        ):
            response, payload = self._get(
                ORDERS % appointment.id, user=user, password=password
            )
            self.assertEqual(response.status_code, 403, user.login)
            self.assertEqual(payload["error"]["code"], "access_denied", user.login)

            response, _p = self._post_body(
                ORDERS % appointment.id,
                {"tests": [self.cbc.id]},
                user=user,
                password=password,
            )
            self.assertEqual(response.status_code, 403, user.login)

    def test_a_doctor_holds_no_write_access_to_results(self):
        """Result entry and validation belong to the bench, and the shipped
        ACL already says so. Pinned here because this slice is the one that
        gives doctors a reason to be near the laboratory models."""
        model = self.env["hospital.laboratory.result"].with_user(self.doctor_user)
        with self.assertRaises(AccessError):
            model.create({"patient_id": self.env["hospital.patient"].sudo().create(
                {"name": "R %s" % uuid.uuid4().hex[:6]}).id})


@tagged("post_install", "-at_install", "doctor_laboratory")
class TestLaboratoryTransactionBoundary(LaboratoryCase):
    """One doctor action is one transaction -- charges included."""

    def test_response_failure_rolls_back_the_request_and_its_charges(self):
        appointment, _e = self._in_consultation_visit()
        consultation = self._consultation_for(appointment)
        charges_before = self.env["hospital.charge.line"].sudo().search_count(
            [("encounter_id", "=", consultation.encounter_id.id)]
        )

        with patch(
            LAB_SERIALIZER_TARGET,
            side_effect=AccessError("simulated post-write failure"),
        ):
            response, payload = self._order(appointment, [self.cbc, self.creatinine])

        self.assertEqual(response.status_code, 500)
        self.assertEqual(payload["error"]["code"], "laboratory_response_failed")
        self.assertNotEqual(payload["error"]["code"], "access_denied")
        serialized = json.dumps(payload)
        self.assertNotIn("Traceback", serialized)
        self.assertNotIn("simulated post-write failure", serialized)

        self.assertFalse(
            self._requests_of(consultation),
            "the laboratory request survived a rolled-back order",
        )
        charges_after = self.env["hospital.charge.line"].sudo().search_count(
            [("encounter_id", "=", consultation.encounter_id.id)]
        )
        self.assertEqual(
            charges_after, charges_before,
            "charges survived a rolled-back order",
        )

    def test_the_retry_after_a_rollback_succeeds(self):
        appointment, _e = self._in_consultation_visit()
        consultation = self._consultation_for(appointment)

        with patch(
            LAB_SERIALIZER_TARGET,
            side_effect=AccessError("simulated post-write failure"),
        ):
            failed, _ = self._order(appointment)
        self.assertEqual(failed.status_code, 500)

        response, payload = self._order(appointment)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(self._orders(payload)), 1)
        self.assertEqual(len(self._requests_of(consultation)), 1)

    def test_a_rolled_back_order_does_not_consume_the_request_token(self):
        appointment, _e = self._in_consultation_visit()
        token = uuid.uuid4().hex

        with patch(
            LAB_SERIALIZER_TARGET,
            side_effect=AccessError("simulated post-write failure"),
        ):
            self._order(appointment, request_token=token)

        response, payload = self._order(appointment, request_token=token)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(self._orders(payload)), 1)

    def test_response_failure_on_cancel_rolls_the_cancellation_back(self):
        appointment, _e = self._in_consultation_visit()
        consultation = self._consultation_for(appointment)
        _r, payload = self._order(appointment)
        order_id = self._orders(payload)[0]["id"]

        with patch(
            LAB_SERIALIZER_TARGET,
            side_effect=AccessError("simulated post-write failure"),
        ):
            response, _body = self._post_body(
                CANCEL % (appointment.id, order_id), {}
            )

        self.assertEqual(response.status_code, 500)
        self.assertEqual(self._requests_of(consultation).state, "requested")


@tagged("post_install", "-at_install", "doctor_laboratory")
class TestLaboratoryLegacyRows(LaboratoryCase):
    """Requests raised outside a consultation stay valid forever."""

    def test_a_walk_in_request_needs_no_consultation(self):
        patient = self.env["hospital.patient"].sudo().create(
            {"name": "Walk-in %s" % uuid.uuid4().hex[:6]}
        )
        request = self.env["hospital.laboratory.request"].sudo().create(
            {
                "patient_id": patient.id,
                "physician_id": self.doctor.id,
                "line_ids": [(0, 0, {"test_id": self.cbc.id})],
            }
        )

        self.assertTrue(request.exists())
        self.assertFalse(request.consultation_id)
        self.assertFalse(request.request_token)
        # Still freely writable: no consultation means no consultation freeze.
        request.write({"clinical_notes": "Walk-in order"})
        self.assertEqual(request.clinical_notes, "Walk-in order")

    def test_a_consultation_request_cannot_cite_another_patient(self):
        appointment, _e = self._in_consultation_visit()
        consultation = self._consultation_for(appointment)
        stranger = self.env["hospital.patient"].sudo().create(
            {"name": "Stranger %s" % uuid.uuid4().hex[:6]}
        )

        with self.assertRaises(ValidationError):
            self.env["hospital.laboratory.request"].sudo().create(
                {
                    "patient_id": stranger.id,
                    "physician_id": consultation.doctor_id.id,
                    "consultation_id": consultation.id,
                    "encounter_id": consultation.encounter_id.id,
                    "line_ids": [(0, 0, {"test_id": self.cbc.id})],
                }
            )

    def test_a_consultation_request_must_carry_the_consultation_visit(self):
        appointment, _e = self._in_consultation_visit()
        consultation = self._consultation_for(appointment)

        with self.assertRaises(ValidationError):
            self.env["hospital.laboratory.request"].sudo().create(
                {
                    "patient_id": consultation.patient_id.id,
                    "physician_id": consultation.doctor_id.id,
                    "consultation_id": consultation.id,
                    "encounter_id": consultation.encounter_id.id,
                    # appointment_id deliberately omitted.
                    "line_ids": [(0, 0, {"test_id": self.cbc.id})],
                }
            )

    def test_a_consultation_request_cannot_name_another_physician(self):
        appointment, _e = self._in_consultation_visit()
        consultation = self._consultation_for(appointment)

        with self.assertRaises(ValidationError):
            self.env["hospital.laboratory.request"].sudo().create(
                {
                    "patient_id": consultation.patient_id.id,
                    "physician_id": self.other_doctor.id,
                    "consultation_id": consultation.id,
                    "encounter_id": consultation.encounter_id.id,
                    "appointment_id": consultation.appointment_id.id,
                    "line_ids": [(0, 0, {"test_id": self.cbc.id})],
                }
            )

    def test_the_model_refuses_ordering_on_a_completed_consultation(self):
        appointment, _e = self._in_consultation_visit()
        consultation = self._consultation_for(appointment)
        consultation.write({"state": "completed"})

        with self.assertRaisesRegex(UserError, "completed"):
            self.env["hospital.laboratory.request"].sudo().create_from_consultation(
                consultation, self.cbc, {}
            )
