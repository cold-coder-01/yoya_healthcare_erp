"""Pharmacy Desk Slice 2: the Prepare and Validate HTTP contract.

WHAT IS HELD HERE
-----------------
  1. THE GATE. Pharmacist, Manager and System Administrator may prepare and
     validate; every other role gets 403 `pharmacy_desk_not_authorized` before
     the dispense is even resolved -- a refused caller cannot tell a real id
     from a missing one.
  2. THE PAYLOAD. Exactly the allowed keys; a token is required; Validate takes
     no quantities.
  3. FIXED ERRORS. Every refusal is a fixed code with a fixed sentence, and no
     response or error carries an amount, a currency, a payer, a receipt, an
     invoice or a batch figure.
  4. ONE SAVEPOINT. A failure while building the response leaves nothing
     behind; a unique-constraint race is a 409.
  5. THE DOCTOR HANDOFF. The prescriber sees what the patient RECEIVED -- not
     what the pharmacist intends to hand over next.

The model-level workflow (charges, stock, locks, three decimals, rollback of
every side effect) is covered by hospital_billing's test_pharmacy_desk_mutations.
"""
import json
import uuid
from unittest.mock import patch

from psycopg2 import IntegrityError

from odoo.tests import tagged

from .test_doctor_medication_api import ORDERS
from .test_pharmacy_desk_api import (
    DETAIL,
    FORBIDDEN_KEY_FRAGMENTS,
    SESSION,
    PharmacyDeskCase,
    _walk_keys,
)

PREPARE = "/yoya-emr/api/v1/pharmacy/dispenses/%s/prepare"
VALIDATE = "/yoya-emr/api/v1/pharmacy/dispenses/%s/validate"

MONEY_TEXT = ("ETB", "Birr", "120.0", "amount", "balance", "currency", "receipt", "invoice", "payer")


class MutationCase(PharmacyDeskCase):

    def _post(self, url, body, user=None, password=None, raw=None):
        self._auth(user or self.pharmacist, password or self.pharmacist_password)
        response = self.url_open(
            url,
            data=raw if raw is not None else json.dumps(body),
            headers={"Content-Type": "application/json"},
        )
        return response, json.loads(response.text)

    def _lines(self, dispense, *quantities):
        lines = dispense.line_ids.sorted("id")
        return [{"line_id": line.id, "intended_quantity": q} for line, q in zip(lines, quantities)]

    def _prepare(self, dispense, *quantities, token=None, revision=None, user=None, password=None):
        dispense.invalidate_recordset()
        return self._post(PREPARE % dispense.id, {
            "operation_token": token or str(uuid.uuid4()),
            "expected_revision": dispense.workflow_revision if revision is None else revision,
            "lines": self._lines(dispense, *quantities),
        }, user, password)

    def _validate_http(self, dispense, token=None, revision=None, user=None, password=None):
        dispense.invalidate_recordset()
        return self._post(VALIDATE % dispense.id, {
            "operation_token": token or str(uuid.uuid4()),
            "expected_revision": dispense.workflow_revision if revision is None else revision,
        }, user, password)

    def _error(self, response, payload, status, code):
        self.assertEqual(response.status_code, status, payload)
        self.assertEqual(payload["error"]["code"], code, payload)
        return payload["error"]["message"]

    def _assert_no_money(self, payload, label):
        for key in _walk_keys(payload):
            for fragment in FORBIDDEN_KEY_FRAGMENTS:
                self.assertNotIn(fragment, str(key).lower(), "%s leaked key %r" % (label, key))
        text = json.dumps(payload)
        for marker in MONEY_TEXT:
            self.assertNotIn(marker, text, "%s leaked %r" % (label, marker))

    def _fresh(self, medicine=None, qty=10.0):
        appointment, encounter = self._visit()
        _rx, dispense = self._rx(appointment, medicine or self.cetiriz, qty=qty)
        return dispense, encounter


