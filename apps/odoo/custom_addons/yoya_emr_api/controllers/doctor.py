"""Doctor Desk API: the clinician's worklist and the consultation gate.

Reads: the doctor's bounded working day, the selected-patient panel, and the
active consultation note.

Writes: two, and NEITHER decides anything. Each loads the appointment through
the caller's own scope, then calls one authoritative model method:

    POST .../visits/<id>/start-consultation
        -> hospital.appointment.action_start_consultation()
           which ALSO opens the hospital.consultation, in the same transaction
    POST .../visits/<id>/consultation/save
        -> hospital.consultation.save_narrative()

    GET  .../visits/<id>/consultation     PURE READ. Creates nothing.

THE CONSULTATION IS OPENED BY THE TRANSITION, NOT BY THE READ.
An earlier version of this module opened it lazily from the GET, which made a
clinical record appear as a side effect of a browser fetch. It is now created by
hospital.appointment.action_start_consultation() -- the act that justifies it --
so the invariant "in_consultation implies a consultation exists" holds for the
Odoo backend button and RPC callers too, not just for this API. The GET
therefore has no mutation and needs no savepoint, and a missing consultation is
reported as an integrity fault rather than quietly conjured.

The consultation routes hold no clinical rule of their own. The precondition
that a visit must be in_consultation, the copy-once presenting-complaint
seeding, the one-per-encounter invariant, the optimistic-concurrency check and
the post-completion freeze all live in hospital.consultation, so the Odoo form
and any RPC caller obey exactly the same rules this API does.

start-consultation loads the appointment through the caller's own scope and calls
hospital.appointment.action_start_consultation(). That single call runs four
independent model-layer gates, in this order:

  1. yoya_reception_bridge._assert_may_start_consultation()
       assigned doctor / manager / admin only            -> AccessError
  2. yoya_reception_bridge._assert_triage_completed()
       nursing evaluation must be done                   -> UserError
  3. hospital_billing.action_start_consultation()
       financial clearance on the consultation charge    -> UserError
  4. hospital_management.action_start_consultation()
       confirmed -> in_consultation, plus the audit log

None of the four is reimplemented, duplicated or bypassed here, and no state is
written by this module. Odoo's refusal is never swallowed: it is mapped to a
status code and forwarded with its own wording, because that sentence is the
only thing that tells the doctor WHICH gate refused.

SCOPING. Every read goes through services/clinical_scope, which composes an
explicit domain (a pure doctor is restricted to doctor_id.user_id = caller) and
lets the ORM apply yoya_reception_bridge's record rules on top. No client
parameter can widen it: department_id and q NARROW an already-scoped set, and
there is deliberately no doctor_id parameter at all -- see _worklist_domain.

The controller stays thin: it validates parameters, resolves records through
the caller's own rules, calls one model method, and serializes. It makes no
workflow decision and never calls sudo().
"""
import functools
import logging

from odoo import fields, http
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.http import request
from odoo.osv import expression

from odoo.addons.yoya_clinical_bridge.models.consultation import (
    CONSULTATION_APPOINTMENT_STATE,
    ConsultationConflict,
    ConsultationIncomplete,
)
from odoo.addons.yoya_clinical_bridge.models.patient_diagnosis import (
    DIAGNOSIS_EDITABLE_FIELDS,
    DiagnosisPrimaryConflict,
)
from odoo.addons.yoya_clinical_bridge.models.laboratory_request import (
    LAB_ORDER_EDITABLE_FIELDS,
)
from odoo.addons.yoya_clinical_bridge.models.radiology_request import (
    RAD_ORDER_EDITABLE_FIELDS,
)

from ..services.api_response import (
    ApiError,
    api_error_response,
    coerce_optional_id,
    coerce_text,
    error_response,
    parse_date,
    parse_int_param,
    read_json_body,
    success_response,
)
from ..services.clinical_scope import (
    find_appointment_in_scope,
    scoped_appointment_domain,
)
from ..services.consultation_serializers import (
    CONSULTATION_NARRATIVE_FIELDS,
    serialize_consultation_envelope,
)
from ..services.laboratory_serializers import (
    CATALOGUE_DEFAULT_LIMIT as LAB_CATALOGUE_DEFAULT_LIMIT,
    CATALOGUE_MAX_LIMIT as LAB_CATALOGUE_MAX_LIMIT,
    serialize_laboratory_orders,
    serialize_laboratory_test,
)
from ..services.radiology_serializers import (
    CATALOGUE_DEFAULT_LIMIT as RAD_CATALOGUE_DEFAULT_LIMIT,
    CATALOGUE_MAX_LIMIT as RAD_CATALOGUE_MAX_LIMIT,
    serialize_radiology_exam,
    serialize_radiology_orders,
)
from ..services.result_serializers import (
    serialize_results,
)
from ..services.medication_serializers import (
    CATALOGUE_DEFAULT_LIMIT as MED_CATALOGUE_DEFAULT_LIMIT,
    CATALOGUE_MAX_LIMIT as MED_CATALOGUE_MAX_LIMIT,
    serialize_medicine,
    serialize_prescriptions,
)
from ..services.diagnosis_serializers import (
    CATALOGUE_DEFAULT_LIMIT,
    CATALOGUE_MAX_LIMIT,
    serialize_diagnosis_list,
    serialize_disease,
)
from ..services.doctor_serializers import (
    STAGE_KEYS,
    bucket_of,
    prefetch_worklist,
    serialize_queue_row,
    serialize_session,
    serialize_visit_detail,
    worklist_counts,
)
from ..services.reception_scope import (
    doctor_capability_flags,
    hospital_day_bounds_utc,
    may_doctor_desk,
    role_flags,
)

_logger = logging.getLogger(__name__)

# A single doctor's day fits inside this many times over. The cap exists so a
# malformed or hostile ?limit= cannot turn one request into a table scan.
DEFAULT_LIMIT = 200
MAX_LIMIT = 500

# The STORED appointment states that make up a doctor's working day.
#
# Bounded in SQL by stored columns only. front_desk_stage is deliberately NOT
# filtered here and CANNOT be: it is a non-stored compute whose inputs traverse
# the encounter and the evaluation, so putting it in a domain would either
# raise or silently scan. The stage is resolved in Python, over this bounded
# candidate set, in serialize_queue_row().
#
# 'done' is included so a doctor keeps the visits they finished today in the
# Finished bucket. 'draft' and 'cancelled' are excluded: neither is a visit the
# doctor can work, and a cancelled row in a clinical queue is noise.
WORKLIST_STATES = ("confirmed", "in_consultation", "done")

# Field names the consultation save endpoint refuses BY NAME rather than
# ignoring. Every one of them is authoritative ownership or workflow state
# derived server-side, and a client that sends one has misunderstood the
# contract badly enough to be worth telling -- silently dropping them would let
# a frontend believe it had reassigned a consultation.
CONSULTATION_PROTECTED_FIELDS = frozenset(
    {
        "id",
        "name",
        "state",
        "started_at",
        "completed_at",
        "encounter_id",
        "appointment_id",
        "patient_id",
        "doctor_id",
        "company_id",
        "active",
        "editable",
    }
)

# Said to a doctor who opens a visit that has not started yet. A fixed string
# written here, never a model message, matching the closed-set discipline
# doctor_serializers applies to clearance wording.
CONSULTATION_NOT_STARTED_REASON = (
    "The consultation has not been started for this visit yet. Start the "
    "consultation to open the clinical note."
)

# Ownership and derived columns on hospital.patient.diagnosis. Every one of
# these is resolved from the consultation server-side, so a client sending one
# has misunderstood the contract badly enough to be worth telling.
DIAGNOSIS_PROTECTED_FIELDS = frozenset(
    {
        "id",
        "patient_id",
        "encounter_id",
        "consultation_id",
        "appointment_id",
        "physician_id",
        "active",
        "editable",
        "disease_code",
        "category_id",
        "diagnosis_date",
    }
)


# Ownership and billing columns on hospital.laboratory.request. All are
# derived server-side from the consultation, or owned entirely by
# hospital_billing. A client sending one has misunderstood the contract badly
# enough to be worth telling.
LAB_ORDER_PROTECTED_FIELDS = frozenset(
    {
        "id",
        "name",
        "state",
        "patient_id",
        "physician_id",
        "encounter_id",
        "appointment_id",
        "consultation_id",
        "evaluation_id",
        "treatment_plan_id",
        "active",
        "request_date",
        "charge_line_ids",
        "billing_blocked",
    }
)


# Ownership and billing columns on hospital.radiology.request. All are derived
# server-side from the consultation, or owned entirely by hospital_billing. A
# client sending one has misunderstood the contract badly enough to be worth
# telling.
#
# `evaluation_id` appears here for the same reason it does on the laboratory
# set: hospital.radiology.request offers it as a second clinical anchor with an
# onchange that overwrites patient and physician from it, so accepting one from
# a browser would let a client re-point an order's ownership through the back
# door.
RAD_ORDER_PROTECTED_FIELDS = frozenset(
    {
        "id",
        "name",
        "state",
        "patient_id",
        "physician_id",
        "encounter_id",
        "appointment_id",
        "consultation_id",
        "evaluation_id",
        "active",
        "request_date",
        "completed_at",
        "completed_by_id",
        "line_ids",
        "result_ids",
        "charge_line_ids",
        "billing_blocked",
    }
)


# The prescription header fields a doctor may write. `notes` is the only one:
# every clinical detail of a medication order lives on the LINE, and the header
# carries ownership, a date and a state that nothing outside the model may set.
MEDICATION_HEADER_FIELDS = ("notes",)

# Everything a client must never write on the header. Same shape and same
# reasoning as the two sets above.
#
# `pharmacy_dispense_ids` appears here because hospital_pharmacy hangs the
# dispense off the prescription as a one2many, and a client that could write it
# would be creating the dispense the Doctor Desk is forbidden to create --
# through the back door, in the same request that writes the prescription.
#
# `prescription_date` is protected rather than editable: it is the date the
# medication was ordered, and a browser is not the authority on when that was.
MEDICATION_PROTECTED_FIELDS = frozenset(
    {
        "id",
        "name",
        "state",
        "patient_id",
        "physician_id",
        "appointment_id",
        "consultation_id",
        "diagnosis_id",
        "active",
        "prescription_date",
        "request_token",
        "line_ids",
        "pharmacy_dispense_ids",
        "pharmacy_dispense_count",
    }
)


class LaboratoryResponseError(Exception):
    """The laboratory order was placed, but its response could not be built.

    Its own type, like every other response-failure on this surface, because
    the sentences differ and a doctor told "the note was not saved" for a
    failed lab order would go looking in the wrong place. By the time this
    reaches the handler the savepoint has already rolled back the request, its
    lines AND the charges hospital_billing raised at confirmation.
    """


class RadiologyResponseError(Exception):
    """The radiology order was placed, but its response could not be built.

    Its own type, separate from LaboratoryResponseError, for the reason every
    other response-failure on this surface has one: the sentences differ. A
    doctor told "the laboratory order was not placed" after an imaging
    submission would go looking at the wrong bench for an order that does not
    exist in either place.

    By the time this reaches the handler the savepoint has already rolled back
    the request, its lines AND the charges hospital_billing raised at
    confirmation.
    """


class MedicationResponseError(Exception):
    """The prescription was written, but its response could not be built.

    Its own type, like every other response-failure on this surface, because the
    sentences differ and a doctor told "the radiology order was not placed"
    after a prescription would go looking at the wrong department.

    By the time this reaches the handler the savepoint has already rolled back
    the prescription, its lines AND the pharmacy dispense hospital_pharmacy
    composed at confirmation. No charge is involved: medication is billed later,
    at the pharmacist's Mark Ready, so unlike the laboratory and radiology cases
    there is nothing financial to unwind here.
    """


