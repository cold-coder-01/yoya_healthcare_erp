"""Doctor consultation payloads: the physician's own note, and nothing else.

WHY THIS IS A SEPARATE MODULE FROM doctor_serializers
-----------------------------------------------------
doctor_serializers.py carries a confidentiality argument that is only auditable
because it is short: it enumerates a closed set of clearance sentences and can be
read end to end to confirm no figure reaches a clinician. Growing it with a
second, unrelated payload would dilute exactly that property.

The split is also operational. The visit-detail payload is read on every queue
selection, including for visits that will never start; the consultation payload
is meaningful only once a visit is in_consultation. Keeping them apart keeps the
queue read cheap and keeps this module's own confidentiality claim narrow enough
to state in one sentence:

    NOTHING BELOW READS ANY MODEL EXCEPT hospital.consultation.

There is no traversal to hospital.encounter, hospital.billing.account,
hospital.charge.line, hospital.payer, hospital.payer.agreement or
hospital.patient.payer, and no code path that could produce an amount, a
balance, a receipt, an agreement, a membership number or a payer name. The
Doctor Desk already receives the clearance VERDICT it needs from
doctor_serializers.serialize_clearance; a consultation note has no financial
dimension at all, so this payload has no financial field to omit.
"""
from odoo.addons.yoya_clinical_bridge.models.consultation import NARRATIVE_FIELDS

from .api_response import datetime_value, float_value

# Re-exported so the controller and the tests name the narrative in one place.
# Imported from the MODEL rather than restated, so a field added there cannot be
# silently missing from the API contract.
CONSULTATION_NARRATIVE_FIELDS = NARRATIVE_FIELDS


def serialize_consultation(consultation):
    """One consultation, flat. Returns None for an empty recordset.

    'version' is hospital.consultation.version_token() -- the record's own
    write_date, serialized by the model. The client hands it back on save and
    the model compares it under a row lock, which is what closes the
    last-write-wins gap on free-text clinical narrative.

    Text fields are passed through UNMODIFIED, including their newlines. A
    clinician's paragraph breaks are clinical content; normalising or trimming
    them here would silently rewrite the record the doctor signed.
    """
    if not consultation:
        return None

    payload = {
        "id": consultation.id,
        "name": consultation.name,
        "state": consultation.state,
        "started_at": datetime_value(consultation.started_at),
        "completed_at": datetime_value(consultation.completed_at),
        "version": consultation.version_token(),
        # An affordance, resolved server-side from the authoritative state so
        # the editor does not have to re-derive it. The model refuses a write to
        # a completed consultation regardless of what this says.
        "editable": consultation.state == "draft",
    }
    for name in CONSULTATION_NARRATIVE_FIELDS:
        payload[name] = consultation[name] or None
    return payload


# ----------------------------------------------------------------------
# COMPLETION.
#
# THE CONFIDENTIALITY LINE THIS SECTION WALKS.
#
# The module header above claims this file reads nothing but
# hospital.consultation. Completion warnings break that claim deliberately and
# narrowly, and the exception is worth stating rather than burying: a doctor
# about to sign off needs to know the patient still owes money at the cashier,
# because the services they just ordered will not be delivered until it is
# paid. Withholding that turns the doctor into the last person who could have
# told the patient.
#
# So ONE figure crosses: encounter.reception_outstanding_amount, which is a
# compute_sudo field on hospital.encounter and is the same total the cashier
# collects. NOTHING ELSE. No charge id, no charge line, no billing account, no
# payer, no sponsor split, no receipt, no responsibility state, no accounting
# bucket. A doctor is told "550.00 is outstanding", never what it is for or who
# is expected to pay it -- which is the same discipline
# doctor_serializers.serialize_clearance already applies to the clearance
# verdict.
#
# Pending orders are counted, never described. "2 laboratory orders remain
# pending" is an operational fact; which tests, for what indication, is content
# the doctor already has on screen and the warning has no reason to restate.
WARNING_OUTSTANDING_BALANCE = "outstanding_balance"
WARNING_PENDING_ORDERS = "pending_orders"

# Laboratory request states that are still operationally open. Restated from
# hospital.laboratory.request rather than imported so this module keeps its
# dependency surface to the models it already names; the count is advisory and
# a drift here changes a sentence, never a gate.
PENDING_LABORATORY_STATES = ("requested", "sample_collected", "in_progress")


def completion_warnings(consultation):
    """Informational only. NOTHING here can block completion.

    Deliberately separate from completion_blockers(), which the MODEL owns and
    which is the only thing action_complete() enforces. Warnings are written
    for the confirmation panel, and mixing the two lists would eventually let a
    presentation concern grow into a gate.
    """
    if not consultation or consultation.state != "draft":
        return []

    warnings = []

    # compute_sudo on hospital.encounter, financial rather than clinical, and
    # already read by the cashier desk. One number, no structure.
    encounter = consultation.sudo().encounter_id
    outstanding = float_value(encounter.reception_outstanding_amount) if encounter else 0.0
    if outstanding > 0:
        warnings.append(
            {
                "code": WARNING_OUTSTANDING_BALANCE,
                "amount": outstanding,
                "message": "%.2f remains outstanding. The patient must settle "
                "at the cashier before pending services can be delivered."
                % outstanding,
            }
        )

    # sudo() on the COUNT: how many orders are open is a property of the visit,
    # and a doctor who may read this consultation may already read its orders.
    pending = consultation.sudo().laboratory_request_ids.filtered(
        lambda request: request.state in PENDING_LABORATORY_STATES
    )
    if pending:
        warnings.append(
            {
                "code": WARNING_PENDING_ORDERS,
                "count": len(pending),
                "message": "%s laboratory order%s remain%s pending. Results "
                "stay associated with this visit."
                % (
                    len(pending),
                    "" if len(pending) == 1 else "s",
                    "s" if len(pending) == 1 else "",
                ),
            }
        )

    return warnings


def serialize_consultation_envelope(consultation, available, reason=None):
    """THE consultation response shape, for GET, save AND complete.

    'available' is what lets the desk distinguish "this visit has no
    consultation because it has not started" from "this visit has a
    consultation and it happens to be empty". Without it the client would have
    to infer the difference from a null, and would get it wrong for a
    consultation whose narrative is genuinely blank.

    'reason' is an operator-facing sentence written here, never a model message
    interpolated from a record -- the same closed-set discipline
    doctor_serializers applies to clearance.

    can_complete / completion_blockers COME FROM THE MODEL, not from this
    layer. hospital.consultation.completion_blockers() is the single
    implementation that action_complete() also enforces, so the button the desk
    enables and the rule the server applies are the same rule. Deriving them
    here would be a second opinion, and the two would diverge on the first
    change to either.
    """
    blockers = consultation.completion_blockers() if consultation else []
    return {
        "available": bool(available),
        "reason": reason,
        "consultation": serialize_consultation(consultation),
        "can_complete": bool(consultation) and not blockers,
        "completion_blockers": blockers,
        "completion_warnings": completion_warnings(consultation),
    }
