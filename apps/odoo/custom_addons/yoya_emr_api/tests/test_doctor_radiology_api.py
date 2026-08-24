"""Radiology ordering from the Doctor Desk.

WHAT THESE TESTS ARE FOR
------------------------
The same four properties that carry the laboratory slice, plus one radiology
only has:

  1. THE DOCTOR DESK CREATES NO CHARGE. It creates a clinical request and calls
     the EXISTING action_confirm_request(); hospital_billing's override is what
     validates the billing configuration and raises one charge per ordered
     examination. The tests below assert the charges appear -- and that they
     appear through that path, not from anything this API does.

  2. ONE DOCTOR ACTION IS ONE TRANSACTION. Request, lines, confirmation and the
     charges it raises commit together or not at all.

  3. OWNERSHIP IS DERIVED. Patient, physician, encounter, appointment and
     consultation all come from the consultation the caller already resolved.

  4. THE PAYLOAD IS CLINICAL. No amount, payer or receipt may appear in what the
     desk receives -- and no radiology REPORT either, which is this slice's own
     boundary: it says a result exists, never what it says.

  5. THE STATUS NEVER OVER-PROMISES. Radiology's unpaid window spans two states,
     `requested` and `scheduled`, because charges are raised at confirmation but
     the gate is Mark In Progress. A status that read clear anywhere in that
     window would tell a doctor the scan can go ahead while the authoritative
     gate would still refuse it.

The clinical status vocabulary is DERIVED from real backend state, so a change
to hospital.radiology.request's workflow surfaces here rather than silently
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

CATALOGUE = "/yoya-emr/api/v1/doctor/catalogue/radiology-exams"
ORDERS = "/yoya-emr/api/v1/doctor/visits/%s/orders/radiology"
CANCEL = "/yoya-emr/api/v1/doctor/visits/%s/orders/radiology/%s/cancel"

RAD_SERIALIZER_TARGET = (
    "odoo.addons.yoya_emr_api.controllers.doctor.serialize_radiology_orders"
)

EXPECTED_ORDER_KEYS = {
    "id", "request_code", "exams", "diagnosis", "clinical_indication",
    "instructions", "priority", "status", "status_label", "ordered_at",
    "created_at", "has_result", "editable", "cancellable",
}

EXPECTED_EXAM_KEYS = {
    "id", "name", "code", "modality", "body_part", "contrast_required",
}

FORBIDDEN_KEYS = (
    "amount", "balance", "outstanding", "paid", "receipt", "sponsor",
    "agreement", "membership", "payer", "tariff", "price", "invoice",
    "charge", "credit_limit", "coverage", "billing_service", "default_price",
)

# A released radiology report is a narrative. None of it belongs in this slice.
FORBIDDEN_REPORT_KEYS = (
    "findings", "impression", "recommendation", "radiologist", "report",
)


class RadiologyCase(DiagnosisCase):
    """DiagnosisCase plus a small, correctly BILLABLE radiology catalogue."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        tag = uuid.uuid4().hex[:6]
        cls.uom = cls.env["uom.uom"].sudo().search([], limit=1)
        cls.ct_brain = cls._make_exam(
            "CT Brain %s" % tag, "CTB%s" % tag.upper(), "ct", "Head", True
        )
        cls.chest_xray = cls._make_exam(
            "Chest X-Ray %s" % tag, "CXR%s" % tag.upper(), "xray", "Chest", False
        )
        cls.abdo_us = cls._make_exam(
            "Abdominal US %s" % tag, "AUS%s" % tag.upper(), "ultrasound",
            "Abdomen", False,
        )
        # An exam with NO billing service -- the shape five of the six exams on
        # the UAT database actually have. Ordering it must fail the whole
        # submission, because _ensure_radiology_billing validates the entire set
        # before raising any charge, so the catalogue must not offer it.
        cls.unmapped = cls.env["hospital.radiology.exam"].sudo().create(
            {
                "name": "Unmapped %s" % tag,
                "code": "UNM%s" % tag.upper(),
                "modality": "mri",
            }
        )
        # Archived: still a row, never orderable.
        cls.archived = cls._make_exam(
            "Archived %s" % tag, "ARC%s" % tag.upper(), "xray", "Pelvis", False
        )
        cls.archived.sudo().write({"active": False})
        # Mapped, but to a service whose effective window has already closed.
        cls.expired = cls._make_exam(
            "Expired %s" % tag, "EXP%s" % tag.upper(), "ct", "Spine", False
        )
        cls.expired.sudo().billing_service_id.write(
            {"effective_date_end": "2000-01-01"}
        )
        # Mapped, but to an ARCHIVED billing service.
        cls.dead_service = cls._make_exam(
            "DeadSvc %s" % tag, "DED%s" % tag.upper(), "mri", "Knee", False
        )
        cls.dead_service.sudo().billing_service_id.write({"active": False})
        # Mapped to a service belonging to ANOTHER company.
        cls.foreign_company = cls.env["res.company"].sudo().create(
            {"name": "Radiology Other Co %s" % tag}
        )
        cls.other_company_exam = cls._make_exam(
            "OtherCo %s" % tag, "OTH%s" % tag.upper(), "ct", "Chest", False
        )
        cls.other_company_exam.sudo().billing_service_id.write(
            {"company_id": cls.foreign_company.id}
        )

    @classmethod
    def _make_exam(cls, name, code, modality, body_part, contrast):
        service = cls.env["hospital.billing.service"].sudo().create(
            {
                "name": "%s Service" % name,
                "code": "T-RAD-%s" % code,
                "service_type": "radiology",
                "default_price": 900.0,
                "company_id": cls.env.company.id,
                "currency_id": cls.env.company.currency_id.id,
                "uom_id": cls.uom.id,
                # PREPAID, which is how this hospital actually prices imaging.
                # It is also what makes an unpaid confirmed request genuinely
                # blocked, which several tests below depend on being true.
                "prepayment_required": True,
                "tax_treatment": "exempt",
            }
        )
        return cls.env["hospital.radiology.exam"].sudo().create(
            {
                "name": name,
                "code": code,
                "modality": modality,
                "body_part": body_part,
                "contrast_required": contrast,
                "billing_service_id": service.id,
            }
        )

    # ------------------------------------------------------------------
    def _order(self, appointment, exams=None, **extra):
        """Place an order the way a conforming client does.

        A FRESH request_token IS ALWAYS SENT, because the endpoint now requires
        one. Each call therefore represents a distinct submission -- which is
        exactly what a tokenless POST used to mean -- so every test written
        before the token became mandatory keeps its original meaning.

        `extra` overrides it, so a test that needs a SPECIFIC token (idempotency)
        or a malformed one (the contract tests) passes request_token= explicitly.
        A test that needs the key ABSENT builds the body itself with _post_body.
        """
        body = {
            "exams": [e.id for e in (exams or [self.ct_brain])],
            "request_token": uuid.uuid4().hex,
        }
        body.update(extra)
        return self._post_body(ORDERS % appointment.id, body)

    def _orders(self, payload):
        return payload["data"]["orders"]

    def _requests_of(self, consultation):
        return (
            self.env["hospital.radiology.request"]
            .sudo()
            .with_context(active_test=False)
            .search([("consultation_id", "=", consultation.id)])
        )

    def _settle(self, encounter):
        """Pay everything currently owed on the visit, as the cashier."""
        account = encounter.billing_account_id.sudo()
        account.invalidate_recordset()
        due = account.amount_due_for_clearance
        if due <= 0:
            return None
        return account.with_user(self.cashier).record_operational_payment(
            due, "cash", intake_token=uuid.uuid4().hex
        )

    def _live_payable(self, request):
        request.invalidate_recordset(["charge_line_ids"])
        return request.sudo().charge_line_ids.filtered(
            lambda c: c.charge_state in ("draft", "active")
            and c.amount_due_for_clearance > 0
        )


