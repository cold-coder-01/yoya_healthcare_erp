"""The visit's PAYER IDENTITY -- who is responsible, never how much.

Phase 2B records WHICH registered eligibility a visit is being presented under.
It records nothing about money. There is no responsibility split here, no limit
consumption, no tariff, no authorization and no clearance decision.

WHY A NEW FIELD AND NOT encounter.payer_type / payer_id
-------------------------------------------------------
This is the single load-bearing decision of the phase.

hospital.billing.engine.check_financial_clearance short-circuits on payer_type:

    if enc.payer_type != "self_pay":
        ...
        return {"cleared": True, "state": "credit_authorized", "amount_due": 0.0}

So expressing "this visit has a sponsor" by writing payer_type would clear every
cash gate on the encounter as a side effect of recording an identity. The stage
would jump awaiting_cashier -> ready_doctor and action_start_consultation's
clearance check would pass, with no money taken and no authorization recorded.

patient_payer_id is therefore a SEPARATE field that nothing financial reads.
Selecting an eligibility leaves payer_type at 'self_pay' and leaves clearance
exactly as it was. When the responsibility engine is built it will consume this
field explicitly, behind res_company.payer_responsibility_mode, which remains
'off' and is still read by nothing.

WHY THE WRITE GUARD IS HERE AND NOT IN THE API
----------------------------------------------
The Front Desk Nurse holds perm_write on hospital.encounter plus an
unrestricted record rule (yoya_reception_bridge). A guard living in an HTTP
controller would be bypassed by one raw call to
/web/dataset/call_kw hospital.encounter write. The boundary has to be the model,
and it is.

Two guarded field groups, with different rules:

  LEGACY_PAYER_FIELDS (payer_type, payer_id)
      Restricted to PAYER_IDENTITY_AUTHORITY. Nothing in the codebase writes
      these fields on any path -- verified -- so restricting them breaks no
      workflow and closes the clearance bypass described above.

  patient_payer_id
      Writable ONLY inside payer_identity_capability(), i.e. only from
      hospital.reception.workflow.set_visit_payer() and create_visit(). Those
      methods own the role check and the freeze; this guard's only job is to
      guarantee they cannot be routed around.

The capability is a contextvars token, not a context key, for exactly the reason
yoya_reception_bridge.reception_capability documents: self.env.context is
attacker-controlled on every RPC call, and a context flag would be forgeable by
sending the same key.
"""

import contextvars
from contextlib import contextmanager

from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError

from .charge_line import G_ADMIN, G_MANAGER
from .payer_agreement import G_INSURANCE_OFFICER

# The established eligibility-authority tuple, restated from
# patient_payer.ELIGIBILITY_OPERATORS rather than imported, because patient_payer
# imports from payer_agreement and this module must not add a third edge to that
# graph. The two are asserted equal in the tests, so they cannot drift.
PAYER_IDENTITY_AUTHORITY = (G_INSURANCE_OFFICER, G_MANAGER, G_ADMIN)

# Encounter states in which the visit has not yet reached the doctor. Once
# action_start_consultation runs, hospital_billing's override calls
# encounter.action_start(), which moves the encounter to 'active' -- so leaving
# this set IS "the consultation has started", derived from existing behaviour
# rather than from a new flag.
PRE_CONSULTATION_STATES = ("planned", "checked_in")

LEGACY_PAYER_FIELDS = frozenset({"payer_type", "payer_id"})

_payer_identity_capability = contextvars.ContextVar(
    "hospital_billing_payer_identity_capability", default=False
)


@contextmanager
def payer_identity_capability():
    """Grant the right to write patient_payer_id for this block only."""
    token = _payer_identity_capability.set(True)
    try:
        yield
    finally:
        _payer_identity_capability.reset(token)


def has_payer_identity_capability():
    return _payer_identity_capability.get()


