"""Pre-admission financial clearance over real HTTP, with real users.

  A. the doctor requests admission -> the estimate is available on the PENDING
     request (before any bed)
  B. no estimate -> no advance can be taken; Admit is not offered and refused
  C. estimate 50,000 -> Cashier: Advance Required 50,000 on the pending request
  D. advance 30,000 -> 20,000 remaining; Admit still refused
  E. advance 20,000 more -> financially cleared; Admit offered
  F. the patient is admitted only after clearance
  G. the doctor cannot take money      H. the cashier cannot change the estimate
  I. the ward nurse sees the reason, never an amount
  J. after admission the final settlement works as built
  K. the existing, audited emergency bypass clears a request before payment;
     a sponsored visit is outside this gate; the backend form obeys it too
"""
import json
import uuid

from odoo.tests import tagged

from odoo.addons.hospital_admission.models.admission_authority import AdmissionWorkflowError

from .test_admissions_desk_api import DETAIL
from .test_admissions_desk_mutations_api import VISIT, AdmissionsMutationCase

ESTIMATE = VISIT + "/admission-estimate"
SETTLEMENT = DETAIL + "/settlement"
CASHIER = "/yoya-emr/api/v1/cashier"
ADVANCE = CASHIER + "/admissions/%s/advance"
CASHIER_DETAIL = CASHIER + "/admissions/%s"
CASHIER_WORKLIST = CASHIER + "/worklist"


def token():
    return str(uuid.uuid4())


