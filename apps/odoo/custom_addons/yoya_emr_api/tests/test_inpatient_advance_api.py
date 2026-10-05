"""Inpatient advance + final settlement over real HTTP, with real users.

  * the doctor gives the estimate on the Doctor Desk; nobody else can
  * the Cashier sees Advance Required, takes the advance (replay-safe)
  * the Admissions Desk opens the Final Settlement window (clerk only; not the
    ward nurse, not the doctor), with server-reported stages
  * a settlement payment must quote the figures it was shown
  * the refund is Accounting's: the Cashier is refused, the Accountant records
  * end to end: estimate -> advance -> care -> medically ready -> settle ->
    finalize
"""
import uuid

from odoo.tests import tagged

from .test_admissions_desk_api import DETAIL
from .test_admissions_desk_mutations_api import VISIT
from .test_cashier_inpatient_settlement_api import (
    CLINICAL_KEYS,
    INPATIENT,
    CashierInpatientCase,
    _keys,
)

ESTIMATE = VISIT + "/admission-estimate"
ADVANCE = INPATIENT + "/advance"
REFUND = INPATIENT + "/refund"
SETTLEMENT = DETAIL + "/settlement"


@tagged("post_install", "-at_install", "inpatient_advance")
class AdvanceApiCase(CashierInpatientCase):
    def _estimate(self, appointment, admission, amount, user=None, **extra):
        body = {
            "operation_token": str(uuid.uuid4()),
            "expected_revision": admission.sudo().workflow_revision,
            "amount": amount,
            "reason": "Planned surgical stay",
        }
        body.update(extra)
        return self._post(ESTIMATE % appointment.id, body, user or self.doctor_user)

    def _advance(self, admission, amount, user=None, key=None):
        return self._post(
            ADVANCE % admission.id,
            {"amount": amount, "payment_method": "cash", "idempotency_key": key or uuid.uuid4().hex},
            user or self.cashier,
        )

    def _care(self, admission, amount):
        engine = self.env["hospital.billing.engine"].sudo()
        service = self.env["hospital.billing.service"].sudo().create({
            "name": "ADV care %s" % uuid.uuid4().hex[:5],
            "service_type": "pharmacy", "default_price": amount, "prepayment_required": False,
        })
        charge = engine.create_or_update_charge(
            admission.sudo().encounter_id, "hospital.pharmacy.dispense", admission.id, "adv_care",
            "ADV care", source_line_id=int(uuid.uuid4().int % 10**8), service=service,
            qty_requested=1, unit_price=amount,
        )
        engine.activate_charge(charge)
        engine.mark_charge_delivered(charge, qty_delivered=1)

    def _free_inpatient(self):
        self.ward_b.sudo().write({"daily_ward_rate": 0.0, "admission_fee": 0.0})
        room = self._room(self.ward_b, "AV%s" % uuid.uuid4().hex[:3])
        return self._inpatient(self._bed(room, "AV-%s" % uuid.uuid4().hex[:3]))


@tagged("post_install", "-at_install", "inpatient_advance")
class TestEstimateApi(AdvanceApiCase):
    def test_the_doctor_gives_and_revises_the_estimate(self):
        appointment, _, admission = self._free_inpatient()
        response, payload = self._estimate(appointment, admission, 50000)
        self.assertEqual(response.status_code, 200, payload)
        data = payload["data"]
        self.assertEqual(data["estimate"]["amount"], 50000.0)
        self.assertEqual(data["estimate"]["revision"], 1)
        self.assertTrue(data["can_edit"])
        self.assertEqual(data["operation"]["type"], "estimate")

        response, payload = self._estimate(appointment, admission, 55000)
        self.assertEqual(response.status_code, 200, payload)
        seen = self._ok(ESTIMATE % appointment.id, self.doctor_user)
        self.assertEqual((seen["estimate"]["amount"], seen["estimate"]["revision"]), (55000.0, 2))
        # The Doctor Desk visit payload stays amount-free.
        self.assertNotIn("55000", str(self._ok(VISIT % appointment.id, self.doctor_user)))

    def test_nobody_else_gives_the_estimate(self):
        appointment, _, admission = self._free_inpatient()
        for user in (self.cashier, self.receptionist, self.nurse_a, self.other_doctor_user):
            response, payload = self._estimate(appointment, admission, 1000, user=user)
            self.assertIn(response.status_code, (403, 404), (user.login, payload))
        self._assert_refused(*self._estimate(appointment, admission, 0), 400, "admission_invalid_payload")
        self._assert_refused(*self._estimate(appointment, admission, 100, extra="x"), 400,
                             "admission_invalid_payload")
        self.assertEqual(admission.sudo().estimated_amount, 0.0)


