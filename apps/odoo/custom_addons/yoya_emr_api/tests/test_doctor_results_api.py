"""Slice 7B: reviewing released laboratory and radiology findings.

WHAT THESE TESTS ARE FOR
------------------------
Four properties carry this slice.

  1. RELEASED, AND NOTHING ELSE. Draft, entered, VALIDATED and cancelled
     results must not reach a doctor. Validated is the one that matters: it
     LOOKS final (laboratory freezes content there, and the charge is already
     delivered) but the laboratory holds it back on purpose, and a validated
     RADIOLOGY report is still fully editable. Publishing either would show a
     clinician a finding nobody has handed off.

  2. AVAILABILITY IS THE RESULT'S STATE, NEVER THE REQUEST'S. A released result
     can sit on a request still marked in_progress -- legacy result lines carry
     a NULL request_line_id, so laboratory completion can never match them and
     the request never reaches 'completed'. Deriving availability from the
     request would hide real released findings; a test below pins exactly that
     shape.

  3. THE DESK REVIEWS AND CANNOT WRITE. No acknowledge, no reviewed flag, no
     sign-off, and no verb but GET on the route.

  4. THE PAYLOAD IS CLINICAL. Results are read next to a billing workflow that
     raises charges at validation, which is precisely why no amount, payer or
     receipt may appear in what the desk receives.

FIXTURES DRIVE STATE, NOT THE DEPARTMENT WORKFLOW, AND THAT IS DELIBERATE.
The results below are moved draft -> entered -> validated -> released by
writing `state`. Laboratory's own _STATE_TRANSITIONS whitelist and its
@api.constrains('state') completeness gate both run on that path, so nothing
illegal is forged: a result still cannot reach 'entered' without a value on
every ordered line. What is bypassed is hospital_billing's action_validate /
action_release OVERRIDES, which deliver charges and re-check financial
clearance -- a whole financial apparatus these tests have no business
exercising to ask what a GET returns. Slice 7B changes no workflow, and
nothing here asserts one.
"""
import json
import uuid

from odoo.tests import tagged

from .test_doctor_laboratory_api import LaboratoryCase

RESULTS = "/yoya-emr/api/v1/doctor/visits/%s/results"

# Money and payer vocabulary, matched RECURSIVELY against the whole payload --
# every key and every string value, at any depth. A flat key check would miss a
# sum embedded in a message, which is exactly how a figure reaches a clinician.
FORBIDDEN = (
    "amount", "balance", "outstanding", "paid", "receipt", "sponsor",
    "agreement", "membership", "payer", "tariff", "price", "invoice",
    "charge", "credit_limit", "coverage", "billing_service", "default_price",
    "prepayment", "journal", "fiscal", "stock", "quant", "login", "email",
)


