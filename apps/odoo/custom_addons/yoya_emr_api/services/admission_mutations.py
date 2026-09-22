"""The Admissions mutation contract shared by the Admissions Desk and the
Doctor Desk (Admissions Slices 2-3: request, admit, transfer, cancel request).

ONE ERROR VOCABULARY. The MODEL chooses the code (hospital_admission's
AdmissionDeskError); this module only chooses the HTTP status and rebuilds the
request body from the allowed keys. Both desks import it, so an admission
refusal means the same thing -- same code, same status, same sentence --
whichever desk the user was on.
"""
import uuid

from odoo.addons.hospital_admission.models.admission_authority import (
    DESK_ERROR_MESSAGES,
)

from .api_response import ApiError, read_json_body

DESK_STATUS = {
    "admission_not_found": 404,
    "admission_not_authorized": 403,
    "admission_invalid_payload": 400,
    "admission_bed_required": 400,
    "admission_revision_conflict": 409,
    "admission_operation_conflict": 409,
    "admission_invalid_state": 409,
    "admission_encounter_mismatch": 409,
    "admission_bed_unavailable": 409,
    "admission_bed_conflict": 409,
    "admission_active_conflict": 409,
    "admission_integrity_error": 409,
    "admission_encounter_required": 422,
    "admission_company_mismatch": 422,
    "admission_location_mismatch": 422,
    # The one code the HTTP layer owns: an unexpected failure, rolled back.
    "admission_mutation_failed": 500,
}
MUTATION_FAILED_MESSAGE = "The admission action could not be completed. Nothing was changed."

ADMIT_KEYS = frozenset({"operation_token", "expected_revision", "bed_id"})
REQUEST_KEYS = frozenset({"operation_token", "reason"})
# Admissions Slice 3. The destination ward and room are derived from the bed.
TRANSFER_KEYS = frozenset({"operation_token", "expected_revision", "bed_id", "reason"})
CANCEL_REQUEST_KEYS = frozenset({"operation_token", "expected_revision"})


def desk_error(code):
    message = DESK_ERROR_MESSAGES.get(code, MUTATION_FAILED_MESSAGE)
    return ApiError(code, message, DESK_STATUS.get(code, 500))


def mutation_body(required_keys):
    """Exactly the allowed keys; anything else is refused before a row is read."""
    try:
        body = read_json_body()
    except ApiError:
        raise desk_error("admission_invalid_payload") from None
    if not isinstance(body, dict) or set(body) != required_keys:
        raise desk_error("admission_invalid_payload")
    return body


def integrity_code(error):
    """A unique index lost a race the locks did not cover. Rolled back."""
    diag = getattr(error, "diag", None)
    text = (getattr(diag, "constraint_name", None) or "") + " " + str(error)
    if "one_active_per_bed" in text:
        return "admission_bed_conflict"
    if "one_active_per_patient" in text:
        return "admission_active_conflict"
    if "operation_token" in text:
        return "admission_operation_conflict"
    return "admission_integrity_error"


def canonical_token(raw):
    try:
        return str(uuid.UUID(str(raw).strip()))
    except (AttributeError, ValueError):
        return raw