# ===========================================================================
@tagged("post_install", "-at_install", "pharmacy_desk_mutation_api")
class TestMutationAuthorization(MutationCase):

    def test_01_desk_roles_may_prepare_and_validate(self):
        for user, password in (
            (self.pharmacist, self.pharmacist_password),
            (self.manager, self.manager_password),
            (self.sysadmin, self.sysadmin_password),
        ):
            dispense, encounter = self._fresh()
            response, payload = self._prepare(dispense, 4.0, user=user, password=password)
            self.assertEqual(response.status_code, 200, (user.login, payload))
            self._pay(encounter)
            response, payload = self._validate_http(dispense, user=user, password=password)
            self.assertEqual(response.status_code, 200, (user.login, payload))
            self.assertEqual(payload["data"]["dispense"]["state"], "partial")

    def test_02_every_other_role_is_refused_before_disclosure(self):
        dispense, _encounter = self._fresh()
        refused = (
            (self.doctor_user, self.doctor_password),
            (self.nurse, self.nurse_password),
            (self.receptionist, self.receptionist_password),
            (self.cashier, self.cashier_password),
            (self.accountant, self.accountant_password),
            (self.front_desk, self.fd_password),
            (self.lab, self.lab_password),
            (self.rad, self.rad_password),
            (self.dpo, self.dpo_password),
            (self.officer, self.officer_password),
        )
        for user, password in refused:
            for url, body in (
                (PREPARE, {"operation_token": str(uuid.uuid4()), "expected_revision": 0,
                           "lines": self._lines(dispense, 4.0)}),
                (VALIDATE, {"operation_token": str(uuid.uuid4()), "expected_revision": 0}),
            ):
                for target in (dispense.id, 999999999):
                    response, payload = self._post(url % target, body, user, password)
                    message = self._error(response, payload, 403, "pharmacy_desk_not_authorized")
                    self.assertEqual(
                        message,
                        "Pharmacy Desk mutation requires a pharmacist, hospital manager, or system administrator.",
                    )
                    self.assertNotIn(dispense.name, response.text)
        dispense.invalidate_recordset()
        self.assertEqual((dispense.state, dispense.workflow_revision), ("draft", 0))

    def test_03_session_and_record_capabilities(self):
        response, payload = self._desk_get(SESSION)
        self.assertEqual(payload["data"]["capabilities"],
                         {"pharmacy_desk": True, "prepare_dispense": True, "validate_dispense": True})
        dispense, encounter = self._fresh()
        detail = self._detail(dispense)
        self.assertEqual((detail["can_prepare"], detail["can_validate"]), (True, False))
        self.assertEqual(detail["workflow_revision"], 0)
        self._prepare(dispense, 4.0)
        detail = self._detail(dispense)
        self.assertEqual(detail["lane"], "awaiting_clearance")
        self.assertEqual((detail["can_prepare"], detail["can_validate"]), (True, False))
        self._pay(encounter)
        detail = self._detail(dispense)
        self.assertEqual(detail["lane"], "ready_to_validate")
        self.assertEqual((detail["can_prepare"], detail["can_validate"]), (True, True))


