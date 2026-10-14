"""The ACCOUNTANT DESK's reads over the inpatient settlement authority.

WHAT THIS IS
------------
Finance follow-up for inpatient accounts the Cashier does not finish:

  refund_due     the stay is over (medically ready or DISCHARGED) and the
                 patient paid more than their share -- the hospital owes it.
                 Discharge is NOT blocked by this (Admissions finalizes once
                 the patient owes nothing); the refund survives the discharge
                 until Finance records it here.
  needs_review   the settlement figures cannot be trusted.
  accounting_pending
                 a DISCHARGED stay whose money is settled operationally but
                 whose accounting has not been posted (receipts, the
                 delivered-care invoice, the advance application). Finance
                 posts it with Post accounting.
  refunded       a refund was recorded and nothing refundable remains
                 (recent, read-only reference).

ACCOUNTING HOOKS. This module knows nothing about the general ledger. The
_accounting_* hooks below are what the admission accounting bridge
(hospital_admission_accounting) overrides; without it, accounting is
"unavailable", Post accounting refuses, and a refund stays operational.

WHAT THIS IS NOT
----------------
No new money and no new formula. Every figure is
_inpatient_financial_summary() (via _cashier_facts); the refund itself is
_cashier_record_refund() -- the existing accounting act, already restricted to
hospital_billing's ACCOUNTING_GROUPS. Its audit row is the refund's history
entry; there is no second refund ledger. Nothing here takes a payment, revises
an estimate, admits, transfers, discharges or touches a bed.

NOT DATE-DRIVEN. Refund work may surface days after discharge, so money still
held on a stay whose care is over is found through the charge lines that hold
it -- not through a visit date or a "recent discharges" window.

Visibility is the caller's own: an admission appears only when its visit is
readable under the caller's record rules (the Accountant reads every visit by
the service-desk rule). Underscored, so none of it is reachable over RPC.
"""
import json
import re

from odoo import api, models
from odoo.exceptions import AccessError

from odoo.addons.hospital_billing.models.charge_line import ACCOUNTING_GROUPS, AMOUNT_TOLERANCE

from .admission_authority import ADMISSION_ACTIVE_STATES
from .admission_cashier import (
    ACTIVE_SCAN_MAX,
    ADVANCE_TOKEN_PREFIX,
    CASHIER_SETTLEMENT_MESSAGES,
    DISCHARGED_SCAN_MAX,
    REFUND_AUDIT_KIND,
    SETTLEMENT_TOKEN_PREFIX,
    AdmissionAccountingError,
    CashierSettlementError,
)
from .admission_stay import STAY_STATES

ACCOUNTING_LANES = ("refund_due", "needs_review", "accounting_pending", "refunded")

# Accounting status of a stay, DERIVED from accounting facts on every read
# (the bridge decides; nothing stores it):
#   unavailable    no accounting module -- operational only
#   nothing_to_post  no money and no delivered care
#   not_posted     receipts, delivered care or the advance application still
#                  have to reach the ledger
#   posted         everything the settlement holds is in the ledger
#   legacy_pending a refund was recorded before refund accounting existed
#   outside_scope  a payer / sponsor stay: payer accounting is not this close
ACCOUNTING_STATUSES = (
    "unavailable", "nothing_to_post", "not_posted", "posted", "legacy_pending", "outside_scope",
)
ACCOUNTING_UNAVAILABLE = {
    "status": "unavailable",
    "reference": None,
    "posted_at": None,
    "posted_by": None,
    "may_post": False,
    "may_post_legacy": False,
    "legacy_refund_total": 0.0,
    "refusal": "accounting_unavailable",
}
# Refund work is rare and must never fall off a window: a generous ceiling,
# reported as truncated if it is ever reached.
REFUND_SCAN_MAX = 500
REFUNDED_RECENT_MAX = 50

# A refund audit written before the structured row existed.
_LEGACY_REFUND = re.compile(r"^Refund of (?P<amount>[0-9]+(?:\.[0-9]+)?) recorded on [^:]+: (?P<reason>.*)$", re.S)


def _sort_key(facts, order):
    """Refund due / review: the OLDEST obligation first. Refunded: the most
    recent refund first."""
    lane = facts["accounting_lane"]
    if lane == "refunded":
        at = facts["last_refund_at"]
        return (order[lane], -(at.timestamp() if at else 0.0), facts["admission"]["id"])
    when = facts["admission"]["discharge_date"] or facts["admission"]["admission_date"]
    return (order[lane], when.timestamp() if when else 0.0, facts["admission"]["id"])


