"""Laboratory Desk: the bench queue, request detail (Slice 1) and sample
collection (Slice 2).

WHAT THESE TESTS ARE FOR
------------------------
Four properties carry this slice.

  1. THE DESK GATE IS NARROWER THAN THE ORM, AND THAT IS THE POINT.
     hospital_management ships a read ACL on hospital.laboratory.request and
     hospital.laboratory.result for the NURSE, the RECEPTIONIST and the DPO
     with no record rule narrowing any of them. The ORM alone would therefore
     serve the whole hospital's bench queue to the front desk. Slice 1 does not
     change those rules -- that is a separate decision -- but it refuses to
     inherit them: /lab/* answers 403 for every role outside LAB_DESK_GROUPS.
     A test that only asserted "the nurse gets an empty list" would pass just
     as well against the broken version, so every denial below asserts the
     STATUS, not the payload.

  2. THE STATUS VOCABULARY IS DERIVED, NEVER INVENTED. hospital.laboratory
     .request.state has exactly six values. 'awaiting_clearance' and
     'ready_for_collection' are not among them: both are `requested`, split by
     hospital_billing's billing_blocked. Every mapping below is asserted
     against a request driven there through the REAL workflow methods, so a
     change to the laboratory's own state machine breaks these tests rather
     than silently producing a label nothing backs.

  3. NOTHING PRICED CROSSES THE BOUNDARY. A bench operator is told THAT a
     request is not financially cleared, never how much is owed or by whom.
     billing_clearance_message is the specific trap: it is computed on the same
     model, one attribute away from billing_blocked, and it can name sums.

  4. THE QUEUE IS BOUNDED. An unbounded bench queue is a table scan with a
     per-row clearance compute behind it.

  5. (SLICE 2) THE DESK CALLS THE AUTHORITATIVE METHOD AND DECIDES NOTHING.
     Collection is hospital.laboratory.request.action_mark_sample_collected(),
     where hospital_billing's clearance gate and the base state machine both
     live. The endpoint writes no state, re-checks no clearance, and sanitises
     the ONE refusal whose wording can carry an amount -- see
     TestLabDeskCollection.test_141, which is about a manager, not a
     technician.

FIXTURES ARE DRIVEN THROUGH THE REAL WORKFLOW. No test below writes `state`.
Requests reach sample_collected through action_mark_sample_collected() (which
is the clearance gate), in_progress through action_mark_in_progress(), and
completed only by releasing a result that covers every ordered test -- which is
what hospital.laboratory.request._evaluate_completion() requires. A fixture
that forged a state would prove nothing about the mapping under test.
"""
import json
import uuid

from odoo.tests import tagged

from odoo.addons.yoya_emr_api.services.lab_desk_serializers import (
    LAB_DESK_ACTIVE_STATUSES,
    LAB_DESK_STATUS_LABELS,
    LAB_DESK_STATUS_STATE,
)
from odoo.addons.yoya_emr_api.services.reception_scope import LAB_DESK_GROUPS

from .test_doctor_desk_api import DoctorDeskCase

SESSION = "/yoya-emr/api/v1/lab/session"
WORKLIST = "/yoya-emr/api/v1/lab/worklist"
DETAIL = "/yoya-emr/api/v1/lab/requests/%s"
COLLECT = "/yoya-emr/api/v1/lab/requests/%s/collect"

G_LAB_TECH = "hospital_management.group_hospital_lab_technician"
G_RECEPTIONIST = "hospital_management.group_hospital_receptionist"
G_PHARMACIST = "hospital_management.group_hospital_pharmacist"
G_ACCOUNTANT = "hospital_management.group_hospital_accountant"

# Every bench status, as the `status` query parameter spells them. Used where a
# test needs the request it just built regardless of which lane it landed in.
ALL_STATUSES = ",".join(LAB_DESK_STATUS_LABELS)

# The fee on the PREPAID laboratory service. Asserted as a string against the
# serialized payload, so a leak of the number itself is caught and not only a
# leak of a field whose name we happened to think of.
PREPAID_LAB_FEE = 640.0

EXPECTED_ROW_KEYS = {
    "id", "request_code", "state", "status", "status_label",
    "priority", "priority_label", "request_date", "created_at",
    "billing_blocked", "active", "patient", "ordering_physician",
    "test_count", "tests_summary", "encounter_code", "department",
    "result_count", "released_count",
}

EXPECTED_DETAIL_EXTRA_KEYS = {"tests", "clinical_notes", "instructions"}

EXPECTED_PATIENT_KEYS = {"id", "name", "mrn", "age", "gender"}

EXPECTED_TEST_KEYS = {
    "id", "test_id", "name", "code", "category", "sample_type",
    "special_instruction", "sequence",
}

# Substrings that must never appear in a Laboratory Desk payload, whatever the
# request's financial situation.
#
# 'billing' ITSELF IS NOT BANNED, deliberately: `billing_blocked` is the one
# billing-derived value this contract permits, and banning the stem would make
# the assertion untestable. The dangerous sibling is named in full instead.
FORBIDDEN_KEYS = (
    "amount", "balance", "outstanding", "paid_amount", "receipt", "allocation",
    "sponsor", "agreement", "membership", "payer", "tariff", "price",
    "invoice", "charge", "coverage", "credit_limit", "billing_service",
    "billing_clearance_message", "clearance_message", "authorization_state",
)