# ===========================================================================
@tagged("post_install", "-at_install", "pharmacy_desk_mutation_api")
class TestMutationContract(MutationCase):

    def test_10_payload_shape(self):
        dispense, _encounter = self._fresh()
        url = PREPARE % dispense.id
        response, payload = self._post(url, None, raw="{not json")
        self._error(response, payload, 400, "pharmacy_invalid_payload")
        response, payload = self._post(url, {"expected_revision": 0, "lines": self._lines(dispense, 4.0)})
        self._error(response, payload, 400, "pharmacy_operation_token_required")
        response, payload = self._post(url, {"operation_token": "  ", "expected_revision": 0,
                                             "lines": self._lines(dispense, 4.0)})
        self._error(response, payload, 400, "pharmacy_operation_token_required")
        response, payload = self._post(url, {"operation_token": str(uuid.uuid4()), "expected_revision": 0,
                                             "lines": self._lines(dispense, 4.0), "state": "dispensed"})
        self._error(response, payload, 400, "pharmacy_invalid_payload")
        response, payload = self._post(VALIDATE % dispense.id, {
            "operation_token": str(uuid.uuid4()), "expected_revision": 0, "lines": self._lines(dispense, 4.0)})
        self._error(response, payload, 400, "pharmacy_invalid_payload")
        response, payload = self._post(url, {"operation_token": str(uuid.uuid4()), "expected_revision": "0",
                                             "lines": self._lines(dispense, 4.0)})
        self._error(response, payload, 400, "pharmacy_invalid_payload")
        response, payload = self._post(PREPARE % 999999999, {
            "operation_token": str(uuid.uuid4()), "expected_revision": 0, "lines": []})
        self._error(response, payload, 404, "pharmacy_dispense_not_found")

    def test_11_fixed_error_codes(self):
        dispense, _encounter = self._fresh()
        response, payload = self._prepare(dispense, 11.0)
        self._error(response, payload, 422, "pharmacy_quantity_invalid")
        response, payload = self._prepare(dispense, 0.0)
        self._error(response, payload, 422, "pharmacy_no_positive_increment")
        line = dispense.line_ids
        response, payload = self._post(PREPARE % dispense.id, {
            "operation_token": str(uuid.uuid4()), "expected_revision": 0,
            "lines": [{"line_id": line.id, "intended_quantity": 1}, {"line_id": line.id, "intended_quantity": 2}]})
        self._error(response, payload, 422, "pharmacy_duplicate_line")
        response, payload = self._prepare(dispense, 4.0, revision=7)
        message = self._error(response, payload, 409, "pharmacy_dispense_revision_conflict")
        self.assertEqual(message, "This dispense changed after it was loaded. Refresh it and review the latest quantities.")
        response, payload = self._validate_http(dispense)
        self._error(response, payload, 409, "pharmacy_dispense_state_conflict")

    def test_12_billing_blocked_is_amount_free(self):
        dispense, _encounter = self._fresh()
        response, payload = self._prepare(dispense, 4.0)
        self.assertEqual(response.status_code, 200, payload)
        response, payload = self._validate_http(dispense)
        message = self._error(response, payload, 422, "pharmacy_billing_blocked")
        self.assertEqual(message, "Financial clearance is required before this dispense can be validated.")
        self._assert_no_money(payload, "billing blocked")

    def test_13_stock_and_mapping_errors_are_fixed(self):
        dispense, encounter = self._fresh(self.dry, qty=4.0)
        response, payload = self._prepare(dispense, 4.0)
        self.assertEqual(response.status_code, 200, payload)
        self._pay(encounter)
        response, payload = self._validate_http(dispense)
        message = self._error(response, payload, 422, "pharmacy_stock_insufficient")
        self.assertEqual(message, "The Pharmacy Store does not have enough usable stock for this dispense.")
        self._assert_no_money(payload, "stock")
        dispense, _encounter = self._fresh(self.unbilled)
        response, payload = self._prepare(dispense, 4.0)
        self._error(response, payload, 422, "pharmacy_billing_mapping_missing")
        dispense, _encounter = self._fresh(self.unstocked)
        response, payload = self._prepare(dispense, 4.0)
        self._error(response, payload, 422, "pharmacy_inventory_mapping_missing")

    def test_14_anomaly_needs_review(self):
        dispense, _encounter = self._fresh()
        dispense.line_ids.with_user(self.pharmacist).write({"dispensed_quantity": 4.0})
        dispense._write_state("partial")  # the legacy flagged-partial shape
        response, payload = self._prepare(dispense, 5.0)
        self._error(response, payload, 409, "pharmacy_dispense_needs_review")

    def test_15_replay_over_http(self):
        dispense, _encounter = self._fresh()
        token = str(uuid.uuid4())
        response, first = self._prepare(dispense, 4.0, token=token)
        self.assertEqual(response.status_code, 200, first)
        self.assertEqual(first["data"]["operation"], {"type": "prepare", "token": token, "replayed": False})
        self.assertEqual(first["data"]["workflow_revision"], 1)
        response, second = self._prepare(dispense, 4.0, token=token, revision=0)
        self.assertEqual(response.status_code, 200, second)
        self.assertTrue(second["data"]["operation"]["replayed"])
        self.assertEqual(second["data"]["workflow_revision"], 1)
        response, payload = self._prepare(dispense, 5.0, token=token, revision=0)
        self._error(response, payload, 409, "pharmacy_idempotency_conflict")
        response, payload = self._prepare(dispense, 4.0, token=token, revision=0,
                                          user=self.manager, password=self.manager_password)
        self._error(response, payload, 409, "pharmacy_idempotency_conflict")

    def test_16_response_failure_rolls_everything_back(self):
        dispense, _encounter = self._fresh()

        def boom(*args, **kwargs):
            raise ValueError("serializer exploded")

        with patch("odoo.addons.yoya_emr_api.controllers.pharmacy.serialize_dispense_detail", boom):
            response, payload = self._prepare(dispense, 4.0)
        message = self._error(response, payload, 500, "pharmacy_mutation_response_failed")
        self.assertEqual(message, "The pharmacy action could not be completed. Nothing was changed.")
        self.assertNotIn("exploded", response.text)
        self.env.invalidate_all()
        self.assertEqual((dispense.state, dispense.workflow_revision), ("draft", 0))
        self.assertEqual(dispense.line_ids.dispensed_quantity, 0.0)
        self.assertFalse(dispense._pharmacy_charges())
        self.assertFalse(self.env["hospital.pharmacy.operation"].sudo().search([("dispense_id", "=", dispense.id)]))

    def test_17_unique_race_is_a_concurrent_conflict(self):
        dispense, _encounter = self._fresh()
        Dispense = type(self.env["hospital.pharmacy.dispense"])

        def race(this):
            raise IntegrityError("duplicate key value violates unique constraint")

        with patch.object(Dispense, "_desk_sync_billing", race):
            response, payload = self._prepare(dispense, 4.0)
        self._error(response, payload, 409, "pharmacy_concurrent_conflict")
        self.env.invalidate_all()
        self.assertEqual(dispense.workflow_revision, 0)

    def test_18_raw_clearance_text_never_escapes(self):
        """A deeper layer's amount-bearing refusal becomes the fixed sentence."""
        dispense, encounter = self._fresh()
        self._prepare(dispense, 4.0)
        self._pay(encounter)
        Dispense = type(self.env["hospital.pharmacy.dispense"])
        from odoo.exceptions import UserError

        def leaky(this, *args, **kwargs):
            raise UserError("Pharmacy dispense cannot be validated -- remaining 480.00 ETB")

        with patch.object(Dispense, "_assert_financially_cleared_for_dispense", leaky):
            response, payload = self._validate_http(dispense)
        self._error(response, payload, 500, "pharmacy_mutation_response_failed")
        self.assertNotIn("480", response.text)
        self.assertNotIn("ETB", response.text)


