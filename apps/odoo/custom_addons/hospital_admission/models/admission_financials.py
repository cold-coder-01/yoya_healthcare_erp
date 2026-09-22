"""The inpatient financial summary: ACTUAL delivered care vs available funds
(Admissions Slice 3).

THE BUSINESS RULE THIS IMPLEMENTS
---------------------------------
Before admission a doctor may ESTIMATE what the stay will cost, and the
patient or a payer may PREPAY or AUTHORIZE an amount against it. Neither is
the bill. During the stay the hospital accrues what it ACTUALLY delivers. At
discharge the two are compared:

    actual > available  ->  the patient / payer owes the difference   (due)
    actual < available  ->  the unused money is the patient's         (refundable)
    actual = available  ->  settled                                   (covered)

An estimate is never revenue, and unused patient money is never silently kept.

ONE READ MODEL, NO SECOND LEDGER
--------------------------------
Nothing here is stored and nothing here writes. Every figure is read from the
record that already owns it:

    BED / STAY    hospital.admission's own stay segments (admission_stay.py),
                  priced from the rate recorded as each segment opened.
    CHARGE LINES  hospital_billing's charge engine on the visit's billing
                  account -- medication (the pharmacy writes DISPENSED quantity
                  into qty_delivered), laboratory (delivered when the result is
                  validated), radiology (delivered when the result is
                  released), the consultation and anything else routed through
                  hospital.billing.engine. The engine's own `amount_eligible`
                  is the actual: delivered quantity only, never the requested
                  quantity, and nothing that is cancelled, reversed, rejected
                  or emergency-bypassed.
    PROCEDURES    hospital.procedure.request, which still bills through the
                  LEGACY hospital.patient.bill -- a procedure counts once it is
                  DONE, at its generated bill's value, or at its price
                  reference while no bill has been generated yet.
    FUNDS         the patient cash the charge engine holds (confirmed receipt
                  allocations, less refunds), plus what was paid on the
                  legacy procedure and admission bills.
    PAYER         the sponsor share the engine has AUTHORIZED, capped per line
                  at what was actually delivered on that line -- an
                  authorization for ten tablets pays for the four dispensed.

No source is read twice: bed/stay never enters the charge engine, procedures
never enter it, and the legacy admission bill contributes only its PAYMENTS
(its lines are the same stay this module prices itself).

WHO SEES WHAT
-------------
_inpatient_financial_summary() returns amounts and is for server code only
(a leading underscore: not callable over RPC). _inpatient_financial_status()
is the amount-free projection the Admissions Desk serializes: a state, three
booleans and fixed review codes. The desk never sees a number.

sudo() throughout: the figures live on fields readable only by the money roles,
and the question "is this stay covered" is a property of the data, not of the
ward nurse asking it. What leaves this module for a non-money caller is the
projection, and only the projection.
"""
from odoo import models

from odoo.addons.hospital_billing.models.billing_account import LIVE_CHARGE_STATES
from odoo.addons.hospital_billing.models.charge_line import AMOUNT_TOLERANCE

from .admission_stay import STAY_REVIEW_MESSAGES, STAY_STATES

FINANCIAL_STATES = (
    "covered",         # actual delivered == available funds + payer share
    "due",             # actual delivered exceeds what is available
    "refundable",      # the patient has paid more than the actual care
    "pending",         # nothing delivered and nothing paid yet
    "not_applicable",  # no stay: a draft or cancelled admission
    "needs_review",    # the figures cannot be trusted until someone looks
)

# Why the figures need review. FIXED SENTENCES, no amount, no payer name.
FINANCIAL_REVIEW_MESSAGES = dict(
    STAY_REVIEW_MESSAGES,
    encounter_missing="The admission is linked to no visit, so its charges cannot be found.",
    delivered_charge_unbillable=(
        "Care was recorded as delivered on a charge that is not billable "
        "(authorization rejected or emergency bypass)."
    ),
    sponsor_coverage_unresolved=(
        "The visit has a sponsor, but the bed stay or a procedure is not yet on "
        "the charge engine, so the sponsor's share of it is not determined."
    ),
    currency_mismatch="The visit's billing account is in a different currency from the stay.",
)