@tagged("post_install", "-at_install", "doctor_radiology")
class TestRadiologyCatalogue(RadiologyCase):
    """The exam picker: bounded, searchable, and priced nowhere."""

    def _catalogue_ids(self, **params):
        _r, payload = self._get(CATALOGUE, **params)
        return {row["id"] for row in payload["data"]["exams"]}

    def test_search_matches_name_and_code(self):
        response, payload = self._get(CATALOGUE, q="CT Brain")
        self.assertEqual(response.status_code, 200)
        self.assertIn(
            self.ct_brain.name, [row["name"] for row in payload["data"]["exams"]]
        )

        _r, by_code = self._get(CATALOGUE, q=self.chest_xray.code)
        self.assertIn(
            self.chest_xray.name,
            [row["name"] for row in by_code["data"]["exams"]],
        )

    def test_search_matches_body_part(self):
        """A doctor thinks in anatomy before they think in modality codes, and
        body_part is on the exam already."""
        ids = self._catalogue_ids(q="Abdomen", limit=50)
        self.assertIn(self.abdo_us.id, ids)

    def test_the_limit_is_clamped_server_side(self):
        _r, payload = self._get(CATALOGUE, limit=9999)
        self.assertLessEqual(payload["data"]["limit"], 50)
        self.assertLessEqual(len(payload["data"]["exams"]), 50)

    def test_an_empty_query_is_still_bounded(self):
        _r, payload = self._get(CATALOGUE)
        self.assertLessEqual(
            len(payload["data"]["exams"]), payload["data"]["limit"]
        )

    def test_the_catalogue_carries_the_clinical_fields_and_nothing_priced(self):
        """hospital.radiology.exam carries billing_service_id once
        hospital_billing is installed. It decides what a scan costs, and it must
        not reach a clinician's screen. Modality, body part and contrast must,
        because they are how a study is chosen and prepared for."""
        self.assertTrue(self.ct_brain.billing_service_id)

        _r, payload = self._get(CATALOGUE, q=self.ct_brain.code)

        serialized = json.dumps(payload).lower()
        for forbidden in FORBIDDEN_KEYS:
            self.assertNotIn(forbidden, serialized, forbidden)

        row = payload["data"]["exams"][0]
        self.assertEqual(set(row), EXPECTED_EXAM_KEYS)
        self.assertEqual(row["modality"], "ct")
        self.assertEqual(row["body_part"], "Head")
        self.assertTrue(row["contrast_required"])

    # ------------------------------------------------------------------
    # Eligibility: the picker must not offer what submission refuses
    # ------------------------------------------------------------------
    def test_a_mapped_active_exam_is_offered(self):
        self.assertIn(self.ct_brain.id, self._catalogue_ids(q=self.ct_brain.code))

    def test_an_unmapped_exam_is_never_offered(self):
        """THE regression this hardening exists for, and it is not hypothetical:
        five of the six exams shipped on the UAT database have exactly this
        shape. Offering one would offer an action the very next step refuses,
        with nothing the doctor could fix."""
        self.assertFalse(self.unmapped.billing_service_id)
        self.assertNotIn(self.unmapped.id, self._catalogue_ids(limit=50))

    def test_searching_for_an_unmapped_exam_by_name_returns_nothing(self):
        """Not merely absent from the default page: unreachable by search."""
        _r, payload = self._get(CATALOGUE, q=self.unmapped.name)
        self.assertEqual(payload["data"]["exams"], [])

    def test_searching_for_an_unmapped_exam_by_code_returns_nothing(self):
        _r, payload = self._get(CATALOGUE, q=self.unmapped.code)
        self.assertEqual(payload["data"]["exams"], [])

    def test_an_archived_exam_is_never_offered(self):
        self.assertFalse(self.archived.active)
        self.assertNotIn(self.archived.id, self._catalogue_ids(limit=50))
        _r, payload = self._get(CATALOGUE, q=self.archived.code)
        self.assertEqual(payload["data"]["exams"], [])

    def test_an_exam_whose_service_is_archived_is_never_offered(self):
        """Mapped is not enough: _assert_billable refuses an archived service,
        so the picker mirrors that rather than only checking for a mapping."""
        self.assertTrue(self.dead_service.billing_service_id)
        self.assertNotIn(self.dead_service.id, self._catalogue_ids(limit=50))
        _r, payload = self._get(CATALOGUE, q=self.dead_service.code)
        self.assertEqual(payload["data"]["exams"], [])

    def test_an_exam_whose_service_has_expired_is_never_offered(self):
        self.assertNotIn(self.expired.id, self._catalogue_ids(limit=50))
        _r, payload = self._get(CATALOGUE, q=self.expired.code)
        self.assertEqual(payload["data"]["exams"], [])

    def test_an_exam_priced_for_another_company_is_never_offered(self):
        """_assert_billable refuses a service belonging to another company, so
        the picker must too -- otherwise a doctor in this hospital would be
        offered a study priced for a different one."""
        self.assertNotIn(self.other_company_exam.id, self._catalogue_ids(limit=50))
        _r, payload = self._get(CATALOGUE, q=self.other_company_exam.code)
        self.assertEqual(payload["data"]["exams"], [])

    def test_the_catalogue_filter_matches_the_confirmation_gate(self):
        """The picker and the gate must agree, item for item.

        Anything the catalogue offers must survive _assert_billable, and
        anything it hides must not. This is the property the domain exists to
        hold; asserting it directly means a future divergence fails here rather
        than in a clinician's hands.
        """
        model = self.env["hospital.radiology.exam"].sudo()
        offered = model.search(model.doctor_orderable_domain())

        for exam in offered:
            # Must not raise.
            exam._assert_billable(self.env.company)

        for hidden in (
            self.unmapped, self.expired, self.dead_service, self.other_company_exam
        ):
            # ValidationError subclasses UserError, so one type covers both.
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


