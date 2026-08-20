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
)
from odoo.addons.yoya_clinical_bridge.models.patient_diagnosis import (
    DIAGNOSIS_EDITABLE_FIELDS,
    DiagnosisPrimaryConflict,
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

        if appointment.state != CONSULTATION_APPOINTMENT_STATE:
            return success_response(
                serialize_consultation_envelope(
                    env["hospital.consultation"].browse(),
                    available=False,
                    reason=CONSULTATION_NOT_STARTED_REASON,
                )
            )

        consultation = _load_consultation(env, appointment)
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
        """
        env = request.env
        _require_doctor_desk(env)

        appointment = _load_visit(env, appointment_id)
        body = read_json_body()
        values = _build_diagnosis_values(body, require_type=True)

        disease_id = coerce_optional_id("disease_id", body.get("disease_id"))
        if not disease_id:
            raise ApiError("invalid_field", "'disease_id' is required.", 400)
        request_token = coerce_text("request_token", body.get("request_token"))

        consultation = _load_open_consultation(env, appointment)

        disease = env["hospital.disease"].browse(disease_id).exists()
        if not disease:
            raise ApiError(
                "disease_not_found", "Diagnosis not found in the catalogue.", 404
            )

        with env.cr.savepoint():
            env["hospital.patient.diagnosis"].add_to_consultation(
                consultation, disease, values, request_token=request_token or None
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
