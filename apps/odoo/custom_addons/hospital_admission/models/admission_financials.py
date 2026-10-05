"""The inpatient financial summary: ACTUAL delivered care vs available funds
(Admissions Slices 3-4).

THE BUSINESS RULE THIS IMPLEMENTS
---------------------------------
Before admission a doctor may ESTIMATE what the stay will cost, and the
patient or a payer may PREPAY or AUTHORIZE an amount against it. Neither is
the bill. During the stay the hospital accrues what it ACTUALLY delivers. At
discharge the two are compared:

    patient share > patient funds  ->  the patient owes the difference   (due)
    patient share < patient funds  ->  the unused money is the patient's (refundable)
    equal                          ->  settled                           (covered)

An estimate is never revenue, and unused patient money is never silently kept.

ONE AUTHORITY: THE CHARGE ENGINE (Slice 4)
------------------------------------------
Every delivered service of the episode is a hospital.charge.line on the visit's
hospital.billing.account, and the summary reads them there:

    BED / STAY    one delivered charge per billed 24-hour period plus the
                  admission fee, posted by _sync_stay_charges() (admission_stay)
    MEDICATION    the pharmacy writes DISPENSED quantity into qty_delivered
    LABORATORY    delivered when the result is validated
    RADIOLOGY     delivered when the result is released
    PROCEDURES    requested on submit, delivered when done (hospital_procedure)
    CONSULTATION  delivered when the consultation completes; anything else
                  routed through hospital.billing.engine

The engine's `amount_eligible` is the actual: delivered quantity only, and
nothing cancelled, reversed, rejected or emergency-bypassed.

Two things are NOT on the engine and are added beside it, never on top of it:

    STAY ACCRUAL  periods that have STARTED but not yet been posted (an active
                  stay between syncs). Computed from the same stay segments and
                  the same period rule, minus what is already posted. Zero after
                  every sync, and always zero after the final discharge.
    LEGACY        procedures completed BEFORE the bridge, billed on a legacy
                  hospital.patient.bill and never on the engine; and what was
                  paid on a legacy admission bill. A procedure with an engine
                  charge is never counted here -- that is the double-count guard.

PAYER SHARE -- THE EXISTING AUTHORITY, NOT A NEW ONE
----------------------------------------------------
    * A visit in the responsibility domain (or self-pay): the sponsor share the
      engine has AUTHORIZED, capped per line at what was delivered on it.
    * A LEGACY third-party visit (payer_type other than self-pay, outside the
      responsibility domain): hospital.billing.engine.check_financial_clearance
      treats it as whole-bill payer credit -- "no cash required" -- once no
      charge awaits authorization. The summary follows that same rule, so the
      discharge gate and the clearance gate cannot disagree. A charge still
      awaiting payer authorization makes the figures needs_review.

WHO SEES WHAT
-------------
_inpatient_financial_summary() returns amounts and is for server code only (a
leading underscore: not callable over RPC). _inpatient_financial_status() is
the amount-free projection the desks serialize. sudo() throughout: the figures
live on fields readable only by the money roles, and whether a stay is covered
is a property of the data, not of the ward nurse asking.
"""
import hashlib
import json

from odoo import models

from odoo.addons.hospital_billing.models.billing_account import LIVE_CHARGE_STATES
from odoo.addons.hospital_billing.models.charge_line import AMOUNT_TOLERANCE

from .admission_authority import ADMISSION_ESTIMATE_STATES
from .admission_cashier import SETTLEMENT_TOKEN_PREFIX
from .admission_estimate import DEPOSIT_EVENT
from .admission_stay import STAY_REVIEW_MESSAGES, STAY_STATES

FINANCIAL_STATES = (
    "covered",         # patient share == patient funds
    "due",             # patient share exceeds patient funds
    "refundable",      # care is over (or medically ready): the patient paid
                       # more than their share, and the excess is theirs back
    "credit",          # still IN CARE with patient funds above the care so far:
                       # an advance / patient credit held toward ongoing care,
                       # NOT a refund -- the stay is still accruing
    "pending",         # nothing delivered and nothing paid yet
    "not_applicable",  # no stay: a draft or cancelled admission
    "needs_review",    # the figures cannot be trusted until someone looks
)

