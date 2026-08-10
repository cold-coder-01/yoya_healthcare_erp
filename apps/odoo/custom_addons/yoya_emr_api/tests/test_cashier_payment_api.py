import json
import uuid
from unittest.mock import patch

from odoo import fields
from odoo.exceptions import AccessError
from odoo.tests import HttpCase, tagged

G_CASHIER = "hospital_billing.group_hospital_cashier"
G_RECEPTIONIST = "hospital_management.group_hospital_receptionist"

# Where the controller looks the serializer up, not where it is defined.
SERIALIZER_TARGET = (
    "odoo.addons.yoya_emr_api.controllers.cashier.serialize_cashier_payment_result"
)


@tagged("post_install", "-at_install", "cashier_payment_api")
class TestCashierPaymentApi(HttpCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.service = (
            cls.env["hospital.billing.service"]
            .sudo()
            .get_default_consultation_service(cls.company)
        )
        cls.service.sudo().write(
            {
                "default_price": 300.0,
                "fixed_fee": True,
                "prepayment_required": True,
                "coverage_auth_required": False,
                "active": True,
                "is_default_consultation": True,
            }
        )
        cls.cashier_password = "cashier-api-test"
        cls.receptionist_password = "reception-api-test"
        cls.cashier = cls._make_user(
            "api_cashier", cls.cashier_password, [G_CASHIER]
        )
        cls.receptionist = cls._make_user(
            "api_receptionist", cls.receptionist_password, [G_RECEPTIONIST]
        )

    @classmethod
    def _make_user(cls, login, password, group_xmlids):
        unique_login = "%s_%s" % (login, uuid.uuid4().hex[:6])
        return (
            cls.env["res.users"]
            .sudo()
            .create(
                {
                    "name": login,
                    "login": unique_login,
                    "password": password,
                    "company_id": cls.env.company.id,
                    "company_ids": [(6, 0, cls.env.company.ids)],
                    "groups_id": [
                        (
                            6,
                            0,
                            [cls.env.ref("base.group_user").id]
                            + [cls.env.ref(x).id for x in group_xmlids],
                        )
                    ],
                }
            )
        )

    def _new_visit(self):
        suffix = uuid.uuid4().hex[:8]
        patient = self.env["hospital.patient"].sudo().create(
            {"name": "API Payment Patient %s" % suffix}
        )
        appointment = self.env["hospital.appointment"].sudo().create(
            {"patient_id": patient.id, "appointment_date": fields.Datetime.now()}
        )
        appointment.action_confirm()
        appointment.invalidate_recordset()
        return appointment

    def _add_account_charge(self, account, amount, description="API extra charge"):
        return self.env["hospital.charge.line"].sudo().create(
            {
                "billing_account_id": account.id,
                "description": "%s %s" % (description, uuid.uuid4().hex[:6]),
                "billing_basis": "prepaid",
                "charge_state": "active",
                "unit_price": amount,
                "qty_requested": 1.0,
            }
        )

    def _login(self, user, password):
        self.authenticate(user.login, password)

    def _post_payment(self, appointment, body):
        return self.url_open(
            "/yoya-emr/api/v1/cashier/visits/%s/payment" % appointment.id,
            data=json.dumps(body),
            headers={"Content-Type": "application/json"},
        )

    def _json(self, response):
        return json.loads(response.text)

    def test_cashier_records_full_payment_and_gets_refreshed_visit(self):
        appointment = self._new_visit()
        self._login(self.cashier, self.cashier_password)

        response = self._post_payment(
            appointment,
            {
                "amount": 300.0,
                "payment_method": "cash",
                "idempotency_key": uuid.uuid4().hex,
            },
        )
        payload = self._json(response)

        self.assertEqual(response.status_code, 200)
        self.assertTrue(payload["success"])
        data = payload["data"]
        receipt = data["receipt"]
        self.assertEqual(receipt["state"], "confirmed")
        self.assertEqual(receipt["received_by"]["id"], self.cashier.id)
        self.assertAlmostEqual(receipt["amount"], 300.0, places=2)
        self.assertAlmostEqual(receipt["allocated_total"], 300.0, places=2)
        self.assertEqual(len(receipt["allocations"]), 1)

        # Operational intake posts nothing to accounting and fiscalizes nothing.
        self.assertFalse(receipt["accounting"]["posted"])
        self.assertFalse(receipt["accounting"]["fiscalized"])
        self.assertFalse(receipt["accounting"]["has_accounting_move"])
        stored = self.env["hospital.charge.receipt"].sudo().browse(receipt["id"])
        self.assertFalse(stored.accounting_posted)
        self.assertFalse(stored.fiscalized)
        if "accounting_move_id" in stored._fields:
            self.assertFalse(stored.accounting_move_id)

        self.assertTrue(data["clearance"]["ok"])
        self.assertAlmostEqual(data["clearance"]["outstanding"], 0.0, places=2)
        self.assertAlmostEqual(
            data["billing_account"]["amount_received"], 300.0, places=2
        )
        self.assertAlmostEqual(
            data["billing_account"]["amount_due_for_clearance"], 0.0, places=2
        )
        self.assertEqual(data["appointment"]["id"], appointment.id)
        self.assertEqual(data["patient"]["id"], appointment.patient_id.id)

        # A cashier may take money; posting it to accounting is not their act.
        self.assertTrue(data["permitted_actions"]["record_payment"])
        self.assertFalse(data["permitted_actions"]["post_receipt_accounting"])

        # Least privilege: the response must carry no clinical payload, and in
        # particular not clinical_queue_stage, whose compute reaches into
        # hospital.patient.evaluation and used to make this endpoint 403.
        self.assertNotIn("clinical_queue_stage", data["appointment"])
        for clinical_key in ("danger_signs", "emergency", "evaluation", "visit"):
            self.assertNotIn(clinical_key, data)
        body_text = json.dumps(data)
        self.assertNotIn("clinical_queue_stage", body_text)
        # Accounting-only money buckets (groups=ACCOUNTING_READ) stay out too.
        for accounting_key in ("amount_invoiced", "receivable_balance"):
            self.assertNotIn(accounting_key, body_text)

    def test_cashier_records_partial_payment_and_clearance_remains_pending(self):
        appointment = self._new_visit()
        self._login(self.cashier, self.cashier_password)

        response = self._post_payment(
            appointment,
            {
                "amount": 125.0,
                "payment_method": "cash",
                "idempotency_key": uuid.uuid4().hex,
            },
        )
        payload = self._json(response)

        self.assertEqual(response.status_code, 200)
        self.assertTrue(payload["success"])
        self.assertFalse(payload["data"]["clearance"]["ok"])
        self.assertAlmostEqual(
            payload["data"]["clearance"]["outstanding"],
            175.0,
            places=2,
        )
        self.assertAlmostEqual(
            payload["data"]["billing_account"]["amount_received"], 125.0, places=2
        )
        self.assertAlmostEqual(
            payload["data"]["billing_account"]["amount_due_for_clearance"],
            175.0,
            places=2,
        )

    def test_receptionist_is_denied(self):
        appointment = self._new_visit()
        self._login(self.receptionist, self.receptionist_password)
        before = self.env["hospital.charge.receipt"].sudo().search_count([])

        response = self._post_payment(
            appointment,
            {
                "amount": 300.0,
                "payment_method": "cash",
                "idempotency_key": uuid.uuid4().hex,
            },
        )
        payload = self._json(response)

        self.assertEqual(response.status_code, 403)
        self.assertFalse(payload["success"])
        self.assertEqual(payload["error"]["code"], "payment_not_authorized")
        self.assertEqual(self.env["hospital.charge.receipt"].sudo().search_count([]), before)

    def test_invalid_amounts_and_missing_reference_return_stable_errors(self):
        appointment = self._new_visit()
        self._login(self.cashier, self.cashier_password)

        for amount in (0.0, -1.0):
            response = self._post_payment(
                appointment,
                {
                    "amount": amount,
                    "payment_method": "cash",
                    "idempotency_key": uuid.uuid4().hex,
                },
            )
            payload = self._json(response)
            self.assertEqual(response.status_code, 400)
            self.assertEqual(payload["error"]["code"], "invalid_amount")

        response = self._post_payment(
            appointment,
            {
                "amount": 10.0,
                "payment_method": "card",
                "idempotency_key": uuid.uuid4().hex,
            },
        )
        payload = self._json(response)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(payload["error"]["code"], "payment_reference_required")

    def test_forged_allocation_fields_are_rejected(self):
        appointment = self._new_visit()
        other = self._new_visit()
        other_charge = other.consultation_charge_id
        self._login(self.cashier, self.cashier_password)

        response = self._post_payment(
            appointment,
            {
                "amount": 10.0,
                "payment_method": "cash",
                "idempotency_key": uuid.uuid4().hex,
                "charge_line_ids": [other_charge.id],
            },
        )
        payload = self._json(response)

        self.assertEqual(response.status_code, 400)
        self.assertFalse(payload["success"])
        self.assertEqual(payload["error"]["code"], "payment_validation_failed")
        other_charge.invalidate_recordset()
        self.assertAlmostEqual(other_charge.amount_received, 0.0, places=2)

    def test_duplicate_idempotency_key_creates_one_receipt_and_one_allocation(self):
        appointment = self._new_visit()
        self._login(self.cashier, self.cashier_password)
        token = uuid.uuid4().hex
        body = {"amount": 300.0, "payment_method": "cash", "idempotency_key": token}

        first = self._json(self._post_payment(appointment, body))["data"]["receipt"]
        second = self._json(self._post_payment(appointment, body))["data"]["receipt"]

        self.assertEqual(first["id"], second["id"])
        self.assertEqual(
            self.env["hospital.charge.receipt"].sudo().search_count([("intake_token", "=", token)]),
            1,
        )
        self.assertEqual(
            self.env["hospital.charge.receipt.allocation"].sudo().search_count([("receipt_id", "=", first["id"])]),
            1,
        )

    def test_duplicate_idempotency_key_with_different_input_conflicts(self):
        appointment = self._new_visit()
        self._login(self.cashier, self.cashier_password)
        token = uuid.uuid4().hex
        self._post_payment(
            appointment,
            {"amount": 100.0, "payment_method": "cash", "idempotency_key": token},
        )

        response = self._post_payment(
            appointment,
            {"amount": 125.0, "payment_method": "cash", "idempotency_key": token},
        )
        payload = self._json(response)

        self.assertEqual(response.status_code, 409)
        self.assertEqual(payload["error"]["code"], "idempotency_conflict")

    def test_api_allocates_only_server_derived_account_charges(self):
        appointment = self._new_visit()
        account = appointment.encounter_id.billing_account_id
        second = self._add_account_charge(account, 200.0)
        self._login(self.cashier, self.cashier_password)

        payload = self._json(
            self._post_payment(
                appointment,
                {
                    "amount": 350.0,
                    "payment_method": "cash",
                    "idempotency_key": uuid.uuid4().hex,
                },
            )
        )
        receipt = self.env["hospital.charge.receipt"].sudo().browse(
            payload["data"]["receipt"]["id"]
        )

        self.assertEqual(receipt.billing_account_id, account)
        self.assertEqual(receipt.encounter_id, appointment.encounter_id)
        self.assertEqual(receipt.patient_id, appointment.patient_id)
        self.assertEqual(receipt.company_id, account.company_id)
        self.assertEqual(receipt.allocation_ids.mapped("charge_line_id"), appointment.consultation_charge_id | second)

    def test_response_failure_after_payment_rolls_the_payment_back(self):
        """Regression: money must never survive a failed success response.

        The original defect was structural, not a bad permission: the payment
        committed and the client got HTTP 403, because the endpoint decorator
        caught the post-write AccessError and returned JSON, which told Odoo's
        dispatcher the request had succeeded.

        The failure is injected by patching the SERIALIZER the controller
        calls -- production security is left exactly as it is. AccessError is
        used as the injected type because that is the real-world cause: the
        response reaching for a relation the cashier cannot read.
        """
        appointment = self._new_visit()
        account = appointment.encounter_id.billing_account_id
        charge = appointment.consultation_charge_id
        self._login(self.cashier, self.cashier_password)

        Receipt = self.env["hospital.charge.receipt"].sudo()
        Allocation = self.env["hospital.charge.receipt.allocation"].sudo()
        receipts_before = Receipt.search_count([])
        allocations_before = Allocation.search_count([])
        account.invalidate_recordset()
        charge.invalidate_recordset()
        received_before = account.amount_received
        due_before = charge.amount_due_for_clearance

        token = uuid.uuid4().hex
        with patch(
            SERIALIZER_TARGET,
            side_effect=AccessError(
                "simulated post-write failure reading Patient Evaluation"
            ),
        ):
            response = self._post_payment(
                appointment,
                {
                    "amount": 300.0,
                    "payment_method": "cash",
                    "idempotency_key": token,
                },
            )
        payload = self._json(response)

        # The client is told the payment did NOT happen...
        self.assertEqual(response.status_code, 500)
        self.assertFalse(payload["success"])
        self.assertEqual(payload["error"]["code"], "payment_response_failed")
        # ...and is not told it was unauthorized, because it was authorized.
        self.assertNotEqual(payload["error"]["code"], "payment_not_authorized")

        # No internals leak to the client.
        serialized = json.dumps(payload)
        self.assertNotIn("Traceback", serialized)
        self.assertNotIn("Patient Evaluation", serialized)
        self.assertNotIn("simulated post-write failure", serialized)

        # ...and the money is genuinely gone from the database.
        account.invalidate_recordset()
        charge.invalidate_recordset()
        self.assertEqual(Receipt.search_count([]), receipts_before)
        self.assertEqual(Allocation.search_count([]), allocations_before)
        self.assertEqual(Receipt.search_count([("intake_token", "=", token)]), 0)
        self.assertAlmostEqual(account.amount_received, received_before, places=2)
        self.assertAlmostEqual(charge.amount_due_for_clearance, due_before, places=2)

        # The rolled-back token is not burned: the retry the client was told to
        # make must be able to reuse it.
        retry = self._post_payment(
            appointment,
            {"amount": 300.0, "payment_method": "cash", "idempotency_key": token},
        )
        retry_payload = self._json(retry)
        self.assertEqual(retry.status_code, 200)
        self.assertTrue(retry_payload["success"])
        self.assertEqual(retry_payload["data"]["receipt"]["state"], "confirmed")
        self.assertEqual(Receipt.search_count([("intake_token", "=", token)]), 1)