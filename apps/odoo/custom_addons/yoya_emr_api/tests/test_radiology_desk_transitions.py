"""Radiology Desk Slice 2: Schedule and Start exam, and the state authority
they rely on.

WHAT THESE TESTS ARE FOR
------------------------
  1. WHO. Radiology Technician, Radiologist, Manager and System Administrator
     may schedule and start; every other role is refused before a record is
     touched.
  2. WHAT. Schedule moves requested -> scheduled only for a clear request with
     an active study; Start moves scheduled -> in_progress only through
     action_mark_in_progress(), so hospital_billing's clearance gate and charge
     moves run exactly as they do from the Odoo form.
  3. NOTHING BYPASSES THEM. A direct state write -- ORM, sudo, or a genuine
     JSON-RPC call_kw with any context -- is refused, including for a doctor on
     their own order.
  4. ALL OR NOTHING. A refusal, a side-effect crash or a serialization failure
     leaves no state, no audit row, no charge move and no persisted clearance.
  5. NO MONEY. Success and error payloads carry no amount, invoice, receipt,
     charge or clearance message, for Manager and Admin as for a technician.
  6. NOTHING ELSE MOVED. Lanes and counts follow the transition, and the Doctor
     Desk reads the same order status and still has no report to show.
"""
import json
from unittest.mock import patch

from odoo.tests import tagged
from odoo.tests.common import JsonRpcException

from ..controllers.radiology import (
    NO_ACTIVE_STUDY_MESSAGE,
    SCHEDULE_BLOCKED_MESSAGE,
    START_BLOCKED_MESSAGE,
    START_FAILED_MESSAGE,
)
from .test_radiology_desk_api import (
    DETAIL,
    FORBIDDEN_KEY_FRAGMENTS,
    FORBIDDEN_TEXT,
    RAD_DESK_LANES,
    RadDeskCase,
    _walk_keys,
)

SCHEDULE = DETAIL + "/schedule"
START = DETAIL + "/start"
DOCTOR_ORDERS = "/yoya-emr/api/v1/doctor/visits/%s/orders/radiology"
DOCTOR_RESULTS = "/yoya-emr/api/v1/doctor/visits/%s/results"

CONTROLLER = "odoo.addons.yoya_emr_api.controllers.radiology"
ENGINE = "odoo.addons.hospital_billing.models.billing_engine.HospitalBillingEngine"


