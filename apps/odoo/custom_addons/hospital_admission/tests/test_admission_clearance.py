"""Pre-admission financial clearance: the model gate.

A self-pay request gets a bed only after the physician's estimate AND the full
advance -- or an authorized emergency bypass. Enforced in _confirm_one(), so the
Admissions Desk and the backend form obey it alike; only superuser (system)
code passes without it. A sponsored visit is outside this gate.
"""
import uuid

from odoo.tests import tagged

from ..models.admission_authority import AdmissionDeskError, AdmissionWorkflowError
from .test_admission_desk_mutations import AdmissionDeskMutationCase

G_CASHIER = "hospital_billing.group_hospital_cashier"


@tagged("post_install", "-at_install", "admission_clearance")
class TestAdmissionClearanceGate(AdmissionDeskMutationCase):
    CLEAR_ADMISSION_VISITS = False

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.cashier = cls._make_user("adm_clear_cashier", [G_CASHIER])

    def _pending(self):
        appointment, encounter = self._visit()
        admission, _ = self._request(appointment)
        return admission, encounter

    def _estimate(self, admission, amount):
        admission.with_user(self.doctor_user)._desk_set_estimate(
            amount, "Planned surgery", str(uuid.uuid4()), admission.sudo().workflow_revision
        )
        self.env.invalidate_all()

    def _advance(self, admission, amount):
        admission.with_user(self.cashier)._cashier_record_advance(
            amount, "cash", idempotency_key=uuid.uuid4().hex
        )
        self.env.invalidate_all()

    def _clearance(self, admission):
        self.env.invalidate_all()
        return admission.sudo()._admission_financial_clearance()

    def _refused(self, admission, bed):
        with self.assertRaises(AdmissionDeskError) as caught:
            with self.env.cr.savepoint():
                self._admit(admission, bed)
        self.assertEqual(caught.exception.code, "admission_financial_clearance_required")
        self.env.invalidate_all()
        self.assertEqual(admission.sudo().state, "draft")
        self.assertFalse(bed.sudo().current_admission_id)

    def test_estimate_then_full_advance_then_bed(self):
        admission, _ = self._pending()
        self.assertEqual(self._clearance(admission)["state"], "awaiting_estimate")
        self._refused(admission, self.bed_a)

        self._estimate(admission, 50000.0)
        verdict = self._clearance(admission)
        self.assertEqual(
            (verdict["state"], verdict["estimate"], verdict["remaining"]),
            ("awaiting_advance", 50000.0, 50000.0),
        )
        self._advance(admission, 30000.0)
        verdict = self._clearance(admission)
        self.assertEqual((verdict["advance_received"], verdict["remaining"]), (30000.0, 20000.0))
        self._refused(admission, self.bed_a)

        self._advance(admission, 20000.0)
        self.assertTrue(self._clearance(admission)["cleared"])
        self._admit(admission, self.bed_a)
        self.env.invalidate_all()
        self.assertEqual(admission.sudo().state, "admitted")
        # The advance is held toward care: credit, not a refund.
        self.assertEqual(admission.sudo()._inpatient_financial_summary()["financial_state"], "credit")

    def test_the_backend_form_obeys_the_gate_and_system_code_does_not(self):
        admission, _ = self._pending()
        admission.sudo().write({
            "ward_id": self.bed_b.ward_id.id, "room_id": self.bed_b.room_id.id, "bed_id": self.bed_b.id,
        })
        with self.assertRaises(AdmissionWorkflowError) as caught:
            with self.env.cr.savepoint():
                admission.with_user(self.receptionist).action_confirm_admission()
        self.assertEqual(caught.exception.code, "admission_financial_clearance_required")
        # Superuser (system / migration) code is not a user channel.
        admission.sudo().action_confirm_admission()
        self.assertEqual(admission.sudo().state, "admitted")

    def test_the_emergency_bypass_is_the_override(self):
        admission, encounter = self._pending()
        with self.assertRaises(Exception):
            encounter.with_user(self.receptionist).write(
                {"emergency_bypass": True, "emergency_bypass_reason": "Unstable"}
            )
        encounter.with_user(self.manager).write(
            {"emergency_bypass": True, "emergency_bypass_reason": "Unstable; theatre now"}
        )
        verdict = self._clearance(admission)
        self.assertEqual((verdict["state"], verdict["cleared"]), ("emergency_bypass", True))
        self.assertEqual(encounter.sudo().emergency_bypass_authorized_by, self.manager)
        self._admit(admission, self.bed_a)
        self.assertEqual(admission.sudo().state, "admitted")

    def test_a_sponsored_visit_is_outside_the_gate(self):
        admission, encounter = self._pending()
        self._raw("UPDATE hospital_encounter SET payer_type = 'insurance' WHERE id = %s", (encounter.id,))
        verdict = self._clearance(admission)
        self.assertEqual((verdict["state"], verdict["cleared"]), ("sponsored", True))

    def test_a_pending_unpaid_estimate_is_revised_and_the_cashier_follows(self):
        """Estimate revision A: before any advance the doctor revises 50,000
        to 45,000; revision 2, and the Cashier now requires 45,000."""
        admission, _ = self._pending()
        self._estimate(admission, 50000.0)
        self._estimate(admission, 45000.0)
        record = admission.sudo()
        self.assertEqual((record.estimated_amount, record.estimate_revision), (45000.0, 2))
        self.assertEqual(record.estimate_revision_ids.mapped("amount"), [50000.0, 45000.0])
        verdict = self._clearance(admission)
        self.assertEqual((verdict["state"], verdict["remaining"]), ("awaiting_advance", 45000.0))
        self.assertEqual(record._cashier_advance_outstanding(), 45000.0)
        # A pending shortfall is the pre-admission advance, not "additional".
        self.assertFalse(record._estimate_additional_advance_required())
        self._advance(admission, 45000.0)
        self.assertTrue(self._clearance(admission)["cleared"])
        self._admit(admission, self.bed_a)
        self.env.invalidate_all()
        self.assertEqual(admission.sudo().state, "admitted")

    def test_no_estimate_no_advance(self):
        admission, _ = self._pending()
        from ..models.admission_cashier import CashierSettlementError
        with self.assertRaises(CashierSettlementError) as caught:
            self._advance(admission, 1000.0)
        self.assertEqual(caught.exception.code, "inpatient_no_estimate")