@tagged("post_install", "-at_install", "inpatient_advance")
class TestEstimateLockApi(AdvanceApiCase):
    """The estimate is revisable through the stay and locked once Request
    discharge has recorded medical readiness. The Doctor Desk is told THAT more
    advance is due, never how much."""

    MONEY_KEYS = ("advance_received", "received", "remaining", "outstanding", "remaining_due",
                  "refundable_balance", "settlement", "patient_funds")

    def _discharge_request(self, appointment, admission):
        body = {"operation_token": str(uuid.uuid4()),
                "expected_revision": admission.sudo().workflow_revision, "summary": "Well; home."}
        return self._post(VISIT % appointment.id + "/discharge-request", body, self.doctor_user)

    def _assert_no_money(self, data):
        keys = set(_keys(data))
        for forbidden in self.MONEY_KEYS:
            self.assertNotIn(forbidden, keys, forbidden)

    def test_revised_up_in_care_flags_additional_advance_without_a_figure(self):
        appointment, _, admission = self._free_inpatient()
        self._estimate(appointment, admission, 50000)
        self._advance(admission, 50000)
        seen = self._ok(ESTIMATE % appointment.id, self.doctor_user)
        self.assertFalse(seen["additional_advance_required"])
        self.assertIsNone(seen["locked_reason"])

        response, payload = self._estimate(appointment, admission, 70000, reason="Second procedure")
        self.assertEqual(response.status_code, 200, payload)
        data = payload["data"]
        self.assertTrue(data["can_edit"])
        self.assertTrue(data["additional_advance_required"])
        self.assertEqual(
            [(row["revision"], row["amount"], row["reason"], row["baseline"]) for row in data["history"]],
            [(1, 50000.0, "Planned surgical stay", False), (2, 70000.0, "Second procedure", False)],
        )
        self.assertTrue(all(row["estimated_by"] and row["estimated_at"] for row in data["history"]))
        self._assert_no_money(data)
        self.assertNotIn("20000", str(data))                       # never the shortfall
        # The Cashier is the authority for the figures.
        [row], _ = self._inpatient_rows(admission)
        self.assertEqual(row["lane"], "advance_required")
        self.assertEqual(
            (row["advance"]["requested"], row["advance"]["received"], row["advance"]["outstanding"]),
            (70000.0, 50000.0, 20000.0),
        )

    def test_after_request_discharge_the_estimate_is_locked(self):
        appointment, _, admission = self._free_inpatient()
        self._estimate(appointment, admission, 50000)
        self._advance(admission, 50000)
        response, payload = self._discharge_request(appointment, admission)
        self.assertEqual(response.status_code, 200, payload)

        seen = self._ok(ESTIMATE % appointment.id, self.doctor_user)
        self.assertEqual(seen["locked_reason"], "medically_ready")
        self.assertFalse(seen["can_edit"])
        self.assertFalse(seen["additional_advance_required"])
        self.assertEqual(seen["estimate"]["amount"], 50000.0)     # still visible
        self._assert_no_money(seen)

        response, payload = self._estimate(appointment, admission, 70000)
        self._assert_refused(response, payload, 409, "admission_estimate_locked")
        self.assertIn("medical discharge has begun", payload["error"]["message"])
        self.env.invalidate_all()
        record = admission.sudo()
        self.assertEqual((record.estimated_amount, record.estimate_revision), (50000.0, 1))
        self.assertEqual(len(record.estimate_revision_ids), 1)