class RadTransitionCase(RadDeskCase):
    """RadDeskCase plus transition fixtures that use only authoritative paths."""

    # ------------------------------------------------------------------
    # HTTP
    # ------------------------------------------------------------------
    def _act(self, url, record, user=None, password=None):
        self._auth(user or self.rad_tech, password or self.tech_password)
        response = self.url_open(
            url % record.id, data="{}", headers={"Content-Type": "application/json"}
        )
        record.invalidate_recordset()
        return response, json.loads(response.text)

    # ------------------------------------------------------------------
    # Fixtures -- every state reached through a workflow method
    # ------------------------------------------------------------------
    def _clear_requested(self):
        appointment, encounter, patient = self._visit()
        record = self._new_order(appointment)
        self._settle(encounter)
        record.invalidate_recordset()
        self.assertEqual((record.state, record.billing_blocked), ("requested", False))
        return record, patient, appointment, encounter

    def _blocked_requested(self):
        appointment, encounter, patient = self._visit()
        record = self._new_order(appointment)
        self.assertEqual((record.state, record.billing_blocked), ("requested", True))
        return record, patient, appointment, encounter

    def _clear_scheduled(self):
        record, patient, appointment, encounter = self._clear_requested()
        record.sudo().action_schedule()
        self.assertEqual(record.state, "scheduled")
        return record, patient, appointment, encounter

    def _blocked_scheduled(self):
        record, patient, appointment, encounter = self._blocked_requested()
        # The MODEL permits scheduling an unpaid request; only the desk refuses.
        record.sudo().action_schedule()
        record.invalidate_recordset()
        self.assertEqual((record.state, record.billing_blocked), ("scheduled", True))
        return record, patient, appointment, encounter

    def _audit(self, record):
        return self.env["hospital.audit.log"].sudo().search([
            ("model_name", "=", "hospital.radiology.request"),
            ("record_id", "=", record.id),
            ("action_type", "=", "state_change"),
        ], order="id asc")

    def _charge_snapshot(self, record):
        charges = record.sudo().charge_line_ids
        charges.invalidate_recordset()
        return sorted(
            (c.id, c.charge_state, c.delivery_state, c.qty_delivered) for c in charges
        )

    def _account_clearance(self, encounter):
        account = encounter.sudo().billing_account_id
        account.invalidate_recordset()
        return account.financial_clearance_state

    def _make_start_side_effects_observable(self, record, encounter):
        """Arrange the visit so a Start WOULD write the encounter and the clearance.

        A Doctor-ordered request comes from a visit whose encounter is already
        'active' and whose stored clearance already matches what the gate
        computes -- so, left alone, Start's encounter.action_start() never runs
        and persist=True writes nothing, and a "rolled back" assertion on either
        would pass vacuously. This puts the encounter back to checked_in and the
        stored clearance on a stale value the gate must overwrite. Returns
        (computed clearance, stale clearance).
        """
        enc = encounter.sudo()
        enc.write({"state": "checked_in"})
        computed = self.env["hospital.billing.engine"].sudo().check_financial_clearance(
            enc, persist=False, charges=record.sudo().charge_line_ids
        )["state"]
        stale = "not_required" if computed != "not_required" else "pending"
        enc.billing_account_id.write({"financial_clearance_state": stale})
        enc.invalidate_recordset()
        record.invalidate_recordset()
        self.assertEqual(enc.state, "checked_in")
        self.assertEqual(self._account_clearance(encounter), stale)
        self.assertNotEqual(computed, stale)
        return computed, stale

    def _assert_money_free(self, payload, text):
        for key in _walk_keys(payload):
            for fragment in FORBIDDEN_KEY_FRAGMENTS:
                self.assertNotIn(fragment, key.lower(), key)
        for marker in FORBIDDEN_TEXT:
            self.assertNotIn(marker, text, marker)
        self.assertNotRegex(text, r"\d+\.\d{2}\b", "no amount-shaped figure")
        for word in ("ETB", "Birr", "payable", "invoice", "receipt"):
            self.assertNotIn(word, text)


# ===========================================================================
# Authorization
# ===========================================================================
@tagged("post_install", "-at_install", "radiology_desk")
class TestRadiologyTransitionAuthorization(RadTransitionCase):

    def test_01_to_04_every_desk_role_may_schedule_and_start(self):
        for user, password in (
            (self.rad_tech, self.tech_password),
            (self.radiologist, self.radiologist_password),
            (self.manager, self.manager_password),
            (self.sysadmin, self.sysadmin_password),
        ):
            record, *_rest = self._clear_requested()
            response, payload = self._act(SCHEDULE, record, user, password)
            self.assertEqual(response.status_code, 200, (user.login, payload))
            self.assertEqual(record.state, "scheduled", user.login)
            response, payload = self._act(START, record, user, password)
            self.assertEqual(response.status_code, 200, (user.login, payload))
            self.assertEqual(record.state, "in_progress", user.login)

    def test_05_to_09_other_roles_are_refused_and_nothing_moves(self):
        requested, *_rest = self._clear_requested()
        scheduled, *_rest = self._clear_scheduled()
        for user, password in (
            (self.lab_tech, self.lab_password),
            (self.doctor_user, self.doctor_password),
            (self.nurse, self.nurse_password),
            (self.receptionist, self.receptionist_password),
            (self.cashier, self.cashier_password),
        ):
            for url, record in ((SCHEDULE, requested), (START, scheduled)):
                response, payload = self._act(url, record, user, password)
                self.assertEqual(response.status_code, 403, (user.login, url))
                self.assertEqual(payload["error"]["code"], "radiology_desk_not_authorized")
        self.assertEqual((requested.state, scheduled.state), ("requested", "scheduled"))

    def test_10_unauthenticated_never_reaches_the_endpoint(self):
        record, *_rest = self._clear_requested()
        self.authenticate(None, None)
        for url in (SCHEDULE, START):
            response = self.url_open(
                url % record.id, data="{}",
                headers={"Content-Type": "application/json"}, allow_redirects=False,
            )
            self.assertIn(response.status_code, (301, 302, 303, 401, 403), url)
        record.invalidate_recordset()
        self.assertEqual(record.state, "requested")


