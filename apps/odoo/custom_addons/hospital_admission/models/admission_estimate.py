"""The inpatient ESTIMATE and the advance DEPOSIT charge it opens.

THE ESTIMATE
------------
Before or during the stay the admission's physician states what the stay is
expected to cost. It is an ESTIMATE: it is not the bill, it is not revenue, and
it never enters the delivered-basis settlement as care. Only the physician (or
oversight) gives or revises it, through _desk_set_estimate(), which records who,
when, why and a revision number, and audits every change.

A RUNNING FORECAST. While the patient is in care (requested, admitted,
transferred) the physician may revise it as often as the plan changes; each
revision resizes the deposit charge, so the Cashier collects only the
difference upward and nothing is refunded downward -- an excess is patient
credit while care goes on. Every revision is kept, complete and immutable, as
a hospital.admission.estimate.revision row; the admission's own fields carry
only the current one.

THE LOCK. Once the doctor's Request discharge has recorded medical readiness
the estimate is FROZEN (_estimate_lock()): final settlement on actual
delivered care is the authority from then on, and the estimate is planning
history. Opening the discharge review is not that point; only the recorded
readiness is.

THE DEPOSIT CHARGE -- WHY A CHARGE LINE
---------------------------------------
Patient money enters this system in exactly one way: a receipt ALLOCATED TO A
CHARGE LINE (hospital_billing). There is no account-level deposit, and a stay
that has just begun has nothing delivered to allocate to. So the estimate opens
ONE server-managed charge per admission on the visit's billing account:

    source   hospital.admission / inpatient_deposit
    service  hospital_admission.billing_service_inpatient_deposit
             (delivery basis: it never blocks clearance for any service)
    price    1.00 per unit, quantity = the estimate

It is NEVER delivered, so it is never care, never invoiced and never revenue.
The Cashier's advance is an ordinary receipt on it, through the ordinary intake,
capped by the ordinary per-line ceiling -- which is exactly the estimate less
what has been paid on it. Its held advance (received - applied - refunded) is
what the accounting slice applies to invoices or refunds, per line, as it does
for any other held advance.

WHY QUANTITY AND NOT PRICE. The engine freezes a charge's commercial snapshot
(unit price, service, basis, tax) on re-emit; repricing is an accountant's act.
A revised estimate is not a repricing, so it moves the quantity, which the
engine's idempotent re-emit updates as it would a changed order quantity.
"""
import math

from odoo import api, fields, models

from .admission_authority import (
    ADMISSION_ESTIMATE_READ,
    ADMISSION_ESTIMATE_STATES,
    AdmissionDeskError,
    AdmissionWorkflowError,
    changed_fields,
    estimate_capability,
    has_estimate_capability,
)

DEPOSIT_EVENT = "inpatient_deposit"

ADMISSION_CLEARANCE_MESSAGES = {
    "emergency_bypass": "Emergency bypass authorized: admission may proceed before payment.",
    "sponsored": "Sponsored visit: no patient advance is required before admission.",
    "awaiting_estimate": "Awaiting the doctor's inpatient estimate.",
    "awaiting_advance": "Advance required at the cashier before admission.",
    "cleared": "Financially cleared for admission.",
}
DEPOSIT_SERVICE_XMLID = "hospital_admission.billing_service_inpatient_deposit"
ESTIMATE_MAX = 1_000_000_000.0