class HospitalAdmissionAccounting(models.Model):
    _inherit = "hospital.admission"

    # ------------------------------------------------------------------
    # Authority
    # ------------------------------------------------------------------
    @api.model
    def _accounting_may_desk(self):
        """hospital_billing's ACCOUNTING groups -- the very groups its
        refund_advance() guard admits: Accountant, Manager, Administrator.
        Real membership of the real user, no env.su shortcut."""
        return any(self.env.user.has_group(group) for group in ACCOUNTING_GROUPS)

    @api.model
    def _accounting_assert_authorized(self):
        if not self._accounting_may_desk():
            raise AccessError(CASHIER_SETTLEMENT_MESSAGES["inpatient_accounting_desk_not_authorized"])

    @api.model
    def _accounting_find(self, admission_id):
        """One admission for the Accountant (a sudo record), discharged
        included. Hidden and missing are the same answer."""
        self._accounting_assert_authorized()
        admission = self.sudo().with_context(active_test=False).search(
            [("id", "=", admission_id), ("state", "in", list(STAY_STATES)),
             ("encounter_id", "!=", False)],
            limit=1,
        )
        admission = admission.with_env(self.env)._cashier_visible()
        if not admission:
            raise CashierSettlementError("inpatient_not_found")
        return admission

    # ------------------------------------------------------------------
    # History (read from what already exists)
    # ------------------------------------------------------------------
    def _accounting_payments_in(self):
        """Every receipt on this visit's billing account, oldest first, with
        what it was for. Read, never written."""
        self.ensure_one()
        admission = self.sudo()
        account = admission._billing_account()
        if not account:
            return []
        receipts = self.env["hospital.charge.receipt"].sudo().search(
            [("billing_account_id", "=", account.id)], order="received_at asc, id asc"
        )
        advance = ADVANCE_TOKEN_PREFIX % admission.id
        settlement = SETTLEMENT_TOKEN_PREFIX % admission.id
        rows = []
        for receipt in receipts:
            token = receipt.intake_token or ""
            kind = (
                "advance" if token.startswith(advance)
                else "settlement" if token.startswith(settlement)
                else "payment"
            )
            rows.append({
                "id": receipt.id,
                "reference": receipt.name,
                "kind": kind,
                "amount": receipt.amount,
                "payment_method": receipt.payment_method,
                "payment_reference": receipt.payment_reference or None,
                "actor": receipt.received_by_id.name or None,
                "at": receipt.received_at,
                "state": receipt.state,
                "accounting_posted": bool(receipt.accounting_posted),
            })
        return rows

    def _accounting_refunds_out(self):
        """Every refund recorded on this admission, oldest first, read from
        the refund's own audit row (structured since this slice; an older
        row's fixed sentence is read as written)."""
        self.ensure_one()
        admission = self.sudo()
        logs = self.env["hospital.audit.log"].sudo().with_context(active_test=False).search(
            [
                ("model_name", "=", admission._name),
                ("record_id", "=", admission.id),
                ("description", "=like", "Refund of %"),
            ],
            order="action_date asc, id asc",
        )
        rows = []
        for index, log in enumerate(logs, start=1):
            data = {}
            try:
                parsed = json.loads(log.new_value or "")
                if isinstance(parsed, dict) and parsed.get("kind") == REFUND_AUDIT_KIND:
                    data = parsed
            except ValueError:
                data = {}
            if not data:
                match = _LEGACY_REFUND.match(log.description or "")
                if not match:
                    continue
                data = {"amount": float(match.group("amount")), "reason": match.group("reason")}
            rows.append({
                "id": log.id,
                "reference": "%s/RF%s" % (admission.name, index),
                "kind": "refund",
                "amount": float(data.get("amount") or 0.0),
                "reason": data.get("reason") or None,
                "refundable_before": data.get("refundable_before"),
                "refundable_after": data.get("refundable_after"),
                "actor": log.user_id.name or None,
                "at": log.action_date,
                "state": "recorded",
                # A refund recorded before refund methods existed has none.
                "payment_method": data.get("payment_method") or None,
                "payment_reference": data.get("payment_reference") or None,
                "operation_token": data.get("operation_token") or None,
                # Filled by the accounting bridge from its refund postings.
                "accounting_posted": False,
                "accounting_reference": None,
                "accounting_posted_at": None,
                "legacy": False,
            })
        return admission._accounting_annotate_refunds(rows)

    # ------------------------------------------------------------------
    # ACCOUNTING HOOKS (overridden by hospital_admission_accounting)
    # ------------------------------------------------------------------
    def _accounting_annotate_refunds(self, rows):
        """Mark which refund rows have a posted refund entry. Base: none."""
        return rows

    def _accounting_status(self, summary=None):
        """This stay's accounting status (see ACCOUNTING_STATUSES)."""
        self.ensure_one()
        return dict(ACCOUNTING_UNAVAILABLE)

    def _accounting_before_refund(self, amount, payment_method, payment_reference, token):
        """Called inside the refund's savepoint, BEFORE any operational line
        moves. The bridge closes the stay's accounting and posts the refund
        entry; raising refuses the whole refund. Base: no ledger, nothing."""
        return None

    def _accounting_after_refund(self, posting, audit_log):
        """Called once the refund's audit row exists, inside the same
        savepoint, so the bridge can link its posting to it."""
        return None

    def _accounting_post(self, idempotency_key):
        """Post a discharged stay's accounting. Returns (operation, replayed)."""
        self.ensure_one()
        self._accounting_assert_authorized()
        raise AdmissionAccountingError("accounting_unavailable")

    def _accounting_post_legacy(self, idempotency_key, payment_method, payment_reference=None,
                                accounting_date=None):
        """Post accounting for refunds recorded before refund accounting
        existed, with the method the Accountant supplies. Returns
        (operation, replayed)."""
        self.ensure_one()
        self._accounting_assert_authorized()
        raise AdmissionAccountingError("accounting_unavailable")

    # ------------------------------------------------------------------
    # Lanes and projections
    # ------------------------------------------------------------------
    def _accounting_lane(self, facts, refunds):
        summary = facts["summary"]
        if summary["financial_state"] == "needs_review":
            return "needs_review"
        # "refundable" is the AUTHORITY's verdict: only once care is over.
        if summary["financial_state"] == "refundable" and summary["refundable_balance"] > AMOUNT_TOLERANCE:
            return "refund_due"
        if refunds and summary["refundable_balance"] <= AMOUNT_TOLERANCE:
            return "refunded"
        # Settled operationally, discharged, and not yet in the ledger.
        accounting = facts.get("accounting") or {}
        if facts["admission"]["state"] == "discharged" and accounting.get("status") == "not_posted":
            return "accounting_pending"
        return None

    def _accounting_facts(self, now=None):
        """The Cashier's facts (identity, location, summary, receipts) plus
        the physician, the refunds and the accounting lane."""
        self.ensure_one()
        admission = self.sudo()
        facts = admission._cashier_facts(now)
        facts["accounting"] = admission._accounting_status(facts["summary"])
        refunds = admission._accounting_refunds_out()
        facts["refunds"] = refunds
        facts["refunded_total"] = sum(row["amount"] for row in refunds)
        facts["last_refund_at"] = refunds[-1]["at"] if refunds else None
        facts["accounting_lane"] = admission._accounting_lane(facts, refunds)
        facts["physician"] = admission.physician_id.name or None
        return facts

    def _accounting_detail(self, now=None):
        self.ensure_one()
        facts = self._accounting_facts(now)
        facts["payments_in"] = self.sudo()._accounting_payments_in()
        return facts

    @api.model
    def _accounting_census(self, search=None, lanes=None, now=None):
        """The Accountant's worklist. Returns (facts list, truncated).

        Candidates, unioned:
          * every stay whose care is over (medically ready or discharged) with
            patient money still HELD on its charge lines -- found through the
            lines, so a discharge days or weeks ago is still found;
          * the ward census and the recent discharges, for needs_review;
          * the most recent refunds, for the refunded lane.
        """
        self._accounting_assert_authorized()
        lanes = tuple(lanes or ACCOUNTING_LANES)
        Admission = self.sudo().with_context(active_test=False)
        base = [("encounter_id", "!=", False), ("state", "in", list(STAY_STATES))]
        if search:
            base += [
                "|", "|", "|",
                ("patient_id.name", "ilike", search),
                ("patient_id.identification_code", "ilike", search),
                ("encounter_id.name", "ilike", search),
                ("name", "ilike", search),
            ]

        held = self.env["hospital.charge.line"].sudo().with_context(active_test=False)._read_group(
            [("amount_prepayment_held", ">", AMOUNT_TOLERANCE), ("encounter_id", "!=", False)],
            groupby=["encounter_id"],
        )
        held_encounter_ids = [encounter.id for (encounter,) in held]
        refund_candidates = Admission.search(
            base + [
                ("encounter_id", "in", held_encounter_ids),
                "|", ("state", "=", "discharged"), ("medical_discharge_ready", "=", True),
            ],
            order="id desc", limit=REFUND_SCAN_MAX + 1,
        )
        active = Admission.search(
            base + [("state", "in", list(ADMISSION_ACTIVE_STATES))],
            order="id desc", limit=ACTIVE_SCAN_MAX + 1,
        )
        discharged = Admission.search(
            base + [("state", "=", "discharged")],
            order="discharge_date desc, id desc", limit=DISCHARGED_SCAN_MAX,
        )
        operations = self.env["hospital.admission.operation"].sudo().search(
            [("operation_type", "=", "refund")],
            order="performed_at desc, id desc", limit=REFUNDED_RECENT_MAX * 4,
        )
        refunded = operations.mapped("admission_id").filtered_domain(base)[:REFUNDED_RECENT_MAX]
        truncated = len(refund_candidates) > REFUND_SCAN_MAX or len(active) > ACTIVE_SCAN_MAX

        candidates = (
            refund_candidates[:REFUND_SCAN_MAX] | active[:ACTIVE_SCAN_MAX] | discharged | refunded
        )
        visible = candidates.with_env(self.env)._cashier_visible()
        order = {lane: index for index, lane in enumerate(ACCOUNTING_LANES)}
        rows = []
        for admission in visible:
            facts = admission._accounting_facts(now)
            if facts["accounting_lane"] in lanes:
                rows.append(facts)
        rows.sort(key=lambda facts: _sort_key(facts, order))
        return rows, truncated