# ===========================================================================
# Schedule
# ===========================================================================
@tagged("post_install", "-at_install", "radiology_desk")
class TestRadiologySchedule(RadTransitionCase):

    def test_11_to_13_clear_requested_is_scheduled_with_lane_and_audit(self):
        record, *_rest = self._clear_requested()
        before = len(self._audit(record))
        response, payload = self._act(SCHEDULE, record)
        self.assertEqual(response.status_code, 200, payload)
        detail = payload["data"]["request"]
        self.assertEqual((detail["id"], detail["state"], detail["lane"]),
                         (record.id, "scheduled", "ready_to_start"))
        self.assertEqual(record.state, "scheduled")
        audit = self._audit(record)
        self.assertEqual(len(audit), before + 1)
        self.assertEqual((audit[-1].old_value, audit[-1].new_value),
                         ("State: requested", "State: scheduled"))
        self.assertEqual(audit[-1].user_id, self.rad_tech)

    def test_14_to_16_schedule_is_refused_outside_requested(self):
        record, *_rest = self._clear_requested()
        self.assertEqual(self._act(SCHEDULE, record)[0].status_code, 200)
        audit_count = len(self._audit(record))
        response, payload = self._act(SCHEDULE, record)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(payload["error"]["code"], "radiology_request_not_schedulable")
        self._act(START, record)
        self.assertEqual(record.state, "in_progress")
        response, payload = self._act(SCHEDULE, record)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(payload["error"]["code"], "radiology_request_not_schedulable")
        self.assertEqual(record.state, "in_progress")
        self.assertEqual(len(self._audit(record)), audit_count + 1, "only the start")

    def test_17_to_19_blocked_request_is_refused_money_free_and_unchanged(self):
        record, _patient, _appointment, encounter = self._blocked_requested()
        audit_count = len(self._audit(record))
        charges = self._charge_snapshot(record)
        clearance = self._account_clearance(encounter)
        response, payload = self._act(SCHEDULE, record)
        self.assertEqual(response.status_code, 422)
        self.assertEqual(payload["error"]["code"], "radiology_request_awaiting_clearance")
        self.assertEqual(payload["error"]["message"], SCHEDULE_BLOCKED_MESSAGE)
        self._assert_money_free(payload, response.text)
        self.assertEqual(record.state, "requested")
        self.assertEqual(len(self._audit(record)), audit_count)
        self.assertEqual(self._charge_snapshot(record), charges)
        self.assertEqual(self._account_clearance(encounter), clearance)

    def test_20_malformed_requests_are_handled_safely(self):
        bare, _patient = self._legacy_request(state="requested", lines=False)
        response, payload = self._act(SCHEDULE, bare)
        self.assertEqual(response.status_code, 422)
        self.assertEqual(payload["error"]["code"], "radiology_request_no_active_study")
        self.assertEqual(payload["error"]["message"], NO_ACTIVE_STUDY_MESSAGE % (bare.name, "scheduled"))
        self.assertEqual(bare.state, "requested")

        # A legacy request with a study but no encounter or charges: the model
        # permits scheduling and the desk policy is satisfied, so it proceeds.
        legacy, _patient = self._legacy_request(state="requested")
        response, payload = self._act(SCHEDULE, legacy)
        self.assertEqual(response.status_code, 200, payload)
        self.assertEqual(legacy.state, "scheduled")

    def test_a_report_conflict_needs_review_instead_of_scheduling(self):
        record, *_rest = self._clear_requested()
        self._report(record, "draft")
        self._report(record, "draft")
        response, payload = self._act(SCHEDULE, record)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(payload["error"]["code"], "radiology_request_needs_review")
        self.assertEqual(record.state, "requested")

    def test_a_missing_request_is_404(self):
        record, _patient = self._legacy_request(state="requested")
        record.sudo().write({"active": False})
        response, payload = self._act(SCHEDULE, record)
        self.assertEqual(response.status_code, 404)
        self.assertEqual(payload["error"]["code"], "radiology_request_not_found")