@tagged("post_install", "-at_install", "inpatient_advance")
class TestAdvanceApi(AdvanceApiCase):
    def test_the_cashier_takes_the_advance_once(self):
        appointment, _, admission = self._free_inpatient()
        self._estimate(appointment, admission, 20000)
        [row], _ = self._inpatient_rows(admission)
        self.assertEqual(row["lane"], "advance_required")
        self.assertEqual(row["advance"]["outstanding"], 20000.0)

        key = uuid.uuid4().hex
        response, payload = self._advance(admission, 15000, key=key)
        self.assertEqual(response.status_code, 200, payload)
        self.assertEqual(payload["data"]["advance"]["received"], 15000.0)
        self.assertFalse(payload["data"]["receipt"]["accounting"]["posted"])
        response, payload = self._advance(admission, 15000, key=key)
        self.assertTrue(payload["data"]["replayed"])
        self.assertEqual(payload["data"]["advance"]["received"], 15000.0)

        self._assert_refused(*self._advance(admission, 5000.01), 400,
                             "inpatient_advance_exceeds_estimate")
        for user in (self.doctor_user, self.nurse_a, self.receptionist):
            response, payload = self._advance(admission, 100, user=user)
            self.assertEqual(response.status_code, 403, payload)


@tagged("post_install", "-at_install", "inpatient_advance")
class TestSettlementWindowApi(AdvanceApiCase):
    def test_only_the_discharging_clerk_opens_the_settlement(self):
        appointment, _, admission = self._free_inpatient()
        self._estimate(appointment, admission, 50000)
        self._advance(admission, 50000)
        self._care(admission, 48000)

        # IN CARE: credit on every desk -- never "refund due".
        in_care = self._ok(SETTLEMENT % admission.id, self.receptionist)["settlement"]
        self.assertEqual(in_care["state"], "credit")
        self.assertEqual(in_care["unapplied_credit"], 2000.0)
        self.assertEqual(in_care["refundable_balance"], 0.0)
        financial = self._ok(DETAIL % admission.id, self.receptionist)["admission"]["financial"]
        self.assertEqual(financial["financial_state"], "credit")
        self.assertIs(financial["refund_due"], False)
        self.assertIs(financial["patient_credit"], True)
        nurse_view = self._ok(DETAIL % admission.id, self.nurse_b)["admission"]["financial"]  # Ward B nurse
        self.assertEqual(nurse_view["financial_state"], "credit")
        self.assertNotIn("2000", str(nurse_view))
        self.assertEqual(self._ok(INPATIENT % admission.id, self.cashier)["lane"], "settled")

        response, payload = self._http_ready(appointment, admission)
        self.assertEqual(response.status_code, 200, payload)
        data = self._ok(SETTLEMENT % admission.id, self.receptionist)
        settlement = data["settlement"]
        self.assertEqual(settlement["estimate_amount"], 50000.0)
        self.assertEqual(settlement["advance_received"], 50000.0)
        self.assertEqual(settlement["actual_delivered"], 48000.0)
        self.assertEqual(settlement["refundable_balance"], 2000.0)
        self.assertEqual(settlement["state"], "refund_due")
        self.assertEqual(
            [stage["key"] for stage in settlement["stages"]],
            ["admission", "stay", "procedures", "pharmacy", "laboratory", "radiology",
             "other", "payer", "payments", "reconciliation"],
        )
        self.assertTrue(all(stage["status"] == "complete" for stage in settlement["stages"]))
        self.assertTrue(data["discharge_allowed"])
        self.assertFalse(set(_keys(data)) & CLINICAL_KEYS)
        for user in (self.nurse_a, self.doctor_user):
            response, payload = self._get(SETTLEMENT % admission.id, user)
            self.assertEqual(response.status_code, 403, payload)
        # The ordinary admission detail stays amount-free.
        self.assertNotIn("48000", str(self._ok(DETAIL % admission.id, self.receptionist)))