PROCEDURE_SOURCE_MODEL = "hospital.procedure.request"

# WHERE THE ACTUAL CAME FROM, in the words a cashier uses. A partition of
# actual_delivered, built from the SAME eligible amounts in the same pass: the
# buckets always sum to the actual, so a breakdown can never disagree with the
# figure it explains. The stay is recognised by its source (this module posts
# it); everything else by the billing catalogue's service_type, snapshotted on
# the charge -- no clinical record is read to categorise money.
DELIVERED_CATEGORIES = (
    "admission", "bed_stay", "procedure", "medication", "laboratory", "radiology",
    "consultation", "other",
)
SERVICE_TYPE_CATEGORY = {
    "admission": "bed_stay",
    "pharmacy": "medication",
    "laboratory": "laboratory",
    "radiology": "radiology",
    "procedure": "procedure",
    "consultation": "consultation",
}

# THE SETTLEMENT STAGES, in the order the desks show them. Each is a part of
# ONE server-side computation, reported with its result; the browser renders
# what the server computed and never calls a department itself.
CARE_STAGES = (
    ("admission", "Admission", ("admission",)),
    ("stay", "Stay / bed", ("bed_stay",)),
    ("procedures", "Procedures", ("procedure",)),
    ("pharmacy", "Pharmacy", ("medication",)),
    ("laboratory", "Laboratory", ("laboratory",)),
    ("radiology", "Radiology", ("radiology",)),
    ("other", "Consultation & other services", ("consultation", "other")),
)
PAYER_STAGE_REASONS = frozenset({"payer_authorization_pending", "sponsor_coverage_unresolved"})

# The final-settlement vocabulary (Advance slice). financial_state keeps its
# Slice 3 meaning for the discharge gate; settlement_state is the same verdict
# in the words the settlement window uses. "covered" and "pending" are EVEN.
SETTLEMENT_STATE = {
    "due": "due",
    "refundable": "refund_due",
    "credit": "credit",
    "covered": "even",
    "pending": "even",
    "needs_review": "needs_review",
    "not_applicable": "not_applicable",
}

# Why the figures need review. FIXED SENTENCES, no amount, no payer name.
FINANCIAL_REVIEW_MESSAGES = dict(
    STAY_REVIEW_MESSAGES,
    encounter_missing="The admission is linked to no visit, so its charges cannot be found.",
    delivered_charge_unbillable=(
        "Care was recorded as delivered on a charge that is not billable "
        "(authorization rejected or emergency bypass)."
    ),
    sponsor_coverage_unresolved=(
        "The visit has a sponsor, but a procedure billed outside the visit's "
        "billing account has no sponsor share decided."
    ),
    payer_authorization_pending=(
        "A charge on this visit is still awaiting the payer's authorization."
    ),
    stay_charge_mismatch=(
        "The stay charges posted to the billing account do not match the stay."
    ),
    currency_mismatch="The visit's billing account is in a different currency from the stay.",
)

NOT_APPLICABLE_STATUS = {
    "financial_state": "not_applicable",
    "billing_blocked": False,
    "settlement_required": False,
    "refund_due": False,
    "patient_credit": False,
    "review_reasons": [],
}


