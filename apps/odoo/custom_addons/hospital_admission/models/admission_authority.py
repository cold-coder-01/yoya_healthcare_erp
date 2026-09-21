"""Model authority for admissions, beds and inpatient occupancy (Admissions Slice 0).

WHAT THIS FILE IS
-----------------
The ONE place that says which facts on an admission and on a bed are workflow
facts -- and therefore cannot be forged by an ordinary write -- and the ONE
mechanism by which the workflow methods that own those facts are allowed to
move them.

Before this slice any user holding write access, and any sudo() code, could:

  * write {"state": "discharged"} on an admission and skip bed release, the
    discharge timestamp and every check in one call;
  * relink an admitted patient to a different bed, ward or room with no
    occupancy bookkeeping at all;
  * write hospital.bed.state = 'available' while a patient was still in it, or
    point bed.current_admission_id at somebody else's admission;
  * move an admission to another patient after the stay had been billed.

WHY A CONTEXTVAR AND NOT A CONTEXT FLAG
---------------------------------------
self.env.context is attacker-controlled on every RPC call: execute_kw and
/web/dataset/call_kw accept an arbitrary context, so a check such as
context.get("allow_state_write") is defeated by sending that key. A ContextVar
is server-side process state that no RPC payload can set; the only way to raise
it is to execute one of the context managers below in Python. This is the
design hospital_radiology's request/result state authority, hospital_pharmacy's
dispense authority, yoya_reception_bridge's reception capability and
hospital_billing's payer capability already use, applied to inpatient
occupancy.

sudo() IS NOT A CAPABILITY. Every guard below ignores env.su on purpose:
sudo() answers "may this code bypass ACLs and record rules", which is a
different question from "is this the workflow method that owns this fact".

That distinction is what makes PART 16 solvable. A receptionist may create and
work an admission but holds no write ACL on hospital.bed, and confirming an
admission has to occupy a bed. The answer is NOT to grant receptionists generic
bed editing, and NOT to sudo() in a controller. It is that
HospitalBed._set_occupancy() is the only code in the system that may move
bed.state and bed.current_admission_id, it refuses unless the occupancy
capability is raised, and only then does it use sudo() -- narrowly, on the bed
row, from inside the admission workflow that has already checked everything.
An ordinary caller who sudo()s a bed write still gets refused, because they do
not have the capability.

THE WINDOWS ARE NARROW. Each capability is raised around the workflow step that
owns the fact and always released in a `finally`, so an exception can never
leave it open for a later call in the same worker.
"""
import contextvars
from contextlib import contextmanager

from odoo.exceptions import UserError

# ---------------------------------------------------------------------------
# Capabilities
# ---------------------------------------------------------------------------
_admission_workflow_var = contextvars.ContextVar(
    "hospital_admission_workflow_capability", default=False
)
_bed_occupancy_var = contextvars.ContextVar(
    "hospital_admission_bed_occupancy_capability", default=False
)
_admission_location_var = contextvars.ContextVar(
    "hospital_admission_location_capability", default=False
)
_admission_billing_var = contextvars.ContextVar(
    "hospital_admission_billing_capability", default=False
)


@contextmanager
def _raised(var):
    token = var.set(True)
    try:
        yield
    finally:
        var.reset(token)


def admission_workflow_capability():
    """Raised ONLY by HospitalAdmission._write_state(). Opens a change of
    hospital.admission.state, plus the timestamps a transition stamps with it
    (discharge_date), and nothing else."""
    return _raised(_admission_workflow_var)


def has_admission_workflow_capability():
    return _admission_workflow_var.get()


def bed_occupancy_capability():
    """Raised ONLY by HospitalBed._set_occupancy(). Opens a change of
    hospital.bed.state and hospital.bed.current_admission_id and nothing else.

    Every path that occupies or frees a bed goes through that one method, which
    is called only from admission workflow methods that have already taken the
    row locks and checked ownership."""
    return _raised(_bed_occupancy_var)


def has_bed_occupancy_capability():
    return _bed_occupancy_var.get()


def admission_location_capability():
    """Raised ONLY by HospitalAdmission._write_location(), i.e. by the transfer
    workflow. Opens ward_id / room_id / bed_id on an admission that has left
    draft.

    Without this, location on an active admission is frozen: the transfer
    workflow is the only thing that may move a patient, because it is the only
    thing that also releases the old bed and occupies the new one."""
    return _raised(_admission_location_var)