class LabDeskCase(DoctorDeskCase):
    """DoctorDeskCase plus the bench roles and a billable laboratory catalogue.

    Two laboratory services, and the difference between them is the whole
    financial half of this slice:

      cleared_test   prepayment_required=False -> amount_due_for_clearance is
                     zero, so check_financial_clearance's cash arm clears it
                     and the request reads ready_for_collection immediately.
      prepaid_test   prepayment_required=True  -> the same arm reports a
                     prepayment due, billing_blocked is True, and the request
                     reads awaiting_clearance until a cashier settles it.

    Neither is a special case invented for the test: they are the two
    configurations hospital.billing.service already supports, and
    test_laboratory_prepayment_gate pins the same distinction at the model.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        tag = uuid.uuid4().hex[:6]

        cls.lab_password = "lab-tech-pw-1"
        cls.lab_tech = cls._make_user("lab_tech", cls.lab_password, [G_LAB_TECH])
        cls.receptionist_password = "lab-recep-pw-1"
        cls.receptionist = cls._make_user(
            "lab_recep", cls.receptionist_password, [G_RECEPTIONIST]
        )
        cls.pharmacist_password = "lab-pharm-pw-1"
        cls.pharmacist = cls._make_user(
            "lab_pharm", cls.pharmacist_password, [G_PHARMACIST]
        )
        cls.accountant_password = "lab-acct-pw-1"
        cls.accountant = cls._make_user(
            "lab_acct", cls.accountant_password, [G_ACCOUNTANT]
        )

        cls.uom = cls.env["uom.uom"].sudo().search([], limit=1)
        cls.cleared_test = cls._make_lab_test(
            "LabDesk Cleared %s" % tag, "LDC%s" % tag.upper(),
            prepayment_required=False, price=120.0,
        )
        cls.second_test = cls._make_lab_test(
            "LabDesk Second %s" % tag, "LDS%s" % tag.upper(),
            prepayment_required=False, price=95.0,
        )
        cls.prepaid_test = cls._make_lab_test(
            "LabDesk Prepaid %s" % tag, "LDP%s" % tag.upper(),
            prepayment_required=True, price=PREPAID_LAB_FEE,
        )

    @classmethod
    def _make_lab_test(cls, name, code, prepayment_required, price):
        service = cls.env["hospital.billing.service"].sudo().create(
            {
                "name": "%s Service" % name,
                "code": "T-LABDESK-%s" % code,
                "service_type": "laboratory",
                "default_price": price,
                "company_id": cls.env.company.id,
                "currency_id": cls.env.company.currency_id.id,
                "uom_id": cls.uom.id,
                "prepayment_required": prepayment_required,
                "tax_treatment": "exempt",
            }
        )
        return cls.env["hospital.laboratory.test"].sudo().create(
            {
                "name": name,
                "code": code,
                "category": "hematology",
                "sample_type": "blood",
                "billing_service_id": service.id,
            }
        )

    # ------------------------------------------------------------------
    # Fixture helpers -- the REAL workflow, never a state write.
    # ------------------------------------------------------------------
    def _lab_request(self, tests=None, confirm=True, notes=None):
        """A laboratory request on a genuine, triaged, paid-for visit.

        Confirmation is action_confirm_request(), which is where
        hospital_billing raises one charge per ordered test. Nothing here
        creates a charge, exactly as the Doctor Desk does not.
        """
        appointment, encounter = self._ready_visit()
        values = {
            "patient_id": appointment.patient_id.id,
            "physician_id": self.doctor.id,
            "appointment_id": appointment.id,
            "encounter_id": encounter.id,
            "line_ids": [
                (0, 0, {"test_id": test.id})
                for test in (tests or [self.cleared_test])
            ],
        }
        if notes:
            values["clinical_notes"] = notes
        record = self.env["hospital.laboratory.request"].sudo().create(values)
        if confirm:
            record.action_confirm_request()
        record.invalidate_recordset()
        return record

    def _collected(self, **kwargs):
        record = self._lab_request(**kwargs)
        record.action_mark_sample_collected()
        record.invalidate_recordset()
        return record

    def _in_progress(self, **kwargs):
        record = self._collected(**kwargs)
        record.action_mark_in_progress()
        record.invalidate_recordset()
        return record

    def _completed(self, **kwargs):
        """Driven to 'completed' the only way the model allows it.

        Release is what triggers _evaluate_completion(), and completion demands
        that EVERY ordered line be covered by exactly one released result line.
        Reaching it therefore exercises the whole reporting chain rather than
        asserting a label against a forged state.
        """
        record = self._in_progress(**kwargs)
        result = self.env["hospital.laboratory.result"].sudo().create(
            {"request_id": record.id}
        )
        for line in result.line_ids:
            line.sudo().write({"result_value": "7.4", "unit": "g/dL"})
        result.action_mark_entered()
        result.action_validate()
        result.action_release()
        record.invalidate_recordset()
        return record

    def _cancelled(self, **kwargs):
        record = self._lab_request(**kwargs)
        record.action_cancel()
        record.invalidate_recordset()
        return record

    # ------------------------------------------------------------------
    def _lab_get(self, url, user=None, password=None, **params):
        return self._get(
            url,
            user=user or self.lab_tech,
            password=password or self.lab_password,
            **params,
        )

    def _lab_post(self, url, user=None, password=None):
        """POST as a chosen role, matching DoctorDeskCase._post exactly."""
        self._auth(user or self.lab_tech, password or self.lab_password)
        response = self.url_open(
            url, data="{}", headers={"Content-Type": "application/json"}
        )
        return response, json.loads(response.text)

    def _collect(self, record, user=None, password=None):
        return self._lab_post(COLLECT % record.id, user=user, password=password)

    def _rows_by_id(self, payload):
        return {row["id"]: row for row in payload["data"]["rows"]}

    def _row_for(self, record, **params):
        """The queue row for one request, asked for by its own status."""
        status = params.pop("status", None)
        _response, payload = self._lab_get(
            WORKLIST, status=status or ALL_STATUSES, **params
        )
        return self._rows_by_id(payload).get(record.id)


@tagged("post_install", "-at_install", "lab_desk")
class TestLabDeskAuthorization(LabDeskCase):
    """PHASE 1. Who may open the bench, and who is refused."""

    def test_01_lab_technician_may_open_the_session(self):
        response, payload = self._lab_get(SESSION)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(payload["success"])
        self.assertTrue(payload["data"]["capabilities"]["lab_desk"])
        self.assertEqual(payload["data"]["user"]["name"], self.lab_tech.name)

    def test_02_lab_technician_may_list_the_worklist(self):
        record = self._lab_request()
        response, payload = self._lab_get(WORKLIST)
        self.assertEqual(response.status_code, 200)
        self.assertIn(record.id, self._rows_by_id(payload))

    def test_03_lab_technician_may_read_request_detail(self):
        record = self._lab_request()
        response, payload = self._lab_get(DETAIL % record.id)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(payload["data"]["request"]["id"], record.id)

    def test_04_manager_may_open_the_bench(self):
        """Manager IMPLIES Doctor, so the doctor denial below must not catch them."""
        record = self._lab_request()
        for url in (SESSION, WORKLIST, DETAIL % record.id):
            response, payload = self._lab_get(
                url, user=self.manager, password=self.manager_password
            )
            self.assertEqual(response.status_code, 200)
            self.assertTrue(payload["success"])

    # ------------------------------------------------------------------
    # The denials. Each asserts the STATUS, never an empty list: a gate that
    # returned 200-with-no-rows would be indistinguishable from having no gate
    # at all on a quiet day.
    # ------------------------------------------------------------------
    def _assert_denied(self, user, password):
        record = self._lab_request()
        for url in (SESSION, WORKLIST, DETAIL % record.id):
            response, payload = self._lab_get(url, user=user, password=password)
            self.assertEqual(
                response.status_code, 403,
                "%s must be refused %s by the desk gate." % (user.login, url),
            )
            self.assertFalse(payload["success"])
            self.assertEqual(payload["error"]["code"], "lab_desk_not_authorized")

    def test_05_doctor_is_denied(self):
        """A doctor ORDERS laboratory work; the bench is not a second desk.

        They hold read/write/create on hospital.laboratory.request, so the ORM
        would have let them straight in.
        """
        self._assert_denied(self.doctor_user, self.doctor_password)

    def test_06_nurse_is_denied(self):
        """THE B9 CASE. A nurse holds an unrestricted read ACL on the laboratory
        models and no record rule narrows it, so only the desk gate refuses."""
        self._assert_denied(self.nurse, self.nurse_password)

    def test_07_receptionist_is_denied(self):
        """THE OTHER B9 CASE, and the reason this gate is not the ORM's."""
        self._assert_denied(self.receptionist, self.receptionist_password)

    def test_08_cashier_is_denied(self):
        self._assert_denied(self.cashier, self.cashier_password)

    def test_09_pharmacist_is_denied(self):
        self._assert_denied(self.pharmacist, self.pharmacist_password)

    def test_10_accountant_is_denied(self):
        self._assert_denied(self.accountant, self.accountant_password)

    def test_11_front_desk_nurse_is_denied(self):
        self._assert_denied(self.front_desk, self.fd_password)

    def test_12_the_session_route_enforces_the_role_gate(self):
        """Identity lookup has the same boundary as the laboratory data."""
        response, payload = self._lab_get(
            SESSION, user=self.nurse, password=self.nurse_password
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(payload["error"]["code"], "lab_desk_not_authorized")

    def test_16_system_administrator_may_open_the_bench(self):
        password = "lab-admin-test-pw"
        administrator = self._make_user(
            "lab_admin", password,
            ["hospital_management.group_hospital_system_administrator"],
        )
        record = self._lab_request()
        for url in (SESSION, WORKLIST, DETAIL % record.id):
            response, payload = self._lab_get(
                url, user=administrator, password=password
            )
            self.assertEqual(response.status_code, 200)
            self.assertTrue(payload["success"])

    def test_13_unauthenticated_never_reaches_the_endpoint(self):
        """An anonymous request is bounced by the framework, not by our guard.

        Two things test_cashier_desk_api learned the hard way and this inherits:

          * HttpCase reuses ONE session across the class and tests run in name
            order, so without an explicit logout this would inherit an earlier
            test's laboratory session and pass for entirely the wrong reason.
          * ``auth="user"`` makes Odoo REDIRECT a public user to /web/login. It
            does not return 401, and following the redirect yields a 200 HTML
            login page -- so the assertion is on the redirect itself.

        lab_endpoint's own _is_public() branch stays in place as a second line
        for any route reached with a session that resolves to the public user;
        it is deliberately not what this test exercises.
        """
        record = self._lab_request()
        self.authenticate(None, None)
        for url in (SESSION, WORKLIST, DETAIL % record.id):
            response = self.url_open(url, allow_redirects=False)
            self.assertIn(
                response.status_code, (301, 302, 303, 401, 403),
                "%s must refuse an anonymous caller." % url,
            )
            self.assertNotIn(
                record.name, response.text or "",
                "No laboratory data may be served to an anonymous caller.",
            )

    def test_14_the_gate_tuple_is_exactly_the_bench_roles(self):
        """Pinned, so widening it is a deliberate edit and not a side effect."""
        self.assertEqual(
            LAB_DESK_GROUPS,
            (
                "hospital_management.group_hospital_lab_technician",
                "hospital_management.group_hospital_manager",
                "hospital_management.group_hospital_system_administrator",
            ),
        )

    def test_15_the_read_routes_are_read_only(self):
        """The routing table is the proof.

        Slice 2 adds exactly one write route, on its own path
        (.../requests/<id>/collect). These three stay GET-only.
        """
        for url in (SESSION, WORKLIST, DETAIL % 1):
            self._auth(self.lab_tech, self.lab_password)
            response = self.url_open(
                url, data="{}", headers={"Content-Type": "application/json"}
            )
            self.assertIn(
                response.status_code, (404, 405),
                "%s must not accept a POST." % url,
            )


@tagged("post_install", "-at_install", "lab_desk")
class TestLabDeskStatusMapping(LabDeskCase):
    """PHASE 3. request.state x billing_blocked -> exactly one bench status."""

    def test_20_blocked_requested_reads_awaiting_clearance(self):
        record = self._lab_request(tests=[self.prepaid_test])
        self.assertEqual(record.state, "requested")
        self.assertTrue(record.sudo().billing_blocked)

        row = self._row_for(record)
        self.assertEqual(row["status"], "awaiting_clearance")
        self.assertEqual(row["status_label"], "Awaiting clearance")
        self.assertEqual(row["state"], "requested")
        self.assertTrue(row["billing_blocked"])

    def test_21_cleared_requested_reads_ready_for_collection(self):
        record = self._lab_request(tests=[self.cleared_test])
        self.assertEqual(record.state, "requested")
        self.assertFalse(record.sudo().billing_blocked)

        row = self._row_for(record)
        self.assertEqual(row["status"], "ready_for_collection")
        self.assertEqual(row["status_label"], "Ready for collection")
        self.assertEqual(row["state"], "requested")
        self.assertFalse(row["billing_blocked"])

    def test_22_sample_collected_maps_through(self):
        record = self._collected()
        self.assertEqual(record.state, "sample_collected")
        row = self._row_for(record)
        self.assertEqual(row["status"], "sample_collected")
        self.assertEqual(row["state"], "sample_collected")

    def test_23_in_progress_maps_through(self):
        record = self._in_progress()
        self.assertEqual(record.state, "in_progress")
        row = self._row_for(record)
        self.assertEqual(row["status"], "in_progress")

    def test_24_completed_maps_through(self):
        record = self._completed()
        self.assertEqual(record.state, "completed")
        row = self._row_for(record, status="completed")
        self.assertEqual(row["status"], "completed")
        self.assertEqual(row["released_count"], 1)

    def test_25_cancelled_maps_through(self):
        record = self._cancelled()
        self.assertEqual(record.state, "cancelled")
        row = self._row_for(record, status="cancelled")
        self.assertEqual(row["status"], "cancelled")

    def test_26_draft_maps_through(self):
        record = self._lab_request(confirm=False)
        self.assertEqual(record.state, "draft")
        row = self._row_for(record, status="draft")
        self.assertEqual(row["status"], "draft")

    def test_27_billing_blocked_is_never_read_as_clearance_outside_requested(self):
        """A collected request reads False, and that is NOT a clearance verdict.

        hospital_billing's own compute short-circuits to False for every state
        but `requested`, so a mapping that consulted it elsewhere would report
        a decision nobody made.
        """
        record = self._collected()
        self.assertFalse(record.sudo().billing_blocked)
        row = self._row_for(record)
        self.assertEqual(
            row["status"], "sample_collected",
            "A False verdict outside `requested` must not become "
            "ready_for_collection.",
        )

    def test_28_every_status_is_backed_by_a_real_request_state(self):
        """No invented database state. The bridge table is asserted whole."""
        real_states = dict(
            self.env["hospital.laboratory.request"]._fields["state"].selection
        )
        for status, state in LAB_DESK_STATUS_STATE.items():
            self.assertIn(
                state, real_states,
                "Bench status '%s' claims state '%s', which does not exist."
                % (status, state),
            )
        self.assertEqual(
            set(LAB_DESK_STATUS_STATE.values()), set(real_states),
            "Every real request state must be reachable from the bench "
            "vocabulary, and no status may name one that is not.",
        )

    def test_29_the_two_derived_statuses_are_not_database_values(self):
        real_states = dict(
            self.env["hospital.laboratory.request"]._fields["state"].selection
        )
        for derived in ("awaiting_clearance", "ready_for_collection"):
            self.assertNotIn(
                derived, real_states,
                "'%s' is a DISPLAY status and must never become a stored "
                "state." % derived,
            )

    def test_30_the_desk_never_writes_a_derived_status_back(self):
        """Reading the queue leaves the authoritative state untouched."""
        record = self._lab_request(tests=[self.prepaid_test])
        before = record.state
        self._lab_get(WORKLIST)
        self._lab_get(DETAIL % record.id)
        record.invalidate_recordset()
        self.assertEqual(record.state, before)


@tagged("post_install", "-at_install", "lab_desk")
class TestLabDeskQueue(LabDeskCase):
    """PHASE 4/7. Query behaviour: defaults, bounds, filters and search."""

    def test_40_the_default_queue_is_active_bench_work(self):
        ready = self._lab_request()
        blocked = self._lab_request(tests=[self.prepaid_test])
        collected = self._collected()
        progressing = self._in_progress()
        draft = self._lab_request(confirm=False)
        done = self._completed()
        cancelled = self._cancelled()

        _response, payload = self._lab_get(WORKLIST)
        rows = self._rows_by_id(payload)

        for record in (ready, blocked, collected, progressing):
            self.assertIn(
                record.id, rows,
                "Active bench work must be in the default queue.",
            )
        for record in (draft, done, cancelled):
            self.assertNotIn(
                record.id, rows,
                "Finished and unordered work must not crowd the default queue.",
            )
        self.assertEqual(
            payload["data"]["filters"]["status"], list(LAB_DESK_ACTIVE_STATUSES)
        )

    def test_41_awaiting_clearance_is_never_silently_dropped(self):
        """The bench is the person the patient asks, so a blocked order shows."""
        blocked = self._lab_request(tests=[self.prepaid_test])
        _response, payload = self._lab_get(WORKLIST)
        row = self._rows_by_id(payload).get(blocked.id)
        self.assertIsNotNone(row, "A blocked order is still bench business.")
        self.assertEqual(row["status"], "awaiting_clearance")

    def test_42_the_status_filter_narrows_exactly(self):
        ready = self._lab_request()
        blocked = self._lab_request(tests=[self.prepaid_test])

        _response, payload = self._lab_get(WORKLIST, status="awaiting_clearance")
        rows = self._rows_by_id(payload)
        self.assertIn(blocked.id, rows)
        self.assertNotIn(
            ready.id, rows,
            "A cleared request must not answer an awaiting_clearance filter -- "
            "the split is refined per row, not left to the state.",
        )
        self.assertTrue(
            all(row["status"] == "awaiting_clearance" for row in rows.values())
        )

    def test_43_an_unknown_status_is_a_400_not_an_empty_queue(self):
        response, payload = self._lab_get(WORKLIST, status="released_request")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(payload["error"]["code"], "invalid_status")

    def test_44_the_queue_is_bounded(self):
        for _index in range(4):
            self._lab_request()
        _response, payload = self._lab_get(WORKLIST, limit=2)
        self.assertLessEqual(len(payload["data"]["rows"]), 2)
        self.assertTrue(payload["data"]["meta"]["truncated"])

    def test_45_the_limit_is_validated(self):
        for bad in ("0", "301", "-4"):
            response, payload = self._lab_get(WORKLIST, limit=bad)
            self.assertEqual(response.status_code, 400, "limit=%s" % bad)
            self.assertEqual(payload["error"]["code"], "invalid_limit")
        response, _payload = self._lab_get(WORKLIST, limit="abc")
        self.assertEqual(response.status_code, 400)

    def test_46_search_finds_by_request_code(self):
        record = self._lab_request()
        _response, payload = self._lab_get(WORKLIST, q=record.name)
        rows = self._rows_by_id(payload)
        self.assertIn(record.id, rows)
        self.assertEqual(len(rows), 1)

    def test_47_search_finds_by_patient_name_and_chart_number(self):
        record = self._lab_request()
        patient = record.patient_id.sudo()

        _response, by_name = self._lab_get(WORKLIST, q=patient.name)
        self.assertIn(record.id, self._rows_by_id(by_name))

        _response, by_mrn = self._lab_get(WORKLIST, q=patient.identification_code)
        self.assertIn(record.id, self._rows_by_id(by_mrn))

    def test_48_search_does_not_reach_into_clinical_narrative(self):
        """A bench search box finds paperwork, not free-text clinical notes."""
        secret = "SEPSISNARRATIVE%s" % uuid.uuid4().hex[:6]
        record = self._lab_request(notes=secret)
        _response, payload = self._lab_get(WORKLIST, q=secret)
        self.assertNotIn(record.id, self._rows_by_id(payload))

    def test_49_the_date_filter_is_optional_and_exact(self):
        """Bench work does not expire at midnight, so there is no day default."""
        record = self._lab_request()
        request_date = str(record.request_date)

        _response, same_day = self._lab_get(WORKLIST, date=request_date)
        self.assertIn(record.id, self._rows_by_id(same_day))

        _response, other_day = self._lab_get(WORKLIST, date="2001-01-01")
        self.assertNotIn(record.id, self._rows_by_id(other_day))

    def test_50_a_malformed_date_is_a_400(self):
        response, payload = self._lab_get(WORKLIST, date="yesterday")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(payload["error"]["code"], "invalid_parameter")

    def test_51_the_page_meta_describes_the_page(self):
        """row_count is the PAGE. The lane counts are `summary`.

        They deliberately answer different questions -- see
        TestLabDeskSummary, which owns the scope-wide counts.
        """
        self._lab_request()
        self._lab_request(tests=[self.prepaid_test])
        _response, payload = self._lab_get(WORKLIST)

        self.assertEqual(
            payload["data"]["meta"]["row_count"], len(payload["data"]["rows"])
        )

    def test_52_the_queue_is_ordered_oldest_first(self):
        first = self._lab_request()
        second = self._lab_request()
        _response, payload = self._lab_get(WORKLIST)
        ids = [row["id"] for row in payload["data"]["rows"]]
        self.assertLess(
            ids.index(first.id), ids.index(second.id),
            "A bench queue is FIFO: the specimen that has waited longest is "
            "the one at risk.",
        )


    def test_53_clearance_filter_reports_unexamined_candidates(self):
        self._lab_request()
        blocked = self._lab_request(tests=[self.prepaid_test])
        _response, payload = self._lab_get(
            WORKLIST, status="awaiting_clearance", limit=1,
        )
        self.assertEqual(payload["data"]["rows"], [])
        self.assertTrue(payload["data"]["meta"]["truncated"])
        _response, narrowed = self._lab_get(
            WORKLIST, status="awaiting_clearance", limit=1, q=blocked.name,
        )
        self.assertIn(blocked.id, self._rows_by_id(narrowed))
        self.assertFalse(narrowed["data"]["meta"]["truncated"])


@tagged("post_install", "-at_install", "lab_desk")
class TestLabDeskDetail(LabDeskCase):
    """PHASE 4/8. One request, read-only, and what it may disclose."""

    def test_60_the_detail_carries_the_ordered_tests(self):
        record = self._lab_request(tests=[self.cleared_test, self.second_test])
        _response, payload = self._lab_get(DETAIL % record.id)
        detail = payload["data"]["request"]

        self.assertEqual(detail["test_count"], 2)
        self.assertEqual(len(detail["tests"]), 2)
        self.assertEqual(
            {test["name"] for test in detail["tests"]},
            {self.cleared_test.name, self.second_test.name},
        )
        for test in detail["tests"]:
            self.assertEqual(set(test), EXPECTED_TEST_KEYS)
            self.assertEqual(test["sample_type"], "blood")

    def test_61_the_request_line_id_is_the_one_carried(self):
        """It is what every later slice needs: result lines link to THIS id."""
        record = self._lab_request()
        _response, payload = self._lab_get(DETAIL % record.id)
        self.assertEqual(
            [test["id"] for test in payload["data"]["request"]["tests"]],
            record.line_ids.ids,
        )

    def test_62_the_detail_is_a_superset_of_the_queue_row(self):
        record = self._lab_request()
        row = self._row_for(record)
        _response, payload = self._lab_get(DETAIL % record.id)
        detail = payload["data"]["request"]

        self.assertEqual(set(detail), EXPECTED_ROW_KEYS | EXPECTED_DETAIL_EXTRA_KEYS)
        for key, value in row.items():
            self.assertEqual(
                detail[key], value,
                "The panel and the queue must agree about '%s'." % key,
            )

    def test_63_patient_identity_is_bounded(self):
        record = self._lab_request()
        _response, payload = self._lab_get(DETAIL % record.id)
        patient = payload["data"]["request"]["patient"]
        self.assertEqual(
            set(patient), EXPECTED_PATIENT_KEYS,
            "A laboratory identifies a specimen; it does not review a chart.",
        )
        self.assertEqual(patient["mrn"], record.patient_id.sudo().identification_code)

    def test_64_visit_context_comes_from_the_encounter(self):
        """A lab technician holds NO ACL on hospital.appointment.

        Resolving the visit through appointment_id would 403 the whole desk for
        the one role it exists to serve, so the encounter is the anchor -- and
        the appointment's raw id is not serialized at all.
        """
        record = self._lab_request()
        _response, payload = self._lab_get(DETAIL % record.id)
        detail = payload["data"]["request"]

        self.assertEqual(detail["encounter_code"], record.encounter_id.sudo().name)
        self.assertNotIn("appointment_id", detail)
        self.assertNotIn("appointment_code", detail)
        self.assertNotIn("consultation_id", detail)

    def test_65_the_ordering_clinicians_own_words_reach_the_bench(self):
        note = "Query sepsis, sample before antibiotics"
        record = self._lab_request(notes=note)
        _response, payload = self._lab_get(DETAIL % record.id)
        self.assertEqual(payload["data"]["request"]["clinical_notes"], note)

    def test_66_an_unknown_request_is_a_flat_404(self):
        response, payload = self._lab_get(DETAIL % 99999999)
        self.assertEqual(response.status_code, 404)
        self.assertEqual(payload["error"]["code"], "lab_request_not_found")
        self.assertEqual(
            payload["error"]["message"], "Laboratory request not found.",
            "The refusal must not describe the id, the model or the query.",
        )

    def test_67_a_zero_id_is_rejected_before_any_lookup(self):
        response, payload = self._lab_get(DETAIL % 0)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(payload["error"]["code"], "invalid_request_id")

    def test_68_the_gate_precedes_existence(self):
        """A refused role learns nothing about which ids are real."""
        record = self._lab_request()
        real, _payload = self._lab_get(
            DETAIL % record.id, user=self.nurse, password=self.nurse_password
        )
        fake, _payload = self._lab_get(
            DETAIL % 99999999, user=self.nurse, password=self.nurse_password
        )
        self.assertEqual(real.status_code, 403)
        self.assertEqual(
            real.status_code, fake.status_code,
            "A real id and an imaginary one must be indistinguishable to a "
            "caller the desk refuses.",
        )


    def test_69_record_rule_hidden_request_matches_missing_404(self):
        record = self._lab_request()
        self.env["ir.rule"].sudo().create({
            "name": "Lab Desk test: hide one request",
            "model_id": self.env["ir.model"]._get_id("hospital.laboratory.request"),
            "domain_force": "[('id', '!=', %s)]" % record.id,
        })
        hidden, hidden_payload = self._lab_get(DETAIL % record.id)
        missing, missing_payload = self._lab_get(DETAIL % 99999999)
        self.assertEqual(hidden.status_code, 404)
        self.assertEqual(missing.status_code, 404)
        self.assertEqual(hidden_payload["error"], missing_payload["error"])


@tagged("post_install", "-at_install", "lab_desk")
class TestLabDeskConfidentiality(LabDeskCase):
    """PHASE 9. Nothing priced crosses the boundary, in any state."""

    def _payloads(self, record):
        _response, worklist = self._lab_get(WORKLIST, status=ALL_STATUSES)
        _response, detail = self._lab_get(DETAIL % record.id)
        return json.dumps(worklist), json.dumps(detail)

    def test_80_no_money_vocabulary_in_any_payload(self):
        blocked = self._lab_request(tests=[self.prepaid_test])
        for blob in self._payloads(blocked):
            lowered = blob.lower()
            for banned in FORBIDDEN_KEYS:
                self.assertNotIn(
                    banned, lowered,
                    "'%s' must never reach the bench." % banned,
                )

    def test_81_the_blocked_amount_itself_never_appears(self):
        """Not only the field NAMES -- the number behind them."""
        blocked = self._lab_request(tests=[self.prepaid_test])
        self.assertTrue(blocked.sudo().billing_blocked)
        for blob in self._payloads(blocked):
            for rendering in ("640", "640.0", "640.00"):
                self.assertNotIn(
                    rendering, blob,
                    "The prepayment due must not be reconstructable.",
                )

    def test_82_billing_clearance_message_is_never_serialized(self):
        """THE SPECIFIC TRAP: one attribute away from billing_blocked, and it
        can name sums for a reader who is allowed to see them."""
        blocked = self._lab_request(tests=[self.prepaid_test])
        message = blocked.sudo().billing_clearance_message
        self.assertTrue(message, "The fixture must actually produce a message.")
        for blob in self._payloads(blocked):
            self.assertNotIn(message, blob)
            self.assertNotIn("billing_clearance_message", blob)

    def test_83_the_only_billing_value_is_a_boolean(self):
        blocked = self._lab_request(tests=[self.prepaid_test])
        row = self._row_for(blocked)
        self.assertIsInstance(row["billing_blocked"], bool)
        billing_keys = [key for key in row if "billing" in key]
        self.assertEqual(
            billing_keys, ["billing_blocked"],
            "billing_blocked is the ONE billing-derived value permitted here.",
        )

    def test_84_no_user_account_details_reach_the_bench(self):
        record = self._lab_request()
        _worklist, detail = self._payloads(record)
        self.assertNotIn(self.doctor_user.login, detail)
        self.assertNotIn("login", detail)
        self.assertNotIn("email", detail)

    def test_85_the_row_shape_is_pinned(self):
        """A field added without a decision shows up here, not in production."""
        record = self._lab_request()
        row = self._row_for(record)
        self.assertEqual(set(row), EXPECTED_ROW_KEYS)

    def test_86_the_session_discloses_no_laboratory_data(self):
        self._lab_request()
        _response, payload = self._lab_get(SESSION)
        self.assertEqual(set(payload["data"]), {"user", "company", "capabilities"})
        self.assertEqual(set(payload["data"]["user"]), {"id", "name"})


@tagged("post_install", "-at_install", "lab_desk")
class TestLabDeskCollection(LabDeskCase):
    """SLICE 2. Sample collection -- the desk's only mutation.

    THE CONTROLLER DECIDES NOTHING, and these tests are written to prove that
    rather than to re-test the model. Every assertion below is about what the
    ENDPOINT does with the authoritative method's answer:

      * it calls action_mark_sample_collected() and writes no state itself
      * it refuses every role outside LAB_DESK_GROUPS before touching a record
      * it never lets a financial figure reach the bench, for ANY role
      * a refusal leaves the request, its charges and its clearance untouched

    THE FIXTURES DRIVE THE REAL WORKFLOW. No test below writes `state`.
    """

    # ------------------------------------------------------------------
    # Authorization
    # ------------------------------------------------------------------
    def test_100_lab_technician_can_collect(self):
        record = self._lab_request()
        self.assertEqual(record.state, "requested")

        response, payload = self._collect(record)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(payload["success"])
        self.assertEqual(payload["data"]["request"]["status"], "sample_collected")

        record.invalidate_recordset()
        self.assertEqual(record.state, "sample_collected")

    def test_101_manager_can_collect(self):
        record = self._lab_request()
        response, payload = self._collect(
            record, user=self.manager, password=self.manager_password
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(payload["success"])
        record.invalidate_recordset()
        self.assertEqual(record.state, "sample_collected")

    def test_102_system_administrator_can_collect(self):
        password = "lab-admin-collect-pw"
        administrator = self._make_user(
            "lab_admin_collect", password,
            ["hospital_management.group_hospital_system_administrator"],
        )
        record = self._lab_request()
        response, _payload = self._collect(
            record, user=administrator, password=password
        )
        self.assertEqual(response.status_code, 200)
        record.invalidate_recordset()
        self.assertEqual(record.state, "sample_collected")

    def _assert_collect_denied(self, user, password):
        """A denied role is refused AND changes nothing.

        The state assertion is the half that matters: a gate that returned 403
        after the transition would pass a status-only test.
        """
        record = self._lab_request()
        response, payload = self._collect(record, user=user, password=password)
        self.assertEqual(
            response.status_code, 403,
            "%s must not be able to collect a sample." % user.login,
        )
        self.assertEqual(payload["error"]["code"], "lab_desk_not_authorized")
        record.invalidate_recordset()
        self.assertEqual(
            record.state, "requested",
            "A refused caller must not move the request.",
        )

    def test_103_doctor_cannot_collect(self):
        """THE CASE THIS GATE EXISTS FOR.

        A Doctor holds read/write/create on hospital.laboratory.request in
        hospital_management -- they order the tests -- so the ORM alone would
        let them call action_mark_sample_collected() quite happily. The Lab API
        role gate is the independent operational boundary that refuses them.
        """
        self._assert_collect_denied(self.doctor_user, self.doctor_password)

    def test_104_nurse_cannot_collect(self):
        self._assert_collect_denied(self.nurse, self.nurse_password)

    def test_105_receptionist_cannot_collect(self):
        self._assert_collect_denied(self.receptionist, self.receptionist_password)

    def test_106_cashier_cannot_collect(self):
        self._assert_collect_denied(self.cashier, self.cashier_password)

    def test_107_pharmacist_and_accountant_cannot_collect(self):
        self._assert_collect_denied(self.pharmacist, self.pharmacist_password)
        self._assert_collect_denied(self.accountant, self.accountant_password)

    def test_108_front_desk_nurse_cannot_collect(self):
        self._assert_collect_denied(self.front_desk, self.fd_password)

    def test_109_unauthenticated_never_reaches_the_endpoint(self):
        """auth=user REDIRECTS a public caller; it does not return 401.

        Same contract test_13 documents for the read routes.
        """
        record = self._lab_request()
        self.authenticate(None, None)
        response = self.url_open(
            COLLECT % record.id, data="{}",
            headers={"Content-Type": "application/json"},
            allow_redirects=False,
        )
        self.assertIn(response.status_code, (301, 302, 303, 401, 403))
        record.invalidate_recordset()
        self.assertEqual(record.state, "requested")

    def test_110_the_gate_precedes_existence(self):
        """A refused role learns nothing about which request ids are real."""
        record = self._lab_request()
        real, _p = self._collect(
            record, user=self.nurse, password=self.nurse_password
        )
        fake, _p = self._lab_post(
            COLLECT % 99999999, user=self.nurse, password=self.nurse_password
        )
        self.assertEqual(real.status_code, 403)
        self.assertEqual(fake.status_code, 403)

    # ------------------------------------------------------------------
    # Workflow
    # ------------------------------------------------------------------
    def test_120_a_ready_request_becomes_sample_collected(self):
        record = self._lab_request()
        self.assertFalse(record.sudo().billing_blocked)

        _response, payload = self._collect(record)
        detail = payload["data"]["request"]

        self.assertEqual(detail["state"], "sample_collected")
        self.assertEqual(detail["status"], "sample_collected")
        self.assertEqual(detail["status_label"], "Sample collected")
        self.assertEqual(detail["id"], record.id)

    def test_121_the_response_is_serialized_after_the_transition(self):
        """The status the bench renders is DERIVED, never guessed from the click."""
        record = self._lab_request()
        _response, payload = self._collect(record)
        record.invalidate_recordset()
        self.assertEqual(payload["data"]["request"]["state"], record.state)

    def test_122_a_blocked_request_cannot_be_collected(self):
        record = self._lab_request(tests=[self.prepaid_test])
        self.assertTrue(record.sudo().billing_blocked)

        response, payload = self._collect(record)
        self.assertEqual(response.status_code, 422)
        self.assertEqual(payload["error"]["code"], "lab_not_financially_cleared")

    def test_123_a_blocked_request_stays_requested(self):
        """ATOMICITY. The refusal leaves the clinical state exactly as it was."""
        record = self._lab_request(tests=[self.prepaid_test])
        self._collect(record)
        record.invalidate_recordset()
        self.assertEqual(record.state, "requested")
        self.assertTrue(record.sudo().billing_blocked)

    def test_124_the_authoritative_method_rechecks_clearance(self):
        """Paying makes the SAME request collectable, with no desk change.

        Proves the endpoint asks the model rather than caching a verdict: the
        first call is refused, the encounter is settled through the cashier's
        own path, and the second call succeeds.
        """
        record = self._lab_request(tests=[self.prepaid_test])
        refused, _payload = self._collect(record)
        self.assertEqual(refused.status_code, 422)

        self._pay(record.encounter_id)
        record.invalidate_recordset()
        self.env.invalidate_all()
        self.assertFalse(record.sudo().billing_blocked)

        allowed, payload = self._collect(record)
        self.assertEqual(allowed.status_code, 200)
        self.assertEqual(payload["data"]["request"]["status"], "sample_collected")

    def test_125_a_collected_request_cannot_be_collected_again(self):
        """IDEMPOTENCY IS THE STATE MACHINE'S. No token, no second transition."""
        record = self._lab_request()
        first, _payload = self._collect(record)
        self.assertEqual(first.status_code, 200)

        second, payload = self._collect(record)
        self.assertEqual(second.status_code, 422)
        self.assertEqual(payload["error"]["code"], "invalid_workflow_state")

        record.invalidate_recordset()
        self.assertEqual(record.state, "sample_collected")

    def test_126_in_progress_cannot_be_collected(self):
        record = self._in_progress()
        response, payload = self._collect(record)
        self.assertEqual(response.status_code, 422)
        self.assertEqual(payload["error"]["code"], "invalid_workflow_state")
        record.invalidate_recordset()
        self.assertEqual(record.state, "in_progress")

    def test_127_completed_cannot_be_collected(self):
        record = self._completed()
        response, _payload = self._collect(record)
        self.assertEqual(response.status_code, 422)
        record.invalidate_recordset()
        self.assertEqual(record.state, "completed")

    def test_128_cancelled_cannot_be_collected(self):
        record = self._cancelled()
        response, _payload = self._collect(record)
        self.assertEqual(response.status_code, 422)
        record.invalidate_recordset()
        self.assertEqual(record.state, "cancelled")

    def test_129_draft_cannot_be_collected(self):
        record = self._lab_request(confirm=False)
        response, _payload = self._collect(record)
        self.assertEqual(response.status_code, 422)
        record.invalidate_recordset()
        self.assertEqual(record.state, "draft")

    def test_130_an_unknown_request_is_a_flat_404(self):
        response, payload = self._lab_post(COLLECT % 99999999)
        self.assertEqual(response.status_code, 404)
        self.assertEqual(payload["error"]["code"], "lab_request_not_found")
        self.assertEqual(
            payload["error"]["message"], "Laboratory request not found."
        )

    def test_131_a_zero_id_is_rejected_before_any_lookup(self):
        response, payload = self._lab_post(COLLECT % 0)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(payload["error"]["code"], "invalid_request_id")

    def test_132_the_read_routes_still_refuse_a_post(self):
        """Slice 2 adds ONE write route. The reads stay reads.

        url_open directly rather than _lab_post: a router-level 404/405 is an
        HTML page, not the JSON envelope, so there is nothing to decode.
        """
        record = self._lab_request()
        for url in (SESSION, WORKLIST, DETAIL % record.id):
            self._auth(self.lab_tech, self.lab_password)
            response = self.url_open(
                url, data="{}", headers={"Content-Type": "application/json"}
            )
            self.assertIn(
                response.status_code, (404, 405),
                "%s must not accept a POST." % url,
            )

    # ------------------------------------------------------------------
    # Confidentiality -- the half that is specific to Slice 2
    # ------------------------------------------------------------------
    def test_140_a_clearance_refusal_carries_no_money_for_a_technician(self):
        record = self._lab_request(tests=[self.prepaid_test])
        _response, payload = self._collect(record)
        blob = json.dumps(payload).lower()
        for banned in FORBIDDEN_KEYS:
            self.assertNotIn(banned, blob, "'%s' must not reach the bench." % banned)
        for rendering in ("640", "640.0", "640.00"):
            self.assertNotIn(rendering, json.dumps(payload))

    def test_141_a_clearance_refusal_carries_no_money_for_a_MANAGER(self):
        """THE TRAP THIS SLICE HAD TO CLOSE, AND IT IS NOT THEORETICAL.

        hospital_billing._clearance_error builds its detail line from the
        CALLER'S groups:

            may_see_cash -> "<test> -- unpaid, 640.00 due"
            otherwise    -> "<test> -- payment not cleared (see front desk)"

        and may_see_cash is true for receptionist, accountant, MANAGER and
        SYSTEM ADMINISTRATOR. Manager and admin are both in LAB_DESK_GROUPS, so
        forwarding that sentence verbatim would have put an amount on the
        Laboratory Desk for exactly those two roles -- a confidentiality rule
        that held for a lab technician and broke silently for their manager.

        Asserted at the HTTP boundary AND against the raw model error, so this
        fails loudly if the sanitisation is ever removed.
        """
        record = self._lab_request(tests=[self.prepaid_test])

        # The model's own sentence DOES name the amount for this role.
        raw = record.with_user(self.manager)._clearance_error(
            {"cleared": False, "reason": "probe"}
        )
        self.assertIn(
            "640", str(raw),
            "Fixture drift: the model error no longer contains the amount, so "
            "this test would pass for the wrong reason.",
        )

        # The desk's answer does not.
        response, payload = self._collect(
            record, user=self.manager, password=self.manager_password
        )
        self.assertEqual(response.status_code, 422)
        self.assertEqual(payload["error"]["code"], "lab_not_financially_cleared")
        blob = json.dumps(payload)
        self.assertNotIn("640", blob)
        for banned in FORBIDDEN_KEYS:
            self.assertNotIn(banned, blob.lower())

    def test_142_a_workflow_refusal_forwards_the_models_own_sentence(self):
        """Not every refusal is sanitised -- only the one that can carry money.

        A state-machine refusal names states and nothing else, and it is the
        only thing that tells the technician WHY, so Odoo's wording is kept.
        """
        record = self._collected()
        _response, payload = self._collect(record)
        self.assertIn("requested", payload["error"]["message"].lower())
        for banned in FORBIDDEN_KEYS:
            self.assertNotIn(banned, payload["error"]["message"].lower())

    def test_143_the_success_payload_is_the_slice_1_shape(self):
        """No new field, and no financial field, arrives with the mutation."""
        record = self._lab_request()
        _response, payload = self._collect(record)
        detail = payload["data"]["request"]
        self.assertEqual(
            set(detail), EXPECTED_ROW_KEYS | EXPECTED_DETAIL_EXTRA_KEYS
        )
        billing_keys = [key for key in detail if "billing" in key]
        self.assertEqual(billing_keys, ["billing_blocked"])
        self.assertIsInstance(detail["billing_blocked"], bool)

    def test_144_no_money_vocabulary_in_a_successful_collection(self):
        record = self._lab_request()
        _response, payload = self._collect(record)
        blob = json.dumps(payload).lower()
        for banned in FORBIDDEN_KEYS:
            self.assertNotIn(banned, blob)

    # ------------------------------------------------------------------
    # Atomicity
    # ------------------------------------------------------------------
    def test_150_a_refused_collection_changes_no_charge_state(self):
        """THE SAVEPOINT'S REASON FOR EXISTING.

        check_financial_clearance(persist=True) WRITES financial_clearance_state
        on the billing account before the refusal is raised. Without the
        savepoint the controller would commit that write while telling the
        bench nothing happened, and the charges would be left mid-flight.
        """
        record = self._lab_request(tests=[self.prepaid_test])
        charges = record.sudo().charge_line_ids
        before = {c.id: (c.charge_state, c.delivery_state) for c in charges}

        response, _payload = self._collect(record)
        self.assertEqual(response.status_code, 422)

        record.invalidate_recordset()
        self.env.invalidate_all()
        after = {
            c.id: (c.charge_state, c.delivery_state)
            for c in record.sudo().charge_line_ids
        }
        self.assertEqual(before, after, "A refusal must move no charge.")
        self.assertEqual(record.state, "requested")

    def test_151_a_successful_collection_commences_the_charges(self):
        """The override's own post-transition work really did run.

        Proves the endpoint called the AUTHORITATIVE method rather than writing
        state: marking charges in progress is hospital_billing's behaviour, not
        anything this API knows how to do.
        """
        record = self._lab_request()
        response, _payload = self._collect(record)
        self.assertEqual(response.status_code, 200)

        self.env.invalidate_all()
        record.invalidate_recordset()
        self.assertEqual(record.state, "sample_collected")
        self.assertTrue(
            record.sudo().charge_line_ids,
            "The fixture must have raised charges at confirmation.",
        )
        for charge in record.sudo().charge_line_ids:
            self.assertNotEqual(
                charge.delivery_state, "delivered",
                "Collection commences work; it does not deliver it.",
            )

    def test_152_the_queue_reflects_the_collection_immediately(self):
        """The desk and the database agree on the very next read."""
        record = self._lab_request()
        before = self._row_for(record)
        self.assertEqual(before["status"], "ready_for_collection")

        self._collect(record)

        after = self._row_for(record)
        self.assertEqual(after["status"], "sample_collected")


@tagged("post_install", "-at_install", "lab_desk")
class TestLabDeskSummary(LabDeskCase):
    """The lane counts. A UAT defect fix, and these pin what went wrong.

    THE BUG. `counts` used to be computed from the rows the client had just
    been handed. Those rows are narrowed twice -- to the SELECTED lane's states
    and then to `limit` -- so every lane the technician had not clicked read
    zero, and clicking one "discovered" its real number. In the UAT database
    Ready for collection showed 0 and became 50 on click, because the oldest
    100 of 173 active rows happened to contain none of it.

    THE CONTRACT NOW. `summary` counts the whole date+search scope, ignoring
    the selected lane, and `meta.row_count` / `meta.truncated` continue to
    describe the page. They answer different questions and must never be the
    same number.
    """

    SUMMARY_KEYS = {
        "active_bench", "draft", "awaiting_clearance", "ready_for_collection",
        "sample_collected", "in_progress", "completed", "cancelled",
        "requested_total",
    }

    def _summary(self, **params):
        _response, payload = self._lab_get(WORKLIST, **params)
        return payload["data"]["summary"], payload["data"]

    # ------------------------------------------------------------------
    # Present on the first load, for every lane
    # ------------------------------------------------------------------
    def test_200_every_lane_count_arrives_on_the_default_load(self):
        """THE DEFECT, ASSERTED DIRECTLY: no lane needs to be clicked."""
        self._lab_request()
        self._lab_request(tests=[self.prepaid_test])
        self._collected()
        self._in_progress()
        self._completed()
        self._cancelled()
        self._lab_request(confirm=False)

        summary, _payload = self._summary()
        self.assertEqual(set(summary), self.SUMMARY_KEYS)
        for key in self.SUMMARY_KEYS:
            self.assertIsNotNone(summary[key], "%s must be counted" % key)

        # Every lane is non-zero WITHOUT having been selected -- the default
        # lane is active bench, which excludes three of these.
        self.assertGreaterEqual(summary["ready_for_collection"], 1)
        self.assertGreaterEqual(summary["awaiting_clearance"], 1)
        self.assertGreaterEqual(summary["sample_collected"], 1)
        self.assertGreaterEqual(summary["in_progress"], 1)
        self.assertGreaterEqual(summary["draft"], 1)
        self.assertGreaterEqual(summary["completed"], 1)
        self.assertGreaterEqual(summary["cancelled"], 1)

    def test_201_ready_count_exists_without_selecting_that_lane(self):
        ready = self._lab_request()
        summary, payload = self._summary(status="awaiting_clearance")
        # The selected lane returns no ready rows at all...
        self.assertNotIn(ready.id, self._rows_by_id({"data": payload}))
        # ...and the badge for it is still right.
        self.assertGreaterEqual(summary["ready_for_collection"], 1)

    def test_202_awaiting_count_exists_without_selecting_that_lane(self):
        blocked = self._lab_request(tests=[self.prepaid_test])
        summary, payload = self._summary(status="ready_for_collection")
        self.assertNotIn(blocked.id, self._rows_by_id({"data": payload}))
        self.assertGreaterEqual(summary["awaiting_clearance"], 1)

    def test_203_selecting_a_lane_zeroes_nothing(self):
        """Counts are identical whichever lane is selected, for one scope."""
        self._lab_request()
        self._lab_request(tests=[self.prepaid_test])
        self._collected()
        self._completed()

        baseline, _p = self._summary()
        for lane in ("ready_for_collection", "awaiting_clearance",
                     "sample_collected", "completed", "cancelled", "draft"):
            other, _p = self._summary(status=lane)
            self.assertEqual(
                other, baseline,
                "Selecting '%s' must not change any lane count." % lane,
            )

    # ------------------------------------------------------------------
    # The clearance split
    # ------------------------------------------------------------------
    def test_210_blocked_requested_counts_only_as_awaiting(self):
        base, _p = self._summary()
        self._lab_request(tests=[self.prepaid_test])
        after, _p = self._summary()
        self.assertEqual(
            after["awaiting_clearance"], base["awaiting_clearance"] + 1
        )
        self.assertEqual(
            after["ready_for_collection"], base["ready_for_collection"]
        )
        self.assertEqual(after["requested_total"], base["requested_total"] + 1)

    def test_211_cleared_requested_counts_only_as_ready(self):
        base, _p = self._summary()
        self._lab_request()
        after, _p = self._summary()
        self.assertEqual(
            after["ready_for_collection"], base["ready_for_collection"] + 1
        )
        self.assertEqual(
            after["awaiting_clearance"], base["awaiting_clearance"]
        )

    def test_212_the_split_always_totals_the_requested_rows(self):
        self._lab_request()
        self._lab_request(tests=[self.prepaid_test])
        summary, _p = self._summary()
        self.assertEqual(
            summary["awaiting_clearance"] + summary["ready_for_collection"],
            summary["requested_total"],
        )

    def test_213_active_bench_is_the_four_active_statuses(self):
        """Pinned, so draft/completed/cancelled can never be folded in."""
        self._lab_request()
        self._lab_request(tests=[self.prepaid_test])
        self._collected()
        self._in_progress()
        self._completed()
        self._cancelled()
        self._lab_request(confirm=False)

        summary, _p = self._summary()
        self.assertEqual(
            summary["active_bench"],
            sum(summary[status] for status in LAB_DESK_ACTIVE_STATUSES),
        )
        # And it really does exclude the other three.
        self.assertLess(
            summary["active_bench"],
            sum(summary[s] for s in LAB_DESK_STATUS_LABELS),
        )

    # ------------------------------------------------------------------
    # The common filters narrow the counts; the lane does not
    # ------------------------------------------------------------------
    def test_220_counts_respect_the_date_filter(self):
        record = self._lab_request()
        same_day, _p = self._summary(date=str(record.request_date))
        self.assertGreaterEqual(same_day["ready_for_collection"], 1)

        other_day, _p = self._summary(date="2001-01-01")
        for key in self.SUMMARY_KEYS:
            self.assertEqual(
                other_day[key], 0,
                "A day with no laboratory work counts zero everywhere.",
            )

    def test_221_counts_respect_the_search_filter(self):
        record = self._lab_request()
        blocked = self._lab_request(tests=[self.prepaid_test])

        mine, _p = self._summary(q=record.name)
        self.assertEqual(mine["ready_for_collection"], 1)
        self.assertEqual(mine["awaiting_clearance"], 0)
        self.assertEqual(mine["requested_total"], 1)

        theirs, _p = self._summary(q=blocked.name)
        self.assertEqual(theirs["awaiting_clearance"], 1)
        self.assertEqual(theirs["ready_for_collection"], 0)

    def test_222_search_counts_describe_the_whole_status_distribution(self):
        """One patient, several statuses: the badges describe all of them.

        The scenario the brief names -- searching a patient must show that
        patient's distribution across every lane, not only the selected one.
        """
        appointment, encounter = self._ready_visit()

        def _request_for(tests, collect=False):
            record = self.env["hospital.laboratory.request"].sudo().create({
                "patient_id": appointment.patient_id.id,
                "physician_id": self.doctor.id,
                "appointment_id": appointment.id,
                "encounter_id": encounter.id,
                "line_ids": [(0, 0, {"test_id": test.id}) for test in tests],
            })
            record.action_confirm_request()
            if collect:
                record.action_mark_sample_collected()
            record.invalidate_recordset()
            return record

        _request_for([self.cleared_test])
        _request_for([self.second_test], collect=True)
        name = appointment.patient_id.name

        summary, _p = self._summary(q=name, status="ready_for_collection")
        self.assertGreaterEqual(summary["ready_for_collection"], 1)
        self.assertGreaterEqual(
            summary["sample_collected"], 1,
            "Selecting one lane must not hide the patient's other work.",
        )

    # ------------------------------------------------------------------
    # Truncation must not make the summary lie
    # ------------------------------------------------------------------
    def test_230_the_summary_is_not_the_page(self):
        """THE EXACT UAT MECHANISM, reproduced in miniature.

        With limit=1 the page holds one row and reports truncated; the summary
        still counts every matching request. A count that equalled the page
        would be the old bug.
        """
        for _index in range(3):
            self._lab_request()
        summary, payload = self._summary(limit=1)

        self.assertEqual(len(payload["rows"]), 1)
        self.assertEqual(payload["meta"]["row_count"], 1)
        self.assertTrue(payload["meta"]["truncated"])
        self.assertGreaterEqual(
            summary["ready_for_collection"], 3,
            "The summary counts the scope, never the page.",
        )

    def test_231_a_truncated_page_still_counts_unselected_lanes(self):
        self._lab_request(tests=[self.prepaid_test])
        for _index in range(3):
            self._lab_request()
        summary, payload = self._summary(status="ready_for_collection", limit=1)
        self.assertTrue(payload["meta"]["truncated"])
        self.assertGreaterEqual(summary["awaiting_clearance"], 1)

    def test_232_summary_exact_is_reported(self):
        self._lab_request()
        _response, payload = self._lab_get(WORKLIST)
        self.assertTrue(payload["data"]["meta"]["summary_exact"])

    # ------------------------------------------------------------------
    # Contract hygiene
    # ------------------------------------------------------------------
    def test_240_the_old_row_scoped_counts_key_is_gone(self):
        """Left in place it would be a trap -- a future reader would grab it
        and rebuild exactly this bug."""
        _response, payload = self._lab_get(WORKLIST)
        self.assertNotIn("counts", payload["data"])

    def test_241_the_summary_carries_no_financial_field(self):
        self._lab_request(tests=[self.prepaid_test])
        _response, payload = self._lab_get(WORKLIST)
        blob = json.dumps(payload["data"]["summary"]).lower()
        for banned in FORBIDDEN_KEYS:
            self.assertNotIn(banned, blob)
        for rendering in ("640", "640.0", "640.00"):
            self.assertNotIn(rendering, json.dumps(payload["data"]["summary"]))

    def test_242_a_denied_role_gets_no_summary(self):
        self._lab_request()
        response, payload = self._lab_get(
            WORKLIST, user=self.nurse, password=self.nurse_password
        )
        self.assertEqual(response.status_code, 403)
        self.assertNotIn("data", payload)

    # ------------------------------------------------------------------
    # Slice 2 interaction
    # ------------------------------------------------------------------
    def test_250_collecting_moves_one_count_across(self):
        """After a collection the badges must reconcile on the next read.

        ready_for_collection -1, sample_collected +1, active_bench unchanged --
        the request moved between lanes, it did not leave the bench.
        """
        record = self._lab_request()
        before, _p = self._summary()

        response, _payload = self._collect(record)
        self.assertEqual(response.status_code, 200)

        after, _p = self._summary()
        self.assertEqual(
            after["ready_for_collection"], before["ready_for_collection"] - 1
        )
        self.assertEqual(
            after["sample_collected"], before["sample_collected"] + 1
        )
        self.assertEqual(
            after["active_bench"], before["active_bench"],
            "Collection moves work within the bench, not off it.",
        )

    def test_251_a_refused_collection_leaves_the_counts_alone(self):
        record = self._lab_request(tests=[self.prepaid_test])
        before, _p = self._summary()

        response, _payload = self._collect(record)
        self.assertEqual(response.status_code, 422)

        after, _p = self._summary()
        self.assertEqual(after, before)