@tagged("post_install", "-at_install", "admission_clearance")
class ClearanceCase(AdmissionsMutationCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.cashier = cls.denied["cashier"]
        cls.accountant = cls.denied["accountant"]
        # A ward that charges nothing for the stay, so the figures below are
        # exactly the care this suite delivers.
        cls.ward_b.sudo().write({"daily_ward_rate": 0.0, "admission_fee": 0.0})

    def _free_bed(self):
        room = self._room(self.ward_b, "CL%s" % uuid.uuid4().hex[:3])
        return self._bed(room, "CL-%s" % uuid.uuid4().hex[:3])

    def _estimate(self, appointment, admission, amount, user=None):
        return self._post(ESTIMATE % appointment.id, {
            "operation_token": token(),
            "expected_revision": admission.sudo().workflow_revision,
            "amount": amount,
            "reason": "Planned surgery",
        }, user or self.doctor_user)

    def _advance(self, admission, amount, user=None):
        return self._post(ADVANCE % admission.id, {
            "amount": amount, "payment_method": "cash", "idempotency_key": uuid.uuid4().hex,
        }, user or self.cashier)

    def _clearance(self, admission, user=None):
        return self._ok(DETAIL % admission.id, user or self.receptionist)["admission"]

    def _desk_admit_http(self, admission, bed):
        return self._http_admit(admission, bed, clear=False)


@tagged("post_install", "-at_install", "admission_clearance")
class TestPreAdmissionFlow(ClearanceCase):
    def test_a_to_f_estimate_advance_then_admit(self):
        appointment, encounter, admission = self._requested()
        self.assertEqual(admission.sudo().state, "draft")
        bed = self._free_bed()

        # A. The estimate is available on the PENDING request.
        seen = self._ok(ESTIMATE % appointment.id, self.doctor_user)
        self.assertEqual(seen["admission"]["state"], "draft")
        self.assertTrue(seen["can_edit"])

        # B. No estimate: nothing to collect, and no bed.
        detail = self._clearance(admission)
        self.assertEqual(detail["admission_clearance"]["state"], "awaiting_estimate")
        self.assertFalse(detail["can_admit"])
        self._assert_refused(*self._advance(admission, 1000), 409, "inpatient_no_estimate")
        self._assert_refused(*self._desk_admit_http(admission, bed), 409, "admission_financial_clearance_required")

        # C. Estimate 50,000 -> Advance Required on the pending request.
        response, payload = self._estimate(appointment, admission, 50000)
        self.assertEqual(response.status_code, 200, payload)
        rows = self._ok(CASHIER_WORKLIST, self.cashier, q=admission.sudo().patient_id.name)
        [row] = [r for r in rows["inpatient_settlement"] if r["admission"]["id"] == admission.id]
        self.assertEqual(row["lane"], "advance_required")
        self.assertEqual(row["admission"]["state"], "draft")
        self.assertEqual(row["admission"]["name"], admission.sudo().name)
        self.assertEqual(row["encounter"]["name"], encounter.sudo().name)
        self.assertEqual(
            (row["advance"]["requested"], row["advance"]["received"], row["advance"]["outstanding"]),
            (50000.0, 0.0, 50000.0),
        )
        self.assertEqual(self._clearance(admission)["admission_clearance"]["state"], "awaiting_advance")

        # D. 30,000 of 50,000: still blocked, 20,000 remaining.
        response, payload = self._advance(admission, 30000)
        self.assertEqual(response.status_code, 200, payload)
        window = self._ok(SETTLEMENT % admission.id, self.receptionist)["admission_clearance"]
        self.assertEqual(
            (window["state"], window["estimate"], window["advance_received"], window["remaining"]),
            ("awaiting_advance", 50000.0, 30000.0, 20000.0),
        )
        self.assertFalse(self._clearance(admission)["can_admit"])
        self._assert_refused(*self._desk_admit_http(admission, bed), 409, "admission_financial_clearance_required")
        self.assertEqual(admission.sudo().state, "draft")
        self.assertEqual(bed.sudo().state, "available")

        # E. The remaining 20,000: cleared.
        response, payload = self._advance(admission, 20000)
        self.assertEqual(response.status_code, 200, payload)
        detail = self._clearance(admission)
        self.assertEqual(detail["admission_clearance"]["state"], "cleared")
        self.assertTrue(detail["can_admit"])

        # F. Admitted only now.
        response, payload = self._desk_admit_http(admission, bed)
        self.assertEqual(response.status_code, 200, payload)
        admission.invalidate_recordset()
        self.assertEqual(admission.sudo().state, "admitted")
        self.assertIsNone(self._clearance(admission)["admission_clearance"])

    def test_g_h_roles(self):
        appointment, _, admission = self._requested()
        self._estimate(appointment, admission, 10000)
        response, payload = self._advance(admission, 100, user=self.doctor_user)
        self.assertEqual(response.status_code, 403, payload)                       # G
        response, payload = self._estimate(appointment, admission, 1, user=self.cashier)
        self.assertEqual(response.status_code, 403, payload)                       # H
        self.assertEqual(admission.sudo().estimated_amount, 10000.0)

    def test_i_the_ward_nurse_sees_the_reason_never_an_amount(self):
        appointment, _, admission = self._requested()
        # A request bound for Ward A, so the Ward A nurse's roster covers it.
        admission.sudo().write({"ward_id": self.ward_a.id})
        self._estimate(appointment, admission, 50000)
        self._advance(admission, 30000)
        response, payload = self._get(DETAIL % admission.id, self.nurse_a)
        self.assertEqual(response.status_code, 200, payload)
        clearance = payload["data"]["admission"]["admission_clearance"]
        self.assertEqual(set(clearance), {"state", "cleared", "message"})
        self.assertEqual(clearance["state"], "awaiting_advance")
        self.assertNotIn("30000", json.dumps(payload))
        self.assertNotIn("50000", json.dumps(payload))
        response, payload = self._get(SETTLEMENT % admission.id, self.nurse_a)
        self.assertEqual(response.status_code, 403, payload)
        response, payload = self._get(ESTIMATE % appointment.id, self.nurse_a)
        self.assertEqual(response.status_code, 403, payload)


@tagged("post_install", "-at_install", "admission_clearance")
class TestAfterAdmission(ClearanceCase):
    def test_j_final_settlement_after_a_cleared_admission(self):
        appointment, encounter, admission = self._requested()
        self._estimate(appointment, admission, 50000)
        self._advance(admission, 50000)
        response, payload = self._desk_admit_http(admission, self._free_bed())
        self.assertEqual(response.status_code, 200, payload)

        engine = self.env["hospital.billing.engine"].sudo()
        service = self.env["hospital.billing.service"].sudo().create({
            "name": "CL care %s" % uuid.uuid4().hex[:5], "service_type": "procedure",
            "default_price": 57000.0, "prepayment_required": False,
        })
        charge = engine.create_or_update_charge(
            encounter.sudo(), "hospital.procedure.request", admission.id, "cl_care", "CL care",
            source_line_id=1, service=service, qty_requested=1, unit_price=57000.0,
        )
        engine.activate_charge(charge)
        engine.mark_charge_delivered(charge, qty_delivered=1)

        body = {"operation_token": token(), "expected_revision": admission.sudo().workflow_revision,
                "summary": "Well; home."}
        response, payload = self._post(VISIT % appointment.id + "/discharge-request", body, self.doctor_user)
        self.assertEqual(response.status_code, 200, payload)

        settlement = self._ok(SETTLEMENT % admission.id, self.receptionist)["settlement"]
        self.assertEqual((settlement["state"], settlement["remaining_due"]), ("due", 7000.0))
        quote = self._ok(CASHIER_DETAIL % admission.id, self.cashier)["settlement"]["quote"]
        response, payload = self._post(CASHIER + "/admissions/%s/payment" % admission.id, {
            "amount": 7000, "payment_method": "cash", "idempotency_key": uuid.uuid4().hex,
            "quote": quote,
        }, self.cashier)
        self.assertEqual(response.status_code, 200, payload)
        self.assertEqual(payload["data"]["settlement"]["state"], "even")

        body = {"operation_token": token(), "expected_revision": admission.sudo().workflow_revision}
        response, payload = self._post(DETAIL % admission.id + "/finalize-discharge", body, self.receptionist)
        self.assertEqual(response.status_code, 200, payload)
        self.assertEqual(admission.sudo().state, "discharged")


@tagged("post_install", "-at_install", "admission_clearance")
class TestOverrides(ClearanceCase):
    def test_k_the_emergency_bypass_clears_before_payment(self):
        _, encounter, admission = self._requested()
        self.assertEqual(self._clearance(admission)["admission_clearance"]["state"], "awaiting_estimate")
        # The existing route: an authorized role, a documented reason, audited.
        with self.assertRaises(Exception):
            encounter.with_user(self.receptionist).write(
                {"emergency_bypass": True, "emergency_bypass_reason": "Unstable"}
            )
        encounter.with_user(self.manager).write(
            {"emergency_bypass": True, "emergency_bypass_reason": "Unstable; theatre now"}
        )
        self.assertEqual(encounter.sudo().emergency_bypass_authorized_by, self.manager)
        detail = self._clearance(admission)
        self.assertEqual(detail["admission_clearance"]["state"], "emergency_bypass")
        self.assertTrue(detail["can_admit"])
        response, payload = self._desk_admit_http(admission, self._free_bed())
        self.assertEqual(response.status_code, 200, payload)

    def test_k_a_sponsored_visit_is_outside_the_advance_gate(self):
        _, encounter, admission = self._requested()
        self._raw("UPDATE hospital_encounter SET payer_type = 'insurance' WHERE id = %s", (encounter.id,))
        self.assertEqual(self._clearance(admission)["admission_clearance"]["state"], "sponsored")

    def test_k_the_backend_form_obeys_the_gate(self):
        _, _, admission = self._requested()
        bed = self._free_bed()
        record = admission.with_user(self.receptionist)
        record.sudo().write({"ward_id": bed.ward_id.id, "room_id": bed.room_id.id, "bed_id": bed.id})
        with self.assertRaises(AdmissionWorkflowError) as caught:
            record.action_confirm_admission()
        self.assertEqual(caught.exception.code, "admission_financial_clearance_required")