class HospitalAdmissionEstimate(models.Model):
    _inherit = "hospital.admission"

    estimated_amount = fields.Float(
        string="Inpatient Estimate",
        digits=(16, 2),
        readonly=True,
        copy=False,
        tracking=True,
        groups=ADMISSION_ESTIMATE_READ,
        help="The physician's estimate of what the stay will cost. Not a bill.",
    )
    estimate_reason = fields.Text(
        readonly=True, copy=False, groups=ADMISSION_ESTIMATE_READ,
    )
    estimated_by_id = fields.Many2one(
        "res.users", string="Estimated By", readonly=True, copy=False,
        groups=ADMISSION_ESTIMATE_READ,
    )
    estimated_at = fields.Datetime(
        readonly=True, copy=False, groups=ADMISSION_ESTIMATE_READ,
    )
    estimate_revision = fields.Integer(
        readonly=True, copy=False, default=0, groups=ADMISSION_ESTIMATE_READ,
    )
    estimate_revision_ids = fields.One2many(
        "hospital.admission.estimate.revision", "admission_id",
        string="Estimate Revisions", readonly=True, copy=False,
        groups=ADMISSION_ESTIMATE_READ,
    )

    # ------------------------------------------------------------------
    # Authority
    # ------------------------------------------------------------------
    def _desk_may_set_estimate(self):
        """The admission's own physician, or oversight -- the same people who
        declare medical readiness. Never the cashier, clerk or nurse."""
        self.ensure_one()
        return self._desk_may_request_medical_discharge()

    def _estimate_lock(self):
        """Why the estimate may no longer be given or revised, or None.

          medically_ready   the doctor's Request discharge succeeded: final
                            settlement on actual delivered care now decides,
                            and the Cashier takes no further advance.

        Collected advance does NOT lock it: the estimate is a running forecast
        and the Cashier collects against its current figure. A discharged or
        cancelled admission is outside ADMISSION_ESTIMATE_STATES and refused
        as an invalid state before this is consulted.
        """
        self.ensure_one()
        if self.sudo().medical_discharge_ready:
            return "medically_ready"
        return None

    @api.model
    def _desk_clean_estimate_amount(self, value):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise AdmissionDeskError("admission_invalid_payload")
        value = float(value)
        if math.isnan(value) or math.isinf(value) or value <= 0 or value > ESTIMATE_MAX:
            raise AdmissionDeskError("admission_invalid_payload")
        currency = self.env.company.currency_id
        return currency.round(value)

    # ------------------------------------------------------------------
    # The deposit charge
    # ------------------------------------------------------------------
    def _deposit_charge(self):
        """This admission's deposit charge (any state), or an empty recordset."""
        self.ensure_one()
        return self.env["hospital.charge.line"].sudo().with_context(active_test=False).search(
            [
                ("source_model", "=", self.STAY_SOURCE_MODEL),
                ("source_res_id", "=", self.id),
                ("source_event", "=", DEPOSIT_EVENT),
            ],
            limit=1,
        )

    def _sync_deposit_charge(self):
        """Open or resize the deposit charge to the current estimate.

        Idempotent. Does nothing without an estimate, without a visit, or once
        the visit can take no charges. sudo() through the engine, as every
        clinical module calls it: the engine is the authority on charges."""
        self.ensure_one()
        admission = self.sudo()
        Charge = self.env["hospital.charge.line"]
        amount = admission.estimated_amount or 0.0
        encounter = admission.encounter_id
        if amount <= 0 or not encounter or encounter.state in ("closed", "cancelled"):
            return Charge.browse()
        service = self.env.ref(DEPOSIT_SERVICE_XMLID, raise_if_not_found=False)
        engine = self.env["hospital.billing.engine"].sudo()
        charge = engine.create_or_update_charge(
            encounter,
            self.STAY_SOURCE_MODEL,
            admission.id,
            DEPOSIT_EVENT,
            "Inpatient advance deposit - %s" % admission.name,
            source_line_id=0,
            service=service or None,
            qty_requested=amount,
            unit_price=1.0,
        )
        if charge.charge_state == "draft":
            engine.activate_charge(charge)
        return charge

    # ------------------------------------------------------------------
    # The Doctor Desk act
    # ------------------------------------------------------------------
    def _desk_set_estimate(self, amount, reason, operation_token, expected_revision):
        """Give or revise the inpatient estimate. Returns (admission, replayed).

        Moves no money and recognises nothing: it records the figure, who gave
        it and why, and sizes the deposit charge the Cashier collects against.
        """
        self.ensure_one()
        if not self._desk_may_set_estimate():
            raise AdmissionDeskError("admission_not_authorized")
        token = self._desk_clean_token(operation_token)
        revision = self._desk_clean_revision(expected_revision)
        amount = self._desk_clean_estimate_amount(amount)
        reason = self._desk_clean_reason(reason)

        try:
            self._lock_for_occupancy(self.env["hospital.bed"])
        except AdmissionWorkflowError:
            raise AdmissionDeskError("admission_not_found") from None

        digest = self._desk_digest(
            "estimate",
            {"admission": self.id, "expected_revision": revision, "amount": amount, "reason": reason},
        )
        if self._desk_find_replay("estimate", token, digest):
            return self, True

        if self.workflow_revision != revision:
            raise AdmissionDeskError("admission_revision_conflict")
        if self.state not in ADMISSION_ESTIMATE_STATES:
            raise AdmissionDeskError("admission_invalid_state")
        if self._estimate_lock():
            raise AdmissionDeskError("admission_estimate_locked")

        admission = self.sudo()
        previous = admission.estimated_amount
        now = fields.Datetime.now()
        with estimate_capability():
            admission.write({
                "estimated_amount": amount,
                "estimate_reason": reason,
                "estimated_by_id": self.env.uid,
                "estimated_at": now,
                "estimate_revision": admission.estimate_revision + 1,
            })
            # The complete, immutable record of THIS revision.
            self.env["hospital.admission.estimate.revision"].sudo().create({
                "admission_id": admission.id,
                "revision": admission.estimate_revision,
                "amount": amount,
                "reason": reason,
                "estimated_by_id": self.env.uid,
                "estimated_at": now,
            })
        self._sync_deposit_charge()
        self._audit(
            patient_id=self.patient_id.id,
            model_name=self._name,
            record_id=self.id,
            action_type="update",
            old_value=str(previous or 0.0),
            new_value=str(amount),
            description="Inpatient estimate for %s set to %.2f (revision %s)."
            % (self.name, amount, admission.estimate_revision),
        )
        # Recorded for replay, WITHOUT bumping the workflow revision: an estimate
        # moves no bed and no state, so it must not turn a clerk's pending
        # transfer or discharge (or the doctor's own discharge request) stale.
        self.env.flush_all()
        self._desk_record_operation("estimate", token, digest)
        self.env.flush_all()
        return self, False

    # ------------------------------------------------------------------
    # Financial clearance for admission (pre-admission gate)
    # ------------------------------------------------------------------
    def _admission_financial_clearance(self):
        """May this requested admission be given a bed, financially? A dict:

          state     emergency_bypass / sponsored / awaiting_estimate /
                    awaiting_advance / cleared
          cleared   bool -- the gate _confirm_one() and the desk apply
          message   fixed, amount-free sentence (every desk role may see it)
          estimate, advance_received, remaining   amounts, for the money and
                    discharging roles' serializers ONLY

        THE RULE, and only this rule (no partial-deposit percentage):
          * an EMERGENCY BYPASS on the visit -- the existing, audited route by
            which an Emergency Authorizer, Manager or Administrator lets care
            precede payment -- clears it;
          * a SPONSORED visit (payer_type other than self-pay) is outside this
            gate: its payer's clearance authority governs, not a patient
            advance;
          * a self-pay visit needs the doctor's estimate AND an advance equal
            to it, held on the admission's deposit charge.
        """
        self.ensure_one()
        admission = self.sudo()
        currency = admission.company_id.currency_id or self.env.company.currency_id
        encounter = admission.encounter_id
        estimate = currency.round(admission.estimated_amount or 0.0)
        received = currency.round(sum(
            line.amount_received - line.amount_refunded for line in admission._deposit_charge()
        ))
        remaining = currency.round(max(0.0, estimate - received))
        facts = {"estimate": estimate, "advance_received": received, "remaining": remaining}
        if encounter and encounter.emergency_bypass:
            state = "emergency_bypass"
        elif encounter and (encounter.payer_type or "self_pay") != "self_pay":
            state = "sponsored"
        elif estimate <= 0.005:
            state = "awaiting_estimate"
        elif remaining > 0.005:
            state = "awaiting_advance"
        else:
            state = "cleared"
        return dict(
            facts,
            state=state,
            cleared=state in ("emergency_bypass", "sponsored", "cleared"),
            message=ADMISSION_CLEARANCE_MESSAGES[state],
        )

    def _estimate_facts(self):
        """The estimate as the Doctor Desk shows it. A plain dict."""
        self.ensure_one()
        admission = self.sudo()
        return {
            "amount": admission.estimated_amount or 0.0,
            "reason": admission.estimate_reason or None,
            "estimated_by": admission.estimated_by_id.name or None,
            "estimated_at": admission.estimated_at,
            "revision": admission.estimate_revision,
            "locked_reason": self._estimate_lock(),
            "history": [
                {
                    "revision": row.revision,
                    "amount": row.amount,
                    "reason": row.reason or None,
                    "estimated_by": row.estimated_by_id.name or None,
                    "estimated_at": row.estimated_at,
                    "baseline": row.is_baseline,
                }
                for row in admission.estimate_revision_ids
            ],
        }

    def _estimate_additional_advance_required(self):
        """True while, in a bed, the current estimate asks more advance than
        has been collected. A FLAG for the Doctor Desk, never a figure: the
        Cashier owns estimate / received / remaining. A pending request's
        shortfall is the ordinary pre-admission advance, not an additional one,
        and the admission gate already holds it."""
        self.ensure_one()
        admission = self.sudo()
        if admission.state not in ("admitted", "transferred") or not admission._cashier_in_care():
            return False
        if (admission.estimated_amount or 0.0) <= 0.005:
            return False
        return admission._cashier_advance_outstanding() > 0.005


