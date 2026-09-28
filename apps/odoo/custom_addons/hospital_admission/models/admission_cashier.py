"""Inpatient settlement at the Cashier.

WHY THIS EXISTS
---------------
The Cashier queue is appointment-driven: one hospital day, front_desk_stage,
and clearance of REQUESTED services. An inpatient whose visit opened days ago,
whose consultation stage never moves, and whose billing account reads
"cleared" against its estimate is invisible to all three -- while the discharge
gate refuses them for an unpaid DELIVERED balance. This file lets the Cashier
see and settle that balance.

ONE AUTHORITY
-------------
Every figure is _inpatient_financial_summary(): the same delivered-basis
calculation the Admissions discharge gate applies. Nothing here recomputes a
balance, and no generic billing-account total (amount_outstanding,
amount_patient_outstanding, financial_clearance_state) is consulted -- those
measure money against the ESTIMATE and read zero for a stay that still owes.
A settlement therefore opens the discharge gate by itself: both desks read one
number.

MONEY MOVES THROUGH THE EXISTING INTAKE, AS THE CALLER
------------------------------------------------------
hospital.billing.account.record_operational_payment() -- the canonical wizard
path: receipt, allocations, audit, per-line patient ceilings and the intake
group check. It is called in the CASHIER's environment, never under sudo, so
hospital_billing's own authorization applies unchanged. No second ledger, no
new receipt type, no journal entry: a settlement receipt is an ordinary
operational receipt, and cash collection stays distinct from revenue
recognition exactly as it is today.

WHAT IS ELEVATED, AND WHY
-------------------------
A Hospital Cashier holds no ACL on hospital.admission, ward, room or bed, and
must not gain one: the admission carries the physician, the reason for
admission and the discharge summary. So the census below reads admissions
under sudo() -- AFTER an explicit group check -- and returns plain dicts of
cashier-safe identity (patient, MRN, visit, admission reference, ward/room/bed,
workflow state) and money. Nothing clinical is ever put in them, and every
method is underscored, so none of this is reachable over RPC. Visibility is
still decided by the caller's own rights: an admission appears only when its
visit (hospital.encounter) is readable by the caller under their record rules.
"""
from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError

from odoo.addons.hospital_billing.models.charge_line import (
    AMOUNT_TOLERANCE,
    OPERATIONAL_INTAKE_GROUPS,
)

from .admission_authority import ADMISSION_ACTIVE_STATES, AdmissionWorkflowError
from .admission_stay import STAY_STATES

# The operational lanes of an inpatient account at the Cashier. Derived from
# the summary on every read; nothing stores them.
#
#   due           the patient owes and no settlement payment has been taken yet
#   part_paid     the patient still owes after at least one settlement payment
#   refund_due    the patient has paid more than their share
#   needs_review  the figures cannot be trusted; nothing may be collected
#   settled       nothing owed and nothing to return
CASHIER_INPATIENT_LANES = ("due", "part_paid", "refund_due", "needs_review", "settled")
CASHIER_INPATIENT_DEFAULT_LANES = ("due", "part_paid", "refund_due", "needs_review")

# Why the row is in the queue, so the desk never has to guess.
CASHIER_INPATIENT_SOURCE = {
    "due": "inpatient_settlement",
    "part_paid": "inpatient_settlement",
    "settled": "inpatient_settlement",
    "refund_due": "refund_due",
    "needs_review": "needs_review",
}

# A settlement receipt is an ordinary receipt whose idempotency token carries
# this admission-scoped prefix. That makes "has a settlement payment been
# taken?" a fact READ FROM THE LEDGER -- not a stored flag that could drift --
# and scopes the client's key to one admission, so the same key can never
# replay a payment onto a different stay.
SETTLEMENT_TOKEN_PREFIX = "inpatient-settlement:%s:"
SETTLEMENT_KEY_MAX_LENGTH = 128

# Bounded scans. The active census is the ward census (tens, not thousands);
# discharged stays are scanned most-recent-first only to surface money still
# owed either way after the patient left.
ACTIVE_SCAN_MAX = 300
DISCHARGED_SCAN_MAX = 100