# ===========================================================================
# Start exam
# ===========================================================================
@tagged("post_install", "-at_install", "radiology_desk")
class TestRadiologyStart(RadTransitionCase):

    def test_21_to_24_clear_scheduled_starts_through_the_billing_gate(self):
        record, *_rest = self._clear_scheduled()
        before = len(self._audit(record))
        self.assertEqual({c[2] for c in self._charge_snapshot(record)}, {"pending"})
        response, payload = self._act(START, record)
        self.assertEqual(response.status_code, 200, payload)
        detail = payload["data"]["request"]
        self.assertEqual((detail["state"], detail["lane"]), ("in_progress", "awaiting_report"))
        self.assertEqual(record.state, "in_progress")
        # hospital_billing's own side effect, reached only through the method.
        self.assertEqual({c[2] for c in self._charge_snapshot(record)}, {"in_progress"})
        audit = self._audit(record)
        self.assertEqual(len(audit), before + 1)
        self.assertEqual((audit[-1].old_value, audit[-1].new_value),
                         ("State: scheduled", "State: in_progress"))

    def test_25_to_27_start_is_refused_outside_scheduled(self):
        record, *_rest = self._clear_scheduled()
        self.assertEqual(self._act(START, record)[0].status_code, 200)
        response, payload = self._act(START, record)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(payload["error"]["code"], "radiology_request_not_startable")

        requested, *_rest = self._clear_requested()
        response, payload = self._act(START, requested)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(payload["error"]["code"], "radiology_request_not_startable")
        self.assertEqual(requested.state, "requested")

        completed, _patient = self._legacy_request(state="completed")
        response, payload = self._act(START, completed)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(payload["error"]["code"], "radiology_request_not_startable")
        self.assertEqual(completed.state, "completed")

    def test_28_29_31_blocked_start_is_refused_money_free_and_unchanged(self):
        record, _patient, _appointment, encounter = self._blocked_scheduled()
        audit_count = len(self._audit(record))
        charges = self._charge_snapshot(record)
        clearance = self._account_clearance(encounter)
        response, payload = self._act(START, record)
        self.assertEqual(response.status_code, 422)
        self.assertEqual(payload["error"]["code"], "radiology_request_start_blocked")
        self.assertEqual(payload["error"]["message"], START_BLOCKED_MESSAGE)
        self._assert_money_free(payload, response.text)
        self.assertEqual(record.state, "scheduled")
        self.assertEqual(len(self._audit(record)), audit_count)
        self.assertEqual(self._charge_snapshot(record), charges)
        self.assertEqual(self._account_clearance(encounter), clearance)

    def test_30_legacy_request_without_encounter_or_charges_fails_safely(self):
        legacy, _patient = self._legacy_request(state="scheduled")
        self.assertFalse(legacy.encounter_id)
        audit_count = len(self._audit(legacy))
        response, payload = self._act(START, legacy)
        self.assertEqual(response.status_code, 422, payload)
        self.assertEqual(payload["error"]["code"], "radiology_request_start_blocked")
        self.assertEqual(payload["error"]["message"], START_FAILED_MESSAGE)
        self.assertNotIn("Traceback", response.text)
        self.assertEqual(legacy.state, "scheduled")
        self.assertEqual(len(self._audit(legacy)), audit_count)

        bare, _patient = self._legacy_request(state="scheduled", lines=False)
        response, payload = self._act(START, bare)
        self.assertEqual(response.status_code, 422)
        self.assertEqual(payload["error"]["code"], "radiology_request_no_active_study")
        self.assertEqual(bare.state, "scheduled")