class HospitalAdmissionFinancials(models.Model):
    _inherit = "hospital.admission"

    # ------------------------------------------------------------------
    # Sources
    # ------------------------------------------------------------------
    def _billing_account(self):
        self.ensure_one()
        encounter = self.sudo().encounter_id
        if not encounter:
            return self.env["hospital.billing.account"].sudo().browse()
        return self.env["hospital.billing.account"].sudo().search(
            [("encounter_id", "=", encounter.id)], limit=1
        )

    def _charge_engine_actuals(self, currency):
        """The visit's charge lines, read once."""
        self.ensure_one()
        result = {
            "actual": 0.0,
            "estimated": 0.0,
            "payer_authorized": 0.0,
            "funds": 0.0,
            "stay_posted": 0.0,
            "fee_posted": 0.0,
            "advance": 0.0,
            "pending_delivery": False,
            "authorization_pending": False,
            "live_lines": self.env["hospital.charge.line"].sudo().browse(),
            "by_category": dict.fromkeys(DELIVERED_CATEGORIES, 0.0),
            "review_reasons": [],
        }
        account = self._billing_account()
        if not account:
            return result
        if account.currency_id and account.currency_id != currency:
            result["review_reasons"].append("currency_mismatch")
        # active_test=False: an archived charge is still money the hospital
        # holds or care it delivered.
        lines = self.env["hospital.charge.line"].sudo().with_context(active_test=False).search(
            [("billing_account_id", "=", account.id)]
        )
        # THE DEPOSIT CHARGE IS MONEY, NEVER CARE. It holds the patient's
        # advance (see admission_estimate): its receipts are funds, below, and
        # it contributes nothing to the actual, the estimate of care, the payer
        # share or "something ordered is still undelivered".
        deposit = lines.filtered(self._is_deposit_line)
        live = lines.filtered(
            lambda line: line.charge_state in LIVE_CHARGE_STATES and line not in deposit
        )
        result["live_lines"] = live
        for line in live:
            result["actual"] += line.amount_eligible
            result["by_category"][self._delivered_category(line)] += line.amount_eligible
            result["estimated"] += line.amount_estimated
            result["payer_authorized"] += min(line.amount_sponsor_authorized, line.amount_eligible)
            if line.authorization_state == "pending":
                result["authorization_pending"] = True
            if line.source_model == self.STAY_SOURCE_MODEL and line.source_res_id == self.id:
                result["stay_posted"] += line.amount_eligible
                if line.source_event == self.ADMISSION_FEE_EVENT:
                    result["fee_posted"] += line.amount_eligible
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
        for line in deposit:
            result["advance"] += line.amount_received - line.amount_refunded
        return result

    def _is_deposit_line(self, line):
        return (
            line.source_model == self.STAY_SOURCE_MODEL
            and line.source_res_id == self.id
            and line.source_event == DEPOSIT_EVENT
        )

    def _delivered_category(self, line):
        """One of DELIVERED_CATEGORIES for a live charge line."""
        if line.source_model == self.STAY_SOURCE_MODEL:
            return "admission" if line.source_event == self.ADMISSION_FEE_EVENT else "bed_stay"
        return SERVICE_TYPE_CATEGORY.get(line.service_id.service_type, "other")

    def _legacy_procedure_actuals(self):
        """DONE procedures on this admission that are NOT on the charge engine
        -- completed before the procedure billing bridge, or on a visit-less
        admission -- valued on their legacy hospital.patient.bill, or at their
        price reference while no bill exists. A soft dependency:
        hospital_procedure depends on this module, so it is looked up."""
        self.ensure_one()
        result = {"actual": 0.0, "funds": 0.0, "count": 0}
        if "hospital.procedure.request" not in self.env:
            return result
        procedures = self.env["hospital.procedure.request"].sudo().with_context(
            active_test=False
        ).search([("admission_id", "=", self.id), ("state", "=", "done")])
        if not procedures:
            return result
        bridged = set(
            self.env["hospital.charge.line"].sudo().with_context(active_test=False).search(
                [
                    ("source_model", "=", PROCEDURE_SOURCE_MODEL),
                    ("source_res_id", "in", procedures.ids),
                    ("charge_state", "in", list(LIVE_CHARGE_STATES)),
                ]
            ).mapped("source_res_id")
        )
        for procedure in procedures:
            if procedure.id in bridged:
                continue
            bill = procedure.bill_id
            if bill:
                result["actual"] += bill.amount_total
                result["funds"] += bill.amount_paid
            else:
                result["actual"] += procedure.default_price or 0.0
            result["count"] += 1
        return result

    def _is_legacy_payer_credit(self, live_lines):
        """Does the EXISTING clearance authority treat this visit as whole-bill
        payer credit? The same test check_financial_clearance() makes, in the
        same order, so the two can never answer differently."""
        self.ensure_one()
        encounter = self.sudo().encounter_id
        if not encounter or (encounter.payer_type or "self_pay") == "self_pay":
            return False
        mode = encounter.company_id.sudo().payer_responsibility_mode or "off"
        engine = self.env["hospital.billing.engine"].sudo()
        if mode == "enforce" and engine._participates_in_responsibility(encounter, live_lines):
            return False
        return True

    # ------------------------------------------------------------------
    # THE summary (amounts; server-side only)
    # ------------------------------------------------------------------
    def _inpatient_financial_summary(self, now=None):
        """Actual delivered care vs available patient funds, for one admission.

        Keys: estimated_or_authorized, actual_delivered, bed_stay,
        stay_unposted, charge_engine_actual, procedures_actual,
        prepayment_available, payer_authorized, patient_responsibility,
        remaining_due, refundable_balance, pending_delivery, financial_state,
        review_reasons, delivered_by_category.

        delivered_by_category partitions actual_delivered over
        DELIVERED_CATEGORIES (the unposted stay tail counts as bed_stay, legacy
        procedures as procedure). Ordered-but-undelivered value is in no bucket.
        """
        self.ensure_one()
        admission = self.sudo()
        currency = admission.company_id.currency_id or self.env.company.currency_id
        zero = dict(
            estimated_or_authorized=0.0, actual_delivered=0.0, bed_stay=0.0,
            stay_unposted=0.0, charge_engine_actual=0.0, procedures_actual=0.0,
            prepayment_available=0.0, payer_authorized=0.0, patient_responsibility=0.0,
            remaining_due=0.0, refundable_balance=0.0, pending_delivery=False,
            delivered_by_category=dict.fromkeys(DELIVERED_CATEGORIES, 0.0),
        )
        if admission.state not in STAY_STATES or (
            # LEGACY HISTORY (e.g. ADM00001): a stay discharged before visits
            # existed. It was billed, if at all, on a legacy bill that remains
            # readable; it is not recomputed or flagged now.
            admission.state == "discharged" and not admission.encounter_id
        ):
            # No stay to settle -- a draft may still hold an ADVANCE, which is
            # reported (and is all the patient has paid against this stay).
            summary = dict(zero, financial_state="not_applicable", review_reasons=[])
            advance = currency.round(sum(
                line.amount_received - line.amount_refunded
                for line in admission._deposit_charge()
            ))
            summary["prepayment_available"] = advance
            summary.update(admission._settlement_extras(summary, currency, advance=advance))
            return summary

        reasons = []
        if not admission.encounter_id:
            reasons.append("encounter_missing")

        stay = admission._bed_stay_breakdown(now)
        reasons += stay["review_reasons"]
        charges = admission._charge_engine_actuals(currency)
        reasons += charges["review_reasons"]
        legacy = admission._legacy_procedure_actuals()

        # Stay value the engine does not hold yet. Only ever the not-yet-posted
        # tail of an active stay; after a sync it is zero.
        if admission.encounter_id:
            unposted = stay["total"] - charges["stay_posted"]
            if unposted < -AMOUNT_TOLERANCE:
                reasons.append("stay_charge_mismatch")
            unposted = max(0.0, unposted)
        else:
            # A visit-less (legacy) stay was never on the engine at all.
            unposted = stay["total"]

        legacy_admission_paid = admission.bill_id.amount_paid if admission.bill_id else 0.0
        actual = currency.round(charges["actual"] + unposted + legacy["actual"])

        if self._is_legacy_payer_credit(charges["live_lines"]):
            if charges["authorization_pending"]:
                reasons.append("payer_authorization_pending")
            # Whole-bill credit: the payer carries the episode, as the
            # clearance authority already says.
            payer = actual
        else:
            payer = currency.round(charges["payer_authorized"])
            encounter = admission.encounter_id
            sponsored = bool(encounter) and bool(encounter.patient_payer_id)
            if sponsored and legacy["actual"] > AMOUNT_TOLERANCE:
                reasons.append("sponsor_coverage_unresolved")

        patient = currency.round(max(0.0, actual - payer))
        funds = currency.round(charges["funds"] + legacy["funds"] + legacy_admission_paid)
        remaining_due = currency.round(max(0.0, patient - funds))
        # THE CARE STAGE DECIDES WHAT AN EXCESS MEANS -- here, once, for every
        # desk. While the patient is in care and not medically ready, funds
        # above the care delivered SO FAR are an advance / patient credit held
        # toward care still to come: the stay is accruing and the next bed-day
        # consumes it. Only once care is over (medically ready, or discharged)
        # is the excess the patient's to be given back. The money is the same
        # either way; only its meaning changes, so it is never hidden.
        excess = currency.round(max(0.0, funds - patient))
        in_care = (
            admission.state in ADMISSION_ESTIMATE_STATES
            and not admission.medical_discharge_ready
        )
        refundable = 0.0 if in_care else excess
        reasons = list(dict.fromkeys(reasons))

        if reasons:
            state = "needs_review"
        elif remaining_due > AMOUNT_TOLERANCE:
            state = "due"
        elif excess > AMOUNT_TOLERANCE:
            state = "credit" if in_care else "refundable"
        elif actual > AMOUNT_TOLERANCE:
            state = "covered"
        else:
            state = "pending"

        by_category = dict(charges["by_category"])
        # The unposted tail splits into the admission fee (if it was never
        # posted) and bed-days, so the admission line is never hidden in stay.
        fee_unposted = min(unposted, max(0.0, stay["admission_fee"] - charges["fee_posted"]))
        by_category["admission"] += fee_unposted
        by_category["bed_stay"] += unposted - fee_unposted
        by_category["procedure"] += legacy["actual"]

        advance = currency.round(charges["advance"])
        settlement_paid = currency.round(admission._settlement_receipts_total())
        other_payments = currency.round(max(0.0, funds - advance - settlement_paid))

        summary = {
            "estimated_or_authorized": currency.round(charges["estimated"] + unposted),
            "actual_delivered": actual,
            "bed_stay": stay["total"],
            "stay_unposted": currency.round(unposted),
            "charge_engine_actual": currency.round(charges["actual"]),
            "procedures_actual": currency.round(legacy["actual"]),
            "prepayment_available": funds,
            "payer_authorized": payer,
            "patient_responsibility": patient,
            "remaining_due": remaining_due,
            "refundable_balance": refundable,
            # Unapplied patient funds, whatever the care stage: credit while in
            # care, refundable once care is over.
            "patient_credit": excess,
            "pending_delivery": charges["pending_delivery"],
            "financial_state": state,
            "review_reasons": reasons,
            "delivered_by_category": {
                key: currency.round(value) for key, value in by_category.items()
            },
        }
        summary.update(admission._settlement_extras(
            summary, currency, advance=advance, settlement_paid=settlement_paid,
            other_payments=other_payments,
        ))
        return summary

    # ------------------------------------------------------------------
    # The final-settlement projection (Advance slice)
    # ------------------------------------------------------------------
    def _settlement_receipts_total(self):
        """Confirmed receipts taken at the Cashier as inpatient SETTLEMENT."""
        self.ensure_one()
        receipts = self.env["hospital.charge.receipt"].sudo().search([
            ("intake_token", "=like", (SETTLEMENT_TOKEN_PREFIX % self.id) + "%"),
            ("state", "=", "confirmed"),
        ])
        return sum(receipts.mapped("amount"))

    def _settlement_extras(self, summary, currency, advance=0.0, settlement_paid=0.0,
                           other_payments=0.0):
        """What the settlement window adds to the summary. Derived ONLY from
        figures the summary already holds -- no second formula.

          advance_applied    the part of the patient's funds consumed by their
                             delivered share: min(funds, share)
          unapplied_credit   the rest, still the patient's money (== refundable)
          settlement_difference  funds - share: negative owed, positive credit
          quote              a fingerprint of the figures a payment is taken
                             against; a payment quoting stale figures is refused
        """
        self.ensure_one()
        patient = summary["patient_responsibility"]
        funds = summary["prepayment_available"]
        reasons = set(summary["review_reasons"])
        by = summary["delivered_by_category"]
        stay_reasons = set(STAY_REVIEW_MESSAGES) | {"stay_charge_mismatch"}

        def status(flagged):
            return "review" if flagged else "complete"

        stages = [
            {
                "key": key,
                "label": label,
                "amount": currency.round(sum(by.get(bucket, 0.0) for bucket in buckets)),
                "status": status(key == "stay" and bool(reasons & stay_reasons)),
            }
            for key, label, buckets in CARE_STAGES
        ]
        stages += [
            {"key": "payer", "label": "Payer responsibility",
             "amount": summary["payer_authorized"],
             "status": status(bool(reasons & PAYER_STAGE_REASONS))},
            {"key": "payments", "label": "Patient advances & payments",
             "amount": funds, "status": "complete"},
            {"key": "reconciliation", "label": "Final reconciliation",
             "amount": currency.round(funds - patient), "status": status(bool(reasons))},
        ]
        fingerprint = json.dumps(
            [self.id, summary["actual_delivered"], patient, funds, summary["remaining_due"],
             summary["refundable_balance"], summary["financial_state"]],
            separators=(",", ":"),
        )
        return {
            "estimate_amount": currency.round(self.sudo().estimated_amount or 0.0),
            "advance_received": advance,
            "settlement_paid": settlement_paid,
            "other_payments": other_payments,
            "patient_funds": funds,
            "advance_applied": currency.round(min(funds, patient)),
            "unapplied_credit": summary.get("patient_credit", summary["refundable_balance"]),
            "settlement_difference": currency.round(funds - patient),
            "settlement_state": SETTLEMENT_STATE.get(summary["financial_state"], "even"),
            "stages": stages,
            "quote": hashlib.sha256(fingerprint.encode("utf-8")).hexdigest()[:24],
        }

    # ------------------------------------------------------------------
    # The amount-free projection (what the desks may serialize)
    # ------------------------------------------------------------------
    def _inpatient_financial_status(self, now=None):
        """{financial_state, billing_blocked, settlement_required, refund_due,
        review_reasons} -- and nothing with a number in it.

        billing_blocked      the figures need review before anything is billed
                             or settled from them.
        settlement_required  the patient owes more than they have paid. False
                             when blocked: an untrusted figure asserts nothing.
        refund_due           care is over and the patient has paid more than
                             their share. Also False when blocked, and False
                             while in care (see patient_credit).
        patient_credit       in care, not medically ready, and the patient's
                             funds exceed the care so far: held toward ongoing
                             care, never "return the money". Deriving it records no refund
                             and moves no money: the cash refund is the
                             Cashier's act, and nothing here consumes it.
        """
        self.ensure_one()
        if self.sudo().state not in STAY_STATES:
            return dict(NOT_APPLICABLE_STATUS)
        summary = self._inpatient_financial_summary(now)
        return self._status_from_summary(summary)

    @staticmethod
    def _status_from_summary(summary):
        state = summary["financial_state"]
        blocked = state == "needs_review"
        return {
            "financial_state": state,
            "billing_blocked": blocked,
            "settlement_required": (not blocked) and summary["remaining_due"] > AMOUNT_TOLERANCE,
            "refund_due": state == "refundable" and summary["refundable_balance"] > AMOUNT_TOLERANCE,
            # Held toward ongoing care, not owed back. Amount-free like the rest.
            "patient_credit": state == "credit",
            "review_reasons": [
                {"code": code, "message": FINANCIAL_REVIEW_MESSAGES.get(code, code)}
                for code in summary["review_reasons"]
            ],
        }

    # ------------------------------------------------------------------
    # THE settlement gate (Slice 4)
    # ------------------------------------------------------------------
    def _discharge_financial_refusal(self, summary):
        """None when the account lets the patient go, else the refusal code.

          due           -> admission_settlement_required. The patient owes; the
                           cashier collects first.
          needs_review  -> admission_financial_review_required. Nothing is
                           settled from figures nobody can trust.
          refundable    -> ALLOWED. The patient paid MORE than their share.
                           Keeping them in the bed until the hospital returns
                           their own money would detain a patient for the
                           hospital's debt, and no existing rule asks for it.
                           The obligation survives the discharge: the credit
                           stays on the billing account for the Cashier.
          covered / pending -> allowed.
        """
        state = summary["financial_state"]
        if state == "due":
            return "admission_settlement_required"
        if state == "needs_review":
            return "admission_financial_review_required"
        return None