class ResultsCase(LaboratoryCase):
    """LaboratoryCase plus a billable imaging catalogue and result builders."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        tag = uuid.uuid4().hex[:6]
        cls.chest_xray = cls._make_exam("Chest X-Ray %s" % tag, "CXR%s" % tag.upper(), "xray")
        cls.head_ct = cls._make_exam("Head CT %s" % tag, "HCT%s" % tag.upper(), "ct")

    @classmethod
    def _make_exam(cls, name, code, modality):
        service = cls.env["hospital.billing.service"].sudo().create(
            {
                "name": "%s Service" % name,
                "code": "T-RAD-%s" % code,
                "service_type": "radiology",
                "default_price": 400.0,
                "company_id": cls.env.company.id,
                "currency_id": cls.env.company.currency_id.id,
                "uom_id": cls.uom.id,
                "prepayment_required": False,
                "tax_treatment": "exempt",
            }
        )
        return cls.env["hospital.radiology.exam"].sudo().create(
            {
                "name": name,
                "code": code,
                "modality": modality,
                "body_part": "Chest",
                "billing_service_id": service.id,
            }
        )

    # ------------------------------------------------------------------
    # Requests
    # ------------------------------------------------------------------
    def _lab_request(self, appointment, tests=None):
        """Place a laboratory order through the real endpoint."""
        response, payload = self._order(appointment, tests=tests or [self.cbc])
        self.assertEqual(response.status_code, 200, payload)
        request_id = payload["data"]["orders"][0]["id"]
        return self.env["hospital.laboratory.request"].sudo().browse(request_id)

    def _rad_request(self, appointment, exams=None):
        """Place a radiology order through the real endpoint."""
        body = {
            "exams": [e.id for e in (exams or [self.chest_xray])],
            "request_token": uuid.uuid4().hex,
        }
        response, payload = self._post_body(
            "/yoya-emr/api/v1/doctor/visits/%s/orders/radiology" % appointment.id,
            body,
        )
        self.assertEqual(response.status_code, 200, payload)
        request_id = payload["data"]["orders"][0]["id"]
        return self.env["hospital.radiology.request"].sudo().browse(request_id)

    # ------------------------------------------------------------------
    # Results
    # ------------------------------------------------------------------
    def _lab_result(self, request, state="released", values=None, **header):
        """A laboratory result on `request`, advanced to `state`.

        The request is walked to sample_collected first, because
        _check_request_state_eligible refuses a result against a request whose
        sample does not yet exist. Values default to a plain normal number so
        the completeness constraint is satisfied for every ordered test.
        """
        if request.state == "requested":
            request.sudo().write({"state": "sample_collected"})
        result = self.env["hospital.laboratory.result"].sudo().create(
            dict({"request_id": request.id}, **header)
        )
        for index, line in enumerate(result.line_ids):
            line.sudo().write(
                values[index] if values else {"result_value": "5.0"}
            )
        for step in ("entered", "validated", "released"):
            if state == "draft":
                break
            result.sudo().write({"state": step})
            if step == state:
                break
        return result

    def _rad_result(self, request, state="released", **header):
        """A radiology report on `request`, advanced to `state`."""
        result = self.env["hospital.radiology.result"].sudo().create(
            dict({"request_id": request.id}, **header)
        )
        for step in ("entered", "validated", "released"):
            if state == "draft":
                break
            result.sudo().write({"state": step})
            if step == state:
                break
        return result

    def _strip_linkage(self, result):
        """Null request_line_id the way a row predating the column carries it.

        DONE IN SQL BECAUSE THE ORM CAN NO LONGER PRODUCE THIS SHAPE, and that
        is precisely why the fallback under test exists. Result lines freeze at
        validation, and @api.constrains('state') refuses to release a result
        whose lines are not all linked -- so a released result with a NULL
        request_line_id cannot be created through the model today. The rows
        that carry NULL predate both rules and are still live in the database
        (the Slice 7 inspection found LABRES0001 among them), so the serializer
        has to handle them. Reproducing the shape is the only way to test that.
        """
        self.env.cr.execute(
            "UPDATE hospital_laboratory_result_line SET request_line_id = NULL "
            "WHERE result_id = %s",
            (result.id,),
        )
        result.line_ids.invalidate_recordset(["request_line_id"])

    def _raw_lab_request(self, appointment, encounter, tests):
        """A laboratory request built directly, so it can order the SAME test
        twice.

        The ordering ENDPOINT de-duplicates, which is correct for a doctor
        placing an order and is why this bypasses it: the ambiguity the
        serializer must refuse to guess at is a real historical shape, not one
        the current desk can create. Built through the ORM with the
        consultation references the bridge's own constraint demands, so nothing
        illegal is forged.
        """
        consultation = self._consultation_of(encounter)
        request = self.env["hospital.laboratory.request"].sudo().create({
            "patient_id": appointment.patient_id.id,
            "physician_id": appointment.doctor_id.id,
            "appointment_id": appointment.id,
            "encounter_id": encounter.id,
            "consultation_id": consultation.id,
            "line_ids": [
                (0, 0, {"test_id": test.id, "sample_type": "blood"})
                for test in tests
            ],
        })
        request.write({"state": "requested"})
        return request

    # ------------------------------------------------------------------
    def _results(self, appointment, user=None, password=None):
        response, payload = self._get(
            RESULTS % appointment.id, user=user, password=password
        )
        return response, payload

    def _lab_rows(self, payload):
        return payload["data"]["laboratory"]

    def _rad_rows(self, payload):
        return payload["data"]["radiology"]


def _walk(node):
    """Every key and every string value in a payload, at any depth."""
    if isinstance(node, dict):
        for key, value in node.items():
            yield str(key)
            yield from _walk(value)
    elif isinstance(node, list):
        for item in node:
            yield from _walk(item)
    elif isinstance(node, str):
        yield node


# ======================================================================
# Released-only: the rule the whole slice exists to keep
# ======================================================================
@tagged("post_install", "-at_install", "doctor_results")
class TestResultsReleasedOnly(ResultsCase):
    def test_own_released_laboratory_result_is_visible(self):
        appointment, _ = self._in_consultation_visit()
        request = self._lab_request(appointment)
        self._lab_result(request)

        _, payload = self._results(appointment)
        row = self._lab_rows(payload)[0]
        self.assertEqual(row["status"], "available")
        self.assertEqual(row["status_label"], "Result available")
        self.assertIsNotNone(row["result"])
        self.assertTrue(row["result"]["lines"])

    def test_a_draft_laboratory_result_is_not_shown(self):
        appointment, _ = self._in_consultation_visit()
        request = self._lab_request(appointment)
        self._lab_result(request, state="draft")

        _, payload = self._results(appointment)
        row = self._lab_rows(payload)[0]
        self.assertEqual(row["status"], "pending")
        self.assertIsNone(row["result"])

    def test_an_entered_laboratory_result_is_not_shown(self):
        appointment, _ = self._in_consultation_visit()
        request = self._lab_request(appointment)
        self._lab_result(request, state="entered")

        _, payload = self._results(appointment)
        row = self._lab_rows(payload)[0]
        self.assertEqual(row["status"], "pending")
        self.assertIsNone(row["result"])

    def test_a_VALIDATED_laboratory_result_is_not_shown(self):
        """THE tempting mistake, pinned.

        Laboratory freezes content at validation and hospital_billing has
        already delivered the charge, so a validated result looks finished.
        Release is the clinical handoff, and the bench holds results back
        between the two on purpose.
        """
        appointment, _ = self._in_consultation_visit()
        request = self._lab_request(appointment)
        result = self._lab_result(request, state="validated")
        self.assertEqual(result.state, "validated")

        _, payload = self._results(appointment)
        row = self._lab_rows(payload)[0]
        self.assertEqual(row["status"], "pending")
        self.assertIsNone(row["result"])

    def test_own_released_radiology_report_is_visible(self):
        appointment, _ = self._in_consultation_visit()
        request = self._rad_request(appointment)
        self._rad_result(
            request,
            findings="Clear lung fields.",
            impression="No acute cardiopulmonary process.",
        )

        _, payload = self._results(appointment)
        row = self._rad_rows(payload)[0]
        self.assertEqual(row["status"], "available")
        self.assertEqual(row["result"]["impression"], "No acute cardiopulmonary process.")
        self.assertTrue(row["result"]["has_report"])

    def test_a_VALIDATED_radiology_report_is_not_shown(self):
        """Stronger than laboratory: radiology's write() freezes only at
        'released', so a validated report is STILL EDITABLE. Publishing one
        would show a paragraph the radiologist may be rewriting."""
        appointment, _ = self._in_consultation_visit()
        request = self._rad_request(appointment)
        result = self._rad_result(request, state="validated", impression="Draft wording")
        self.assertEqual(result.state, "validated")

        _, payload = self._results(appointment)
        row = self._rad_rows(payload)[0]
        self.assertEqual(row["status"], "pending")
        self.assertIsNone(row["result"])

    def test_no_unreleased_narrative_leaks_anywhere_in_the_payload(self):
        """Not merely absent from `result`: absent from the whole response."""
        appointment, _ = self._in_consultation_visit()
        request = self._rad_request(appointment)
        self._rad_result(
            request, state="validated", impression="SECRETUNRELEASEDIMPRESSION"
        )

        _, payload = self._results(appointment)
        self.assertNotIn("SECRETUNRELEASEDIMPRESSION", json.dumps(payload))


# ======================================================================
# Availability is the RESULT's state, never the request's
# ======================================================================
@tagged("post_install", "-at_install", "doctor_results")
class TestResultsAvailabilityPredicate(ResultsCase):
    def test_released_result_is_available_while_the_request_is_still_in_progress(self):
        """The legacy shape, reproduced exactly.

        A result line with a NULL request_line_id can never satisfy laboratory
        completion, which matches ordered lines to result lines through that
        column -- so the request stays in_progress forever while a genuinely
        released result sits on it. Reading availability off request.state
        would hide it.
        """
        appointment, _ = self._in_consultation_visit()
        request = self._lab_request(appointment)
        result = self._lab_result(request)
        # Strip the linkage the way a pre-column row carries it, and put the
        # request back where such a request really sits.
        self._strip_linkage(result)
        request.sudo().write({"state": "in_progress"})
        request.invalidate_recordset()
        self.assertEqual(request.state, "in_progress")

        _, payload = self._results(appointment)
        row = self._lab_rows(payload)[0]
        self.assertEqual(row["workflow_status"], "in_progress")
        self.assertEqual(
            row["status"],
            "available",
            "a released result was hidden because its request never completed",
        )
        self.assertIsNotNone(row["result"])

    def test_a_pending_laboratory_request_reports_its_ordered_tests(self):
        appointment, _ = self._in_consultation_visit()
        self._lab_request(appointment)

        _, payload = self._results(appointment)
        row = self._lab_rows(payload)[0]
        self.assertEqual(row["status"], "pending")
        self.assertIsNone(row["result"])
        self.assertEqual(
            [test["name"] for test in row["pending_tests"]], [self.cbc.name]
        )

    def test_a_pending_radiology_request_reports_its_ordered_exams(self):
        appointment, _ = self._in_consultation_visit()
        self._rad_request(appointment)

        _, payload = self._results(appointment)
        row = self._rad_rows(payload)[0]
        self.assertEqual(row["status"], "pending")
        self.assertIsNone(row["result"])
        self.assertEqual(
            [exam["name"] for exam in row["pending_exams"]], [self.chest_xray.name]
        )
        self.assertEqual(row["pending_exams"][0]["modality"], "xray")
        self.assertEqual(row["pending_exams"][0]["modality_label"], "X-Ray")

    def test_a_cancelled_laboratory_request_reports_cancelled_and_no_content(self):
        appointment, _ = self._in_consultation_visit()
        request = self._lab_request(appointment)
        request.sudo().action_cancel()

        _, payload = self._results(appointment)
        row = self._lab_rows(payload)[0]
        self.assertEqual(row["status"], "cancelled")
        self.assertEqual(row["status_label"], "Cancelled")
        self.assertIsNone(row["result"])

    def test_cancelled_radiology_request_lines_are_excluded_from_pending(self):
        """A cancelled study is not work still to come. Laboratory request
        lines have no such state; radiology's do."""
        appointment, _ = self._in_consultation_visit()
        request = self._rad_request(
            appointment, exams=[self.chest_xray, self.head_ct]
        )
        cancelled = request.line_ids.filtered(
            lambda line: line.exam_id == self.head_ct
        )
        cancelled.sudo().write({"state": "cancelled"})

        _, payload = self._results(appointment)
        row = self._rad_rows(payload)[0]
        names = [exam["name"] for exam in row["pending_exams"]]
        self.assertIn(self.chest_xray.name, names)
        self.assertNotIn(self.head_ct.name, names)