# ===========================================================================
# State authority through the real RPC channel
# ===========================================================================
@tagged("post_install", "-at_install", "radiology_desk")
class TestRadiologyStateAuthorityOverRpc(RadTransitionCase):

    def _rpc_write(self, record, state, user, password, context=None):
        self._auth(user, password)
        return self.make_jsonrpc_request(
            "/web/dataset/call_kw/hospital.radiology.request/write",
            {
                "model": "hospital.radiology.request",
                "method": "write",
                "args": [[record.id], {"state": state}],
                "kwargs": {"context": context or {}},
            },
        )

    def test_32_to_36_direct_state_writes_over_json_rpc_are_refused(self):
        requested, *_rest = self._clear_requested()
        scheduled, *_rest = self._clear_scheduled()
        draft, _patient = self._legacy_request(state="draft")
        forged = {
            "skip_radiology_request_write_audit": True,
            "hospital_radiology_request_state_capability": True,
        }
        for record, state in (
            (requested, "scheduled"),
            (scheduled, "in_progress"),
            (requested, "in_progress"),
            (draft, "completed"),
        ):
            before = record.state
            for context in (None, forged):
                with self.assertRaises(JsonRpcException, msg=(record.name, state, context)):
                    self._rpc_write(record, state, self.manager, self.manager_password, context)
                record.invalidate_recordset()
                self.assertEqual(record.state, before)

    def test_40_a_doctor_cannot_move_their_own_order_by_direct_write(self):
        record, *_rest = self._clear_requested()
        self.assertEqual(record.physician_id.user_id, self.doctor_user)
        for state in ("scheduled", "in_progress"):
            with self.assertRaises(JsonRpcException):
                self._rpc_write(record, state, self.doctor_user, self.doctor_password)
        record.invalidate_recordset()
        self.assertEqual(record.state, "requested")

    def test_private_workflow_helpers_are_not_callable_over_rpc(self):
        record, *_rest = self._clear_requested()
        self._auth(self.manager, self.manager_password)
        for method, args in (("_write_state", ["in_progress"]),
                             ("_workflow_write", [{"state": "in_progress"}])):
            with self.assertRaises(JsonRpcException, msg=method):
                self.make_jsonrpc_request(
                    "/web/dataset/call_kw/hospital.radiology.request/%s" % method,
                    {"model": "hospital.radiology.request", "method": method,
                     "args": [[record.id], *args], "kwargs": {}},
                )
        record.invalidate_recordset()
        self.assertEqual(record.state, "requested")

    def test_37_38_39_the_authoritative_methods_still_work_with_audit(self):
        record, *_rest = self._clear_requested()
        record.with_user(self.rad_tech).action_schedule()
        record.with_user(self.rad_tech).action_mark_in_progress()
        self.assertEqual(record.state, "in_progress")
        self.assertEqual(
            self._audit(record).mapped("new_value")[-2:],
            ["State: scheduled", "State: in_progress"],
        )


