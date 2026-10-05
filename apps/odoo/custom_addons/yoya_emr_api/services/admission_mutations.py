"""The Admissions mutation contract shared by the Admissions Desk and the
Doctor Desk (Admissions Slices 2-4: request, admit, transfer, cancel request,
medical discharge, final discharge).

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
    "admission_financial_clearance_required": 409,
    "admission_bed_conflict": 409,
    "admission_active_conflict": 409,
    "admission_integrity_error": 409,
    "admission_encounter_required": 422,
    "admission_company_mismatch": 422,
    "admission_location_mismatch": 422,
    # Admissions Slice 4: discharge gates. 409 -- the request was well formed;
    # the admission is not in a condition to accept it yet.
    "admission_not_medically_ready": 409,
    "admission_settlement_required": 409,
    "admission_financial_review_required": 409,
    # The estimate lock: well formed, but medical discharge has begun.
    "admission_estimate_locked": 409,
    # The one code the HTTP layer owns: an unexpected failure, rolled back.
    "admission_mutation_failed": 500,
}
MUTATION_FAILED_MESSAGE = "The admission action could not be completed. Nothing was changed."

ADMIT_KEYS = frozenset({"operation_token", "expected_revision", "bed_id"})
REQUEST_KEYS = frozenset({"operation_token", "reason"})
# Admissions Slice 3. The destination ward and room are derived from the bed.
TRANSFER_KEYS = frozenset({"operation_token", "expected_revision", "bed_id", "reason"})
CANCEL_REQUEST_KEYS = frozenset({"operation_token", "expected_revision"})
# Admissions Slice 4. The doctor writes the discharge summary; the clerk sends
# nothing but the token and the revision -- every gate is re-derived server-side.
DISCHARGE_REQUEST_KEYS = frozenset({"operation_token", "expected_revision", "summary"})
FINALIZE_DISCHARGE_KEYS = frozenset({"operation_token", "expected_revision"})
# Advance slice. The physician's estimate: an amount and why. Nothing else.
ESTIMATE_KEYS = frozenset({"operation_token", "expected_revision", "amount", "reason"})


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