# ======================================================================
# Result content
# ======================================================================
@tagged("post_install", "-at_install", "doctor_results")
class TestResultContent(ResultsCase):
    def test_laboratory_values_travel_verbatim(self):
        """Char in, identical Char out. Nothing parses, rounds or re-ranges a
        clinical value, and a client that did would be a second opinion with no
        clinician behind it."""
        appointment, _ = self._in_consultation_visit()
        request = self._lab_request(appointment)
        self._lab_result(
            request,
            values=[{
                "result_value": "< 0.01",
                "unit": "mg/dL",
                "reference_range": "70/100",
            }],
        )

        _, payload = self._results(appointment)
        line = self._lab_rows(payload)[0]["result"]["lines"][0]
        self.assertEqual(line["value"], "< 0.01")
        self.assertEqual(line["reference_range"], "70/100")
        self.assertEqual(line["unit"], "mg/dL")

    def test_a_qualitative_value_is_not_coerced(self):
        appointment, _ = self._in_consultation_visit()
        request = self._lab_request(appointment)
        self._lab_result(request, values=[{"result_value": "Negative"}])

        _, payload = self._results(appointment)
        line = self._lab_rows(payload)[0]["result"]["lines"][0]
        self.assertEqual(line["value"], "Negative")
        self.assertIsNone(line["unit"])
        self.assertIsNone(line["reference_range"])

    def test_the_abnormal_flag_carries_its_key_and_the_backend_label(self):
        """Both, so the client never invents wording for a clinical judgement
        and never has to recompute the judgement itself."""
        appointment, _ = self._in_consultation_visit()
        request = self._lab_request(appointment)
        self._lab_result(
            request,
            values=[{"result_value": "18.2", "abnormal_flag": "critical"}],
        )

        _, payload = self._results(appointment)
        line = self._lab_rows(payload)[0]["result"]["lines"][0]
        self.assertEqual(line["abnormal_flag"], "critical")
        self.assertEqual(line["abnormal_flag_label"], "Critical")

    def test_every_abnormal_flag_the_model_defines_has_a_label(self):
        """A flag added to the model must surface as a failing test, not as a
        silently blank cell next to a clinical value."""
        from odoo.addons.yoya_emr_api.services.result_serializers import (
            ABNORMAL_FLAG_LABELS,
        )
        field = self.env["hospital.laboratory.result.line"]._fields["abnormal_flag"]
        keys = {key for key, _label in field.selection}
        self.assertEqual(keys, set(ABNORMAL_FLAG_LABELS))

    def test_every_modality_the_model_defines_has_a_label(self):
        from odoo.addons.yoya_emr_api.services.result_serializers import (
            MODALITY_LABELS,
        )
        field = self.env["hospital.radiology.exam"]._fields["modality"]
        keys = {key for key, _label in field.selection}
        self.assertEqual(keys, set(MODALITY_LABELS))

    def test_an_empty_released_radiology_report_says_so(self):
        """A REACHABLE state: radiology has no completeness constraint, so a
        report can be released with no findings and no impression. A card
        rendering that as blank space reads as a broken screen."""
        appointment, _ = self._in_consultation_visit()
        request = self._rad_request(appointment)
        result = self._rad_result(request)
        self.assertEqual(result.state, "released")

        _, payload = self._results(appointment)
        row = self._rad_rows(payload)[0]
        self.assertEqual(row["status"], "available")
        self.assertIsNotNone(row["result"])
        self.assertFalse(row["result"]["has_report"])
        self.assertIsNone(row["result"]["findings"])
        self.assertIsNone(row["result"]["impression"])

    def test_a_line_summary_alone_still_counts_as_a_report(self):
        appointment, _ = self._in_consultation_visit()
        request = self._rad_request(appointment)
        result = self._rad_result(request)
        result.sudo().line_ids.write({"result_summary": "Normal study."})

        _, payload = self._results(appointment)
        self.assertTrue(self._rad_rows(payload)[0]["result"]["has_report"])

    def test_whitespace_only_narrative_is_not_a_report(self):
        appointment, _ = self._in_consultation_visit()
        request = self._rad_request(appointment)
        self._rad_result(request, findings="   \n  ", impression="")

        _, payload = self._results(appointment)
        result = self._rad_rows(payload)[0]["result"]
        self.assertFalse(result["has_report"])
        self.assertIsNone(result["findings"])

    def test_the_radiologist_is_a_display_name_and_nothing_else(self):
        appointment, _ = self._in_consultation_visit()
        request = self._rad_request(appointment)
        self._rad_result(request, impression="Normal.")

        _, payload = self._results(appointment)
        result = self._rad_rows(payload)[0]["result"]
        self.assertIsInstance(result["radiologist"], str)
        blob = json.dumps(result)
        self.assertNotIn("radiologist_id", blob)
        self.assertNotIn("@", blob)

    def test_the_laboratory_technician_is_not_serialized_at_all(self):
        appointment, _ = self._in_consultation_visit()
        request = self._lab_request(appointment)
        self._lab_result(request)

        _, payload = self._results(appointment)
        blob = json.dumps(payload)
        self.assertNotIn("technician", blob)
        self.assertNotIn("lab_technician_id", blob)