# ===========================================================================
# Atomicity
# ===========================================================================
@tagged("post_install", "-at_install", "radiology_desk")
class TestRadiologyTransitionAtomicity(RadTransitionCase):

    def _explode(self, *args, **kwargs):
        raise RuntimeError("serialization exploded")

    def test_41_42_schedule_serialization_failure_rolls_everything_back(self):
        record, *_rest = self._clear_requested()
        audit_count = len(self._audit(record))
        with patch(CONTROLLER + ".serialize_request_detail", side_effect=self._explode), \
                self.assertLogs(CONTROLLER, level="ERROR"):
            response, payload = self._act(SCHEDULE, record)
        self.assertEqual(response.status_code, 500)
        self.assertEqual(payload["error"]["code"], "radiology_request_transition_response_failed")
        self.assertNotIn("exploded", response.text)
        self.assertEqual(record.state, "requested")
        self.assertEqual(len(self._audit(record)), audit_count)

    def test_start_side_effects_are_real_under_the_rollback_arrangement(self):
        """CONTROL for the rollback tests below: the same arrangement, no fault,
        and Start starts the encounter AND persists the clearance -- so their
        "unchanged" assertions are proving a rollback, not an absence of writes."""
        record, _patient, _appointment, encounter = self._clear_scheduled()
        computed, _stale = self._make_start_side_effects_observable(record, encounter)
        response, payload = self._act(START, record)
        self.assertEqual(response.status_code, 200, payload)
        self.assertEqual(record.state, "in_progress")
        encounter.invalidate_recordset()
        self.assertEqual(encounter.sudo().state, "active")
        self.assertEqual(self._account_clearance(encounter), computed)
        self.assertEqual({c[2] for c in self._charge_snapshot(record)}, {"in_progress"})

    def test_45_46_47_start_serialization_failure_rolls_everything_back(self):
        record, _patient, _appointment, encounter = self._clear_scheduled()
        _computed, stale = self._make_start_side_effects_observable(record, encounter)
        audit_count = len(self._audit(record))
        charges = self._charge_snapshot(record)
        clearance = self._account_clearance(encounter)
        encounter_state = encounter.sudo().state
        with patch(CONTROLLER + ".serialize_request_detail", side_effect=self._explode), \
                self.assertLogs(CONTROLLER, level="ERROR"):
            response, payload = self._act(START, record)
        self.assertEqual(response.status_code, 500)
        self.assertEqual(payload["error"]["code"], "radiology_request_transition_response_failed")
        self.assertEqual(record.state, "scheduled")
        self.assertEqual(len(self._audit(record)), audit_count)
        self.assertEqual(self._charge_snapshot(record), charges)
        self.assertEqual(self._account_clearance(encounter), clearance)
        encounter.invalidate_recordset()
        self.assertEqual(encounter.sudo().state, encounter_state)
        self.assertEqual(encounter_state, "checked_in")
        self.assertEqual(clearance, stale)

    def test_44_47_a_charge_side_effect_failure_rolls_everything_back(self):
        record, _patient, _appointment, encounter = self._clear_scheduled()
        _computed, stale = self._make_start_side_effects_observable(record, encounter)
        audit_count = len(self._audit(record))
        charges = self._charge_snapshot(record)
        with patch(ENGINE + ".mark_charge_in_progress", side_effect=self._explode):
            response, payload = self._act(START, record)
        self.assertEqual(response.status_code, 422)
        self.assertEqual(payload["error"]["message"], START_FAILED_MESSAGE)
        self.assertNotIn("exploded", response.text)
        self.assertEqual(record.state, "scheduled")
        self.assertEqual(len(self._audit(record)), audit_count)
        self.assertEqual(self._charge_snapshot(record), charges)
        # The fault is AFTER the encounter was started and the clearance persisted.
        encounter.invalidate_recordset()
        self.assertEqual(encounter.sudo().state, "checked_in")
        self.assertEqual(self._account_clearance(encounter), stale)

    def test_43_a_financial_refusal_by_the_model_rolls_back_the_persisted_clearance(self):
        """The desk's own pre-check is bypassed so hospital_billing's gate is the
        one that refuses -- it PERSISTS the clearance state before raising, and
        that write must not survive."""
        record, _patient, _appointment, encounter = self._blocked_scheduled()
        _computed, stale = self._make_start_side_effects_observable(record, encounter)
        audit_count = len(self._audit(record))
        charges = self._charge_snapshot(record)
        clearance = self._account_clearance(encounter)
        self.assertEqual(clearance, stale)
        with patch(CONTROLLER + ".rad_desk_lane", return_value=("ready_to_start", None)):
            response, payload = self._act(START, record)
        self.assertEqual(response.status_code, 422)
        self.assertEqual(payload["error"]["code"], "radiology_request_start_blocked")
        self.assertEqual(payload["error"]["message"], START_BLOCKED_MESSAGE)
        self._assert_money_free(payload, response.text)
        self.assertEqual(record.state, "scheduled")
        self.assertEqual(len(self._audit(record)), audit_count)
        self.assertEqual(self._charge_snapshot(record), charges)
        self.assertEqual(self._account_clearance(encounter), clearance)
        encounter.invalidate_recordset()
        self.assertEqual(encounter.sudo().state, "checked_in")