@tagged("post_install", "-at_install", "doctor_radiology")
class TestRadiologyOrdering(RadiologyCase):
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
            "billing_blocked", "evaluation_id", "completed_at",
        ):
            response, payload = self._order(appointment, **{field: 1})
            self.assertEqual(response.status_code, 400, field)
            self.assertEqual(payload["error"]["code"], "protected_field", field)

    def test_an_unrecognised_field_is_named_rather_than_dropped(self):
        appointment, _e = self._in_consultation_visit()
        response, payload = self._order(appointment, contrast_agent="iodine")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(payload["error"]["code"], "unknown_field")

    def test_one_request_can_carry_several_studies(self):
        """The base model supports a multi-exam request, so one doctor action
        produces ONE request rather than three."""
        appointment, _e = self._in_consultation_visit()
        consultation = self._consultation_for(appointment)

        _r, payload = self._order(
            appointment, [self.ct_brain, self.chest_xray, self.abdo_us]
        )

        orders = self._orders(payload)
        self.assertEqual(len(orders), 1)
        self.assertEqual(len(orders[0]["exams"]), 3)
        self.assertEqual(len(self._requests_of(consultation)), 1)
        self.assertEqual(len(self._requests_of(consultation).line_ids), 3)

    def test_a_repeated_exam_in_one_submission_is_ordered_once(self):
        """Two of the same study in one submission is a client mistake, and
        would otherwise raise two charges for one scan."""
        appointment, _e = self._in_consultation_visit()
        consultation = self._consultation_for(appointment)

        _r, payload = self._order(
            appointment, [self.ct_brain, self.ct_brain, self.chest_xray]
        )

        self.assertEqual(len(self._orders(payload)[0]["exams"]), 2)
        self.assertEqual(len(self._requests_of(consultation).line_ids), 2)

    def test_the_indication_instructions_and_priority_are_stored(self):
        appointment, _e = self._in_consultation_visit()
        indication = "Head injury.\nRule out bleed."
        instructions = "Patient is claustrophobic; sedation may be needed."

        _r, payload = self._order(
            appointment,
            clinical_indication=indication,
            instructions=instructions,
            priority="urgent",
        )

        order = self._orders(payload)[0]
        self.assertEqual(order["clinical_indication"], indication)
        self.assertEqual(order["instructions"], instructions)
        self.assertEqual(order["priority"], "urgent")

    def test_an_invalid_priority_is_refused(self):
        appointment, _e = self._in_consultation_visit()
        consultation = self._consultation_for(appointment)

        response, _payload = self._order(appointment, priority="whenever")

        self.assertEqual(response.status_code, 400)
        self.assertFalse(self._requests_of(consultation))

    def test_the_line_is_seeded_from_the_catalogue(self):
        """body_part and contrast_required are copied from the exam exactly as
        the Odoo form's onchange would, because an onchange does not run for a
        programmatic create -- and contrast is patient preparation, not
        decoration."""
        appointment, _e = self._in_consultation_visit()
        consultation = self._consultation_for(appointment)

        self._order(appointment, [self.ct_brain])

        line = self._requests_of(consultation).line_ids
        self.assertEqual(line.body_part, self.ct_brain.body_part)
        self.assertTrue(line.contrast_required)

    def test_the_ordered_study_is_serialized_with_its_preparation(self):
        appointment, _e = self._in_consultation_visit()
        _r, payload = self._order(appointment, [self.ct_brain])

        exam = self._orders(payload)[0]["exams"][0]
        self.assertEqual(exam["exam_id"], self.ct_brain.id)
        self.assertEqual(exam["modality"], "ct")
        self.assertEqual(exam["body_part"], "Head")
        self.assertTrue(exam["contrast_required"])

    def test_an_unknown_exam_is_a_404(self):
        appointment, _e = self._in_consultation_visit()
        response, payload = self._post_body(
            ORDERS % appointment.id,
            {"exams": [99999999], "request_token": uuid.uuid4().hex},
        )
        self.assertEqual(response.status_code, 404)
        self.assertEqual(payload["error"]["code"], "radiology_exam_not_found")

    def test_an_empty_exam_list_is_refused(self):
        appointment, _e = self._in_consultation_visit()
        response, payload = self._post_body(ORDERS % appointment.id, {"exams": []})
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

    def test_the_model_refuses_a_completed_consultation_independently(self):
        """Defence in depth: the freeze is the MODEL's, so an RPC caller that
        never touches this controller is refused too."""
        appointment, _e = self._in_consultation_visit()
        consultation = self._consultation_for(appointment)
        consultation.write({"state": "completed"})

        with self.assertRaises(UserError):
            self.env["hospital.radiology.request"].sudo().create_from_consultation(
                consultation, self.ct_brain, {}
            )


@tagged("post_install", "-at_install", "doctor_radiology")
class TestRadiologyBillingHandoff(RadiologyCase):
    """The charges come from the radiology model; the Desk never makes one."""

    def test_confirmation_uses_the_authoritative_workflow(self):
        """The request must be REQUESTED, not left in draft.

        A draft request raises no charge, never reaches the imaging worklist and
        can only be rescued by a manual Odoo intervention -- so `requested` is
        the state that means the doctor has really ordered the study.
        """
        appointment, _e = self._in_consultation_visit()
        consultation = self._consultation_for(appointment)

        self._order(appointment, [self.ct_brain, self.chest_xray])

        self.assertEqual(self._requests_of(consultation).state, "requested")

    def test_confirmation_raises_one_charge_per_ordered_study(self):
        appointment, _e = self._in_consultation_visit()
        consultation = self._consultation_for(appointment)

        self._order(appointment, [self.ct_brain, self.chest_xray])

        stored = self._requests_of(consultation)
        self.assertEqual(len(stored.sudo().charge_line_ids), 2)

    def test_the_doctor_controller_never_touches_a_charge_model(self):
        """The Doctor API must never OPEN a billing model.

        A source assertion is crude, but it pins the architectural rule far more
        reliably than a behavioural test: charge creation belongs to
        hospital_billing, and the day someone reaches for
        env['hospital.charge.line'] here to "just check something", this fails.
        """
        import inspect
        import re

        from odoo.addons.yoya_emr_api.controllers import doctor as controller
        from odoo.addons.yoya_emr_api.services import radiology_serializers

        opens_billing = re.compile(
            r"""env\[\s*["']hospital\.(charge|billing)[.\w]*["']\s*\]"""
        )
        for module in (controller, radiology_serializers):
            hits = opens_billing.findall(inspect.getsource(module))
            self.assertEqual(
                hits, [],
                "%s opens a billing model directly: %s" % (module.__name__, hits),
            )

    def test_the_doctor_api_never_calls_sudo_on_the_radiology_path(self):
        """Elevation belongs inside hospital_billing's engine, which already has
        it. A sudo() here would quietly bypass the record rules this slice just
        added -- which, for radiology, are the ONLY ones that have ever
        existed."""
        import inspect

        from odoo.addons.yoya_emr_api.services import radiology_serializers

        self.assertNotIn("sudo(", inspect.getsource(radiology_serializers))

    def test_an_unmapped_exam_places_no_order_at_all(self):
        """ALL-OR-NOTHING. _ensure_radiology_billing validates the whole set
        before creating any charge, so a single unmapped exam must leave no
        request and no charge behind -- not a partially charged order."""
        appointment, _e = self._in_consultation_visit()
        consultation = self._consultation_for(appointment)

        charges_before = self.env["hospital.charge.line"].sudo().search_count(
            [("encounter_id", "=", consultation.encounter_id.id)]
        )

        response, _payload = self._order(
            appointment, [self.ct_brain, self.unmapped]
        )

        self.assertGreaterEqual(response.status_code, 400)
        self.assertFalse(
            self._requests_of(consultation), "a half-billable order survived"
        )
        # DEFENCE IN DEPTH. The catalogue no longer offers an unmapped exam, but
        # a hand-built request, an RPC caller, or a mapping removed between
        # picking and submitting must still be refused here.
        charges_after = self.env["hospital.charge.line"].sudo().search_count(
            [("encounter_id", "=", consultation.encounter_id.id)]
        )
        self.assertEqual(charges_after, charges_before)

    def test_the_order_payload_carries_no_financial_field(self):
        appointment, _e = self._in_consultation_visit()
        self._order(appointment, [self.ct_brain, self.chest_xray])

        _r, payload = self._get(ORDERS % appointment.id)

        serialized = json.dumps(payload).lower()
        for forbidden in FORBIDDEN_KEYS:
            self.assertNotIn(
                forbidden, serialized,
                "'%s' leaked into the radiology payload" % forbidden,
            )


