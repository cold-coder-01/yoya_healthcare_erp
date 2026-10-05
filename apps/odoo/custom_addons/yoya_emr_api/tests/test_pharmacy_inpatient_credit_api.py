"""Pharmacy clearance for an ACTIVE INPATIENT is decided by the inpatient
settlement authority's unapplied credit (UAT RX0144 / ADM00004).

  A. credit well above the intended value         -> covered, ready to validate
  B. validate 4 of 10                             -> 4 delivered, 6 remaining,
                                                     billing on 4 only
  C. after dispense: delivered care +480 exactly, credit -480 exactly
  D. credit below the intended value              -> blocked, "shortfall"
  E. a smaller intended quantity within the credit -> covered
  F. an outpatient keeps per-service clearance (unchanged)
  G. an inpatient with no advance and no credit   -> blocked
  H. Pharmacy creates no receipt
  I. two dispenses cannot spend the same remaining credit
  J. a doctor still cannot validate a dispense

Every answer crosses the Pharmacy Desk as a WORD (financial_cover) -- never an
amount.
"""
import uuid

from odoo import fields
from odoo.tests import tagged

from .test_pharmacy_desk_mutations_api import MutationCase

G_CASHIER = "hospital_billing.group_hospital_cashier"
PRICE = 120.0


@tagged("post_install", "-at_install", "pharmacy_inpatient_credit")
class PharmacyInpatientCreditCase(MutationCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.cashier_password = "pic-cash-pw-1"
        cls.cashier = cls._make_user("pic_cash", cls.cashier_password, [G_CASHIER])
        # A ward that charges nothing for the stay, so the credit moves only
        # with what this suite delivers.
        cls.ic_ward = cls.env["hospital.ward"].sudo().create({
            "name": "IC Ward %s" % uuid.uuid4().hex[:5],
            "code": "IC%s" % uuid.uuid4().hex[:5].upper(),
            "ward_type": "surgical",
            "company_id": cls.env.company.id,
            "daily_ward_rate": 0.0,
            "admission_fee": 0.0,
        })
        cls.ic_room = cls.env["hospital.room"].sudo().create({
            "name": "IC Room", "code": "ICR%s" % uuid.uuid4().hex[:5].upper(),
            "ward_id": cls.ic_ward.id,
        })

    # ------------------------------------------------------------------
    def _bed(self):
        return self.env["hospital.bed"].sudo().create({
            "name": "IC Bed %s" % uuid.uuid4().hex[:4],
            "code": "ICB%s" % uuid.uuid4().hex[:5].upper(),
            "room_id": self.ic_room.id,
        })

    def _inpatient(self, advance=None, estimate=None):
        """The visit (paid as usual), admitted to a zero-rate bed, with the
        doctor's estimate and the cashier's advance when asked for."""
        appointment, encounter = self._visit()
        bed = self._bed()
        admission = self.env["hospital.admission"].sudo().create({
            "patient_id": appointment.patient_id.id,
            "physician_id": self.doctor.id,
            "company_id": self.env.company.id,
            "encounter_id": encounter.id,
            "ward_id": bed.ward_id.id, "room_id": bed.room_id.id, "bed_id": bed.id,
        })
        admission.action_confirm_admission()  # system path: the gate is not under test here
        if estimate:
            admission.with_user(self.doctor_user)._desk_set_estimate(
                estimate, "Planned stay", str(uuid.uuid4()), admission.workflow_revision
            )
            admission.with_user(self.cashier)._cashier_record_advance(
                advance if advance is not None else estimate, "cash",
                idempotency_key=uuid.uuid4().hex,
            )
        self.env.invalidate_all()
        return appointment, encounter, admission

    def _summary(self, admission):
        self.env.invalidate_all()
        return admission.sudo()._inpatient_financial_summary()

    def _credit_to(self, admission, target):
        """Deliver neutral care until the patient's credit is exactly `target`."""
        credit = self._summary(admission)["patient_credit"]
        burn = credit - target
        self.assertGreaterEqual(burn, 0.0)
        if burn > 0:
            engine = self.env["hospital.billing.engine"].sudo()
            service = self.env["hospital.billing.service"].sudo().create({
                "name": "IC burn %s" % uuid.uuid4().hex[:5], "service_type": "procedure",
                "default_price": burn, "prepayment_required": False,
            })
            charge = engine.create_or_update_charge(
                admission.encounter_id, "hospital.procedure.request", admission.id, "ic_burn",
                "IC burn", source_line_id=int(uuid.uuid4().int % 10**8), service=service,
                qty_requested=1, unit_price=burn,
            )
            engine.activate_charge(charge)
            engine.mark_charge_delivered(charge, qty_delivered=1)
        self.assertEqual(self._summary(admission)["patient_credit"], target)

    def _cover(self, dispense):
        return self._detail(dispense).get("financial_cover")


@tagged("post_install", "-at_install", "pharmacy_inpatient_credit")
class TestInpatientCredit(PharmacyInpatientCreditCase):
    def test_a_b_c_h_covered_by_the_advance_then_4_of_10_delivered(self):
        appointment, encounter, admission = self._inpatient(estimate=50000.0)
        _rx, dispense = self._rx(appointment, self.amoxil, qty=10.0)
        before = self._summary(admission)
        receipts_before = self.env["hospital.charge.receipt"].sudo().search_count([])

        response, payload = self._prepare(dispense, 4.0)
        self.assertEqual(response.status_code, 200, payload)
        detail = self._detail(dispense)
        self.assertEqual(detail["financial_cover"], "inpatient_credit")      # A
        self.assertFalse(detail["billing_blocked"])
        self.assertNotEqual(detail["lane"], "awaiting_clearance")
        self._assert_no_money(detail, "covered detail")

        response, payload = self._validate_http(dispense)
        self.assertEqual(response.status_code, 200, payload)
        self.env.invalidate_all()
        line = dispense.sudo().line_ids[:1]                                  # B
        self.assertEqual(line.billing_delivered_quantity, 4.0)
        self.assertEqual(line.prescribed_quantity - line.billing_delivered_quantity, 6.0)
        charge = line.charge_line_id.sudo()
        self.assertEqual(charge.qty_delivered, 4.0)
        self.assertEqual(charge.amount_eligible, 4 * PRICE)

        after = self._summary(admission)                                     # C
        self.assertEqual(after["actual_delivered"] - before["actual_delivered"], 4 * PRICE)
        self.assertEqual(before["patient_credit"] - after["patient_credit"], 4 * PRICE)
        self.assertEqual(after["delivered_by_category"]["medication"], 4 * PRICE)
        self.assertEqual(after["financial_state"], "credit")
        # H: the credit was consumed by DELIVERY; Pharmacy took no money.
        self.assertEqual(self.env["hospital.charge.receipt"].sudo().search_count([]), receipts_before)

    def test_d_a_shortfall_blocks(self):
        appointment, _encounter, admission = self._inpatient(estimate=50000.0)
        self._credit_to(admission, 400.0)
        _rx, dispense = self._rx(appointment, self.amoxil, qty=10.0)
        self._prepare(dispense, 4.0)                                         # 480 > 400
        detail = self._detail(dispense)
        self.assertEqual(detail["lane"], "awaiting_clearance")
        self.assertEqual(detail["financial_cover"], "shortfall")
        self._assert_no_money(detail, "shortfall detail")
        response, payload = self._validate_http(dispense)
        self._error(response, payload, 422, "pharmacy_billing_blocked")
        self.assertEqual(dispense.sudo().line_ids[:1].billing_delivered_quantity, 0.0)

    def test_e_a_smaller_quantity_within_the_credit(self):
        appointment, _encounter, admission = self._inpatient(estimate=50000.0)
        self._credit_to(admission, 400.0)
        _rx, dispense = self._rx(appointment, self.amoxil, qty=10.0)
        self._prepare(dispense, 3.0)                                         # 360 <= 400
        self.assertEqual(self._cover(dispense), "inpatient_credit")
        response, payload = self._validate_http(dispense)
        self.assertEqual(response.status_code, 200, payload)

    def test_g_no_advance_and_no_credit_blocks(self):
        appointment, _encounter, admission = self._inpatient()
        self._credit_to(admission, 0.0)
        _rx, dispense = self._rx(appointment, self.amoxil, qty=10.0)
        self._prepare(dispense, 4.0)
        detail = self._detail(dispense)
        self.assertEqual(detail["lane"], "awaiting_clearance")
        self.assertEqual(detail["financial_cover"], "shortfall")

    def test_i_two_dispenses_cannot_spend_the_same_credit(self):
        appointment, _encounter, admission = self._inpatient(estimate=50000.0)
        self._credit_to(admission, 600.0)
        _rx1, first = self._rx(appointment, self.amoxil, qty=10.0)
        _rx2, second = self._rx(appointment, self.cetiriz, qty=10.0)
        self._prepare(first, 4.0)                                            # 480 of 600
        self._prepare(second, 4.0)                                           # 480 of 600
        # Each alone fits; together they would spend 960 of 600.
        self.assertEqual(self._cover(first), "inpatient_credit")
        self.assertEqual(self._cover(second), "inpatient_credit")
        response, payload = self._validate_http(first)
        self.assertEqual(response.status_code, 200, payload)
        # Validate re-decides under the lock, from the credit as it now stands.
        response, payload = self._validate_http(second)
        self._error(response, payload, 422, "pharmacy_billing_blocked")
        self.assertEqual(self._cover(second), "shortfall")
        self.assertEqual(self._summary(admission)["patient_credit"], 120.0)

    def test_j_a_doctor_still_cannot_validate(self):
        appointment, _encounter, _admission = self._inpatient(estimate=50000.0)
        _rx, dispense = self._rx(appointment, self.amoxil, qty=10.0)
        self._prepare(dispense, 4.0)
        response, payload = self._validate_http(
            dispense, user=self.doctor_user, password=self.doctor_password
        )
        self.assertIn(response.status_code, (403, 404), payload)
        self.assertEqual(dispense.sudo().line_ids[:1].billing_delivered_quantity, 0.0)


@tagged("post_install", "-at_install", "pharmacy_inpatient_credit")
class TestOutpatientUnchanged(PharmacyInpatientCreditCase):
    def test_f_an_outpatient_keeps_per_service_clearance(self):
        dispense, encounter = self._fresh(self.amoxil, qty=10.0)
        self._prepare(dispense, 4.0)
        detail = self._detail(dispense)
        self.assertEqual(detail["lane"], "awaiting_clearance")
        self.assertIsNone(detail["financial_cover"])
        response, payload = self._validate_http(dispense)
        self._error(response, payload, 422, "pharmacy_billing_blocked")
        # Paid at the cashier, as before: cleared by the service's own payment.
        self._pay(encounter)
        self.assertEqual(self._cover(dispense), "service")
        response, payload = self._validate_http(dispense)
        self.assertEqual(response.status_code, 200, payload)