class ConsultationResponseError(Exception):
    """The consultation started, but its success response could not be built.

    A separate type on purpose, exactly like cashier.PaymentResponseError.
    Without it, an AccessError raised while SERIALIZING the result is
    indistinguishable from one raised by _assert_may_start_consultation, and
    the doctor would be told they are not the assigned clinician for a visit
    they were in fact authorized to start -- and which, before the savepoint,
    had already been committed.

    By the time this reaches the handler below, the savepoint has already
    rolled the transition back, so the message it produces ("nothing changed")
    is a statement of fact rather than a hope.
    """


class ConsultationNoteResponseError(Exception):
    """The consultation note was saved, but its response could not be built.

    Separate from ConsultationResponseError above for the same reason that one
    is separate from AccessError: the two failures need DIFFERENT sentences.
    "The consultation was not started" told to a doctor whose note failed to
    serialize would send them back to press Start Consultation on a visit that
    is already in consultation.

    Like its sibling, by the time this reaches the handler the savepoint has
    already rolled the write back, so "your note was not saved" is a statement
    of fact.
    """


class ConsultationCompleteResponseError(Exception):
    """The consultation was completed, but its response could not be built.

    A FOURTH response-failure type, for the same reason the third exists: the
    sentence has to match the act. This one matters most of all, because the
    act it reports is IRREVERSIBLE by any workflow this system has -- there is
    no amendment path for a completed consultation. A doctor told "your note
    failed to save" when what actually happened was a completed, frozen
    consultation would be left believing they still had a note to finish.

    By the time this reaches the handler the savepoint has rolled back the
    consultation state, the completed_at stamp, the appointment transition, the
    encounter completion and the consultation charge delivery -- all of it, as
    one unit -- so "nothing was changed" is a statement of fact and the retry
    is safe.
    """


class DiagnosisResponseError(Exception):
    """The diagnosis was written, but its response could not be built.

    A third response-failure type for the same reason the second exists: the
    sentence has to match the act. Telling a doctor their consultation note
    failed to save, when what actually failed was the confirmation of a
    diagnosis they added, would send them to the wrong screen to recover.

    By the time this reaches the handler the savepoint has already rolled the
    write back, so "nothing was recorded" is a statement of fact.
    """


def doctor_endpoint(func):
    """Stable error envelope. Never leaks a traceback.

    Ordering matters three times over.

    The two response-failure types are caught FIRST so a post-write
    serialization failure cannot be reported as an authorization denial.

    ConsultationConflict comes next. It subclasses UserError -- so that any
    caller which does not know about it still sees a clean refusal -- which
    means the broad UserError handler would otherwise swallow it and answer 422
    invalid_workflow_state. A stale write is neither an invalid transition nor
    an authorization failure: it is a recoverable concurrency outcome, and 409
    is the only status that tells the client to re-read and retry.

    And in Odoo AccessError and ValidationError both subclass UserError too, so
    the broad handler has to come last.
    """

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        try:
            if request.env.user._is_public():
                return error_response(
                    "authentication_required", "Authentication is required.", 401
                )
            return func(*args, **kwargs)
        except ApiError as error:
            return api_error_response(error)
        except ConsultationResponseError:
            # Already logged with its cause at the raise site, and the savepoint
            # has already rolled the transition back.
            return error_response(
                "consultation_response_failed",
                "The consultation was not started because its confirmation "
                "could not be produced. Nothing was changed. Please retry.",
                500,
            )
        except ConsultationNoteResponseError:
            # Same contract as above: logged at the raise site, and the
            # savepoint has already rolled the write back.
            return error_response(
                "consultation_note_response_failed",
                "Your consultation note was not saved because the confirmation "
                "could not be produced. Nothing was changed. Please retry.",
                500,
            )
        except LaboratoryResponseError:
            return error_response(
                "laboratory_response_failed",
                "The laboratory order was not placed because the confirmation "
                "could not be produced. Nothing was changed. Please retry.",
                500,
            )
        except RadiologyResponseError:
            return error_response(
                "radiology_response_failed",
                "The radiology order was not placed because the confirmation "
                "could not be produced. Nothing was changed. Please retry.",
                500,
            )
        except MedicationResponseError:
            return error_response(
                "medication_response_failed",
                "The prescription was not written because the confirmation "
                "could not be produced. Nothing was changed. Please retry.",
                500,
            )
        except ConsultationCompleteResponseError:
            # Logged at the raise site. The savepoint has already undone the
            # completion, the appointment transition AND the encounter move.
            return error_response(
                "consultation_complete_response_failed",
                "The consultation was not completed because the confirmation "
                "could not be produced. Nothing was changed. Please retry.",
                500,
            )
        except DiagnosisResponseError:
            return error_response(
                "diagnosis_response_failed",
                "The diagnosis was not recorded because the confirmation could "
                "not be produced. Nothing was changed. Please retry.",
                500,
            )
        except ConsultationConflict as error:
            # The model's own sentence: it tells the doctor to reload rather
            # than retry blindly, which is the only safe recovery for free text.
            return error_response("consultation_conflict", str(error), 409)
        except DiagnosisPrimaryConflict as error:
            # Also a UserError subclass, so it needs its own branch above the
            # broad handler for the same reason ConsultationConflict does. 409
            # rather than 422 because the doctor CAN resolve it -- by demoting
            # the existing primary -- and the message names which one holds the
            # slot.
            return error_response("diagnosis_primary_exists", str(error), 409)
        except AccessError as error:
            # Reaching here means the AUTHORIZATION path denied the caller --
            # scope, the desk gate, or _assert_may_start_consultation.
            # Response-building failures cannot land here; they are
            # ConsultationResponseError above.
            _logger.warning(
                "Doctor desk endpoint %s denied for uid=%s",
                func.__name__,
                request.env.uid,
            )
            # Odoo's own sentence, forwarded. _assert_may_start_consultation
            # raises AccessError and names the doctor the visit is assigned to;
            # replacing that with generic copy would hide which gate refused.
            return error_response("access_denied", str(error), 403)
        except ConsultationIncomplete as error:
            # Above the ValidationError branch it subclasses, for the same
            # reason ConsultationConflict sits above UserError. 422 rather
            # than 400 because the REQUEST was fine: the record is not
            # finished, and the doctor -- not a developer -- can fix it.
            return error_response("consultation_incomplete", str(error), 422)
        except ValidationError as error:
            return error_response("validation_error", str(error), 400)
        except UserError as error:
            return error_response("invalid_workflow_state", str(error), 422)
        except Exception:
            _logger.exception(
                "Unexpected error in YOYA doctor endpoint %s", func.__name__
            )
            return error_response(
                "internal_error", "An unexpected error occurred.", 500
            )

    return wrapper


def _require_doctor_desk(env):
    """Fail fast for DOCTOR_DESK_GROUPS. Never the only control.

    Scoping restricts WHICH visits are returned; this decides whether the desk
    opens at all. Without it a nurse or cashier would get a 200 with an empty
    list, which reads as "no patients today" rather than "not your workstation".
    """
    if not may_doctor_desk(env):
        raise ApiError(
            "access_denied",
            "Doctor Desk access requires the Hospital Doctor, Hospital Manager "
            "or Hospital System Administrator role.",
            403,
        )


def _load_visit(env, appointment_id):
    """Resolve a visit through the caller's own scope.

    Distinguishes a visit that does not exist from one the caller may not
    reach, which is what lets the desk say "not found" rather than implying a
    record exists that the doctor cannot see.
    """
    appointment, reason = find_appointment_in_scope(env, appointment_id)
    if reason == "not_found":
        raise ApiError("visit_not_found", "Visit not found.", 404)
    if reason == "out_of_scope":
        raise ApiError(
            "out_of_scope",
            "This visit is outside your clinical scope.",
            403,
        )
    return appointment


def _load_consultation(env, appointment):
    """The consultation for a STARTED visit. Reads only; never opens one.

    THE INVARIANT THIS RELIES ON, AND WHY IT DOES NOT SELF-HEAL.
    hospital.appointment.action_start_consultation() opens the consultation as
    part of the transition, inside whatever transaction moved the visit, so a
    visit in consultation always has one. If that is not true here, something
    has gone wrong that a clinician cannot fix by retrying and that this layer
    must not disguise: a record removed underneath the workflow, or a visit that
    predates the invariant and was never backfilled.

    Creating one on the spot would produce an EMPTY note for a consultation that
    may already have been conducted -- the doctor would see a blank screen where
    their examination findings used to be and have no way to tell that anything
    was lost. A 500 with a reference they can quote is the honest answer.

    Deliberately NOT 404: the visit exists and the caller may reach it. This is
    a server-side integrity fault, and reporting it as "not found" would send
    support looking for a missing appointment.
    """
    if not appointment.encounter_id:
        # NOT an integrity fault. hospital.consultation.encounter_id is
        # required, so a visit that never had an encounter -- a legacy row
        # predating encounter tracking -- can never carry a note. Reporting it
        # as a server error would send support hunting a bug that is really a
        # property of the data, so it is a 409 with the reason stated.
        raise ApiError(
            "consultation_unavailable",
            "This visit has no encounter, so there is no episode of care to "
            "document. It predates encounter tracking and cannot carry a "
            "consultation note.",
            409,
        )

    consultation = env["hospital.consultation"].find_for_appointment(appointment)
    if not consultation:
        _logger.error(
            "INTEGRITY: appointment=%s is %s but has no hospital.consultation "
            "(encounter=%s). It was not opened by action_start_consultation, or "
            "it predates that invariant and was not backfilled.",
            appointment.id,
            appointment.state,
            appointment.encounter_id.id or None,
        )
        raise ApiError(
            "consultation_missing",
            "This visit is in consultation but its clinical note is missing. "
            "Nothing has been changed. Please report visit %s to support."
            % (appointment.appointment_code or appointment.id),
            500,
        )
    return consultation


def _load_open_consultation(env, appointment):
    """The consultation a diagnosis may be written into.

    Every diagnosis mutation needs the SAME three answers -- the visit has
    started, its consultation exists, and it is still open -- so they are
    resolved once here rather than restated at four call sites where they could
    drift apart.

    The freeze is checked again inside the model for every channel; this only
    decides the status code the desk sees.
    """
    if appointment.state != CONSULTATION_APPOINTMENT_STATE:
        # A DONE VISIT IS NOT AN UNSTARTED ONE. Telling a doctor "the
        # consultation has not been started" about a visit they signed ten
        # seconds ago sends them hunting for a Start button on a locked
        # record -- the same class of message bug the consultation GET
        # carried before Slice 4.
        signed = env["hospital.consultation"].find_for_appointment(appointment)
        if signed and signed.state == "completed":
            raise ApiError(
                "consultation_completed",
                "This consultation is completed and its diagnoses are locked.",
                409,
            )
        raise ApiError(
            "consultation_not_available", CONSULTATION_NOT_STARTED_REASON, 409
        )
    consultation = _load_consultation(env, appointment)
    if consultation.state != "draft":
        raise ApiError(
            "consultation_completed",
            "This consultation is completed and its diagnoses are locked.",
            409,
        )
    return consultation


def _load_diagnosis(env, consultation, diagnosis_id):
    """One diagnosis, resolved through the caller's own rules AND this visit.

    THE CONSULTATION CHECK IS NOT REDUNDANT with the record rule. The doctor
    rule admits every diagnosis this doctor authored, across all their
    patients; without the consultation filter, a diagnosis id from one visit
    could be edited through another visit's URL. That would still be the
    doctor's own record, so no rule would object -- but it would be filed and
    displayed against the wrong consultation.

    A diagnosis outside this consultation reads as not found rather than
    forbidden, so the endpoint cannot be used to probe which ids exist.
    """
    diagnosis = env["hospital.patient.diagnosis"].browse(diagnosis_id).exists()
    if not diagnosis or diagnosis.consultation_id != consultation:
        raise ApiError(
            "diagnosis_not_found",
            "Diagnosis not found for this consultation.",
            404,
        )
    return diagnosis