def has_admission_location_capability():
    return _admission_location_var.get()


def admission_billing_capability():
    """Raised ONLY by HospitalAdmission.action_generate_admission_bill() around
    the one write that links the generated bill. Opens bill_id and nothing
    else, so a settled bill cannot be re-pointed by an ordinary write."""
    return _raised(_admission_billing_var)


def has_admission_billing_capability():
    return _admission_billing_var.get()


# ---------------------------------------------------------------------------
# States
# ---------------------------------------------------------------------------
# The states in which a patient is physically in a bed in this hospital. These
# are the states the uniqueness indexes, the encounter coherence constraint and
# the bed ownership checks all key on, so they are declared once.
ADMISSION_ACTIVE_STATES = ("admitted", "transferred")

# The stay is over. History, not an occupancy claim.
ADMISSION_TERMINAL_STATES = ("discharged", "cancelled")

# Encounter states in which an episode may be adopted and retyped as inpatient.
# This is the complement of hospital.encounter.EPISODE_CLOSED_STATES as
# yoya_reception_bridge defines it. It is redeclared rather than imported
# because hospital_admission must not depend on yoya_reception_bridge -- that
# module depends (transitively) on this one. _episode_closed_states() below
# reads the live value off the encounter model when the bridge is installed, so
# the two cannot drift silently.
ENCOUNTER_ADMISSIBLE_STATES = ("planned", "checked_in", "active")

# Fallback when yoya_reception_bridge is absent.
ENCOUNTER_CLOSED_STATES_FALLBACK = ("completed", "closed", "cancelled")


def episode_closed_states(encounter_model):
    """The live definition of "this episode is over".

    yoya_reception_bridge owns EPISODE_CLOSED_STATES and enforces one active
    episode per patient against it. Reading it off the model instead of copying
    it means the admission adoption logic and the reception duplicate guard can
    never disagree about what an active episode is.
    """
    return tuple(
        getattr(encounter_model, "EPISODE_CLOSED_STATES", ENCOUNTER_CLOSED_STATES_FALLBACK)
    )


# ---------------------------------------------------------------------------
# What is protected
# ---------------------------------------------------------------------------
# WHO and WHAT an admission is FOR. Set while the admission is a draft and
# never relinked afterwards: moving an active or historical admission to
# another patient, encounter or company would carry its bed occupancy, its
# nursing notes, its medication administration records, its procedure requests,
# its stock movements and its bill to someone else's record.
#
# `coverage_id` and `guarantee_id` are deliberately NOT listed. They are
# contributed by hospital_insurance, which is installed but lives outside this
# repository; insurance cover is a payer fact that is legitimately attached and
# re-attached during and after a stay, and freezing it here would break that
# module without improving safety. See PART 15 / PART 20 of the slice report.
ADMISSION_IDENTITY_FIELDS = (
    "patient_id",
    "encounter_id",
    "company_id",
)

# The clinical attribution of the stay. Frozen once the patient is physically
# admitted, because from that moment nursing, procedure and billing records
# have been filed against them.
ADMISSION_ATTRIBUTION_FIELDS = (
    "physician_id",
    "appointment_id",
    "diagnosis_id",
)

# WHERE the patient is. Frozen on an active admission except through the
# transfer workflow, which holds admission_location_capability.
ADMISSION_LOCATION_FIELDS = (
    "ward_id",
    "room_id",
    "bed_id",
)

# WHEN the stay happened. admission_date is settled once the patient is in a
# bed; discharge_date is stamped by the discharge workflow and never by a
# caller. Backdating either rewrites the billable length of stay.
ADMISSION_TIMELINE_FIELDS = (
    "admission_date",
    "discharge_date",
)

# What a bed's occupancy IS. Only _set_occupancy() may move these.
BED_OCCUPANCY_FIELDS = (
    "state",
    "current_admission_id",
)