@tagged("post_install", "-at_install", "inpatient_advance")
class TestQuoteAndRefundApi(AdvanceApiCase):
    def test_a_payment_must_quote_the_current_settlement(self):
        _, _, admission = self._prior_day_due()
        due = self._remaining(admission)
        self._assert_refused(*self._pay(admission, due, quote=None), 400, "inpatient_quote_required")
        self._assert_refused(*self._pay(admission, due, quote="0" * 24), 409, "inpatient_quote_stale")
        self.assertEqual(self._remaining(admission), due)

    def test_the_refund_is_accountings(self):
        appointment, _, admission = self._free_inpatient()
        self._estimate(appointment, admission, 50000)
        self._advance(admission, 50000)
        self._care(admission, 48000)
        response, payload = self._http_ready(appointment, admission)
        self.assertEqual(response.status_code, 200, payload)
        detail = self._ok(INPATIENT % admission.id, self.cashier)
        self.assertEqual(detail["lane"], "refund_due")
        self.assertEqual(detail["refund"]["routed_to"], "accounting")
        self.assertFalse(detail["refund"]["may_record"])

        body = {"amount": 2000, "reason": "Unused advance", "idempotency_key": str(uuid.uuid4())}
        response, payload = self._post(REFUND % admission.id, body, self.cashier)
        self.assertEqual(response.status_code, 403, payload)
        response, payload = self._post(REFUND % admission.id, body, self.accountant)
        self.assertEqual(response.status_code, 200, payload)
        self.assertEqual(payload["data"]["settlement"]["state"], "even")
        self.assertEqual(payload["data"]["settlement"]["actual_delivered"], 48000.0)
        response, payload = self._post(REFUND % admission.id, body, self.accountant)
        self.assertTrue(payload["data"]["replayed"])


@tagged("post_install", "-at_install", "inpatient_advance")
class TestEndToEndApi(AdvanceApiCase):
    def test_estimate_advance_care_ready_settle_finalize(self):
        appointment, encounter, admission = self._free_inpatient()
        self._estimate(appointment, admission, 50000)
        self._advance(admission, 50000)
        self._care(admission, 57000)
        response, payload = self._http_ready(appointment, admission)
        self.assertEqual(response.status_code, 200, payload)

        window = self._ok(SETTLEMENT % admission.id, self.receptionist)
        self.assertEqual(window["settlement"]["state"], "due")
        self.assertEqual(window["settlement"]["remaining_due"], 7000.0)
        self.assertFalse(window["discharge_allowed"])
        self._assert_refused(*self._http_finalize(admission), 409, "admission_settlement_required")

        response, payload = self._pay(admission, 3000)
        self.assertEqual(payload["data"]["lane"], "part_paid")
        self._assert_refused(*self._http_finalize(admission), 409, "admission_settlement_required")
        response, payload = self._pay(admission, 4000)
        self.assertEqual(response.status_code, 200, payload)
        self.assertEqual(payload["data"]["settlement"]["state"], "even")
        funds = payload["data"]["settlement"]["funds"]
        self.assertEqual(
            (funds["advance"], funds["settlement_payments"], funds["total"]), (50000.0, 7000.0, 57000.0)
        )

        response, payload = self._http_finalize(admission)
        self.assertEqual(response.status_code, 200, payload)
        self.env.invalidate_all()
        self.assertEqual(admission.sudo().state, "discharged")
        self.assertEqual(encounter.sudo().state, "completed")