class HospitalEncounterPayerIdentity(models.Model):
    """Adds the encounter -> patient eligibility link.

    Declared in hospital_billing, not hospital_management, because
    hospital.patient.payer lives here and hospital_management is upstream --
    the same dependency-direction argument payer.py already documents.
    """

    _inherit = "hospital.encounter"

    patient_payer_id = fields.Many2one(
        "hospital.patient.payer",
        string="Patient Eligibility",
        ondelete="restrict",
        index=True,
        copy=False,
        help="The registered payer eligibility this visit is presented under. "
        "Identity only: it records WHO is responsible, never how much. It does "
        "not affect financial clearance and does not change the payer type.",
    )

    # ------------------------------------------------------------------
    # Selectability -- reuses the authoritative Phase 2A logic
    # ------------------------------------------------------------------
    @api.model
    def _assert_eligibility_selectable(self, eligibility, patient, company):
        """The ONE selectability rule, used by the constraint and the lookup.

        is_valid_today is the authoritative Phase 2A predicate: it already
        composes the eligibility's own state and window WITH its agreement's
        state and window (patient_payer._compute_is_valid_today). It is not
        restated here, and it is not stored -- it depends on today.
        """
        if not eligibility:
            return
        record = eligibility.sudo()
        if record.patient_id != patient:
            raise ValidationError(
                "Eligibility %s belongs to %s, not %s."
                % (
                    record.display_name,
                    record.patient_id.display_name,
                    patient.display_name,
                )
            )
        if record.company_id != company:
            raise ValidationError(
                "Eligibility %s belongs to company %s, not %s."
                % (
                    record.display_name,
                    record.company_id.display_name,
                    company.display_name,
                )
            )
        if not record.is_valid_today:
            raise ValidationError(
                "Eligibility %s is not selectable today (state '%s', valid %s to "
                "%s under agreement %s in state '%s')."
                % (
                    record.display_name,
                    record.state,
                    record.effective_from,
                    record.effective_to or "open-ended",
                    record.agreement_id.display_name,
                    record.agreement_id.state,
                )
            )

    @api.constrains("patient_payer_id", "patient_id", "company_id")
    def _check_patient_payer_identity(self):
        """Correctness, not authorization -- so it runs for sudo() too.

        Read through sudo() inside the helper: a doctor may write their own
        encounter but holds no ACL on hospital.patient.payer, and a constraint
        must not turn into an AccessError for an unrelated write.
        """
        for encounter in self:
            if not encounter.patient_payer_id:
                continue
            self._assert_eligibility_selectable(
                encounter.patient_payer_id,
                encounter.patient_id,
                encounter.company_id,
            )

    # ------------------------------------------------------------------
    # Payer-change freeze
    # ------------------------------------------------------------------
    def _payer_identity_freeze_reason(self):
        """Why the front desk may no longer change this visit's payer, or None.

        THE AUTHORITATIVE 'money was taken' RELATION is a CONFIRMED
        hospital.charge.receipt whose encounter_id is this encounter. That field
        is a stored compute derived from the receipt's allocations through
        charge_line -> billing_account -> encounter (charge_receipt._compute_context),
        and 'confirmed' is the same state that feeds charge.amount_received.

        Deliberately NOT a check on any amount_* figure: those are derived
        totals, they can be zero on a legitimately allocated receipt, and reading
        them requires OPERATIONAL_MONEY_READ. The receipt IS the payment event.
        """
        self.ensure_one()
        if self.state not in PRE_CONSULTATION_STATES:
            return (
                "encounter %s is %s; the consultation has already started."
                % (self.name, self.state)
            )
        # sudo: the guard must be able to see a receipt the caller may not read.
        # It only ever restricts, never grants.
        receipt = self.env["hospital.charge.receipt"].sudo().search(
            [("encounter_id", "=", self.id), ("state", "=", "confirmed")],
            limit=1,
        )
        if receipt:
            return (
                "payment %s has already been received against encounter %s."
                % (receipt.name, self.name)
            )
        return None

    def _assert_payer_identity_changeable(self):
        """Raise unless this encounter is still before the freeze threshold."""
        for encounter in self:
            reason = encounter._payer_identity_freeze_reason()
            if reason:
                raise UserError(
                    "The payer identity can no longer be changed at the front "
                    "desk: %s\n\nA correction now requires an Insurance/Credit "
                    "Officer, Hospital Manager or Hospital System Administrator."
                    % reason
                )

    @api.model
    def _has_payer_identity_authority(self):
        user = self.env.user
        return any(user.has_group(group) for group in PAYER_IDENTITY_AUTHORITY)

    # ------------------------------------------------------------------
    # Write guard
    # ------------------------------------------------------------------
    @api.model
    def _assert_payer_fields_writable(self, vals):
        """The model-level boundary. Never bypassable from an API or a view."""
        if self.env.su:
            return

        legacy = LEGACY_PAYER_FIELDS.intersection(vals)
        if legacy and not self._has_payer_identity_authority():
            raise AccessError(
                "You are not allowed to change %s on an encounter. These fields "
                "decide whether the visit is billed to a third party at all, and "
                "hospital.billing.engine.check_financial_clearance treats any "
                "non-self-pay value as credit-authorized -- so writing them "
                "waives the cash gate. Required: Insurance/Credit Officer, "
                "Hospital Manager or Hospital System Administrator."
                % ", ".join(sorted(legacy))
            )

        if "patient_payer_id" in vals and not has_payer_identity_capability():
            raise AccessError(
                "The visit payer identity cannot be written directly. Use "
                "hospital.reception.workflow.set_visit_payer(), which checks the "
                "operational role, validates the eligibility against the patient "
                "and company, and enforces the payer-change freeze."
            )

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            self._assert_payer_fields_writable(vals)
        return super().create(vals_list)

    def write(self, vals):
        self._assert_payer_fields_writable(vals)
        return super().write(vals)

    # NOTE: patient_payer_id is deliberately ABSENT from
    # _get_locked_writable_fields(). A closed or cancelled encounter therefore
    # keeps the parent's lock, and its payer identity is already frozen by it --
    # no second mechanism is added for a case the existing one covers.