# ===========================================================================
# Confidentiality
# ===========================================================================
@tagged("post_install", "-at_install", "radiology_desk")
class TestRadiologyTransitionConfidentiality(RadTransitionCase):

    def test_48_49_success_payloads_are_money_free(self):
        record, *_rest = self._clear_requested()
        for url in (SCHEDULE, START):
            response, payload = self._act(url, record)
            self.assertEqual(response.status_code, 200, payload)
            self._assert_money_free(payload, response.text)
            billing = [key for key in _walk_keys(payload) if "billing" in key]
            self.assertEqual(billing, ["billing_blocked"])

    def test_50_51_error_payloads_are_money_free(self):
        blocked, *_rest = self._blocked_requested()
        response, payload = self._act(SCHEDULE, blocked)
        self._assert_money_free(payload, response.text)
        blocked_scheduled, *_rest = self._blocked_scheduled()
        response, payload = self._act(START, blocked_scheduled)
        self._assert_money_free(payload, response.text)

    def test_52_manager_and_admin_get_the_same_money_free_answers(self):
        blocked, *_rest = self._blocked_scheduled()
        answers = []
        for user, password in (
            (self.rad_tech, self.tech_password),
            (self.manager, self.manager_password),
            (self.sysadmin, self.sysadmin_password),
        ):
            response, payload = self._act(START, blocked, user, password)
            self._assert_money_free(payload, response.text)
            answers.append((response.status_code, payload))
        self.assertTrue(all(answer == answers[0] for answer in answers))
        for user, password in ((self.manager, self.manager_password),
                               (self.sysadmin, self.sysadmin_password)):
            record, *_rest = self._clear_requested()
            response, payload = self._act(SCHEDULE, record, user, password)
            self.assertEqual(response.status_code, 200)
            self._assert_money_free(payload, response.text)


# ===========================================================================
# Regression
# ===========================================================================
@tagged("post_install", "-at_install", "radiology_desk")
class TestRadiologyTransitionRegression(RadTransitionCase):

    def test_53_54_55_lanes_counts_and_detail_follow_the_transitions(self):
        record, patient, *_rest = self._clear_requested()
        scope = {"q": patient.name, "status": ",".join(RAD_DESK_LANES)}

        summary = self._worklist(**scope)["summary"]
        self.assertEqual((summary["to_schedule"], summary["ready_to_start"],
                          summary["awaiting_report"]), (1, 0, 0))

        self._act(SCHEDULE, record)
        data = self._worklist(**scope)
        self.assertEqual((data["summary"]["to_schedule"], data["summary"]["ready_to_start"]), (0, 1))
        self.assertEqual(self._rows(data)[record.id]["lane"], "ready_to_start")
        self.assertEqual(self._detail(record)["lane"], "ready_to_start")

        self._act(START, record)
        data = self._worklist(**scope)
        self.assertEqual((data["summary"]["ready_to_start"], data["summary"]["awaiting_report"]), (0, 1))
        self.assertEqual(self._rows(data)[record.id]["lane"], "awaiting_report")
        self.assertEqual(self._detail(record)["lane"], "awaiting_report")

    def test_56_57_the_doctor_desk_reads_the_same_status_and_still_no_report(self):
        record, _patient, appointment, _encounter = self._clear_requested()

        def doctor_order():
            response, payload = self._get(
                DOCTOR_ORDERS % appointment.id, self.doctor_user, self.doctor_password
            )
            self.assertEqual(response.status_code, 200)
            return next(o for o in payload["data"]["orders"] if o["id"] == record.id)

        def doctor_result():
            response, payload = self._get(
                DOCTOR_RESULTS % appointment.id, self.doctor_user, self.doctor_password
            )
            self.assertEqual(response.status_code, 200)
            return next(r for r in payload["data"]["radiology"] if r["request_id"] == record.id)

        self.assertEqual(doctor_order()["status"], "awaiting_scheduling")
        self._act(SCHEDULE, record)
        self.assertEqual(doctor_order()["status"], "scheduled")
        self._act(START, record)
        order = doctor_order()
        self.assertEqual((order["status"], order["has_result"]), ("in_progress", False))
        result = doctor_result()
        self.assertEqual((result["status"], result["result"]), ("pending", None))