@tagged("post_install", "-at_install", "doctor_radiology")
class TestRadiologyStatus(RadiologyCase):
    """The status must never claim ready while the gate would refuse."""

    def _status(self, appointment):
        _r, payload = self._get(ORDERS % appointment.id)
        return self._orders(payload)[0]

    def test_an_unpaid_confirmed_order_reads_awaiting_clearance(self):
        """THE DEFECT THIS SLICE FIXED, SEEN FROM THE DOCTOR'S SIDE.

        billing_blocked used to compute only at `scheduled`, so a confirmed,
        prepaid, entirely unpaid study reported clear -- and any status derived
        from it would have told the doctor imaging could go ahead.
        """
        appointment, _e = self._in_consultation_visit()
        self._order(appointment)

        order = self._status(appointment)
        self.assertEqual(order["status"], "awaiting_clearance")
        self.assertEqual(order["status_label"], "Awaiting clearance")

    def test_paying_moves_it_to_awaiting_scheduling(self):
        appointment, encounter = self._in_consultation_visit()
        self._order(appointment)
        self._settle(encounter)

        order = self._status(appointment)
        self.assertEqual(order["status"], "awaiting_scheduling")
        self.assertEqual(order["status_label"], "Awaiting scheduling")

    def test_the_status_follows_the_real_workflow_forward(self):
        appointment, encounter = self._in_consultation_visit()
        consultation = self._consultation_for(appointment)
        self._order(appointment)
        self._settle(encounter)
        stored = self._requests_of(consultation)

        stored.sudo().action_schedule()
        self.assertEqual(self._status(appointment)["status"], "scheduled")

        stored.sudo().action_mark_in_progress()
        order = self._status(appointment)
        self.assertEqual(order["status"], "in_progress")
        self.assertEqual(order["status_label"], "Imaging in progress")
        self.assertFalse(order["has_result"])

    def test_a_scheduled_but_unpaid_order_still_reads_awaiting_clearance(self):
        """RADIOLOGY'S SECOND UNPAID STATE. Scheduling is a booking, not a
        payment, so a booked-but-unpaid study must not read as ready."""
        appointment, _e = self._in_consultation_visit()
        consultation = self._consultation_for(appointment)
        self._order(appointment)
        self._requests_of(consultation).sudo().action_schedule()

        self.assertEqual(self._status(appointment)["status"], "awaiting_clearance")

    def test_no_status_claims_ready_while_the_gate_would_refuse(self):
        """THE PROPERTY, ASSERTED AGAINST THE GATE ITSELF rather than against a
        label. Whatever the desk shows for an unpaid study, driving the
        authoritative transition must still refuse -- and the two must not
        disagree."""
        appointment, _e = self._in_consultation_visit()
        consultation = self._consultation_for(appointment)
        self._order(appointment)
        stored = self._requests_of(consultation)
        stored.sudo().action_schedule()

        self.assertEqual(self._status(appointment)["status"], "awaiting_clearance")
        with self.assertRaises(UserError):
            stored.sudo().action_mark_in_progress()

    def test_the_serializer_and_the_billing_layer_agree_on_the_unpaid_window(self):
        """The serializer restates hospital_billing's BILLING_BLOCKED_STATES
        rather than importing it, because the API must not import from the
        billing module. Restated constants drift, so the agreement is asserted
        here instead of trusted."""
        from odoo.addons.hospital_billing.models.radiology_billing import (
            BILLING_BLOCKED_STATES,
        )
        from odoo.addons.yoya_emr_api.services.radiology_serializers import (
            _CLEARABLE_STATES,
        )

        self.assertEqual(set(_CLEARABLE_STATES), set(BILLING_BLOCKED_STATES))

    def test_a_cancelled_order_reads_cancelled(self):
        appointment, _e = self._in_consultation_visit()
        consultation = self._consultation_for(appointment)
        self._order(appointment)
        self._requests_of(consultation).sudo().action_cancel()

        self.assertEqual(self._status(appointment)["status"], "cancelled")


@tagged("post_install", "-at_install", "doctor_radiology")
class TestRadiologyResultBoundary(RadiologyCase):
    """This slice reports THAT a study finished, never what it found."""

    def _complete_with_report(self, appointment, encounter):
        consultation = self._consultation_for(appointment)
        self._order(appointment, [self.ct_brain])
        self._settle(encounter)
        stored = self._requests_of(consultation)
        stored.sudo().action_schedule()
        stored.sudo().action_mark_in_progress()

        result = self.env["hospital.radiology.result"].sudo().create(
            {
                "request_id": stored.id,
                "patient_id": stored.patient_id.id,
                "physician_id": stored.physician_id.id,
                "findings": "Hyperdense collection in the left frontal lobe.",
                "impression": "Acute intraparenchymal haemorrhage.",
                "recommendations": "Urgent neurosurgical referral.",
                "line_ids": [
                    (0, 0, {"exam_id": self.ct_brain.id,
                            "request_line_id": stored.line_ids.id})
                ],
            }
        )
        result.sudo().action_mark_entered()
        return stored, result

    def test_a_completed_study_reports_that_a_result_exists(self):
        appointment, encounter = self._in_consultation_visit()
        stored, result = self._complete_with_report(appointment, encounter)
        result.sudo().action_validate()
        result.sudo().action_release()

        _r, payload = self._get(ORDERS % appointment.id)
        order = self._orders(payload)[0]

        self.assertEqual(order["status"], "result_available")
        self.assertEqual(order["status_label"], "Result available")
        self.assertTrue(order["has_result"])

    def test_the_report_narrative_never_reaches_the_doctor_payload(self):
        """THE SLICE BOUNDARY. Findings, impression and recommendations belong
        to the Results slice, with its own endpoint. A released report must
        change the STATUS here and nothing else."""
        appointment, encounter = self._in_consultation_visit()
        stored, result = self._complete_with_report(appointment, encounter)
        result.sudo().action_validate()
        result.sudo().action_release()

        _r, payload = self._get(ORDERS % appointment.id)
        serialized = json.dumps(payload).lower()

        for forbidden in FORBIDDEN_REPORT_KEYS:
            self.assertNotIn(
                forbidden, serialized,
                "'%s' leaked out of the Results slice" % forbidden,
            )
        self.assertNotIn("haemorrhage", serialized)
        self.assertNotIn("neurosurgical", serialized)


