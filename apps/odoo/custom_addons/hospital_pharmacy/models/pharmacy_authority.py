"""Model authority for prescriptions and pharmacy dispenses (Pharmacy Slice 0).

WHAT THIS FILE IS
-----------------
The ONE place that says which facts on a prescription and a dispense are
clinical or workflow facts -- and therefore cannot be forged by an ordinary
write -- and the ONE mechanism by which the workflow methods that own those
facts are allowed to move them.

Before this slice any user holding write access, and any sudo() code, could:

  * write {"state": "dispensed"} on a dispense and skip Mark Ready, financial
    clearance, charge delivery and stock consumption in one call;
  * relink a dispense to another prescription or another patient;
  * rewrite the quantity, medicine or dosage of a CONFIRMED prescription after
    the pharmacy had started filling it.

WHY A CONTEXTVAR AND NOT A CONTEXT FLAG
---------------------------------------
self.env.context is attacker-controlled on every RPC call: execute_kw and
/web/dataset/call_kw accept an arbitrary context, so a check such as
context.get("allow_state_write") is defeated by sending that key. A ContextVar
is server-side process state that no RPC payload can set; the only way to raise
it is to execute one of the context managers below in Python. This is the
design hospital_radiology's request/result state authority, yoya_reception_
bridge's reception capability and hospital_billing's payer capability already
use, applied to the third ancillary service.

sudo() IS NOT A CAPABILITY. Every guard below ignores env.su on purpose: sudo()
answers "may this code bypass ACLs and record rules", which is a different
question from "is this the workflow method that owns this fact". The prescription
composition runs under sudo() for a doctor; it must still not be able to
fabricate a dispensed state.

THE WINDOWS ARE NARROW. Each capability is raised around the workflow step that
owns the fact and always released in a `finally`, so an exception can never
leave it open for a later call in the same worker.
"""
import contextvars
from contextlib import contextmanager

# ---------------------------------------------------------------------------
# Capabilities
# ---------------------------------------------------------------------------
_prescription_workflow_var = contextvars.ContextVar(
    "hospital_pharmacy_prescription_workflow_capability", default=False
)
_dispense_workflow_var = contextvars.ContextVar(
    "hospital_pharmacy_dispense_workflow_capability", default=False
)
_dispense_composition_var = contextvars.ContextVar(
    "hospital_pharmacy_dispense_composition_capability", default=False
)


@contextmanager
def _raised(var):
    token = var.set(True)
    try:
        yield
    finally:
        var.reset(token)


def prescription_workflow_capability():
    """Raised ONLY by the prescription's own transition methods (confirm, mark
    dispensed, cancel, reset). Opens exactly one thing: a change of
    hospital.prescription.state. It never opens the locked clinical fields."""
    return _raised(_prescription_workflow_var)


def has_prescription_workflow_capability():
    return _prescription_workflow_var.get()


def dispense_workflow_capability():
    """Raised ONLY by HospitalPharmacyDispense._write_state(). Opens a change of
    hospital.pharmacy.dispense.state and nothing else."""
    return _raised(_dispense_workflow_var)


def has_dispense_workflow_capability():
    return _dispense_workflow_var.get()


def dispense_composition_capability():
    """Raised ONLY by hospital.prescription._get_or_create_pharmacy_dispense()
    around the one create that composes a dispense from a prescription. Opens
    the creation of lines on a prescription-linked dispense -- the lines are
    derived from the prescription, never from a client payload."""
    return _raised(_dispense_composition_var)


def has_dispense_composition_capability():
    return _dispense_composition_var.get()


# ---------------------------------------------------------------------------
# What is protected
# ---------------------------------------------------------------------------
# The prescription's clinical intent. Frozen once the prescription leaves draft,
# because from confirmation onward a dispense has been composed FROM these values
# and a pharmacist may already be filling it. `consultation_id` is contributed by
# yoya_clinical_bridge; listing it here is harmless when that module is absent
# because only keys present in a write are ever examined.
PRESCRIPTION_LOCKED_FIELDS = (
    "patient_id",
    "physician_id",
    "appointment_id",
    "diagnosis_id",
    "consultation_id",
    "line_ids",
)

# One prescribed medicine, as the prescriber wrote it.
PRESCRIPTION_LINE_LOCKED_FIELDS = (
    "prescription_id",
    "medicine_id",
    "medicine_name",
    "dosage",
    "route",
    "frequency",
    "duration",
    "quantity",
    "instructions",
    "sequence",
)

# Who and what a dispense is FOR. Set once, at creation, and never relinked.
DISPENSE_IDENTITY_FIELDS = (
    "prescription_id",
    "patient_id",
    "physician_id",
    "appointment_id",
    "pharmacist_id",
)

# What a dispense line IS: which dispense, which medicine, how much was
# prescribed. Frozen on a prescription-linked dispense from creation, and on any
# dispense once it has left draft.
DISPENSE_LINE_IDENTITY_FIELDS = (
    "dispense_id",
    "medicine_id",
    "prescribed_quantity",
)

# The prescription is editable while it is a draft and in no other state.
PRESCRIPTION_EDITABLE_STATES = ("draft",)

# The pharmacist's cumulative intended quantity may move only while there is
# still something to prepare. `ready` is included deliberately: hospital_billing
# re-synchronises the medication charges to the intended quantity at every
# payment preparation and again inside Validate Dispense, so an edit there is
# re-billed before anything is delivered.
DISPENSE_INTENT_EDITABLE_STATES = ("draft", "ready", "partial")

# Quantity tolerance, the same 3-decimal precision the billing and inventory
# high-water marks are stored at.
QTY_PRECISION_DIGITS = 3


def m2o_id(value):
    """The id a many2one write value designates: int, record, or False."""
    if hasattr(value, "id"):
        return value.id or False
    return value or False


def same_value(record, field_name, value):
    """Whether writing `value` to `field_name` would leave `record` unchanged.

    Writing the value a record already has is not a change, so a form or import
    that echoes the current value does not break.
    """
    field = record._fields[field_name]
    current = record[field_name]
    if field.type == "many2one":
        return (current.id or False) == m2o_id(value)
    if field.type in ("one2many", "many2many"):
        # Any command against a locked x2many is a change.
        return False
    if field.type == "float":
        return round(current or 0.0, 6) == round(value or 0.0, 6)
    if field.type in ("char", "text", "selection"):
        return (current or False) == (value or False)
    return current == value
