"""Doctor Desk API: the clinician's worklist and the consultation gate.

Reads: the doctor's bounded working day and the selected-patient panel.

Writes: exactly one, and it decides nothing --

    POST .../visits/<id>/start-consultation

which loads the appointment through the caller's own scope and calls
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

from ..services.api_response import (
    ApiError,
    api_error_response,
    error_response,
    parse_date,
    parse_int_param,
    success_response,
)
from ..services.clinical_scope import (
    find_appointment_in_scope,
    scoped_appointment_domain,
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


def doctor_endpoint(func):
    """Stable error envelope. Never leaks a traceback.

    Ordering matters twice over. ConsultationResponseError is caught FIRST so a
    post-write serialization failure cannot be reported as an authorization
    denial. And in Odoo AccessError and ValidationError both subclass
    UserError, so the broad handler has to come last.
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
        """THE ONLY WRITE ON THIS SURFACE, AND IT DECIDES NOTHING.

        Loads the appointment through the caller's legitimate scope and calls
        action_start_consultation(). It does NOT write appointment.state, does
        NOT write encounter.state, does NOT re-check triage, does NOT re-check
        financial clearance and does NOT sudo the mutation. Every one of those
        belongs to the model, which enforces them whatever this route allows.

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