CASHIER_SETTLEMENT_MESSAGES = {
    "inpatient_not_found": "Inpatient account not found.",
    "inpatient_desk_not_authorized": (
        "Inpatient settlement requires the Hospital Cashier, Accountant, Hospital "
        "Manager or Hospital System Administrator role."
    ),
    "inpatient_financial_review_required": (
        "The inpatient account's figures need review before any payment can be "
        "taken against them. Nothing was charged."
    ),
    "inpatient_nothing_due": (
        "Nothing is currently owed on this inpatient account. Nothing was charged."
    ),
    "inpatient_payment_exceeds_due": (
        "The payment is more than the amount due. A settlement payment may not "
        "exceed the remaining balance; take an advance through the Billing "
        "Account instead. Nothing was charged."
    ),
    "inpatient_settlement_not_allocatable": (
        "The delivered charges on this visit cannot take this amount. The "
        "account needs review. Nothing was charged."
    ),
    "inpatient_invalid_amount": (
        "The payment amount must be greater than zero. Nothing was charged."
    ),
    "inpatient_idempotency_key_required": (
        "A settlement payment must carry an idempotency key so a retry cannot "
        "charge twice."
    ),
}


class CashierSettlementError(UserError):
    """A settlement refusal with a FIXED code and FIXED wording."""

    def __init__(self, code):
        self.code = code
        super().__init__(CASHIER_SETTLEMENT_MESSAGES[code])