NOT_APPLICABLE_STATUS = {
    "financial_state": "not_applicable",
    "billing_blocked": False,
    "settlement_required": False,
    "refund_due": False,
    "review_reasons": [],
}


class HospitalAdmissionFinancials(models.Model):
    _inherit = "hospital.admission"

    # ------------------------------------------------------------------
    # Sources
    # ------------------------------------------------------------------
    def _charge_engine_actuals(self, currency):
        """The visit's charge lines, read once. All amounts in the account's
        currency, which _inpatient_financial_summary() checks is the stay's."""
        self.ensure_one()
        result = {
            "actual": 0.0,
            "estimated": 0.0,
            "payer_authorized": 0.0,
            "funds": 0.0,
            "pending_delivery": False,
            "review_reasons": [],
        }
        encounter = self.sudo().encounter_id
        if not encounter:
            return result
        account = self.env["hospital.billing.account"].sudo().search(
            [("encounter_id", "=", encounter.id)], limit=1
        )
        if not account:
            return result
        if account.currency_id and account.currency_id != currency:
            result["review_reasons"].append("currency_mismatch")
        # active_test=False: an archived charge is still money the hospital
        # holds or care it delivered.
        lines = self.env["hospital.charge.line"].sudo().with_context(active_test=False).search(
            [("billing_account_id", "=", account.id)]
        )
        live = lines.filtered(lambda line: line.charge_state in LIVE_CHARGE_STATES)
        for line in live:
            result["actual"] += line.amount_eligible
            result["estimated"] += line.amount_estimated
            result["payer_authorized"] += min(line.amount_sponsor_authorized, line.amount_eligible)
            if line.amount_delivered - line.amount_eligible > AMOUNT_TOLERANCE:
                if "delivered_charge_unbillable" not in result["review_reasons"]:
                    result["review_reasons"].append("delivered_charge_unbillable")
            if (
                line.delivery_state not in ("delivered", "not_delivered")
                and line.qty_requested - line.qty_delivered > 1e-6
            ):
                result["pending_delivery"] = True
        # CASH FACTS COVER EVERY LINE, cancelled ones included: money taken
        # against a cancelled prepaid service is still the patient's money.
        for line in lines:
            result["funds"] += line.amount_received - line.amount_refunded
        return result

    def _procedure_actuals(self):
        """DONE procedures on this admission, still billed through the legacy
        hospital.patient.bill. A soft dependency: hospital_procedure depends on
        this module, so the model is looked up rather than imported."""
        self.ensure_one()
        result = {"actual": 0.0, "funds": 0.0, "count": 0}
        if "hospital.procedure.request" not in self.env:
            return result
        procedures = self.env["hospital.procedure.request"].sudo().with_context(
            active_test=False
        ).search([("admission_id", "=", self.id), ("state", "=", "done")])
        for procedure in procedures:
            bill = procedure.bill_id
            if bill:
                result["actual"] += bill.amount_total
                result["funds"] += bill.amount_paid
            else:
                # Not billed yet: valued at its price reference, which is what
                # action_generate_procedure_bill() would bill.
                result["actual"] += procedure.default_price or 0.0
            result["count"] += 1
        return result

    # ------------------------------------------------------------------
    # THE summary (amounts; server-side only)
    # ------------------------------------------------------------------
    def _inpatient_financial_summary(self, now=None):
        """Actual delivered care vs available funds, for one admission.

        Keys: estimated_or_authorized, actual_delivered, bed_stay,
        charge_engine_actual, procedures_actual, prepayment_available,
        payer_authorized, patient_responsibility, remaining_due,
        refundable_balance, pending_delivery, financial_state, review_reasons.

        estimated_or_authorized is what was ORDERED on the charge engine
        (requested quantities, including any prepaid estimate or deposit
        charge) plus the stay so far. It is reported beside the actual so the
        difference is visible; it never enters the comparison.
        """
        self.ensure_one()
        admission = self.sudo()
        currency = admission.company_id.currency_id or self.env.company.currency_id
        zero = dict(
            estimated_or_authorized=0.0, actual_delivered=0.0, bed_stay=0.0,
            charge_engine_actual=0.0, procedures_actual=0.0, prepayment_available=0.0,
            payer_authorized=0.0, patient_responsibility=0.0, remaining_due=0.0,
            refundable_balance=0.0, pending_delivery=False,
        )
        if admission.state not in STAY_STATES:
            return dict(zero, financial_state="not_applicable", review_reasons=[])

        reasons = []
        if not admission.encounter_id:
            reasons.append("encounter_missing")

        stay = admission._bed_stay_breakdown(now)
        reasons += stay["review_reasons"]
        charges = admission._charge_engine_actuals(currency)
        reasons += charges["review_reasons"]
        procedures = admission._procedure_actuals()

        # The legacy admission bill is the same stay this module prices; only
        # what was PAID on it is new information.
        legacy_admission_paid = admission.bill_id.amount_paid if admission.bill_id else 0.0

        # Only the charge engine can split a charge with a sponsor. The stay
        # and legacy procedures cannot, so for a sponsored visit their
        # patient/payer split is genuinely undetermined -- and saying "due"
        # would bill the patient for what a payer may carry.
        encounter = admission.encounter_id
        sponsored = bool(encounter) and (encounter.payer_type or "self_pay") != "self_pay"
        if sponsored and (stay["total"] + procedures["actual"]) > AMOUNT_TOLERANCE:
            reasons.append("sponsor_coverage_unresolved")

        actual = currency.round(stay["total"] + charges["actual"] + procedures["actual"])
        payer = currency.round(charges["payer_authorized"])
        patient = currency.round(actual - payer)
        funds = currency.round(charges["funds"] + procedures["funds"] + legacy_admission_paid)
        remaining_due = currency.round(max(0.0, patient - funds))
        refundable = currency.round(max(0.0, funds - patient))
        reasons = list(dict.fromkeys(reasons))

        if reasons:
            state = "needs_review"
        elif remaining_due > AMOUNT_TOLERANCE:
            state = "due"
        elif refundable > AMOUNT_TOLERANCE:
            state = "refundable"
        elif actual > AMOUNT_TOLERANCE:
            state = "covered"
        else:
            state = "pending"

        return {
            "estimated_or_authorized": currency.round(charges["estimated"] + stay["total"]),
            "actual_delivered": actual,
            "bed_stay": stay["total"],
            "charge_engine_actual": currency.round(charges["actual"]),
            "procedures_actual": currency.round(procedures["actual"]),
            "prepayment_available": funds,
            "payer_authorized": payer,
            "patient_responsibility": patient,
            "remaining_due": remaining_due,
            "refundable_balance": refundable,
            "pending_delivery": charges["pending_delivery"],
            "financial_state": state,
            "review_reasons": reasons,
        }

    # ------------------------------------------------------------------
    # The amount-free projection (what the desk may serialize)
    # ------------------------------------------------------------------
    def _inpatient_financial_status(self, now=None):
        """{financial_state, billing_blocked, settlement_required, refund_due,
        review_reasons} -- and nothing with a number in it.

        billing_blocked      the figures need review before anything is billed
                             or settled from them.
        settlement_required  the patient owes more than they have paid. False
                             when blocked: an untrusted figure asserts nothing.
        refund_due           the patient has paid more than the actual care.
                             Also False when blocked. Deriving it records no
                             refund and moves no money: the cash refund is the
                             Cashier's act, and nothing here consumes the
                             balance.
        """
        self.ensure_one()
        if self.sudo().state not in STAY_STATES:
            return dict(NOT_APPLICABLE_STATUS)
        summary = self._inpatient_financial_summary(now)
        state = summary["financial_state"]
        blocked = state == "needs_review"
        return {
            "financial_state": state,
            "billing_blocked": blocked,
            "settlement_required": (not blocked) and summary["remaining_due"] > AMOUNT_TOLERANCE,
            "refund_due": (not blocked) and summary["refundable_balance"] > AMOUNT_TOLERANCE,
            "review_reasons": [
                {"code": code, "message": FINANCIAL_REVIEW_MESSAGES.get(code, code)}
                for code in summary["review_reasons"]
            ],
        }