# ======================================================================
# Ordered-test linkage
# ======================================================================
@tagged("post_install", "-at_install", "doctor_results")
class TestResultLinkage(ResultsCase):
    def test_a_linked_result_line_reports_its_ordered_line(self):
        appointment, _ = self._in_consultation_visit()
        request = self._lab_request(appointment)
        self._lab_result(request)

        _, payload = self._results(appointment)
        line = self._lab_rows(payload)[0]["result"]["lines"][0]
        self.assertTrue(line["ordered"])
        self.assertEqual(line["request_line_id"], request.line_ids[0].id)

    def test_a_legacy_null_linkage_resolves_when_exactly_one_test_matches(self):
        appointment, _ = self._in_consultation_visit()
        request = self._lab_request(appointment)
        result = self._lab_result(request)
        self._strip_linkage(result)

        _, payload = self._results(appointment)
        line = self._lab_rows(payload)[0]["result"]["lines"][0]
        self.assertTrue(line["ordered"])
        self.assertEqual(line["request_line_id"], request.line_ids[0].id)

    def test_an_ambiguous_legacy_linkage_is_reported_unresolved_not_guessed(self):
        """The same test ordered twice. hospital_billing REFUSES to guess here
        because delivering a sibling's charge is worse than stopping; the
        read-only equivalent is showing the value while withholding the claim
        about which ordered test it answers."""
        appointment, encounter = self._in_consultation_visit()
        request = self._raw_lab_request(appointment, encounter, [self.cbc, self.cbc])
        self.assertEqual(len(request.line_ids), 2)
        result = self._lab_result(
            request, values=[{"result_value": "1.0"}, {"result_value": "2.0"}]
        )
        self._strip_linkage(result)

        _, payload = self._results(appointment)
        lines = self._lab_rows(payload)[0]["result"]["lines"]
        for line in lines:
            self.assertFalse(line["ordered"])
            self.assertIsNone(line["request_line_id"])
            # The measurement is still delivered; only the mapping is withheld.
            self.assertIsNotNone(line["value"])

    def test_reading_results_never_heals_the_linkage(self):
        """hospital_billing's resolver WRITES request_line_id back. A GET must
        not: repairing data from a read is a mutation with no audit trail and
        no transaction the caller asked for."""
        appointment, _ = self._in_consultation_visit()
        request = self._lab_request(appointment)
        result = self._lab_result(request)
        self._strip_linkage(result)

        self._results(appointment)

        result.line_ids.invalidate_recordset()
        self.assertFalse(
            result.line_ids.sudo().mapped("request_line_id"),
            "the results GET wrote request_line_id back",
        )

    def test_only_the_newest_released_result_is_reported_and_the_rest_counted(self):
        """Repeat testing is modelled as a NEW result, so more than one
        released result is a real shape. The newest is reported; the others are
        counted rather than dropped or merged."""
        appointment, _ = self._in_consultation_visit()
        request = self._lab_request(appointment)
        first = self._lab_result(
            request, values=[{"result_value": "1.0"}], result_date="2020-01-01"
        )
        request.invalidate_recordset()
        # result_date is set AT CREATION: it is frozen content once the result
        # is released, which is the model behaving correctly.
        second = self._lab_result(
            request, values=[{"result_value": "2.0"}], result_date="2099-01-01"
        )

        _, payload = self._results(appointment)
        row = self._lab_rows(payload)[0]
        self.assertEqual(row["result"]["id"], second.id)
        self.assertEqual(row["superseded_count"], 1)
        self.assertNotEqual(row["result"]["id"], first.id)