def _diagnosis_payload(env, consultation):
    """The whole diagnosis list, re-read from the database.

    Every mutation answers with the full list rather than the row it touched:
    adding a primary changes what the OTHER rows may become, and removing one
    frees the primary slot, so a single-row response would leave the desk
    holding a stale picture of the rest.
    """
    consultation.invalidate_recordset()
    diagnoses = env["hospital.patient.diagnosis"].for_consultation(consultation)
    return serialize_diagnosis_list(diagnoses, consultation.state == "draft")


def _load_laboratory_order(env, consultation, order_id):
    """One laboratory order, through the caller's rules AND this consultation.

    The consultation filter is not redundant with the record rule: the doctor
    rule admits every request they ordered, across all their patients, so
    without it an order id from one visit could be cancelled through another
    visit's URL. An order outside this consultation reads as not found rather
    than forbidden, so the endpoint cannot be used to probe which ids exist.
    """
    order = env["hospital.laboratory.request"].browse(order_id).exists()
    if not order or order.consultation_id != consultation:
        raise ApiError(
            "laboratory_order_not_found",
            "Laboratory order not found for this consultation.",
            404,
        )
    return order


def _laboratory_payload(env, consultation):
    """The whole order list, re-read from the database after any mutation."""
    consultation.invalidate_recordset()
    orders = env["hospital.laboratory.request"].for_consultation(consultation)
    return serialize_laboratory_orders(orders, consultation.state == "draft")


def _build_laboratory_values(body):
    """The doctor's clinical intent. Ownership and billing never cross here."""
    provided = set(body)
    for key in ("tests", "test_ids", "diagnosis_id", "request_token"):
        provided.discard(key)

    protected = provided & LAB_ORDER_PROTECTED_FIELDS
    if protected:
        raise ApiError(
            "protected_field",
            "These fields cannot be written directly: %s."
            % ", ".join(sorted(protected)),
            400,
        )

    unknown = provided - set(LAB_ORDER_EDITABLE_FIELDS)
    if unknown:
        raise ApiError(
            "unknown_field", "Unrecognised fields: %s." % ", ".join(sorted(unknown)), 400
        )
    return {key: body[key] for key in provided}


def _load_radiology_order(env, consultation, order_id):
    """One radiology order, through the caller's rules AND this consultation.

    The consultation filter is not redundant with the record rule: the doctor
    rule admits every request they ordered, across all their patients, so
    without it an order id from one visit could be cancelled through another
    visit's URL. An order outside this consultation reads as not found rather
    than forbidden, so the endpoint cannot be used to probe which ids exist.
    """
    order = env["hospital.radiology.request"].browse(order_id).exists()
    if not order or order.consultation_id != consultation:
        raise ApiError(
            "radiology_order_not_found",
            "Radiology order not found for this consultation.",
            404,
        )
    return order


def _radiology_payload(env, consultation):
    """The whole order list, re-read from the database after any mutation."""
    consultation.invalidate_recordset()
    orders = env["hospital.radiology.request"].for_consultation(consultation)
    return serialize_radiology_orders(orders, consultation.state == "draft")


def _build_radiology_values(body):
    """The doctor's clinical intent. Ownership and billing never cross here."""
    provided = set(body)
    for key in ("exams", "exam_ids", "diagnosis_id", "request_token"):
        provided.discard(key)

    protected = provided & RAD_ORDER_PROTECTED_FIELDS
    if protected:
        raise ApiError(
            "protected_field",
            "These fields cannot be written directly: %s."
            % ", ".join(sorted(protected)),
            400,
        )

    unknown = provided - set(RAD_ORDER_EDITABLE_FIELDS)
    if unknown:
        raise ApiError(
            "unknown_field", "Unrecognised fields: %s." % ", ".join(sorted(unknown)), 400
        )
    return {key: body[key] for key in provided}


def _load_prescription(env, consultation, prescription_id):
    """One prescription, through the caller's rules AND this consultation.

    The consultation filter is not redundant with the record rule: the doctor
    rule admits every prescription they wrote, across all their patients, so
    without it a prescription id from one visit could be cancelled through
    another visit's URL. A prescription outside this consultation reads as not
    found rather than forbidden, so the endpoint cannot be used to probe which
    ids exist.
    """
    prescription = env["hospital.prescription"].browse(prescription_id).exists()
    if not prescription or prescription.consultation_id != consultation:
        raise ApiError(
            "prescription_not_found",
            "Prescription not found for this consultation.",
            404,
        )
    return prescription


def _medication_payload(env, consultation):
    """The whole prescription list, re-read from the database after a mutation."""
    consultation.invalidate_recordset()
    prescriptions = env["hospital.prescription"].for_consultation(consultation)
    return serialize_prescriptions(prescriptions, consultation.state == "draft")


def _build_medicine_entries(body):
    """The prescribed medicines, validated as a SHAPE and nothing more.

    THIS LAYER CHECKS STRUCTURE; THE MODEL CHECKS MEANING. Whether a quantity is
    positive, whether a route is in the line's selection, whether a medicine is
    orderable at all -- every one of those is decided by
    hospital.prescription.create_from_consultation(), because they are the same
    questions asked of every caller and not only of this endpoint.

    ONE ENTRY PER PRESCRIBED MEDICINE, and duplicates are NOT collapsed. That is
    a real difference from the radiology endpoint, which de-duplicates its exam
    list because ordering the same study twice in one submission is always a
    client mistake. Prescribing the same drug twice on one prescription is not:
    a tapering course and a rescue dose are two lines of the same medicine with
    different instructions, and merging them would silently delete a clinical
    decision.
    """
    raw = body.get("medicines")
    if not isinstance(raw, list) or not raw:
        raise ApiError(
            "invalid_field",
            "'medicines' must be a non-empty list of prescribed medicines.",
            400,
        )

    allowed = {
        "medicine_id",
        "quantity",
        "dosage",
        "route",
        "frequency",
        "duration",
        "instructions",
    }

    entries = []
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            raise ApiError(
                "invalid_field",
                "Each entry in 'medicines' must be an object with a medicine_id.",
                400,
            )
        unknown = set(item) - allowed
        if unknown:
            raise ApiError(
                "unknown_field",
                "Unrecognised fields on medicine %s: %s."
                % (index + 1, ", ".join(sorted(unknown))),
                400,
            )
        medicine_id = coerce_optional_id("medicine_id", item.get("medicine_id"))
        if not medicine_id:
            raise ApiError(
                "invalid_field",
                "Each entry in 'medicines' needs a medicine id.",
                400,
            )
        entry = {key: item[key] for key in item if key != "medicine_id"}
        entry["medicine_id"] = medicine_id
        entries.append(entry)
    return entries


def _build_medication_header(body):
    """The prescription header's own optional fields.

    Patient, physician, appointment and consultation are derived server-side
    from the consultation record. A client that sends one of them is told so by
    name rather than having it silently dropped, which is what stops a frontend
    believing it had reassigned a prescription.
    """
    provided = set(body)
    for key in ("medicines", "diagnosis_id", "request_token"):
        provided.discard(key)

    protected = provided & MEDICATION_PROTECTED_FIELDS
    if protected:
        raise ApiError(
            "protected_field",
            "These fields cannot be written directly: %s."
            % ", ".join(sorted(protected)),
            400,
        )

    unknown = provided - set(MEDICATION_HEADER_FIELDS)
    if unknown:
        raise ApiError(
            "unknown_field", "Unrecognised fields: %s." % ", ".join(sorted(unknown)), 400
        )
    return {key: body[key] for key in provided}


def _require_request_token(body, duplicate_consequence):
    """The idempotency token is MANDATORY on every Doctor Desk create.

    ONE VALIDATOR FOR ALL THREE MUTATIONS -- diagnoses, laboratory orders and
    radiology orders. They had three subtly different tolerances before this,
    which is exactly how a contract rots: the same client mistake was a silent
    duplicate on one endpoint and a refusal on another, and no reader could tell
    which without opening all three. The only thing that varies below is the
    sentence describing what a duplicate would COST, because that is genuinely
    endpoint-specific and is the half of the message a developer acts on.

    WHY THIS IS A REFUSAL AND NOT A DEFAULT. All three models carry the same
    shape: an OPTIONAL request_token column and a PARTIAL unique index,
    `WHERE consultation_id IS NOT NULL AND request_token IS NOT NULL`. A NULL
    token is therefore deduplicated by nothing -- not by the index, which does
    not cover it, and not by the model's own lookup, which each of the three
    service methods skips entirely when no token is supplied. A tokenless
    submission is a submission with no replay protection at all.

    Minting one server-side would be worse than refusing. A token the SERVER
    invented is different on every attempt, so it would satisfy the column while
    protecting nothing -- the retry would still duplicate, and the index would
    certify that it was fine. The token has to come from the client precisely
    because the client is the only party that knows two requests are the same
    submission.

    THIS IS AN API CONTRACT, NOT A MODEL RULE. The columns stay optional and the
    indexes stay partial, because all three models are legitimately created
    outside this API: at the laboratory or imaging department, from the Odoo
    form, by a scheduled job. Those callers have no submission to identify and
    must not be forced to invent one. Every service method therefore keeps
    accepting request_token=None; only these endpoints insist.

    NOT NORMALISED, ONLY VALIDATED. The token is an opaque client string and is
    stored exactly as sent. Trimming it here would silently merge two tokens the
    client considers distinct, which is the same class of mistake as minting
    one. Blank-after-strip is refused rather than trimmed, because whitespace is
    not an identifier.

    A wrong TYPE is reported as invalid_field by coerce_text, not as a missing
    token: sending 42 is a different client bug from sending nothing, and one
    error code for both would send the wrong developer to the wrong line.
    """
    token = coerce_text("request_token", body.get("request_token"))
    if not token or not token.strip():
        raise ApiError(
            "missing_request_token",
            "'request_token' is required and must be a non-empty string. It is "
            "what makes a retried submission return the existing record instead "
            "of %s." % duplicate_consequence,
            400,
        )
    return token


def _build_diagnosis_values(body, require_type):
    """The clinical fields, and nothing that decides ownership.

    Patient, encounter, appointment, consultation and physician are derived
    server-side from the consultation record. A client that sends one of them
    is told so by name rather than having it silently dropped, which is what
    stops a frontend believing it had reassigned a diagnosis.
    """
    provided = set(body)
    provided.discard("disease_id")
    provided.discard("request_token")

    protected = provided & DIAGNOSIS_PROTECTED_FIELDS
    if protected:
        raise ApiError(
            "protected_field",
            "These fields cannot be written directly: %s."
            % ", ".join(sorted(protected)),
            400,
        )

    unknown = provided - set(DIAGNOSIS_EDITABLE_FIELDS)
    if unknown:
        raise ApiError(
            "unknown_field", "Unrecognised fields: %s." % ", ".join(sorted(unknown)), 400
        )

    if require_type and not body.get("diagnosis_type"):
        raise ApiError(
            "invalid_field",
            "'diagnosis_type' is required when recording a diagnosis.",
            400,
        )

    return {key: body[key] for key in provided}


def _limit_param(raw):
    if raw in (None, "", False):
        return DEFAULT_LIMIT
    limit = parse_int_param("limit", raw)
    if limit <= 0:
        raise ApiError("invalid_parameter", "'limit' must be positive.", 400)
    return min(limit, MAX_LIMIT)


