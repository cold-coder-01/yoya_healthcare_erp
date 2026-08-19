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

from .api_response import datetime_value

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


def serialize_consultation_envelope(consultation, available, reason=None):
    """THE consultation response shape, for both GET and save.

    'available' is what lets the desk distinguish "this visit has no
    consultation because it has not started" from "this visit has a
    consultation and it happens to be empty". Without it the client would have
    to infer the difference from a null, and would get it wrong for a
    consultation whose narrative is genuinely blank.

    'reason' is an operator-facing sentence written here, never a model message
    interpolated from a record -- the same closed-set discipline
    doctor_serializers applies to clearance.
    """
    return {
        "available": bool(available),
        "reason": reason,
        "consultation": serialize_consultation(consultation),
    }