# ======================================================================
# Scope, roles and the completed consultation
# ======================================================================
@tagged("post_install", "-at_install", "doctor_results")
class TestResultsAccess(ResultsCase):
    def test_another_doctors_results_are_not_reachable(self):
        """ABSENT, not forbidden.

        find_appointment_in_scope counts the appointment through the CALLER's
        own record rules, so a visit outside this doctor's scope is not merely
        refused -- it does not exist as far as the answer is concerned. That is
        the stronger privacy answer and the one every other Doctor Desk
        endpoint already gives, so Results must not become the one route that
        confirms another doctor's visit is there.
        """
        theirs, encounter = self._in_consultation_visit(doctor=self.other_doctor)
        # Built directly rather than through the ordering endpoint: that
        # endpoint would itself refuse this caller, and the point here is that
        # a REAL released result of another doctor stays unreachable.
        request = self._raw_lab_request(theirs, encounter, [self.cbc])
        self._lab_result(request)

        response, payload = self._results(theirs)
        self.assertEqual(response.status_code, 404)
        self.assertEqual(payload["error"]["code"], "visit_not_found")
        self.assertNotIn("laboratory", json.dumps(payload.get("data") or {}))

    def test_a_visit_with_no_consultation_returns_an_empty_payload(self):
        appointment, _ = self._ready_visit()
        response, payload = self._results(appointment)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(payload["data"], {"laboratory": [], "radiology": []})

    def test_results_stay_readable_after_the_consultation_is_completed(self):
        """THE core requirement. Chasing a result is precisely what a doctor
        does after the visit is finished, and Slice 4's completion policy does
        not wait for laboratory or imaging."""
        appointment, encounter = self._in_consultation_visit()
        request = self._lab_request(appointment)
        self._lab_result(request)

        consultation = self._consultation_of(encounter)
        consultation.sudo().write({"state": "completed"})
        appointment.sudo().write({"state": "done"})
        appointment.invalidate_recordset()

        response, payload = self._results(appointment)
        self.assertEqual(response.status_code, 200)
        row = self._lab_rows(payload)[0]
        self.assertEqual(row["status"], "available")
        self.assertIsNotNone(row["result"])

    def test_manager_reads_results_of_a_visit_they_do_not_own(self):
        """Manager IMPLIES Doctor, so the doctor scope must not have become a
        manager's ceiling."""
        appointment, _ = self._in_consultation_visit()
        request = self._lab_request(appointment)
        self._lab_result(request)

        response, payload = self._results(
            appointment, user=self.manager, password=self.manager_password
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self._lab_rows(payload)[0]["status"], "available")

    def test_the_front_office_and_the_bench_roles_are_refused(self):
        appointment, _ = self._in_consultation_visit()
        for user, password in (
            (self.receptionist, self.receptionist_password),
            (self.cashier, self.cashier_password),
            (self.nurse, self.nurse_password),
            (self.accountant, self.accountant_password),
        ):
            response, _payload = self._results(
                appointment, user=user, password=password
            )
            self.assertIn(
                response.status_code, (403, 404),
                "%s reached the results endpoint" % user.login,
            )


