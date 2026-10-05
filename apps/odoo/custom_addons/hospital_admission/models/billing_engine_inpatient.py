"""The inpatient answer to hospital.billing.engine._inpatient_credit_clearance.

WHY THIS EXISTS
---------------
Service clearance (check_financial_clearance) counts cash ALLOCATED TO THE
SERVICE'S OWN CHARGE. An admitted self-pay patient's money is held elsewhere on
the visit -- the admission's deposit charge (the advance) and earlier payments
-- and is meant to fund the care delivered during the stay. Asked per charge,
a patient holding 50,000 of credit is told to pay again for 1,200 of medicine.

THE ANSWER, FROM THE ONE AUTHORITY
----------------------------------
For an ACTIVE admission on a SELF-PAY visit, compare

    required   the value about to be DELIVERED on the charges asked about --
               their requested-but-undelivered value, i.e. only the intended
               increment, never the whole prescription
    available  the patient's unapplied credit from _inpatient_financial_summary()
               (patient funds - patient share of care delivered so far)

required <= available -> covered. Otherwise the shortfall is reported.

Nothing is written: no receipt, no allocation, no advance deduction. When the
charge is then DELIVERED through the ordinary billing path, the settlement's
delivered care rises by exactly that value and the credit falls with it -- the
single ledger does the consuming.

NOT FOR: an outpatient (no active admission), a sponsored visit (payer_type
other than self-pay, or a payer eligibility on the visit -- the payer's own
authorization rules decide), or figures under review (never covered).
"""
from odoo import api, models

from odoo.addons.hospital_billing.models.billing_account import LIVE_CHARGE_STATES
from odoo.addons.hospital_billing.models.charge_line import AMOUNT_TOLERANCE

from .admission_authority import ADMISSION_ACTIVE_STATES


class HospitalBillingEngineInpatient(models.AbstractModel):
    _inherit = "hospital.billing.engine"

    @api.model
    def _inpatient_credit_clearance(self, encounter, charges, lock=False):
        enc = encounter.sudo()
        admission = self.env["hospital.admission"].sudo().search(
            [("encounter_id", "=", enc.id), ("state", "in", list(ADMISSION_ACTIVE_STATES))],
            order="id desc", limit=1,
        )
        if not admission:
            return super()._inpatient_credit_clearance(encounter, charges, lock=lock)
        if (enc.payer_type or "self_pay") != "self_pay" or (
            "patient_payer_id" in enc._fields and enc.patient_payer_id
        ):
            return super()._inpatient_credit_clearance(encounter, charges, lock=lock)

        if lock:
            # The admission row is the credit's serialization point: every
            # inpatient delivery that relies on the credit takes it, so two
            # cannot both spend the same remainder. Plain FOR UPDATE -- a read
            # lock, not a write.
            self.env.flush_all()
            self.env.cr.execute(
                "SELECT id FROM hospital_admission WHERE id = %s FOR UPDATE", (admission.id,)
            )
            self.env.invalidate_all()

        currency = admission.company_id.currency_id or self.env.company.currency_id
        # Only the charges the cash rule is actually waiting on (a positive
        # amount due for clearance), never the admission's deposit charge, and
        # only their UNDELIVERED value: care already delivered is already in
        # the settlement's figures, so counting it again would spend it twice.
        pending = charges.sudo().filtered(
            lambda charge: charge.charge_state in LIVE_CHARGE_STATES
            and not admission._is_deposit_line(charge)
            and charge.amount_due_for_clearance > AMOUNT_TOLERANCE
        )
        required = currency.round(sum(
            max(0.0, charge.amount_estimated - charge.amount_eligible) for charge in pending
        ))
        summary = admission._inpatient_financial_summary()
        if summary["financial_state"] == "needs_review":
            return {"covered": False, "required": required, "available": 0.0, "shortfall": required}
        # COMMITTED BUT NOT YET DELIVERED. A scan that has started or a sample
        # that has been drawn was cleared against this credit, but the
        # settlement counts it only once it is delivered (released). Until
        # then its undelivered value is spoken for, so another department
        # cannot be cleared against the same money. Read from the charges
        # themselves -- no reservation record, no ledger. Only "in_progress"
        # (commenced, nothing delivered yet): the undispensed remainder of a
        # partly dispensed prescription has not been committed to anyone.
        account = enc.billing_account_id
        committed = currency.round(sum(
            max(0.0, charge.amount_estimated - charge.amount_eligible)
            for charge in (account.charge_line_ids if account else charges.browse()) - pending
            if charge.charge_state in LIVE_CHARGE_STATES
            and charge.delivery_state == "in_progress"
            and not admission._is_deposit_line(charge)
            and charge.amount_due_for_clearance > AMOUNT_TOLERANCE
        ))
        available = currency.round(
            summary.get("patient_credit", summary["refundable_balance"]) - committed
        )
        shortfall = currency.round(max(0.0, required - available))
        return {
            "covered": shortfall <= AMOUNT_TOLERANCE,
            "required": required,
            "available": available,
            "shortfall": shortfall,
        }