class HospitalAdmissionCashier(models.Model):
    _inherit = "hospital.admission"

    # ------------------------------------------------------------------
    # Authority
    # ------------------------------------------------------------------
    @api.model
    def _cashier_may_settle(self):
        """The Cashier Desk's own group set: hospital_billing's operational
        intake groups, which are also the groups allowed to take the money.

        Real membership of the REAL user, deliberately with no env.su shortcut:
        this runs on sudo records inside this file, and a sudo record must not
        be able to authorize itself."""
        return any(self.env.user.has_group(group) for group in OPERATIONAL_INTAKE_GROUPS)

    @api.model
    def _cashier_assert_authorized(self):
        if not self._cashier_may_settle():
            raise AccessError(CASHIER_SETTLEMENT_MESSAGES["inpatient_desk_not_authorized"])

    def _cashier_visible(self):
        """These admissions (as sudo records), keeping only those whose VISIT
        the calling user may read under their own record rules."""
        admissions = self.sudo()
        encounters = admissions.mapped("encounter_id")
        if not encounters:
            return admissions.browse()
        Encounter = self.env["hospital.encounter"].with_env(self.env(su=False))
        readable = set(
            Encounter.with_context(active_test=False).search([("id", "in", encounters.ids)]).ids
        )
        return admissions.filtered(lambda admission: admission.encounter_id.id in readable)

    @api.model
    def _cashier_find(self, admission_id):
        """One admission for the Cashier (a sudo record), or
        CashierSettlementError(inpatient_not_found). Hidden and missing are the
        same answer."""
        self._cashier_assert_authorized()
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
    # Ledger facts
    # ------------------------------------------------------------------
    def _cashier_settlement_receipts(self):
        """Confirmed receipts taken through inpatient settlement for this stay."""
        self.ensure_one()
        return self.env["hospital.charge.receipt"].sudo().search(
            [
                ("intake_token", "=like", (SETTLEMENT_TOKEN_PREFIX % self.id) + "%"),
                ("state", "=", "confirmed"),
            ],
            order="received_at asc, id asc",
        )

    @staticmethod
    def _cashier_lane(summary, settlement_paid):
        if summary["financial_state"] == "needs_review":
            return "needs_review"
        if summary["remaining_due"] > AMOUNT_TOLERANCE:
            return "part_paid" if settlement_paid > AMOUNT_TOLERANCE else "due"
        if summary["refundable_balance"] > AMOUNT_TOLERANCE:
            return "refund_due"
        return "settled"

    # ------------------------------------------------------------------
    # Projections (plain dicts; cashier-safe by construction)
    # ------------------------------------------------------------------
    def _cashier_facts(self, now=None):
        """Identity, location, workflow state, the summary's money and the
        lane. No physician, no reason, no discharge summary, no diagnosis."""
        self.ensure_one()
        admission = self.sudo()
        summary = admission._inpatient_financial_summary(now)
        receipts = admission._cashier_settlement_receipts()
        settlement_paid = sum(receipts.mapped("amount"))
        lane = self._cashier_lane(summary, settlement_paid)
        patient = admission.patient_id
        encounter = admission.encounter_id
        currency = admission.company_id.currency_id or self.env.company.currency_id
        return {
            "admission": {
                "id": admission.id,
                "name": admission.name,
                "state": admission.state,
                "medical_discharge_ready": bool(admission.medical_discharge_ready),
                "admission_date": admission.admission_date,
                "discharge_date": admission.discharge_date,
            },
            "patient": {
                "id": patient.id,
                "name": patient.name,
                "identification_code": patient.identification_code or None,
            },
            "encounter": {
                "id": encounter.id,
                "name": encounter.name,
                "state": encounter.state,
            },
            "location": {
                "ward": admission.ward_id.name or None,
                "ward_code": admission.ward_id.code or None,
                "room": admission.room_id.name or None,
                "bed": admission.bed_id.name or None,
                "bed_code": admission.bed_id.code or None,
            },
            "currency": currency.name or None,
            "summary": summary,
            "settlement_paid": settlement_paid,
            "settlement_receipts": receipts,
            "lane": lane,
            "source": CASHIER_INPATIENT_SOURCE[lane],
        }

    @api.model
    def _cashier_inpatient_census(self, search=None, department_id=None, lanes=None, now=None):
        """The inpatient accounts the Cashier should see. Returns
        (facts list, truncated).

        NOT DATE-DRIVEN. An inpatient's appointment date says nothing about
        when their account needs settling, so the census is every ACTIVE stay
        (admitted / transferred, medically ready or not) plus recently
        discharged stays that still owe or are owed money. The selected queue
        date governs the outpatient lanes only.
        """
        self._cashier_assert_authorized()
        lanes = tuple(lanes or CASHIER_INPATIENT_DEFAULT_LANES)
        Admission = self.sudo().with_context(active_test=False)
        base = [("encounter_id", "!=", False)]
        if department_id:
            base.append(("ward_id.department_id", "=", department_id))
        if search:
            base += [
                "|", "|", "|",
                ("patient_id.name", "ilike", search),
                ("patient_id.identification_code", "ilike", search),
                ("encounter_id.name", "ilike", search),
                ("name", "ilike", search),
            ]
        active = Admission.search(
            base + [("state", "in", list(ADMISSION_ACTIVE_STATES))],
            order="id desc", limit=ACTIVE_SCAN_MAX + 1,
        )
        discharged = Admission.search(
            base + [("state", "=", "discharged")],
            order="discharge_date desc, id desc", limit=DISCHARGED_SCAN_MAX + 1,
        )
        truncated = len(active) > ACTIVE_SCAN_MAX or len(discharged) > DISCHARGED_SCAN_MAX
        candidates = active[:ACTIVE_SCAN_MAX] | discharged[:DISCHARGED_SCAN_MAX]
        visible = candidates.with_env(self.env)._cashier_visible()

        rows = []
        for admission in visible:
            facts = admission._cashier_facts(now)
            # A discharged stay is here only for money still moving. Its
            # review (if any) belongs to Billing, not to the window.
            if admission.state == "discharged" and facts["lane"] not in (
                "due", "part_paid", "refund_due",
            ):
                continue
            if facts["lane"] in lanes:
                rows.append(facts)
        return rows, truncated

    def _cashier_detail(self, now=None):
        """One admission's facts plus the settlement receipts."""
        self.ensure_one()
        return self._cashier_facts(now)

    # ------------------------------------------------------------------
    # THE settlement payment
    # ------------------------------------------------------------------
    @api.model
    def _cashier_clean_key(self, idempotency_key):
        if not isinstance(idempotency_key, str) or not idempotency_key.strip():
            raise CashierSettlementError("inpatient_idempotency_key_required")
        key = idempotency_key.strip()
        if len(key) > SETTLEMENT_KEY_MAX_LENGTH:
            raise CashierSettlementError("inpatient_idempotency_key_required")
        return key

    def _cashier_record_settlement(
        self, amount, payment_method, payment_reference=None, note=None, idempotency_key=None,
    ):
        """Take `amount` of the remaining delivered-basis balance. Returns
        (receipt, replayed).

          1. authority   the operational intake groups (and hospital_billing
                         checks them again, as the caller, at the money)
          2. replay      the same key on this admission returns the same
                         receipt; a reused key with different terms is refused
          3. lock        the admission row, so two cashiers cannot both
                         collect the same balance
          4. post stay   every bed-day started by now -- the same idempotent
                         act the discharge performs -- so the balance being
                         settled is one the charge engine can hold
          5. summary     THE authority; needs_review / nothing due / more than
                         due are refused with fixed codes
          6. pay         record_operational_payment as the caller, delivered
                         charges first, within their existing ceilings

        Any refusal raises before a receipt exists; the caller's savepoint
        rolls back the stay posting with it.
        """
        self.ensure_one()
        self._cashier_assert_authorized()
        # THE MONEY MOVES AS THE REAL USER, whatever env this record carries:
        # hospital_billing's intake check and ACLs must see the cashier.
        caller = self.env(su=False)
        if (
            isinstance(amount, bool)
            or not isinstance(amount, (int, float))
            or amount != amount  # NaN
            or amount <= AMOUNT_TOLERANCE
        ):
            raise CashierSettlementError("inpatient_invalid_amount")
        amount = float(amount)
        key = self._cashier_clean_key(idempotency_key)
        admission = self.sudo()
        token = (SETTLEMENT_TOKEN_PREFIX % admission.id) + key

        existing = self.env["hospital.charge.receipt"].sudo().search(
            [("intake_token", "=", token)], limit=1
        )
        if existing:
            # The canonical launcher compares amount, method, reference and
            # note, and returns the same receipt or refuses the reused key.
            account = existing.billing_account_id.with_env(caller)
            return account.record_operational_payment(
                amount, payment_method, payment_reference=payment_reference,
                note=note, intake_token=token,
            ), True

        if admission.state not in STAY_STATES or not admission.encounter_id:
            raise CashierSettlementError("inpatient_not_found")

        admission._lock_for_occupancy(self.env["hospital.bed"])
        now = fields.Datetime.now()
        if admission.state in ADMISSION_ACTIVE_STATES:
            try:
                admission._sync_stay_charges(now)
            except AdmissionWorkflowError:
                raise CashierSettlementError("inpatient_financial_review_required") from None
        self.env.flush_all()
        self.env.invalidate_all()

        summary = admission._inpatient_financial_summary(now)
        if summary["financial_state"] == "needs_review":
            raise CashierSettlementError("inpatient_financial_review_required")
        remaining = summary["remaining_due"]
        if remaining <= AMOUNT_TOLERANCE:
            raise CashierSettlementError("inpatient_nothing_due")
        if amount - remaining > AMOUNT_TOLERANCE:
            raise CashierSettlementError("inpatient_payment_exceeds_due")

        account = admission._billing_account()
        if not account:
            raise CashierSettlementError("inpatient_settlement_not_allocatable")

        # Capacity under the SAME per-line patient ceilings the wizard applies.
        # A settlement never becomes an overpayment or an advance.
        Wizard = self.env["hospital.charge.payment.wizard"]
        payable = Wizard._payable_charges(account.charge_line_ids).sorted("id")
        capacity = sum(Wizard._charge_available(charge) for charge in payable)
        if amount - capacity > AMOUNT_TOLERANCE:
            raise CashierSettlementError("inpatient_settlement_not_allocatable")

        # DELIVERED CHARGES FIRST. Settlement cash pays for care already given;
        # letting it land on an ordered-but-undelivered line would silently
        # clear that order for service at the cashier's hand.
        delivered_first = [
            charge.id for charge in payable if charge.amount_eligible > AMOUNT_TOLERANCE
        ]
        receipt = account.with_env(caller).record_operational_payment(
            amount,
            payment_method,
            payment_reference=payment_reference,
            note=note,
            intake_token=token,
            allocation_priority=delivered_first,
        )
        return receipt, False