class HospitalAdmissionEstimateRevision(models.Model):
    """One estimate revision, exactly as given. APPEND-ONLY.

    Created only by _desk_set_estimate() (under estimate_capability()); never
    edited, never deleted -- an estimate the Cashier collected against is
    evidence of what the patient was told. A `is_baseline` row was written by
    the 18.0.1.12.0 migration from the estimate stored on the admission when
    history began. Earlier revisions were never stored with their reasons and
    are NOT reconstructed; the audit log keeps what it kept.
    """

    _name = "hospital.admission.estimate.revision"
    _description = "Inpatient Estimate Revision"
    _order = "admission_id, revision"

    admission_id = fields.Many2one(
        "hospital.admission", required=True, ondelete="cascade", index=True, readonly=True,
    )
    revision = fields.Integer(required=True, readonly=True)
    amount = fields.Float(digits=(16, 2), required=True, readonly=True)
    reason = fields.Text(readonly=True)
    estimated_by_id = fields.Many2one("res.users", string="Estimated By", readonly=True)
    estimated_at = fields.Datetime(readonly=True)
    is_baseline = fields.Boolean(
        string="Migration Baseline", readonly=True,
        help="Copied from the admission when revision history began; not a new revision.",
    )

    _sql_constraints = [
        (
            "admission_revision_unique",
            "unique(admission_id, revision)",
            "An estimate revision number is used once per admission.",
        ),
    ]

    @api.model_create_multi
    def create(self, vals_list):
        if not has_estimate_capability():
            raise AdmissionWorkflowError("admission_estimate_history_refused")
        for vals in vals_list:
            # Only the migration writes baselines, and it does so in SQL.
            vals["is_baseline"] = False
        return super().create(vals_list)

    def write(self, vals):
        names = [name for name in vals if name in self._fields]
        for rec in self.sudo():
            if changed_fields(rec, names, vals):
                raise AdmissionWorkflowError("admission_estimate_history_refused")
        return super().write(vals)

    def unlink(self):
        """History is not deleted. A deleted draft admission takes its rows
        with it through the database cascade, which does not pass through here."""
        raise AdmissionWorkflowError("admission_estimate_history_refused")