# ---------------------------------------------------------------------------
# Refusals
# ---------------------------------------------------------------------------
# One fixed sentence per refusal. The MODEL chooses the code, so a refusal
# means the same thing whichever caller reached it, and a later Desk API can
# map each code to a status without inventing its own vocabulary. No sentence
# below names an amount, a currency, a payer or a diagnosis.
ADMISSION_ERROR_MESSAGES = {
    "admission_state_write_refused": (
        "An admission's state is moved by its workflow actions (Confirm Admission, "
        "Transfer, Discharge, Cancel, Reset to Draft) and cannot be written directly. "
        "Nothing was changed."
    ),
    "admission_identity_write_refused": (
        "An admission's patient, encounter and company are set when it is prepared "
        "and cannot be changed afterwards. Nothing was changed."
    ),
    "admission_attribution_write_refused": (
        "An admission's physician, appointment and diagnosis are settled once the "
        "patient is admitted and cannot be changed afterwards. Nothing was changed."
    ),
    "admission_location_write_refused": (
        "An admitted patient is moved by the Transfer action, which releases the old "
        "bed and occupies the new one. The ward, room and bed cannot be written "
        "directly. Nothing was changed."
    ),
    "admission_timeline_write_refused": (
        "An admission's admission and discharge times are stamped by its workflow "
        "actions and cannot be written directly. Nothing was changed."
    ),
    "admission_bill_write_refused": (
        "An admission's bill link is set by Generate Admission Bill and cannot be "
        "written directly. Nothing was changed."
    ),
    "admission_encounter_required": (
        "This patient has no open visit to admit. Register the patient at the front "
        "desk first, then admit them from that visit."
    ),
    "admission_encounter_ambiguous": (
        "This patient has more than one open visit, so it is not clear which one "
        "this admission belongs to. Resolve the duplicate visit first."
    ),
    "admission_encounter_patient_mismatch": (
        "The linked visit belongs to a different patient than this admission."
    ),
    "admission_encounter_company_mismatch": (
        "The linked visit belongs to a different company than this admission."
    ),
    "admission_encounter_closed": (
        "The linked visit is closed or cancelled and cannot carry an inpatient stay."
    ),
    "admission_bed_not_available": (
        "That bed is not available. Refresh the bed list and choose another."
    ),
    "admission_bed_owned_by_other": (
        "That bed is occupied by another admission. Refresh the bed list and choose "
        "another."
    ),
    "admission_bed_not_owned": (
        "This admission does not hold that bed, so it cannot release it. Nothing was "
        "changed."
    ),
    "admission_location_incoherent": (
        "The ward, room and bed do not belong together. Choose a bed that is in the "
        "selected room, in the selected ward."
    ),
    "admission_patient_already_admitted": (
        "This patient already has an active admission. Discharge or cancel it before "
        "admitting them again."
    ),
    "admission_duplicate_bed_in_batch": (
        "This request would put two admissions in the same bed. Each admission needs "
        "its own bed."
    ),
    "admission_occupancy_write_refused": (
        "A bed's occupancy is set by the admission workflow (Confirm Admission, "
        "Transfer, Discharge, Cancel) and cannot be written directly. Nothing was "
        "changed."
    ),
    "admission_encounter_has_active_admission": (
        "This visit has an active inpatient admission. Discharge or cancel the "
        "admission before closing or cancelling the visit."
    ),
    "admission_company_mismatch": (
        "The ward, room or bed belongs to a different company than this admission."
    ),
}


class AdmissionWorkflowError(UserError):
    """An admission refusal with a FIXED code and FIXED wording.

    A UserError, so every existing caller that already rolls back on UserError
    keeps doing so -- including the Odoo web client, which shows the message and
    discards the transaction. Its message is always the fixed sentence for its
    code: nothing from a deeper layer can ride along into it.
    """

    def __init__(self, code):
        self.code = code
        super().__init__(ADMISSION_ERROR_MESSAGES[code])


# ---------------------------------------------------------------------------
# Write comparison helpers
# ---------------------------------------------------------------------------
def m2o_id(value):
    """The id a many2one write value designates: int, record, or False."""
    if hasattr(value, "id"):
        return value.id or False
    return value or False


def same_value(record, field_name, value):
    """Whether writing `value` to `field_name` would leave `record` unchanged.

    Writing the value a record already has is not a change, so a form or an
    import that echoes the current values does not break. This is what lets the
    Odoo form view save an admitted admission without the user having touched a
    single guarded field -- the client sends back everything it loaded.
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


def changed_fields(record, field_names, vals):
    """The subset of `field_names` present in `vals` that would really change."""
    return [
        name
        for name in field_names
        if name in vals and not same_value(record, name, vals[name])
    ]