# ======================================================================
# The payload boundary, and the absence of any verb but GET
# ======================================================================
@tagged("post_install", "-at_install", "doctor_results")
class TestResultsBoundary(ResultsCase):
    def _full_payload(self):
        appointment, _ = self._in_consultation_visit()
        lab = self._lab_request(appointment)
        self._lab_result(lab)
        rad = self._rad_request(appointment)
        self._rad_result(rad, findings="F", impression="I", recommendations="R")
        _, payload = self._results(appointment)
        return appointment, payload

    def test_no_billing_or_payer_vocabulary_appears_anywhere(self):
        """RECURSIVE. A flat key check would miss a sum embedded in a message,
        which is exactly how a figure reaches a clinician."""
        _appointment, payload = self._full_payload()
        for token in _walk(payload):
            lowered = token.lower()
            for banned in FORBIDDEN:
                self.assertNotIn(
                    banned, lowered,
                    "'%s' surfaced in the results payload: %r" % (banned, token),
                )

    def test_no_orm_or_audit_metadata_appears(self):
        _appointment, payload = self._full_payload()
        blob = json.dumps(payload)
        for banned in ("create_uid", "write_uid", "write_date", "__last_update",
                       "audit", "res.users", "ir.model"):
            self.assertNotIn(banned, blob)

    def test_the_payload_declares_no_writable_action(self):
        """No can_*, no editable, no acknowledge. Reviewing a result is not an
        act the desk may perform, and offering a flag would imply it is."""
        _appointment, payload = self._full_payload()
        keys = set(_walk(payload["data"]))
        for banned in ("can_order", "can_review", "can_acknowledge", "editable",
                       "cancellable", "acknowledged", "reviewed", "seen"):
            self.assertNotIn(banned, keys)

    def test_the_results_route_answers_no_verb_but_get(self):
        appointment, _ = self._in_consultation_visit()
        self._auth()
        url = RESULTS % appointment.id
        for method in ("POST", "PUT", "PATCH", "DELETE"):
            response = self.url_open(
                url,
                data="{}",
                headers={"Content-Type": "application/json", "X-HTTP-Method": method},
            ) if method == "POST" else self.opener.request(
                method, self.base_url() + url
            )
            self.assertNotEqual(
                response.status_code, 200,
                "%s on the results route was accepted" % method,
            )

    def test_the_status_vocabulary_is_exactly_three_model_backed_keys(self):
        from odoo.addons.yoya_emr_api.services.result_serializers import (
            RESULT_STATUS_LABELS,
        )
        self.assertEqual(
            set(RESULT_STATUS_LABELS), {"pending", "available", "cancelled"}
        )
        for invented in ("reviewed", "seen", "acknowledged", "signed"):
            self.assertNotIn(invented, RESULT_STATUS_LABELS)