# ===========================================================================
@tagged("post_install", "-at_install", "pharmacy_desk_mutation_api")
class TestMutationFlowAndDoctorHandoff(MutationCase):

    def _doctor_line(self, appointment):
        _response, payload = self._get(ORDERS % appointment.id)
        row = self._prescriptions(payload)[0]
        return row["status"], row["medicines"][0]

    def test_20_uat_flow_4_then_10(self):
        """The planned human UAT, automated: prescribe 10, prepare 4, validate,
        prepare CUMULATIVE 10 (not 6), validate."""
        appointment, encounter = self._in_consultation_visit()
        self._prescribe(appointment, medicines=[{"medicine_id": self.cetiriz.id, "quantity": 10}])
        prescription = self._prescriptions_of(self._consultation_of(encounter))
        dispense = self._dispense_of(prescription)

        response, payload = self._prepare(dispense, 4.0)
        self.assertEqual(response.status_code, 200, payload)
        self._assert_no_money(payload, "prepare")
        self.assertEqual(payload["data"]["dispense"]["state"], "ready")
        status, line = self._doctor_line(appointment)
        self.assertEqual((status, line["dispensed_quantity"], line["remaining_quantity"]),
                         ("ready_at_pharmacy", 0.0, 10.0), "intent is not delivery")

        self._pay(encounter)
        response, payload = self._validate_http(dispense)
        self.assertEqual(response.status_code, 200, payload)
        self._assert_no_money(payload, "validate")
        data = payload["data"]["dispense"]
        self.assertEqual((data["state"], data["lane"]), ("partial", "partially_supplied"))
        (desk_line,) = data["lines"]
        self.assertEqual((desk_line["delivered_quantity"], desk_line["consumed_quantity"],
                          desk_line["remaining_quantity"]), (4.0, 4.0, 6.0))
        status, line = self._doctor_line(appointment)
        self.assertEqual((status, line["dispensed_quantity"], line["remaining_quantity"]),
                         ("partially_dispensed", 4.0, 6.0))

        response, payload = self._prepare(dispense, 10.0)
        self.assertEqual(response.status_code, 200, payload)
        status, line = self._doctor_line(appointment)
        self.assertEqual((status, line["dispensed_quantity"], line["remaining_quantity"]),
                         ("partially_dispensed", 4.0, 6.0),
                         "the prescriber still sees what was RECEIVED, not the new intent")

        self._pay(encounter)
        response, payload = self._validate_http(dispense)
        self.assertEqual(response.status_code, 200, payload)
        data = payload["data"]["dispense"]
        self.assertEqual((data["state"], data["lane"]), ("dispensed", "completed"))
        self.assertEqual((data["can_prepare"], data["can_validate"]), (False, False))
        (desk_line,) = data["lines"]
        self.assertEqual((desk_line["delivered_quantity"], desk_line["remaining_quantity"]), (10.0, 0.0))
        status, line = self._doctor_line(appointment)
        self.assertEqual((status, line["dispensed_quantity"], line["remaining_quantity"]),
                         ("dispensed", 10.0, 0.0))
        consumed = sum(dispense.sudo().inventory_consumption_ids.mapped("line_ids.quantity"))
        self.assertEqual(consumed, 10.0, "4 then 6: never 14")