def _worklist_domain(env, day, department_id, search):
    """ONE domain over STORED columns, evaluated once in SQL.

    THERE IS NO doctor_id PARAMETER, deliberately. Accepting one would let a
    client name a colleague, and while scoped_appointment_domain would still
    AND the caller's own restriction over it -- so a pure doctor could never
    actually read another doctor's visit -- the parameter would be a widening
    surface for a manager and an invitation to build a UI against it. The
    doctor whose queue this is comes from the SESSION and from nowhere else.

    department_id and q only ever NARROW. Both are ANDed inside the scoped
    domain, so neither can reach a visit scope had already excluded.
    """
    start, end = hospital_day_bounds_utc(env, day)
    domain = [
        ("state", "in", list(WORKLIST_STATES)),
        ("appointment_date", ">=", start),
        ("appointment_date", "<=", end),
    ]
    if department_id:
        domain.append(("department_id", "=", department_id))
    if search:
        domain = expression.AND(
            [
                domain,
                [
                    "|",
                    "|",
                    ("patient_id.name", "ilike", search),
                    ("patient_id.identification_code", "ilike", search),
                    ("appointment_code", "ilike", search),
                ],
            ]
        )
    return domain


def _build_consultation_values(body):
    """The narrative, and the version. NOTHING else crosses this boundary.

    THE ALLOWLIST IS THE POINT. Ownership -- which patient, which encounter,
    which visit, which physician -- is derived server-side from a record the
    caller already resolved through their own scope. Accepting any of it here
    would make the client a participant in deciding whose note this is, which
    is exactly the class of bug that produces a consultation filed against the
    wrong patient.

    Unknown and protected keys are REJECTED rather than dropped, matching
    clinical.py._build_save_values: a client sending doctor_id has a real
    misunderstanding, and silently ignoring it lets that misunderstanding ship.
    """
    provided = set(body)

    version = body.get("version")
    if not isinstance(version, str) or not version:
        raise ApiError(
            "missing_version",
            "'version' is required and must be the token returned by the last "
            "consultation read.",
            400,
        )
    provided.discard("version")

    protected = provided & CONSULTATION_PROTECTED_FIELDS
    if protected:
        raise ApiError(
            "protected_field",
            "These fields cannot be written directly: %s."
            % ", ".join(sorted(protected)),
            400,
        )

    unknown = provided - set(CONSULTATION_NARRATIVE_FIELDS)
    if unknown:
        raise ApiError(
            "unknown_field",
            "Unrecognised fields: %s." % ", ".join(sorted(unknown)),
            400,
        )

    # coerce_text maps null/false to False, which CLEARS the field. That is a
    # legitimate edit -- a doctor deleting a paragraph they wrote in error --
    # and is distinct from omitting the key, which leaves the field untouched.
    values = {name: coerce_text(name, body[name]) for name in provided}
    return version, values


def _build_completion_version(body):
    """Completion takes the version token and NOTHING else.

    A stricter allowlist than _build_consultation_values, and deliberately so:
    completing is not an edit. A client sending narrative alongside the
    completion would be asking for an unsaved paragraph to be silently written
    and then frozen -- which is exactly the "complete with unsaved changes"
    hazard the Doctor Desk disables its button to prevent. Rejecting the field
    by name says so, where dropping it silently would let that client ship.
    """
    provided = set(body)

    version = body.get("version")
    if not isinstance(version, str) or not version:
        raise ApiError(
            "missing_version",
            "'version' is required and must be the token returned by the last "
            "consultation read.",
            400,
        )
    provided.discard("version")

    protected = provided & CONSULTATION_PROTECTED_FIELDS
    if protected:
        raise ApiError(
            "protected_field",
            "These fields cannot be written directly: %s."
            % ", ".join(sorted(protected)),
            400,
        )

    narrative = provided & set(CONSULTATION_NARRATIVE_FIELDS)
    if narrative:
        raise ApiError(
            "unknown_field",
            "Completing a consultation writes no clinical content. Save the "
            "note first, then complete it. Rejected: %s."
            % ", ".join(sorted(narrative)),
            400,
        )

    if provided:
        raise ApiError(
            "unknown_field",
            "Unrecognised fields: %s." % ", ".join(sorted(provided)),
            400,
        )

    return version