@tagged("post_install", "-at_install", "doctor_radiology")
class TestRadiologyDiagnosisLink(RadiologyCase):
    """The optional indication must come from THIS consultation."""

    def _diagnosis_in(self, appointment, disease=None):
        _r, payload = self._post_body(
            "/yoya-emr/api/v1/doctor/visits/%s/diagnoses" % appointment.id,
            {
                "disease_id": (disease or self.disease).id,
                "diagnosis_type": "primary",
                "request_token": uuid.uuid4().hex,
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
        """hospital.radiology.request has an onchange that copies the patient
        from a chosen diagnosis but NO constraint keeping them together, so
        without this the same patient's diagnosis from an earlier visit would
        slip straight through."""
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
            self.env["hospital.radiology.request"].sudo().create_from_consultation(
                consultation, self.ct_brain, {}, diagnosis=foreign
            )


@tagged("post_install", "-at_install", "doctor_radiology")
class TestRadiologyIdempotency(RadiologyCase):
    """A retried submission must not order -- or bill -- the same studies twice."""

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
        for the same studies, on the same encounter, for the same patient -- and
        imaging is expensive enough that the patient would notice."""
        appointment, _e = self._in_consultation_visit()
        consultation = self._consultation_for(appointment)
        token = uuid.uuid4().hex

        self._order(appointment, [self.ct_brain, self.chest_xray], request_token=token)
        self._order(appointment, [self.ct_brain, self.chest_xray], request_token=token)

        stored = self._requests_of(consultation)
        self.assertEqual(len(stored), 1)
        self.assertEqual(len(stored.sudo().charge_line_ids), 2)

    def test_a_different_token_creates_a_distinct_request(self):
        """Ordering the same study twice at different clinical moments is
        legitimate -- a follow-up film after an intervention, for instance -- so
        the token, not the exam set, is what de-duplicates."""
        appointment, _e = self._in_consultation_visit()
        consultation = self._consultation_for(appointment)

        _r1, first = self._order(appointment, request_token=uuid.uuid4().hex)
        _r2, second = self._order(appointment, request_token=uuid.uuid4().hex)

        self.assertEqual(len(self._orders(second)), 2)
        self.assertNotEqual(
            self._orders(first)[0]["id"],
            [o["id"] for o in self._orders(second) if o["id"]
             != self._orders(first)[0]["id"]][0],
        )
        self.assertEqual(len(self._requests_of(consultation)), 2)

    def test_the_same_token_in_two_consultations_stays_independent(self):
        first, _e1 = self._in_consultation_visit()
        second, _e2 = self._in_consultation_visit()
        token = uuid.uuid4().hex

        one, one_body = self._order(first, [self.ct_brain], request_token=token)
        two, two_body = self._order(second, [self.chest_xray], request_token=token)

        self.assertEqual(one.status_code, 200)
        self.assertEqual(two.status_code, 200, "the shared token was refused")
        self.assertNotEqual(
            self._orders(one_body)[0]["id"], self._orders(two_body)[0]["id"]
        )
        self.assertEqual(
            self._orders(two_body)[0]["exams"][0]["exam_id"], self.chest_xray.id
        )

    def test_the_database_refuses_a_duplicate_token_in_one_consultation(self):
        """The index is the last line of defence: the lookup above is a read,
        and two concurrent submissions could both miss it."""
        appointment, _e = self._in_consultation_visit()
        consultation = self._consultation_for(appointment)
        token = uuid.uuid4().hex
        self._order(appointment, request_token=token)

        with self.assertRaises(IntegrityError), mute_logger("odoo.sql_db"):
            with self.cr.savepoint():
                self.env.cr.execute(
                    """
                    INSERT INTO hospital_radiology_request
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


@tagged("post_install", "-at_install", "doctor_radiology")
class TestRadiologyRequestTokenContract(RadiologyCase):
    """The token is REQUIRED, and a refusal costs the patient nothing.

    WHY THIS IS ITS OWN CLASS. TestRadiologyIdempotency proves the token WORKS.
    This proves it cannot be SKIPPED -- a different property, and the one a
    client actually gets wrong. A tokenless submission is not deduplicated by
    anything: the partial unique index is `WHERE ... request_token IS NOT NULL`,
    so NULL tokens never collide, and create_from_consultation() skips its
    lookup entirely when none is supplied. Accepting one would mean a
    double-clicked Place Order raises a second full set of imaging charges with
    nothing anywhere to stop it.
    """

    def _post_raw(self, appointment, body):
        return self._post_body(ORDERS % appointment.id, body)

    def _refused(self, appointment, body):
        """Assert the shape of a refusal AND that it cost nothing."""
        consultation = self._consultation_for(appointment)
        charges_before = self.env["hospital.charge.line"].sudo().search_count(
            [("encounter_id", "=", consultation.encounter_id.id)]
        )

        response, payload = self._post_raw(appointment, body)

        self.assertEqual(response.status_code, 400, json.dumps(payload))
        self.assertEqual(payload["error"]["code"], "missing_request_token")
        # NO REQUEST, NO LINES, NO CHARGES. The check runs before the
        # consultation is resolved and long before the savepoint, so a refusal
        # cannot leave a half-placed order behind.
        self.assertFalse(
            self._requests_of(consultation),
            "a refused submission created a radiology request",
        )
        charges_after = self.env["hospital.charge.line"].sudo().search_count(
            [("encounter_id", "=", consultation.encounter_id.id)]
        )
        self.assertEqual(
            charges_after, charges_before,
            "a refused submission created a charge",
        )
        return payload

    # ------------------------------------------------------------------
    def test_a_missing_request_token_is_refused(self):
        """The key is absent entirely -- the ordinary client omission."""
        appointment, _e = self._in_consultation_visit()
        self._refused(appointment, {"exams": [self.ct_brain.id]})

    def test_a_null_request_token_is_refused(self):
        """JSON null, which is what a client sends when its own token variable
        was never populated."""
        appointment, _e = self._in_consultation_visit()
        self._refused(
            appointment, {"exams": [self.ct_brain.id], "request_token": None}
        )

    def test_a_blank_request_token_is_refused(self):
        appointment, _e = self._in_consultation_visit()
        self._refused(
            appointment, {"exams": [self.ct_brain.id], "request_token": ""}
        )

    def test_a_whitespace_only_request_token_is_refused(self):
        """Whitespace is not an identifier. It is refused rather than trimmed:
        trimming would silently merge tokens the client considers distinct."""
        appointment, _e = self._in_consultation_visit()
        self._refused(
            appointment, {"exams": [self.ct_brain.id], "request_token": "   "}
        )

    def test_a_non_string_request_token_is_a_type_error_not_a_missing_one(self):
        """Sending 42 is a different client bug from sending nothing, and one
        error code for both would send the wrong developer to the wrong line."""
        appointment, _e = self._in_consultation_visit()
        consultation = self._consultation_for(appointment)

        response, payload = self._post_raw(
            appointment, {"exams": [self.ct_brain.id], "request_token": 42}
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(payload["error"]["code"], "invalid_field")
        self.assertFalse(self._requests_of(consultation))

    def test_a_valid_token_places_the_order(self):
        appointment, _e = self._in_consultation_visit()
        consultation = self._consultation_for(appointment)

        response, payload = self._post_raw(
            appointment,
            {"exams": [self.ct_brain.id], "request_token": uuid.uuid4().hex},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(self._orders(payload)), 1)
        stored = self._requests_of(consultation)
        self.assertEqual(len(stored), 1)
        self.assertEqual(stored.state, "requested")
        self.assertEqual(len(stored.sudo().charge_line_ids), 1)

    def test_the_stored_token_is_the_one_the_client_sent(self):
        """Stored verbatim, not normalised. The server does not mint or reshape
        an opaque client identifier."""
        appointment, _e = self._in_consultation_visit()
        consultation = self._consultation_for(appointment)
        token = "Client-Token_%s" % uuid.uuid4().hex

        self._post_raw(
            appointment, {"exams": [self.ct_brain.id], "request_token": token}
        )

        self.assertEqual(self._requests_of(consultation).request_token, token)

    def test_a_retry_with_the_same_valid_token_stays_idempotent(self):
        """THE property the requirement exists to guarantee. Now that a token
        cannot be omitted, every Doctor Desk submission has this protection."""
        appointment, _e = self._in_consultation_visit()
        consultation = self._consultation_for(appointment)
        token = uuid.uuid4().hex
        body = {
            "exams": [self.ct_brain.id, self.chest_xray.id],
            "request_token": token,
        }

        first, first_body = self._post_raw(appointment, body)
        second, second_body = self._post_raw(appointment, body)

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(
            self._orders(first_body)[0]["id"], self._orders(second_body)[0]["id"]
        )
        stored = self._requests_of(consultation)
        self.assertEqual(len(stored), 1)
        # The whole point: one submission, one set of charges, however many
        # times the button was pressed.
        self.assertEqual(len(stored.sudo().charge_line_ids), 2)

    def test_the_model_still_accepts_a_tokenless_department_order(self):
        """THE CONTRACT IS THE API'S, NOT THE MODEL'S.

        hospital.radiology.request is legitimately created outside this API --
        at the imaging department, from the Odoo form, by a scheduled job --
        and those callers have no submission to identify. Forcing a token on
        them would make the column a lie and break the department. The column
        stays optional and create_from_consultation() keeps accepting None.
        """
        appointment, _e = self._in_consultation_visit()
        consultation = self._consultation_for(appointment)

        stored = self.env["hospital.radiology.request"].sudo().create_from_consultation(
            consultation, self.ct_brain, {}, request_token=None
        )

        self.assertTrue(stored)
        self.assertFalse(stored.request_token)
        self.assertEqual(stored.state, "requested")

    def test_the_column_and_the_index_were_not_tightened(self):
        """Defence against over-correcting the fix.

        A tempting 'stronger' version of this hardening makes the column
        required or the index global. Both would be wrong: a required column
        breaks every department-raised order, and a global index would let one
        client's opaque string collide with another's across unrelated episodes
        of care -- handing back ANOTHER PATIENT'S imaging request as though this
        submission had created it.
        """
        field = self.env["hospital.radiology.request"]._fields["request_token"]
        self.assertFalse(field.required, "the token column was made required")

        self.env.cr.execute(
            """
            SELECT indexdef FROM pg_indexes
            WHERE tablename = 'hospital_radiology_request'
              AND indexname = 'hospital_radiology_request_consultation_token_uniq'
            """
        )
        row = self.env.cr.fetchone()
        self.assertTrue(row, "the idempotency index is missing")
        indexdef = row[0]
        self.assertIn("consultation_id", indexdef)
        self.assertIn("WHERE", indexdef, "the index stopped being partial")


@tagged("post_install", "-at_install", "doctor_radiology")
class TestRadiologyCancellation(RadiologyCase):
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

    def test_cancelling_leaves_no_payable_charge(self):
        """THE LEAK THIS SLICE CLOSED. Before the hospital_billing override,
        this cancelled the study and left the patient owing 900 ETB for imaging
        that would never be performed."""
        appointment, _e = self._in_consultation_visit()
        consultation = self._consultation_for(appointment)
        order_id = self._place(appointment)
        stored = self._requests_of(consultation)
        self.assertTrue(self._live_payable(stored), "the fixture raised no charge")

        self._post_body(CANCEL % (appointment.id, order_id), {})

        self.assertFalse(self._live_payable(stored))
        for charge in stored.sudo().charge_line_ids:
            self.assertEqual(charge.charge_state, "cancelled")

    def test_a_scheduled_order_can_still_be_cancelled(self):
        """Radiology permits cancellation from `scheduled`, which laboratory has
        no equivalent of -- and it is the state a patient most often declines
        from, having just heard the price."""
        appointment, _e = self._in_consultation_visit()
        consultation = self._consultation_for(appointment)
        order_id = self._place(appointment)
        stored = self._requests_of(consultation)
        stored.sudo().action_schedule()

        response, payload = self._post_body(CANCEL % (appointment.id, order_id), {})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(self._orders(payload)[0]["status"], "cancelled")
        self.assertFalse(self._live_payable(stored))

    def test_cancellation_is_refused_once_imaging_is_under_way(self):
        """The base model's transition table has no in_progress -> cancelled
        edge, and the Doctor Desk does not invent one."""
        appointment, encounter = self._in_consultation_visit()
        consultation = self._consultation_for(appointment)
        order_id = self._place(appointment)
        self._settle(encounter)
        stored = self._requests_of(consultation)
        stored.sudo().action_schedule()
        stored.sudo().action_mark_in_progress()

        response, _payload = self._post_body(CANCEL % (appointment.id, order_id), {})

        self.assertGreaterEqual(response.status_code, 400)
        self.assertEqual(self._requests_of(consultation).state, "in_progress")

    def test_an_in_progress_order_is_not_offered_as_cancellable(self):
        appointment, encounter = self._in_consultation_visit()
        consultation = self._consultation_for(appointment)
        self._place(appointment)
        self._settle(encounter)
        stored = self._requests_of(consultation)
        stored.sudo().action_schedule()
        stored.sudo().action_mark_in_progress()

        _r, payload = self._get(ORDERS % appointment.id)

        self.assertFalse(self._orders(payload)[0]["cancellable"])

    def test_an_order_from_another_visit_is_not_reachable(self):
        first, _e1 = self._in_consultation_visit()
        second, _e2 = self._in_consultation_visit()
        foreign_id = self._place(second)

        response, payload = self._post_body(CANCEL % (first.id, foreign_id), {})

        self.assertEqual(response.status_code, 404)
        self.assertEqual(payload["error"]["code"], "radiology_order_not_found")

    def test_orders_are_never_editable(self):
        appointment, _e = self._in_consultation_visit()
        self._place(appointment)
        _r, payload = self._get(ORDERS % appointment.id)
        self.assertFalse(self._orders(payload)[0]["editable"])


@tagged("post_install", "-at_install", "doctor_radiology")
class TestRadiologyCompletedConsultation(RadiologyCase):
    """Slice 4's policy: completion does not wait for imaging, and does not
    strand it either."""

    CONSULTATION = "/yoya-emr/api/v1/doctor/visits/%s/consultation"
    SAVE = "/yoya-emr/api/v1/doctor/visits/%s/consultation/save"
    COMPLETE = "/yoya-emr/api/v1/doctor/visits/%s/consultation/complete"

    def _version(self, appointment):
        _r, payload = self._get(self.CONSULTATION % appointment.id)
        return payload["data"]["consultation"]["version"]

    def _completable_visit(self):
        """in_consultation + assessment + active primary diagnosis.

        Built through the REAL endpoints rather than sudo() writes: the version
        chaining and the clinical minimum are part of what completion checks,
        and a fixture that bypassed them would prove nothing about whether a
        pending study is what blocked the sign-off.
        """
        appointment, encounter = self._in_consultation_visit()
        _r, body = self._post_body(
            self.SAVE % appointment.id,
            {
                "version": self._version(appointment),
                "assessment": "Head injury, imaging requested.",
            },
        )
        self.assertTrue(body["success"], body)
        _r2, diag = self._add(appointment, diagnosis_type="primary")
        self.assertTrue(diag["success"], diag)
        return appointment, encounter

    def test_a_pending_study_does_not_block_consultation_completion(self):
        """THE POLICY, ASSERTED. Radiology routinely outlives the consultation
        that ordered it; making the doctor wait for a scan before signing would
        hold a clinician at a desk for an hour."""
        appointment, _e = self._completable_visit()
        consultation = self._consultation_for(appointment)
        self._order(appointment)
        stored = self._requests_of(consultation)
        self.assertEqual(stored.state, "requested", "the fixture placed no order")

        response, payload = self._post_body(
            self.COMPLETE % appointment.id, {"version": self._version(appointment)}
        )

        self.assertEqual(
            response.status_code, 200,
            "a pending study blocked sign-off: %s" % json.dumps(payload),
        )
        consultation.invalidate_recordset()
        self.assertEqual(consultation.state, "completed")
        # And the study is untouched by the sign-off.
        stored.invalidate_recordset()
        self.assertEqual(stored.state, "requested")

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

    def test_the_doctor_cannot_cancel_after_completion(self):
        appointment, _e = self._in_consultation_visit()
        consultation = self._consultation_for(appointment)
        _r, payload = self._order(appointment)
        order_id = self._orders(payload)[0]["id"]
        consultation.write({"state": "completed"})

        response, body = self._post_body(CANCEL % (appointment.id, order_id), {})

        self.assertEqual(response.status_code, 409)
        self.assertEqual(body["error"]["code"], "consultation_completed")
        self.assertEqual(self._requests_of(consultation).state, "requested")

    def test_the_desk_stops_offering_cancel_after_completion(self):
        appointment, _e = self._in_consultation_visit()
        self._order(appointment)
        self._consultation_for(appointment).write({"state": "completed"})
        appointment.sudo().action_done()
        appointment.invalidate_recordset()

        _r, payload = self._get(ORDERS % appointment.id)

        self.assertFalse(self._orders(payload)[0]["cancellable"])

    def test_imaging_continues_after_the_visit_is_done(self):
        """THE OTHER HALF OF THE POLICY. The department must still be able to
        schedule, clear and perform the study once the doctor has signed off --
        that is the ordinary outpatient shape."""
        appointment, encounter = self._in_consultation_visit()
        consultation = self._consultation_for(appointment)
        self._order(appointment)
        stored = self._requests_of(consultation)

        consultation.write({"state": "completed"})
        appointment.sudo().action_done()
        encounter.invalidate_recordset()

        # The patient pays at the window after leaving the consulting room.
        self._settle(encounter)
        stored.sudo().action_schedule()
        stored.sudo().action_mark_in_progress()

        self.assertEqual(stored.state, "in_progress")

    def test_a_completed_encounter_does_not_block_the_imaging_gate(self):
        """`completed` is not in LOCKED_ENCOUNTER_STATES and the encounter's
        conditional action_start() safely skips it, so the study still clears
        and starts after the whole visit has been closed."""
        appointment, encounter = self._in_consultation_visit()
        consultation = self._consultation_for(appointment)
        self._order(appointment)
        stored = self._requests_of(consultation)
        self._settle(encounter)

        consultation.write({"state": "completed"})
        appointment.sudo().action_done()
        encounter.invalidate_recordset()

        stored.sudo().action_schedule()
        stored.sudo().action_mark_in_progress()

        self.assertEqual(stored.state, "in_progress")


@tagged("post_install", "-at_install", "doctor_radiology")
class TestRadiologyCashierHandoff(RadiologyCase):
    """The generic SERVICE PAYMENTS lane, with no radiology-specific code."""

    WORKLIST = "/yoya-emr/api/v1/cashier/worklist"

    def _lane_rows(self, appointment):
        """The SERVICE PAYMENTS lane, read exactly as the cashier tests read it.

        The worklist answers with two named lanes side by side; the active one
        is `active_service_clearance`. No parameter selects it, and none should:
        a cashier sees both queues at once.
        """
        self._auth(self.cashier, self.cashier_password)
        payload = json.loads(self.url_open(self.WORKLIST).text)
        self.assertTrue(payload.get("success"), payload)
        rows = payload["data"]["active_service_clearance"]
        return payload, [r for r in rows if r["appointment_id"] == appointment.id]

    def test_an_unpaid_study_appears_in_service_payments(self):
        appointment, _e = self._in_consultation_visit()
        self._order(appointment)

        _payload, rows = self._lane_rows(appointment)

        self.assertEqual(len(rows), 1, "the unpaid study did not reach the cashier")
        categories = {c["key"] for c in rows[0]["service_categories"]}
        self.assertIn("radiology", categories)
        self.assertIn(
            "Radiology", {c["label"] for c in rows[0]["service_categories"]}
        )

    def test_it_stays_there_after_the_consultation_is_completed(self):
        """The ordinary outpatient shape: the doctor signs off, and the patient
        walks to the cashier and then to imaging."""
        appointment, _e = self._in_consultation_visit()
        self._order(appointment)
        self._consultation_for(appointment).write({"state": "completed"})
        appointment.sudo().action_done()
        appointment.invalidate_recordset()

        _payload, rows = self._lane_rows(appointment)

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["visit_state"], "done")

    def test_payment_removes_it_from_the_lane(self):
        appointment, encounter = self._in_consultation_visit()
        self._order(appointment)
        _p, before = self._lane_rows(appointment)
        self.assertEqual(len(before), 1)

        self._settle(encounter)

        _p2, after = self._lane_rows(appointment)
        self.assertFalse(after, "a settled visit stayed in SERVICE PAYMENTS")

    def test_cancelling_the_study_removes_it_from_the_lane(self):
        """The other way the lane empties, and the one the charge leak broke:
        the patient declines the scan, so there is nothing left to collect."""
        appointment, _e = self._in_consultation_visit()
        _r, payload = self._order(appointment)
        order_id = self._orders(payload)[0]["id"]
        _p, before = self._lane_rows(appointment)
        self.assertEqual(len(before), 1)

        self._post_body(CANCEL % (appointment.id, order_id), {})

        _p2, after = self._lane_rows(appointment)
        self.assertFalse(
            after,
            "a cancelled study left the patient stranded in SERVICE PAYMENTS",
        )

    def test_no_clinical_detail_reaches_the_cashier(self):
        """The cashier is told a radiology service is unpaid. They are NOT told
        which study, which body part, or why it was ordered."""
        appointment, _e = self._in_consultation_visit()
        self._order(
            appointment,
            [self.ct_brain],
            clinical_indication="Rule out intracranial haemorrhage.",
            instructions="Sedation may be required.",
        )

        payload, rows = self._lane_rows(appointment)
        self.assertEqual(len(rows), 1)

        serialized = json.dumps(payload).lower()
        self.assertNotIn("haemorrhage", serialized)
        self.assertNotIn("sedation", serialized)
        self.assertNotIn(self.ct_brain.name.lower(), serialized)
        self.assertNotIn("head", serialized)


@tagged("post_install", "-at_install", "doctor_radiology")
class TestRadiologyAccess(RadiologyCase):
    """Scope, which the radiology models shipped WITHOUT ENTIRELY."""

    def test_a_pure_doctor_cannot_order_through_another_doctors_visit(self):
        appointment, _e = self._in_consultation_visit(doctor=self.other_doctor)

        response, payload = self._post_body(
            ORDERS % appointment.id,
            {"exams": [self.ct_brain.id], "request_token": uuid.uuid4().hex},
            user=self.doctor_user,
            password=self.doctor_password,
        )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(payload["error"]["code"], "visit_not_found")

    def test_the_record_rule_hides_another_doctors_request_at_the_orm(self):
        """NOT MERELY CONTROLLER-DEEP. Before this slice there was no ir.rule on
        any radiology model anywhere, so an RPC caller holding the shipped
        doctor ACL read every imaging request in the hospital."""
        appointment, _e = self._in_consultation_visit(doctor=self.other_doctor)
        self._post_body(
            ORDERS % appointment.id,
            {"exams": [self.ct_brain.id], "request_token": uuid.uuid4().hex},
            user=self.other_user,
            password=self.other_password,
        )
        stored = self._requests_of(self._consultation_for(appointment))
        self.assertTrue(stored)

        visible = (
            self.env["hospital.radiology.request"]
            .with_user(self.doctor_user)
            .search([("id", "=", stored.id)])
        )

        self.assertFalse(visible)

    def test_a_doctor_cannot_mutate_another_doctors_request(self):
        """The shipped ACL grants the doctor group WRITE on this model, so
        without the rule a stranger could have re-prioritised, re-indicated or
        cancelled somebody else's imaging order."""
        appointment, _e = self._in_consultation_visit(doctor=self.other_doctor)
        self._post_body(
            ORDERS % appointment.id,
            {"exams": [self.ct_brain.id], "request_token": uuid.uuid4().hex},
            user=self.other_user,
            password=self.other_password,
        )
        stored = self._requests_of(self._consultation_for(appointment))

        with self.assertRaises(AccessError):
            stored.with_user(self.doctor_user).write({"priority": "stat"})

    def test_a_doctor_cannot_cancel_another_doctors_request_at_the_orm(self):
        appointment, _e = self._in_consultation_visit(doctor=self.other_doctor)
        self._post_body(
            ORDERS % appointment.id,
            {"exams": [self.ct_brain.id], "request_token": uuid.uuid4().hex},
            user=self.other_user,
            password=self.other_password,
        )
        stored = self._requests_of(self._consultation_for(appointment))

        with self.assertRaises(AccessError):
            stored.with_user(self.doctor_user).action_cancel()
        self.assertEqual(stored.state, "requested")

    def test_the_record_rule_hides_another_doctors_request_lines(self):
        appointment, _e = self._in_consultation_visit(doctor=self.other_doctor)
        self._post_body(
            ORDERS % appointment.id,
            {"exams": [self.ct_brain.id], "request_token": uuid.uuid4().hex},
            user=self.other_user,
            password=self.other_password,
        )
        lines = self._requests_of(self._consultation_for(appointment)).line_ids

        visible = (
            self.env["hospital.radiology.request.line"]
            .with_user(self.doctor_user)
            .search([("id", "in", lines.ids)])
        )

        self.assertFalse(visible)

    def test_the_ordering_doctor_reaches_their_own_request(self):
        appointment, _e = self._in_consultation_visit(doctor=self.doctor)
        self._order(appointment)
        stored = self._requests_of(self._consultation_for(appointment))

        visible = (
            self.env["hospital.radiology.request"]
            .with_user(self.doctor_user)
            .search([("id", "=", stored.id)])
        )

        self.assertEqual(visible, stored)

    def test_a_doctor_cannot_read_another_doctors_result(self):
        """A radiology report is the most confidential thing this module
        touches, and it had no rule at all before this slice."""
        appointment, encounter = self._in_consultation_visit(doctor=self.other_doctor)
        self._post_body(
            ORDERS % appointment.id,
            {"exams": [self.ct_brain.id], "request_token": uuid.uuid4().hex},
            user=self.other_user,
            password=self.other_password,
        )
        stored = self._requests_of(self._consultation_for(appointment))
        result = self.env["hospital.radiology.result"].sudo().create(
            {
                "request_id": stored.id,
                "patient_id": stored.patient_id.id,
                "physician_id": stored.physician_id.id,
                "impression": "Confidential.",
            }
        )

        visible = (
            self.env["hospital.radiology.result"]
            .with_user(self.doctor_user)
            .search([("id", "=", result.id)])
        )

        self.assertFalse(visible)

    def test_the_referring_doctor_reaches_their_own_result(self):
        appointment, _e = self._in_consultation_visit(doctor=self.doctor)
        self._order(appointment)
        stored = self._requests_of(self._consultation_for(appointment))
        result = self.env["hospital.radiology.result"].sudo().create(
            {
                "request_id": stored.id,
                "patient_id": stored.patient_id.id,
                "physician_id": stored.physician_id.id,
            }
        )

        visible = (
            self.env["hospital.radiology.result"]
            .with_user(self.doctor_user)
            .search([("id", "=", result.id)])
        )

        self.assertEqual(visible, result)

    def test_a_doctor_holds_no_write_access_to_results(self):
        """Reporting, validating and releasing belong to the imaging
        department, and the shipped ACL already says so. Pinned here because
        this slice is the one that gives doctors a reason to be near these
        models."""
        appointment, _e = self._in_consultation_visit(doctor=self.doctor)
        self._order(appointment)
        stored = self._requests_of(self._consultation_for(appointment))

        model = self.env["hospital.radiology.result"].with_user(self.doctor_user)
        with self.assertRaises(AccessError):
            model.create(
                {
                    "request_id": stored.id,
                    "patient_id": stored.patient_id.id,
                }
            )

    def test_the_imaging_bench_still_sees_every_request(self):
        """THE regression a doctor-scoped rule most easily causes. The
        department works a cross-patient queue; scoping it would break imaging.
        group_hospital_lab_technician is the operational radiology role in this
        repository -- there is no radiographer group."""
        bench = self._make_user(
            "rad_tech", "rad-pw-1",
            ["hospital_management.group_hospital_lab_technician"],
        )
        appointment, _e = self._in_consultation_visit(doctor=self.doctor)
        self._order(appointment)
        stored = self._requests_of(self._consultation_for(appointment))

        self.assertEqual(
            self.env["hospital.radiology.request"]
            .with_user(bench)
            .search([("id", "=", stored.id)]),
            stored,
        )
        self.assertEqual(
            self.env["hospital.radiology.request.line"]
            .with_user(bench)
            .search([("id", "in", stored.line_ids.ids)]),
            stored.line_ids,
        )

    def test_the_bench_can_still_drive_the_workflow(self):
        """Read is not enough: the department has to be able to schedule and
        start the study it has been asked to do."""
        bench = self._make_user(
            "rad_tech_w", "rad-pw-2",
            ["hospital_management.group_hospital_lab_technician"],
        )
        appointment, encounter = self._in_consultation_visit(doctor=self.doctor)
        self._order(appointment)
        self._settle(encounter)
        stored = self._requests_of(self._consultation_for(appointment))

        stored.with_user(bench).action_schedule()
        stored.with_user(bench).action_mark_in_progress()

        self.assertEqual(stored.state, "in_progress")

    def test_the_manager_keeps_full_visibility(self):
        """Manager IMPLIES Doctor, so without an explicit unrestricted rule the
        doctor scope would silently have become a manager's ceiling."""
        appointment, _e = self._in_consultation_visit(doctor=self.other_doctor)
        self._post_body(
            ORDERS % appointment.id,
            {"exams": [self.ct_brain.id], "request_token": uuid.uuid4().hex},
            user=self.other_user,
            password=self.other_password,
        )
        stored = self._requests_of(self._consultation_for(appointment))

        visible = (
            self.env["hospital.radiology.request"]
            .with_user(self.manager)
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
                {"exams": [self.ct_brain.id], "request_token": uuid.uuid4().hex},
                user=user,
                password=password,
            )
            self.assertEqual(response.status_code, 403, user.login)


@tagged("post_install", "-at_install", "doctor_radiology")
class TestRadiologyTransactionBoundary(RadiologyCase):
    """One doctor action is one transaction -- charges included."""

    def test_response_failure_rolls_back_the_request_and_its_charges(self):
        appointment, _e = self._in_consultation_visit()
        consultation = self._consultation_for(appointment)
        charges_before = self.env["hospital.charge.line"].sudo().search_count(
            [("encounter_id", "=", consultation.encounter_id.id)]
        )

        with patch(
            RAD_SERIALIZER_TARGET,
            side_effect=AccessError("simulated post-write failure"),
        ):
            response, payload = self._order(
                appointment, [self.ct_brain, self.chest_xray]
            )

        self.assertEqual(response.status_code, 500)
        self.assertEqual(payload["error"]["code"], "radiology_response_failed")
        self.assertNotEqual(payload["error"]["code"], "access_denied")
        serialized = json.dumps(payload)
        self.assertNotIn("Traceback", serialized)
        self.assertNotIn("simulated post-write failure", serialized)

        self.assertFalse(
            self._requests_of(consultation),
            "the radiology request survived a rolled-back order",
        )
        charges_after = self.env["hospital.charge.line"].sudo().search_count(
            [("encounter_id", "=", consultation.encounter_id.id)]
        )
        self.assertEqual(
            charges_after, charges_before, "charges survived a rolled-back order"
        )

    def test_the_retry_after_a_rollback_succeeds(self):
        appointment, _e = self._in_consultation_visit()
        consultation = self._consultation_for(appointment)

        with patch(
            RAD_SERIALIZER_TARGET,
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
            RAD_SERIALIZER_TARGET,
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
            RAD_SERIALIZER_TARGET,
            side_effect=AccessError("simulated post-write failure"),
        ):
            response, _body = self._post_body(
                CANCEL % (appointment.id, order_id), {}
            )

        self.assertEqual(response.status_code, 500)
        stored = self._requests_of(consultation)
        self.assertEqual(
            stored.state, "requested", "the cancellation survived its rollback"
        )
        # And the charges must be live again, or the patient would owe nothing
        # for a study the system still believes is ordered.
        self.assertTrue(self._live_payable(stored))