class YoyaEmrDoctorController(http.Controller):

    # ------------------------------------------------------------------
    # 1. Session
    # ------------------------------------------------------------------
    @http.route(
        "/yoya-emr/api/v1/doctor/session",
        type="http", auth="user", methods=["GET"], csrf=False,
    )
    @doctor_endpoint
    def doctor_session(self, **params):
        """Who is signed in and what the desk may offer them.

        Deliberately NOT gated on _require_doctor_desk: the shell calls this to
        decide what to tell a user who reached /doctor without the role, and a
        403 here would leave it unable to say anything useful. It exposes
        identity and capability flags only, both of which the caller already
        knows about themselves.
        """
        env = request.env
        return success_response(
            serialize_session(env, doctor_capability_flags(env), role_flags(env))
        )

    # ------------------------------------------------------------------
    # 2. Worklist
    # ------------------------------------------------------------------
    @http.route(
        "/yoya-emr/api/v1/doctor/worklist",
        type="http", auth="user", methods=["GET"], csrf=False,
    )
    @doctor_endpoint
    def worklist(self, **params):
        """The doctor's working day, with the AUTHORITATIVE stage on every row.

        Two passes on purpose, and they are not interchangeable:

          SQL     bounded by stored columns (state, date, department) AND the
                  caller's scope domain. This is the security boundary.
          Python  front_desk_stage resolved per row and serialized unchanged.
                  This is the workflow truth, and it cannot be expressed in a
                  domain because the field is not stored.

        Counters are computed from the SAME rows the client receives, so a
        bucket count and the list under it can never disagree.
        """
        env = request.env
        _require_doctor_desk(env)

        day = (
            parse_date(params["date"])
            if params.get("date")
            else fields.Date.context_today(env["hospital.appointment"])
        )
        limit = _limit_param(params.get("limit"))
        department_id = (
            parse_int_param("department_id", params["department_id"])
            if params.get("department_id")
            else None
        )
        search = (params.get("q") or "").strip() or None

        base_domain = _worklist_domain(env, day, department_id, search)
        # The scope domain is ANDed OUTSIDE the caller's filters, so no
        # parameter above can escape it.
        domain = scoped_appointment_domain(env, base_domain)

        appointments = env["hospital.appointment"].search(
            domain, order="appointment_date asc, id asc", limit=limit + 1
        )
        truncated = len(appointments) > limit
        if truncated:
            appointments = appointments[:limit]

        prefetch_worklist(appointments)
        capabilities = doctor_capability_flags(env)
        rows = [
            serialize_queue_row(appointment, capabilities)
            for appointment in appointments
        ]

        return success_response(
            {
                "rows": rows,
                "counts": worklist_counts(rows),
                "capabilities": capabilities,
                "filters": {
                    "date": day.isoformat(),
                    "department_id": department_id,
                    "q": search,
                    "limit": limit,
                },
                "meta": {
                    "row_count": len(rows),
                    "truncated": truncated,
                    "states": list(WORKLIST_STATES),
                    "stages": list(STAGE_KEYS),
                },
            }
        )

    # ------------------------------------------------------------------
    # 3. Visit detail
    # ------------------------------------------------------------------
    @http.route(
        "/yoya-emr/api/v1/doctor/visits/<int:appointment_id>",
        type="http", auth="user", methods=["GET"], csrf=False,
    )
    @doctor_endpoint
    def visit_detail(self, appointment_id, **params):
        """The selected-patient panel, resolved from one read.

        Scoped to ONE visit by the URL, never by a client-supplied domain, and
        resolved through the caller's own record rules.
        """
        env = request.env
        _require_doctor_desk(env)

        appointment = _load_visit(env, appointment_id)
        prefetch_worklist(appointment)
        return success_response(
            serialize_visit_detail(appointment, doctor_capability_flags(env))
        )

    # ------------------------------------------------------------------
    # 4. Start consultation
    # ------------------------------------------------------------------
    @http.route(
        "/yoya-emr/api/v1/doctor/visits/<int:appointment_id>/start-consultation",
        type="http", auth="user", methods=["POST"], csrf=False,
    )
    @doctor_endpoint
    def start_consultation(self, appointment_id, **params):
        """THE consultation-opening write, AND IT DECIDES NOTHING.

        Loads the appointment through the caller's legitimate scope and calls
        action_start_consultation(). It does NOT write appointment.state, does
        NOT write encounter.state, does NOT re-check triage, does NOT re-check
        financial clearance, does NOT create the consultation record itself and
        does NOT sudo the mutation. Every one of those belongs to the model,
        which enforces them whatever this route allows.

        THE CONSULTATION RECORD IS OPENED BY THAT SAME CALL.
        yoya_clinical_bridge extends action_start_consultation() to open the
        hospital.consultation once the transition has actually happened. It runs
        inside the savepoint below with no extra plumbing, so the transition and
        the record it justifies commit together or not at all -- and no nested
        savepoint exists that could let the record outlive a rolled-back
        transition.

        Odoo's refusal is forwarded, not swallowed. AccessError and UserError
        both propagate to doctor_endpoint, which maps them to 403 and 422 with
        Odoo's own wording -- the sentence that names the gate.

        ATOMICITY: WHY THE EXPLICIT SAVEPOINT IS REQUIRED
        ------------------------------------------------
        Because doctor_endpoint CATCHES exceptions and RETURNS a Response.

        That is the whole problem, and it inverts the intuition that "an
        exception rolls the request back". Odoo's dispatcher decides whether to
        COMMIT from how the handler RETURNS: a normal return -- including the
        error Response the decorator builds -- is a served request, and it
        commits. An exception only rolls back if it escapes the handler, and
        the decorator's entire job is to stop that happening.

        So without the savepoint below, a failure AFTER
        action_start_consultation() succeeded but BEFORE the response was
        finished would commit the transition and hand the client an error. The
        patient would be in_consultation, the encounter active and the
        consultation charge in progress, while the doctor's screen said the
        start had failed -- and the retry would then be refused because the
        visit is no longer 'confirmed'.

        This is the same defect class already fixed in reception.create_visit
        (orphan confirmed appointments behind a "duplicate visit" error) and in
        cashier.record_payment (confirmed receipts behind HTTP 403). The
        mechanism is identical: env.cr.savepoint() is a _FlushingSavepoint,
        which flushes on entry and, on any exception, clears pending ORM state
        and issues ROLLBACK TO SAVEPOINT before re-raising -- so by the time the
        decorator builds an error body, the transition is gone.

        The response object is built INSIDE the block for the same reason: a
        failure while SERIALIZING or JSON-encoding must not leave a committed
        consultation behind an error the client reads as failure.

        _load_visit stays OUTSIDE, matching reception, cashier and
        insurance_credit. It is a pure read that writes nothing, so there is
        nothing for a savepoint to roll back; and its 404/403 must keep their
        own status codes rather than being folded into the response-failure
        branch below.
        """
        env = request.env
        _require_doctor_desk(env)

        appointment = _load_visit(env, appointment_id)

        # ONE atomic unit: the transition AND its serialized response.
        with env.cr.savepoint():
            appointment.action_start_consultation()

            try:
                # Re-serialized AFTER the mutation, from the records as they
                # now stand. The stage the client renders is the DERIVED one,
                # never a value it guessed from the action it just took.
                appointment.invalidate_recordset()
                prefetch_worklist(appointment)
                payload = serialize_visit_detail(
                    appointment, doctor_capability_flags(env)
                )
                payload["bucket"] = bucket_of(
                    {
                        "queue_stage": payload["visit"]["queue_stage"],
                        "state": payload["visit"]["state"],
                    }
                )
                response = success_response(payload)
            except Exception as error:
                # Log the real cause here -- the client never sees it.
                _logger.exception(
                    "Doctor start-consultation response failed for "
                    "appointment=%s uid=%s; rolling the transition back",
                    appointment_id,
                    env.uid,
                )
                raise ConsultationResponseError(str(error)) from error

        return response

    # ------------------------------------------------------------------
    # 5. Consultation note -- read
    # ------------------------------------------------------------------
    @http.route(
        "/yoya-emr/api/v1/doctor/visits/<int:appointment_id>/consultation",
        type="http", auth="user", methods=["GET"], csrf=False,
    )
    @doctor_endpoint
    def consultation_detail(self, appointment_id, **params):
        """The active consultation for one visit. A PURE READ.

        WHY THIS IS A SEPARATE ENDPOINT FROM /visits/<id>
        The visit-detail read fires on EVERY queue selection, including for
        visits that have not started and never will. Folding the consultation
        into it would pay for a second read on every selection and would dilute
        a serializer whose confidentiality reasoning is auditable precisely
        because it is small.

        THIS ENDPOINT CREATES NOTHING, AND THAT IS THE POINT.
        It previously called get_or_create_for_appointment, which made opening
        a clinical record a side effect of a GET -- something a browser prefetch,
        a double render or a link preview could trigger. The consultation is now
        opened exactly once, by hospital.appointment.action_start_consultation(),
        which is the act that justifies it. There is therefore no mutation here,
        no savepoint, and nothing for a rollback to undo.

        A MISSING CONSULTATION IS REPORTED, NOT REPAIRED.
        Given the model-layer invariant, in_consultation with no consultation is
        a genuine integrity fault -- a record deleted underneath the workflow, or
        a visit that predates the invariant and was not backfilled. Silently
        creating one here would paper over it and reintroduce exactly the
        creating-GET this change removes, so it is surfaced as a server error.
        """
        env = request.env
        _require_doctor_desk(env)

        appointment = _load_visit(env, appointment_id)

        # KEYED ON THE CONSULTATION, NOT ON THE APPOINTMENT STATE.
        #
        # This previously short-circuited on `state != in_consultation`, which
        # meant that the moment a visit was completed the note written during
        # it disappeared from the desk -- and disappeared behind the sentence
        # "The consultation has not been started for this visit yet", told to
        # the doctor who had just signed it. Diagnoses and laboratory orders
        # were converted to the consultation-keyed shape for exactly this
        # reason; the note was the last of the three still doing it.
        #
        # Three cases, distinguished by the only thing that governs the answer:
        #
        #   no consultation (pre-start)   -> unavailable, with the reason
        #   consultation, state draft     -> available, editable true
        #   consultation, state completed -> available, editable FALSE
        #
        # find_for_appointment() is the pure lookup and creates nothing, so this
        # GET stays a pure read for a visit that never started.
        consultation = env["hospital.consultation"].find_for_appointment(appointment)
        if not consultation:
            # An in_consultation visit with no consultation is the integrity
            # fault _load_consultation reports; anything earlier in the workflow
            # simply has no note yet, which is a normal shape.
            if appointment.state == CONSULTATION_APPOINTMENT_STATE:
                consultation = _load_consultation(env, appointment)
            else:
                return success_response(
                    serialize_consultation_envelope(
                        env["hospital.consultation"].browse(),
                        available=False,
                        reason=CONSULTATION_NOT_STARTED_REASON,
                    )
                )

        return success_response(
            serialize_consultation_envelope(consultation, available=True)
        )

    # ------------------------------------------------------------------
    # 6. Consultation note -- save
    # ------------------------------------------------------------------
    @http.route(
        "/yoya-emr/api/v1/doctor/visits/<int:appointment_id>/consultation/save",
        type="http", auth="user", methods=["POST"], csrf=False,
    )
    @doctor_endpoint
    def consultation_save(self, appointment_id, **params):
        """Save the narrative. Version-checked, and it decides nothing else.

        The controller validates the SHAPE of the request and calls one model
        method. It does not check the freeze, does not compare versions, does
        not stamp ownership and does not sudo(): every one of those belongs to
        hospital.consultation.save_narrative(), which enforces them for the
        Odoo form and any RPC caller as well as for this route.

        ATOMICITY. The mutation, the reload and the response are one unit,
        for the reason spelled out at length in start_consultation: Odoo's
        dispatcher commits on a normal return, and doctor_endpoint's job is to
        turn exceptions into normal returns. Without the savepoint a failure
        while serializing would commit the doctor's note and tell them it had
        not saved -- and their retry would then be refused as a stale version,
        because the write they were told had failed had actually bumped
        write_date.
        """
        env = request.env
        _require_doctor_desk(env)

        appointment = _load_visit(env, appointment_id)
        version, values = _build_consultation_values(read_json_body())

        if appointment.state != CONSULTATION_APPOINTMENT_STATE:
            signed = env["hospital.consultation"].find_for_appointment(
                appointment
            )
            if signed and signed.state == "completed":
                raise ApiError(
                    "consultation_completed",
                    "This consultation is completed and its note is locked.",
                    409,
                )
            raise ApiError(
                "consultation_not_available",
                CONSULTATION_NOT_STARTED_REASON,
                409,
            )

        consultation = _load_consultation(env, appointment)

        with env.cr.savepoint():
            # Raises ConsultationConflict on a stale version, which propagates
            # out of the savepoint -- so a refused save leaves the stored note
            # byte-for-byte as it was.
            consultation.save_narrative(values, version)

            try:
                # Re-serialized from the record as it now stands, so the client
                # renders the stored note and the NEW version token rather than
                # the values it optimistically sent.
                consultation.invalidate_recordset()
                response = success_response(
                    serialize_consultation_envelope(consultation, available=True)
                )
            except Exception as error:
                _logger.exception(
                    "Doctor consultation save response failed for "
                    "appointment=%s uid=%s; rolling the write back",
                    appointment_id,
                    env.uid,
                )
                raise ConsultationNoteResponseError(str(error)) from error

        return response

    # ------------------------------------------------------------------
    # 6b. Consultation -- complete
    # ------------------------------------------------------------------
    @http.route(
        "/yoya-emr/api/v1/doctor/visits/<int:appointment_id>/consultation/complete",
        type="http", auth="user", methods=["POST"], csrf=False,
    )
    @doctor_endpoint
    def consultation_complete(self, appointment_id, **params):
        """THE completion write, AND IT DECIDES NOTHING.

        Loads the visit through the caller's own scope, reads the version off
        the body and calls one model method. It does NOT check authorization
        beyond opening the desk, does NOT evaluate the clinical minimum, does
        NOT compare versions, does NOT write consultation.state, does NOT move
        the appointment, does NOT touch the encounter or any charge, and does
        NOT sudo(). Every one of those belongs to
        hospital.consultation.action_complete(), which enforces them for the
        Odoo form and any RPC caller as well as for this route.

        ATOMICITY, AND WHY IT MATTERS MORE HERE THAN ANYWHERE ELSE.
        One savepoint spans a transition that reaches four records: the
        consultation freezes, the appointment moves to done, the encounter moves
        active -> completed and the consultation charge is marked delivered.
        doctor_endpoint catches exceptions and RETURNS a response, and Odoo's
        dispatcher commits on a normal return -- so without the savepoint a
        failure while serializing would commit all four and tell the doctor
        completion had failed. They would then be looking at a frozen note they
        believe is still open, with no amendment workflow to recover through.

        THE RESPONSE IS THE WHOLE POST-COMPLETION PICTURE. Envelope plus visit
        detail plus bucket, so the desk re-renders the read-only workspace AND
        re-buckets the queue row from one payload rather than firing three
        follow-up reads against a state it has to guess at.
        """
        env = request.env
        _require_doctor_desk(env)

        appointment = _load_visit(env, appointment_id)
        version = _build_completion_version(read_json_body())

        if appointment.state != CONSULTATION_APPOINTMENT_STATE:
            # Completing anything else is not a stale read, it is the wrong
            # visit -- a done visit is already complete and a confirmed one was
            # never started. 409 with the state named, rather than letting the
            # model raise a message written for the Odoo form.
            raise ApiError(
                "consultation_not_available",
                "Visit %s is not in consultation, so there is nothing to "
                "complete." % (appointment.appointment_code or appointment.id),
                409,
            )

        consultation = _load_consultation(env, appointment)

        # ONE atomic unit: the transition AND its serialized response.
        with env.cr.savepoint():
            # Raises AccessError (403), ValidationError (422) or
            # ConsultationConflict (409) -- all before anything is written, and
            # all propagating out of the savepoint untouched.
            consultation.action_complete(version)

            try:
                # Re-read from the records as they NOW stand. The desk renders
                # the derived state, never a value it guessed from the action it
                # just took.
                consultation.invalidate_recordset()
                appointment.invalidate_recordset()
                prefetch_worklist(appointment)

                payload = serialize_consultation_envelope(
                    consultation, available=True
                )
                # Nested under its own key rather than merged into the
                # envelope: the two payloads are independently owned and carry
                # two different confidentiality arguments, and flattening them
                # would make either one's key set impossible to audit.
                detail = serialize_visit_detail(
                    appointment, doctor_capability_flags(env)
                )
                payload["visit_detail"] = detail
                payload["bucket"] = bucket_of(
                    {
                        "queue_stage": detail["visit"]["queue_stage"],
                        "state": detail["visit"]["state"],
                    }
                )
                response = success_response(payload)
            except Exception as error:
                _logger.exception(
                    "Doctor consultation-complete response failed for "
                    "appointment=%s uid=%s; rolling the completion back",
                    appointment_id,
                    env.uid,
                )
                raise ConsultationCompleteResponseError(str(error)) from error

        return response

    # ------------------------------------------------------------------
    # 7. Disease catalogue -- read
    # ------------------------------------------------------------------
    @http.route(
        "/yoya-emr/api/v1/doctor/catalogue/diseases",
        type="http", auth="user", methods=["GET"], csrf=False,
    )
    @doctor_endpoint
    def diagnosis_catalogue(self, **params):
        """Search the disease catalogue. READ ONLY, and bounded in SQL.

        Without a server-side limit the first render of a diagnosis picker
        would pull every disease row over the wire and leave the trimming to
        JavaScript -- a table dump with extra steps, which gets slower exactly
        as the catalogue becomes useful. `limit` is CLAMPED, so a client cannot
        opt out of the cap.

        This is reference data with no patient in it, so there is nothing to
        scope: hospital_management already grants Hospital Doctor read on
        hospital.disease, and nothing here widens that.
        """
        env = request.env
        _require_doctor_desk(env)

        search = (params.get("q") or "").strip()
        limit = CATALOGUE_DEFAULT_LIMIT
        if params.get("limit"):
            limit = max(
                1,
                min(parse_int_param("limit", params["limit"]), CATALOGUE_MAX_LIMIT),
            )

        domain = []
        if search:
            domain = ["|", ("name", "ilike", search), ("code", "ilike", search)]

        diseases = env["hospital.disease"].search(domain, limit=limit, order="name")
        return success_response(
            {
                "diseases": [serialize_disease(disease) for disease in diseases],
                "query": search or None,
                "limit": limit,
                # Honest about the cap, so the desk can say "refine your
                # search" rather than implying these are all the matches.
                "truncated": len(diseases) == limit,
            }
        )

    # ------------------------------------------------------------------
    # 8. Consultation diagnoses -- read
    # ------------------------------------------------------------------
    @http.route(
        "/yoya-emr/api/v1/doctor/visits/<int:appointment_id>/diagnoses",
        type="http", auth="user", methods=["GET"], csrf=False,
    )
    @doctor_endpoint
    def diagnosis_list(self, appointment_id, **params):
        """The diagnoses recorded in this visit's consultation. Pure read.

        KEYED ON THE CONSULTATION, NOT ON THE APPOINTMENT STATE.

        This previously short-circuited to an empty list whenever the visit was
        not `in_consultation`, which contradicted its own docstring: the moment
        the consultation was completed and the appointment moved to `done`, the
        diagnoses recorded during it vanished from the desk. That is precisely
        when a clinician most often re-reads them.

        The three cases are now distinguished by whether a consultation exists
        and what state it is in, which is the only thing that actually governs
        the answer:

          no consultation (pre-start)   -> empty, editable false
          consultation, state draft     -> diagnoses, editable true
          consultation, state completed -> diagnoses, editable FALSE

        find_for_appointment() is the pure lookup and never opens anything, so
        a visit that has not started still creates no clinical record by being
        read. The MUTATION gates are untouched and still demand an open draft
        consultation -- `editable` is an affordance, and the model refuses a
        frozen write regardless of what it says.
        """
        env = request.env
        _require_doctor_desk(env)

        appointment = _load_visit(env, appointment_id)
        consultation = env["hospital.consultation"].find_for_appointment(appointment)
        if not consultation:
            return success_response(
                {"diagnoses": [], "editable": False, "has_primary": False}
            )

        diagnoses = env["hospital.patient.diagnosis"].for_consultation(consultation)
        return success_response(
            serialize_diagnosis_list(diagnoses, consultation.state == "draft")
        )

    # ------------------------------------------------------------------
    # 9. Consultation diagnoses -- record
    # ------------------------------------------------------------------
    @http.route(
        "/yoya-emr/api/v1/doctor/visits/<int:appointment_id>/diagnoses",
        type="http", auth="user", methods=["POST"], csrf=False,
    )
    @doctor_endpoint
    def diagnosis_add(self, appointment_id, **params):
        """Record a diagnosis. Ownership is derived, never supplied.

        The controller resolves the visit, the consultation and the disease
        through the CALLER's own record rules and hands three records to one
        model method. It does not stamp the patient, does not resolve the
        encounter, does not check the primary invariant, does not check the
        freeze and does not sudo(): all of those belong to
        add_to_consultation(), which enforces them for the Odoo form and any
        RPC caller too.

        `request_token` IS REQUIRED. Unlike every other field on this body it is
        refused when absent rather than defaulted, because it is the only replay
        protection a diagnosis has: see _require_request_token().
        """
        env = request.env
        _require_doctor_desk(env)

        appointment = _load_visit(env, appointment_id)
        body = read_json_body()
        values = _build_diagnosis_values(body, require_type=True)

        disease_id = coerce_optional_id("disease_id", body.get("disease_id"))
        if not disease_id:
            raise ApiError("invalid_field", "'disease_id' is required.", 400)
        # REQUIRED, and checked before the consultation is resolved so a
        # submission with no replay protection is refused having touched
        # nothing. A retried Add without one would file the same disease twice
        # -- and, for a primary, be answered with "a primary already exists",
        # blaming the doctor for the browser's second request.
        request_token = _require_request_token(
            body, "filing the same diagnosis twice"
        )

        consultation = _load_open_consultation(env, appointment)

        disease = env["hospital.disease"].browse(disease_id).exists()
        if not disease:
            raise ApiError(
                "disease_not_found", "Diagnosis not found in the catalogue.", 404
            )

        with env.cr.savepoint():
            env["hospital.patient.diagnosis"].add_to_consultation(
                # Passed straight through, with no `or None` fallback: the token
                # is already guaranteed non-empty above, and a fallback here
                # would quietly re-open the tokenless path this endpoint exists
                # to close.
                consultation, disease, values, request_token=request_token
            )
            try:
                response = success_response(_diagnosis_payload(env, consultation))
            except Exception as error:
                _logger.exception(
                    "Doctor diagnosis add response failed for appointment=%s "
                    "uid=%s; rolling the write back",
                    appointment_id,
                    env.uid,
                )
                raise DiagnosisResponseError(str(error)) from error

        return response

    # ------------------------------------------------------------------
    # 10. Consultation diagnoses -- update
    # ------------------------------------------------------------------
    @http.route(
        "/yoya-emr/api/v1/doctor/visits/<int:appointment_id>"
        "/diagnoses/<int:diagnosis_id>/update",
        type="http", auth="user", methods=["POST"], csrf=False,
    )
    @doctor_endpoint
    def diagnosis_update(self, appointment_id, diagnosis_id, **params):
        """Edit a diagnosis while its consultation is open.

        A POST action rather than PATCH, matching start-consultation and
        consultation/save: this API is consistently action-shaped, and one REST
        verb in a controller full of POST actions is a surprise, not a purity
        win.
        """
        env = request.env
        _require_doctor_desk(env)

        appointment = _load_visit(env, appointment_id)
        values = _build_diagnosis_values(read_json_body(), require_type=False)
        consultation = _load_open_consultation(env, appointment)
        diagnosis = _load_diagnosis(env, consultation, diagnosis_id)

        with env.cr.savepoint():
            diagnosis.update_from_consultation(values)
            try:
                response = success_response(_diagnosis_payload(env, consultation))
            except Exception as error:
                _logger.exception(
                    "Doctor diagnosis update response failed for diagnosis=%s "
                    "uid=%s; rolling the write back",
                    diagnosis_id,
                    env.uid,
                )
                raise DiagnosisResponseError(str(error)) from error

        return response

    # ------------------------------------------------------------------
    # 11. Consultation diagnoses -- remove
    # ------------------------------------------------------------------
    @http.route(
        "/yoya-emr/api/v1/doctor/visits/<int:appointment_id>"
        "/diagnoses/<int:diagnosis_id>/remove",
        type="http", auth="user", methods=["POST"], csrf=False,
    )
    @doctor_endpoint
    def diagnosis_remove(self, appointment_id, diagnosis_id, **params):
        """Remove a diagnosis from an open consultation.

        The model ARCHIVES rather than deletes -- see remove_from_consultation().
        The doctor sees the entry leave the consultation; the patient's
        longitudinal record and the audit trail keep it.
        """
        env = request.env
        _require_doctor_desk(env)

        appointment = _load_visit(env, appointment_id)
        consultation = _load_open_consultation(env, appointment)
        diagnosis = _load_diagnosis(env, consultation, diagnosis_id)

        with env.cr.savepoint():
            diagnosis.remove_from_consultation()
            try:
                response = success_response(_diagnosis_payload(env, consultation))
            except Exception as error:
                _logger.exception(
                    "Doctor diagnosis remove response failed for diagnosis=%s "
                    "uid=%s; rolling the write back",
                    diagnosis_id,
                    env.uid,
                )
                raise DiagnosisResponseError(str(error)) from error

        return response

    # ------------------------------------------------------------------
    # 12. Laboratory test catalogue -- read
    # ------------------------------------------------------------------
    @http.route(
        "/yoya-emr/api/v1/doctor/catalogue/laboratory-tests",
        type="http", auth="user", methods=["GET"], csrf=False,
    )
    @doctor_endpoint
    def laboratory_catalogue(self, **params):
        """Search the laboratory test catalogue. READ ONLY, bounded in SQL.

        Clinical and reference fields only. hospital.laboratory.test carries
        billing_service_id once hospital_billing is installed -- the mapping
        that decides what a test costs -- and it is never serialized.

        Reference data with no patient in it, so there is nothing to scope:
        hospital_management already grants Hospital Doctor read on
        hospital.laboratory.test, and nothing here widens that.
        """
        env = request.env
        _require_doctor_desk(env)

        search = (params.get("q") or "").strip()
        limit = LAB_CATALOGUE_DEFAULT_LIMIT
        if params.get("limit"):
            limit = max(
                1,
                min(
                    parse_int_param("limit", params["limit"]),
                    LAB_CATALOGUE_MAX_LIMIT,
                ),
            )

        model = env["hospital.laboratory.test"]
        # ORDERABLE ONLY. The desk confirms on submission, and confirmation
        # refuses the whole order if any test is unmapped or misconfigured --
        # so offering one would offer an action this workflow deterministically
        # refuses. The predicate is the model's, mirroring _assert_billable;
        # this controller does not know what makes a test billable and does not
        # decide it. Archived tests are excluded by the ORM's own active_test.
        domain = model.doctor_orderable_domain()
        if search:
            domain = expression.AND(
                [
                    domain,
                    ["|", ("name", "ilike", search), ("code", "ilike", search)],
                ]
            )

        tests = model.search(domain, limit=limit, order="name")
        return success_response(
            {
                "tests": [serialize_laboratory_test(test) for test in tests],
                "query": search or None,
                "limit": limit,
                "truncated": len(tests) == limit,
            }
        )

    # ------------------------------------------------------------------
    # 13. Consultation laboratory orders -- read
    # ------------------------------------------------------------------
    @http.route(
        "/yoya-emr/api/v1/doctor/visits/<int:appointment_id>/orders/laboratory",
        type="http", auth="user", methods=["GET"], csrf=False,
    )
    @doctor_endpoint
    def laboratory_order_list(self, appointment_id, **params):
        """The laboratory orders placed in this visit's consultation.

        Keyed on the CONSULTATION, not the appointment state, so orders stay
        readable after the visit finishes -- which is exactly when a doctor
        chases a pending result. `can_order` reports whether new orders may
        still be placed.
        """
        env = request.env
        _require_doctor_desk(env)

        appointment = _load_visit(env, appointment_id)
        consultation = env["hospital.consultation"].find_for_appointment(appointment)
        if not consultation:
            return success_response({"orders": [], "can_order": False})

        orders = env["hospital.laboratory.request"].for_consultation(consultation)
        return success_response(
            serialize_laboratory_orders(orders, consultation.state == "draft")
        )

    # ------------------------------------------------------------------
    # 14. Consultation laboratory orders -- place
    # ------------------------------------------------------------------
    @http.route(
        "/yoya-emr/api/v1/doctor/visits/<int:appointment_id>/orders/laboratory",
        type="http", auth="user", methods=["POST"], csrf=False,
    )
    @doctor_endpoint
    def laboratory_order_create(self, appointment_id, **params):
        """Place a laboratory order. THE CONTROLLER CREATES NO CHARGE.

        It resolves the visit, the consultation, the tests and the optional
        diagnosis through the CALLER's own record rules, then hands records to
        one model method. Everything financial belongs to hospital_billing's
        action_confirm_request() override, which create_from_consultation()
        invokes: validating every test's billing configuration before raising
        any charge, resolving the encounter, asserting patient/appointment/
        encounter agreement, and creating one charge per ordered test
        all-or-nothing.

        ATOMICITY. The create, the confirmation, the charges it raises, the
        reload and the response are ONE savepoint. A failure while serializing
        must not leave a confirmed request with live charges behind a message
        saying the order was not placed -- the doctor would re-order and the
        patient would be billed twice.

        `request_token` IS REQUIRED. Unlike every other field on this body it is
        refused when absent rather than defaulted, because it is the only replay
        protection an order has: see _require_request_token().
        """
        env = request.env
        _require_doctor_desk(env)

        appointment = _load_visit(env, appointment_id)
        body = read_json_body()
        values = _build_laboratory_values(body)

        raw_tests = body.get("tests") or body.get("test_ids") or []
        if not isinstance(raw_tests, list) or not raw_tests:
            raise ApiError(
                "invalid_field",
                "'tests' must be a non-empty list of laboratory test ids.",
                400,
            )
        test_ids = []
        for entry in raw_tests:
            # Accept either a bare id or {"test_id": n}, so the client may send
            # the shape it already holds without reshaping it.
            candidate = entry.get("test_id") if isinstance(entry, dict) else entry
            test_id = coerce_optional_id("test_id", candidate)
            if not test_id:
                raise ApiError(
                    "invalid_field", "Each entry in 'tests' needs a test id.", 400
                )
            test_ids.append(test_id)

        diagnosis_id = coerce_optional_id("diagnosis_id", body.get("diagnosis_id"))
        # REQUIRED, and checked here -- before the consultation is resolved and
        # long before the savepoint -- so a submission with no replay protection
        # is refused having touched nothing at all.
        request_token = _require_request_token(
            body, "ordering the same tests -- and billing them -- twice"
        )

        consultation = _load_open_consultation(env, appointment)

        # DE-DUPLICATED BEFORE THE EXISTENCE CHECK. recordset.exists() preserves
        # duplicate ids, so browsing [7, 7, 9] returns three records and a naive
        # length comparison would report a perfectly valid test as missing. The
        # model de-duplicates again when building the lines; this is about
        # answering "does every id you sent exist", not about the ordered set.
        unique_test_ids = list(dict.fromkeys(test_ids))
        tests = env["hospital.laboratory.test"].browse(unique_test_ids).exists()
        if len(tests) != len(unique_test_ids):
            raise ApiError(
                "laboratory_test_not_found",
                "One or more laboratory tests were not found in the catalogue.",
                404,
            )

        diagnosis = env["hospital.patient.diagnosis"].browse(diagnosis_id).exists()
        if diagnosis_id and not diagnosis:
            raise ApiError(
                "diagnosis_not_found",
                "Diagnosis not found for this consultation.",
                404,
            )

        with env.cr.savepoint():
            env["hospital.laboratory.request"].create_from_consultation(
                consultation,
                tests,
                values,
                diagnosis=diagnosis or None,
                # Passed straight through, with no `or None` fallback: the token
                # is already guaranteed non-empty above, and a fallback here
                # would quietly re-open the tokenless path this endpoint exists
                # to close.
                request_token=request_token,
            )
            try:
                response = success_response(_laboratory_payload(env, consultation))
            except Exception as error:
                _logger.exception(
                    "Doctor laboratory order response failed for appointment=%s "
                    "uid=%s; rolling the request and its charges back",
                    appointment_id,
                    env.uid,
                )
                raise LaboratoryResponseError(str(error)) from error

        return response

    # ------------------------------------------------------------------
    # 15. Consultation laboratory orders -- cancel
    # ------------------------------------------------------------------
    @http.route(
        "/yoya-emr/api/v1/doctor/visits/<int:appointment_id>"
        "/orders/laboratory/<int:order_id>/cancel",
        type="http", auth="user", methods=["POST"], csrf=False,
    )
    @doctor_endpoint
    def laboratory_order_cancel(self, appointment_id, order_id, **params):
        """Cancel a laboratory order through the model's own workflow.

        The base transition guard permits cancellation only from draft or
        requested and refuses a request carrying a validated or released
        result; hospital_billing's override then cancels the operational
        charges and refuses outright if any has been delivered. None of that is
        reimplemented, and the refusal reaches the doctor with its own wording,
        because that sentence is the only thing that says WHY.
        """
        env = request.env
        _require_doctor_desk(env)

        appointment = _load_visit(env, appointment_id)
        consultation = _load_open_consultation(env, appointment)
        order = _load_laboratory_order(env, consultation, order_id)

        with env.cr.savepoint():
            order.cancel_from_consultation()
            try:
                response = success_response(_laboratory_payload(env, consultation))
            except Exception as error:
                _logger.exception(
                    "Doctor laboratory cancel response failed for order=%s "
                    "uid=%s; rolling the cancellation back",
                    order_id,
                    env.uid,
                )
                raise LaboratoryResponseError(str(error)) from error

        return response

    # ------------------------------------------------------------------
    # 16. Radiology exam catalogue
    # ------------------------------------------------------------------
    @http.route(
        "/yoya-emr/api/v1/doctor/catalogue/radiology-exams",
        type="http", auth="user", methods=["GET"], csrf=False,
    )
    @doctor_endpoint
    def radiology_catalogue(self, **params):
        """Search the radiology exam catalogue. READ ONLY, bounded in SQL.

        Clinical and reference fields only. hospital.radiology.exam carries
        billing_service_id once hospital_billing is installed -- the mapping
        that decides what a scan costs -- and it is never serialized.

        Reference data with no patient in it, so there is nothing to scope:
        hospital_radiology already grants Hospital Doctor read on
        hospital.radiology.exam, and nothing here widens that.
        """
        env = request.env
        _require_doctor_desk(env)

        search = (params.get("q") or "").strip()
        limit = RAD_CATALOGUE_DEFAULT_LIMIT
        if params.get("limit"):
            limit = max(
                1,
                min(
                    parse_int_param("limit", params["limit"]),
                    RAD_CATALOGUE_MAX_LIMIT,
                ),
            )

        model = env["hospital.radiology.exam"]
        # ORDERABLE ONLY, AND THIS MATTERS MORE HERE THAN IT DID FOR THE LAB.
        # The desk confirms on submission, and confirmation refuses the whole
        # order if any exam is unmapped or misconfigured -- and most of the
        # shipped radiology catalogue carries no billing service at all. Without
        # this predicate the picker would be mostly traps. It is the MODEL's,
        # mirroring _assert_billable; this controller does not know what makes
        # an exam billable and does not decide it. Archived exams are excluded
        # by the ORM's own active_test.
        domain = model.doctor_orderable_domain()
        if search:
            domain = expression.AND(
                [
                    domain,
                    [
                        "|", "|",
                        ("name", "ilike", search),
                        ("code", "ilike", search),
                        ("body_part", "ilike", search),
                    ],
                ]
            )

        exams = model.search(domain, limit=limit, order="name")
        return success_response(
            {
                "exams": [serialize_radiology_exam(exam) for exam in exams],
                "query": search or None,
                "limit": limit,
                "truncated": len(exams) == limit,
            }
        )

    # ------------------------------------------------------------------
    # 21. Consultation results -- read (Laboratory + Radiology)
    # ------------------------------------------------------------------
    @http.route(
        "/yoya-emr/api/v1/doctor/visits/<int:appointment_id>/results",
        type="http", auth="user", methods=["GET"], csrf=False,
    )
    @doctor_endpoint
    def results_review(self, appointment_id, **params):
        """Released laboratory and radiology findings for this visit.

        THE ONLY VERB IS GET, and that is the whole design. Reporting,
        validating and releasing belong to the laboratory and the imaging
        department; the Doctor Desk reviews what they have handed off and can
        do nothing else to it. There is no acknowledge route, no reviewed
        flag and no sign-off, because no model records any of those and an
        endpoint would be asserting something no record supports.

        BOTH SERVICES IN ONE PAYLOAD. The Results tab always renders
        laboratory and radiology together, so splitting them would buy two
        round trips, two loading states and two failure modes for one screen.

        KEYED ON THE CONSULTATION, NOT THE APPOINTMENT STATE -- and here that
        matters more than anywhere else in this controller. Chasing a result is
        precisely what a doctor does AFTER the visit is finished: laboratory
        and imaging routinely outlive the consultation that ordered them, and
        Slice 4's completion policy explicitly does not wait for either. A
        completed consultation, a done appointment and a closed encounter all
        keep reading here.

        NO SUDO, ANYWHERE. Requests are found through the caller's own record
        rules and results are reached through request.result_ids, so Slice 7A's
        laboratory-result rules and Slice 5's radiology-result rules are the
        security boundary. A doctor sees the results of their own visits
        because the ORM says so, not because this function filtered.
        """
        env = request.env
        _require_doctor_desk(env)

        appointment = _load_visit(env, appointment_id)
        consultation = env["hospital.consultation"].find_for_appointment(appointment)
        if not consultation:
            # A visit that never opened a consultation ordered nothing, so it
            # has nothing to report. An empty payload, exactly as the orders
            # endpoints answer -- not a 404 for a visit the doctor can see.
            return success_response({"laboratory": [], "radiology": []})

        return success_response(
            serialize_results(
                env["hospital.laboratory.request"].for_consultation(consultation),
                env["hospital.radiology.request"].for_consultation(consultation),
            )
        )

    # ------------------------------------------------------------------
    # 17. Consultation radiology orders -- read
    # ------------------------------------------------------------------
    @http.route(
        "/yoya-emr/api/v1/doctor/visits/<int:appointment_id>/orders/radiology",
        type="http", auth="user", methods=["GET"], csrf=False,
    )
    @doctor_endpoint
    def radiology_order_list(self, appointment_id, **params):
        """The radiology orders placed in this visit's consultation.

        Keyed on the CONSULTATION, not the appointment state, so orders stay
        readable after the visit finishes -- which is exactly when a doctor
        chases a pending study. That is not incidental for radiology: imaging
        routinely outlives the consultation that ordered it, and Slice 4's
        completion policy explicitly does not wait for it. `can_order` reports
        whether new orders may still be placed.
        """
        env = request.env
        _require_doctor_desk(env)

        appointment = _load_visit(env, appointment_id)
        consultation = env["hospital.consultation"].find_for_appointment(appointment)
        if not consultation:
            return success_response({"orders": [], "can_order": False})

        orders = env["hospital.radiology.request"].for_consultation(consultation)
        return success_response(
            serialize_radiology_orders(orders, consultation.state == "draft")
        )

    # ------------------------------------------------------------------
    # 18. Consultation radiology orders -- place
    # ------------------------------------------------------------------
    @http.route(
        "/yoya-emr/api/v1/doctor/visits/<int:appointment_id>/orders/radiology",
        type="http", auth="user", methods=["POST"], csrf=False,
    )
    @doctor_endpoint
    def radiology_order_create(self, appointment_id, **params):
        """Place a radiology order. THE CONTROLLER CREATES NO CHARGE.

        It resolves the visit, the consultation, the exams and the optional
        diagnosis through the CALLER's own record rules, then hands records to
        one model method. Everything financial belongs to hospital_billing's
        action_confirm_request() override, which create_from_consultation()
        invokes: validating every exam's billing configuration before raising
        any charge, resolving the encounter, asserting patient/appointment/
        encounter agreement, and creating one charge per ordered study
        all-or-nothing.

        ATOMICITY. The create, the confirmation, the charges it raises, the
        reload and the response are ONE savepoint. A failure while serializing
        must not leave a confirmed request with live charges behind a message
        saying the order was not placed -- the doctor would re-order and the
        patient would be billed twice.

        `request_token` IS REQUIRED. Unlike every other field on this body it is
        refused when absent rather than defaulted, because it is the only replay
        protection an imaging order has: see
        _require_radiology_request_token().
        """
        env = request.env
        _require_doctor_desk(env)

        appointment = _load_visit(env, appointment_id)
        body = read_json_body()
        values = _build_radiology_values(body)

        raw_exams = body.get("exams") or body.get("exam_ids") or []
        if not isinstance(raw_exams, list) or not raw_exams:
            raise ApiError(
                "invalid_field",
                "'exams' must be a non-empty list of radiology exam ids.",
                400,
            )
        exam_ids = []
        for entry in raw_exams:
            # Accept either a bare id or {"exam_id": n}, so the client may send
            # the shape it already holds without reshaping it.
            candidate = entry.get("exam_id") if isinstance(entry, dict) else entry
            exam_id = coerce_optional_id("exam_id", candidate)
            if not exam_id:
                raise ApiError(
                    "invalid_field", "Each entry in 'exams' needs an exam id.", 400
                )
            exam_ids.append(exam_id)

        diagnosis_id = coerce_optional_id("diagnosis_id", body.get("diagnosis_id"))
        # REQUIRED, and checked here -- before the consultation is resolved and
        # long before the savepoint -- so a submission with no replay protection
        # is refused having touched nothing at all.
        request_token = _require_request_token(
            body, "ordering the same studies -- and billing them -- twice"
        )

        consultation = _load_open_consultation(env, appointment)

        # DE-DUPLICATED BEFORE THE EXISTENCE CHECK. recordset.exists() preserves
        # duplicate ids, so browsing [7, 7, 9] returns three records and a naive
        # length comparison would report a perfectly valid exam as missing. The
        # model de-duplicates again when building the lines; this is about
        # answering "does every id you sent exist", not about the ordered set.
        unique_exam_ids = list(dict.fromkeys(exam_ids))
        exams = env["hospital.radiology.exam"].browse(unique_exam_ids).exists()
        if len(exams) != len(unique_exam_ids):
            raise ApiError(
                "radiology_exam_not_found",
                "One or more radiology exams were not found in the catalogue.",
                404,
            )

        diagnosis = env["hospital.patient.diagnosis"].browse(diagnosis_id).exists()
        if diagnosis_id and not diagnosis:
            raise ApiError(
                "diagnosis_not_found",
                "Diagnosis not found for this consultation.",
                404,
            )

        with env.cr.savepoint():
            env["hospital.radiology.request"].create_from_consultation(
                consultation,
                exams,
                values,
                diagnosis=diagnosis or None,
                # Passed straight through, with no `or None` fallback: the token
                # is already guaranteed non-empty above, and a fallback here
                # would quietly re-open the tokenless path this endpoint exists
                # to close.
                request_token=request_token,
            )
            try:
                response = success_response(_radiology_payload(env, consultation))
            except Exception as error:
                _logger.exception(
                    "Doctor radiology order response failed for appointment=%s "
                    "uid=%s; rolling the request and its charges back",
                    appointment_id,
                    env.uid,
                )
                raise RadiologyResponseError(str(error)) from error

        return response

    # ------------------------------------------------------------------
    # 19. Consultation radiology orders -- cancel
    # ------------------------------------------------------------------
    @http.route(
        "/yoya-emr/api/v1/doctor/visits/<int:appointment_id>"
        "/orders/radiology/<int:order_id>/cancel",
        type="http", auth="user", methods=["POST"], csrf=False,
    )
    @doctor_endpoint
    def radiology_order_cancel(self, appointment_id, order_id, **params):
        """Cancel a radiology order through the model's own workflow.

        The base transition guard permits cancellation only from draft,
        requested or scheduled; hospital_billing's override then cancels the
        operational charges and refuses outright if any has been delivered.
        None of that is reimplemented, and the refusal reaches the doctor with
        its own wording, because that sentence is the only thing that says WHY.

        THE CHARGE CLEANUP IS NOT OPTIONAL HERE. Radiology raises its charges at
        confirmation, so a cancelled order that left them live would leave the
        patient owing for a scan nobody will ever perform -- and sitting in the
        cashier's queue with nothing to collect against.
        """
        env = request.env
        _require_doctor_desk(env)

        appointment = _load_visit(env, appointment_id)
        consultation = _load_open_consultation(env, appointment)
        order = _load_radiology_order(env, consultation, order_id)

        with env.cr.savepoint():
            order.cancel_from_consultation()
            try:
                response = success_response(_radiology_payload(env, consultation))
            except Exception as error:
                _logger.exception(
                    "Doctor radiology cancel response failed for order=%s "
                    "uid=%s; rolling the cancellation back",
                    order_id,
                    env.uid,
                )
                raise RadiologyResponseError(str(error)) from error

        return response

    # ------------------------------------------------------------------
    # 20. Medicine catalogue -- search
    # ------------------------------------------------------------------
    @http.route(
        "/yoya-emr/api/v1/doctor/catalogue/medicines",
        type="http", auth="user", methods=["GET"], csrf=False,
    )
    @doctor_endpoint
    def medicine_catalogue(self, **params):
        """Search the medicine catalogue. READ ONLY, bounded in SQL.

        Clinical and reference fields only. hospital.pharmacy.medicine carries
        `sale_price` on the record itself once the fiscal bridge is installed,
        plus `billing_service_id` and `inventory_item_id`; none of the three is
        ever serialized.

        ORDERABLE ONLY, AND THIS MATTERS MORE HERE THAN FOR EITHER SIBLING. The
        medicine picker predicts TWO downstream gates, not one: the pharmacist's
        Mark Ready, which refuses an unmapped billing service, and Validate
        Dispense, which refuses a missing inventory item. The second is why this
        is not cosmetic -- a billable-but-unstocked medicine gets prescribed,
        priced, AND PAID FOR before anything refuses, so offering one would be a
        way to take money for medication that cannot be handed over. The
        predicate is the MODEL's; this controller does not know what makes a
        medicine orderable and does not decide it.

        Reference data with no patient in it, so there is nothing to scope:
        hospital_pharmacy already grants Hospital Doctor read on
        hospital.pharmacy.medicine, and nothing here widens that.
        """
        env = request.env
        _require_doctor_desk(env)

        search = (params.get("q") or "").strip()
        limit = MED_CATALOGUE_DEFAULT_LIMIT
        if params.get("limit"):
            limit = max(
                1,
                min(
                    parse_int_param("limit", params["limit"]),
                    MED_CATALOGUE_MAX_LIMIT,
                ),
            )

        model = env["hospital.pharmacy.medicine"]
        domain = model.doctor_orderable_domain()
        if search:
            domain = expression.AND(
                [
                    domain,
                    [
                        "|", "|", "|",
                        ("name", "ilike", search),
                        ("code", "ilike", search),
                        ("generic_name", "ilike", search),
                        ("brand_name", "ilike", search),
                    ],
                ]
            )

        medicines = model.search(domain, limit=limit, order="name")
        return success_response(
            {
                "medicines": [serialize_medicine(m) for m in medicines],
                "query": search or None,
                "limit": limit,
                "truncated": len(medicines) == limit,
            }
        )

    # ------------------------------------------------------------------
    # 21. Consultation prescriptions -- read
    # ------------------------------------------------------------------
    @http.route(
        "/yoya-emr/api/v1/doctor/visits/<int:appointment_id>/orders/medications",
        type="http", auth="user", methods=["GET"], csrf=False,
    )
    @doctor_endpoint
    def medication_order_list(self, appointment_id, **params):
        """The prescriptions written in this visit's consultation.

        Keyed on the CONSULTATION, not the appointment state, so prescriptions
        stay readable after the visit finishes -- which is exactly when a doctor
        checks whether the patient ever collected them. That is not incidental
        for medication: the ordinary outpatient shape is that the doctor signs
        off, the patient walks to the cashier and then to the pharmacy, so the
        dispense routinely outlives the consultation. `can_order` reports
        whether new prescriptions may still be written.
        """
        env = request.env
        _require_doctor_desk(env)

        appointment = _load_visit(env, appointment_id)
        consultation = env["hospital.consultation"].find_for_appointment(appointment)
        if not consultation:
            return success_response({"prescriptions": [], "can_order": False})

        prescriptions = env["hospital.prescription"].for_consultation(consultation)
        return success_response(
            serialize_prescriptions(prescriptions, consultation.state == "draft")
        )

    # ------------------------------------------------------------------
    # 22. Consultation prescriptions -- write
    # ------------------------------------------------------------------
    @http.route(
        "/yoya-emr/api/v1/doctor/visits/<int:appointment_id>/orders/medications",
        type="http", auth="user", methods=["POST"], csrf=False,
    )
    @doctor_endpoint
    def medication_order_create(self, appointment_id, **params):
        """Write a prescription. THE CONTROLLER CREATES NOTHING DOWNSTREAM.

        It resolves the visit, the consultation and the optional diagnosis
        through the CALLER's own record rules, then hands them to one model
        method. create_from_consultation() re-checks every medicine against
        doctor_orderable_domain(), builds the lines, and calls action_confirm()
        -- where hospital_pharmacy composes exactly one draft dispense.

        NOTHING FINANCIAL HAPPENS HERE OR ANYWHERE BELOW THIS CALL, and that is
        a real difference from the laboratory and radiology endpoints. Those
        raise charges at confirmation. Medication is billed later, by the
        PHARMACIST, at Mark Ready, using the quantity they intend to hand over.
        So this endpoint creates no charge, resolves no encounter, checks no
        clearance and touches no stock -- there is nothing yet to touch.

        ONE SUBMISSION, ONE PRESCRIPTION, HOWEVER MANY MEDICINES. The domain
        models a prescription as a header with many lines, and
        unique(prescription_id) on the dispense means one prescription becomes
        one dispense. Writing one prescription per medicine would send the
        patient to the counter once per drug.

        ATOMICITY. The create, the confirmation, the dispense it composes, the
        reload and the response are ONE savepoint. A failure while serializing
        must not leave a confirmed prescription and a live pharmacy dispense
        behind a message saying nothing was written -- the doctor would
        re-prescribe and the patient would be dispensed twice.

        `request_token` IS REQUIRED. Unlike every other field on this body it is
        refused when absent rather than defaulted, because it is the only replay
        protection a prescription has.
        """
        env = request.env
        _require_doctor_desk(env)

        appointment = _load_visit(env, appointment_id)
        body = read_json_body()
        header = _build_medication_header(body)
        entries = _build_medicine_entries(body)

        diagnosis_id = coerce_optional_id("diagnosis_id", body.get("diagnosis_id"))
        # REQUIRED, and checked here -- before the consultation is resolved and
        # long before the savepoint -- so a submission with no replay protection
        # is refused having touched nothing at all.
        request_token = _require_request_token(
            body, "prescribing the same medicines twice"
        )

        consultation = _load_open_consultation(env, appointment)

        diagnosis = env["hospital.patient.diagnosis"].browse(diagnosis_id).exists()
        if diagnosis_id and not diagnosis:
            raise ApiError(
                "diagnosis_not_found",
                "Diagnosis not found for this consultation.",
                404,
            )

        with env.cr.savepoint():
            env["hospital.prescription"].create_from_consultation(
                consultation,
                entries,
                header,
                diagnosis=diagnosis or None,
                # Passed straight through, with no `or None` fallback: the token
                # is already guaranteed non-empty above, and a fallback here
                # would quietly re-open the tokenless path this endpoint exists
                # to close.
                request_token=request_token,
            )
            try:
                response = success_response(_medication_payload(env, consultation))
            except Exception as error:
                _logger.exception(
                    "Doctor prescription response failed for appointment=%s "
                    "uid=%s; rolling the prescription and its dispense back",
                    appointment_id,
                    env.uid,
                )
                raise MedicationResponseError(str(error)) from error

        return response

    # ------------------------------------------------------------------
    # 23. Consultation prescriptions -- cancel
    # ------------------------------------------------------------------
    @http.route(
        "/yoya-emr/api/v1/doctor/visits/<int:appointment_id>"
        "/orders/medications/<int:prescription_id>/cancel",
        type="http", auth="user", methods=["POST"], csrf=False,
    )
    @doctor_endpoint
    def medication_order_cancel(self, appointment_id, prescription_id, **params):
        """Cancel a prescription through the model's own workflow.

        Slice 6A made action_cancel() authoritative for all of this, and none of
        it is reimplemented here: the base guard permits cancellation only from
        draft or confirmed, hospital_pharmacy then refuses outright if the
        linked dispense is partial or dispensed, cancels the dispense when it is
        draft or ready, and hospital_billing cancels any medication charges the
        pharmacist had already raised. Its refusal reaches the doctor with its
        own wording, because that sentence is the only thing that says WHY.

        THE CHARGE CLEANUP MATTERS EVEN THOUGH PRESCRIBING RAISES NO CHARGE. By
        the time a doctor thinks to cancel, the pharmacist may well have marked
        the dispense ready -- and that is the moment the charges appear. A
        cancellation that left them live would strand the visit in the cashier's
        SERVICE PAYMENTS lane, collecting for medication nobody will hand over.
        """
        env = request.env
        _require_doctor_desk(env)

        appointment = _load_visit(env, appointment_id)
        consultation = _load_open_consultation(env, appointment)
        prescription = _load_prescription(env, consultation, prescription_id)

        with env.cr.savepoint():
            prescription.cancel_from_consultation()
            try:
                response = success_response(_medication_payload(env, consultation))
            except Exception as error:
                _logger.exception(
                    "Doctor prescription cancel response failed for "
                    "prescription=%s uid=%s; rolling the cancellation back",
                    prescription_id,
                    env.uid,
                )
                raise MedicationResponseError(str(error)) from error

        return response
