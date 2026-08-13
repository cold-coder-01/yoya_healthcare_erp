"""Front Desk API: the nurse-staffed hospital entrance.

Reads: the consolidated worklist and the selected-visit panel the dense
workstation needs.

Writes: exactly two, both of which exist because no authoritative endpoint
covered them and the front desk cannot work without them:

    POST .../visits/<id>/start-triage   claims the nursing evaluation
    POST .../visits/<id>/doctor         assigns or changes the doctor

Neither writes a workflow state or a queue stage. Both delegate to an
authoritative model method (action_start_evaluation, assign_doctor) and then
re-serialize the visit, so the stage the client receives is the DERIVED one and
cannot disagree with the worklist. Registration, evaluation save, payment and
consultation start keep their existing endpoints and are not duplicated here.

The controller stays thin: it validates parameters, resolves records through the
caller's own record rules, calls one model method, and serializes. It makes no
workflow decision and never calls sudo().
"""
import functools
import logging

from psycopg2 import IntegrityError

from odoo import fields, http
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.http import request
from odoo.osv import expression

from ..services.api_response import (
    ApiError,
    api_error_response,
    coerce_optional_id,
    error_response,
    parse_date,
    parse_int_param,
    read_json_body,
    success_response,
)
from ..services.clinical_scope import latest_evaluation_for
from ..services.front_desk_serializers import (
    ACTIVE_STAGE_KEYS,
    STAGE_KEYS,
    prefetch_worklist,
    serialize_front_desk_visit,
    serialize_worklist_row,
    worklist_counts,
)
from ..services.reception_scope import (
    front_desk_capability_flags,
    hospital_day_bounds_utc,
    may_front_desk,
    may_intake,
    may_triage,
)

_logger = logging.getLogger(__name__)

# A day's arrivals at one hospital fit comfortably inside this. The cap exists so
# a malformed or hostile ?limit= cannot turn one request into a table scan.
DEFAULT_LIMIT = 200
MAX_LIMIT = 500


def front_desk_endpoint(func):
    """Stable error envelope. Never leaks a traceback."""

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
        except AccessError:
            _logger.warning(
                "Front desk endpoint %s denied for uid=%s",
                func.__name__,
                request.env.uid,
            )
            return error_response(
                "access_denied",
                "You are not allowed to perform this action.",
                403,
            )
        except ValidationError as error:
            return error_response("validation_error", str(error), 400)
        except UserError as error:
            return error_response("invalid_workflow_state", str(error), 422)
        except Exception:
            _logger.exception(
                "Unexpected error in YOYA front desk endpoint %s", func.__name__
            )
            return error_response(
                "internal_error", "An unexpected error occurred.", 500
            )

    return wrapper


def _require_front_desk(env):
    if not may_front_desk(env):
        raise ApiError(
            "access_denied",
            "Front desk access requires the Hospital Front Desk Nurse, Hospital "
            "Nurse, Receptionist, Hospital Manager or Hospital System "
            "Administrator role.",
            403,
        )


def _require_triage(env):
    """Mirrors hospital.patient.evaluation's ACL. Never the only control."""
    if not may_triage(env):
        raise ApiError(
            "access_denied",
            "Recording triage requires the Hospital Front Desk Nurse, Hospital "
            "Nurse, Doctor, Hospital Manager or Hospital System Administrator "
            "role.",
            403,
        )


def _require_intake(env):
    """Fail-fast for hospital.reception.workflow.REGISTRATION_GROUPS.

    _assert_group inside the workflow method enforces it again; this only turns
    it into a clean 403 instead of a bare AccessError.
    """
    if not may_intake(env):
        raise ApiError(
            "access_denied",
            "Changing a visit requires the Hospital Front Desk Nurse, "
            "Receptionist, Hospital Manager or Hospital System Administrator "
            "role.",
            403,
        )


def _load_visit(env, appointment_id):
    """Resolve a visit through the caller's own record rules.

    search() rather than browse(): a record the user's rules exclude must be a
    404, not an AccessError that leaks its existence.
    """
    appointment = env["hospital.appointment"].search(
        [("id", "=", appointment_id)], limit=1
    )
    if not appointment:
        raise ApiError("visit_not_found", "Visit not found.", 404)
    return appointment


def _visit_response(appointment):
    """THE canonical response for every front-desk visit endpoint.

    Re-serializing after a mutation is what keeps the client honest: the stage
    it renders is the derived one, computed from the records as they now stand,
    never a value the client guessed from the action it just took.
    """
    env = appointment.env
    prefetch_worklist(appointment)
    return success_response(
        serialize_front_desk_visit(appointment, front_desk_capability_flags(env))
    )


def _requested_stages(raw):
    """Parse ?stage=a,b. Unknown keys are an error, not a silent empty result."""
    if raw in (None, "", False):
        return list(ACTIVE_STAGE_KEYS)
    requested = [part.strip() for part in str(raw).split(",") if part.strip()]
    unknown = [key for key in requested if key not in STAGE_KEYS]
    if unknown:
        raise ApiError(
            "invalid_parameter",
            "Unknown stage(s): %s. Valid stages: %s."
            % (", ".join(sorted(unknown)), ", ".join(STAGE_KEYS)),
            400,
        )
    return requested or list(ACTIVE_STAGE_KEYS)


def _limit_param(raw):
    if raw in (None, "", False):
        return DEFAULT_LIMIT
    limit = parse_int_param("limit", raw)
    if limit <= 0:
        raise ApiError("invalid_parameter", "'limit' must be positive.", 400)
    return min(limit, MAX_LIMIT)


def _assert_startable(evaluation):
    if evaluation.state != "draft":
        raise ApiError(
            "triage_not_startable",
            "Evaluation %s is %s. Only a draft evaluation can be started; a "
            "completed triage must be reopened by a Hospital Manager first."
            % (evaluation.name, evaluation.state),
            409,
        )
    return evaluation


def _evaluation_to_start(env, appointment):
    """The draft evaluation for this visit, created if there is not one yet.

    CONCURRENCY. hospital.patient.evaluation carries unique(appointment_id), so
    two nurses double-clicking Start Triage on the same visit race on the
    INSERT. The loser must not see a 500: starting triage is idempotent by
    intent, and the winner's evaluation is exactly the record the loser wanted.

    The create is therefore wrapped in a savepoint so the failed INSERT rolls
    back without poisoning the request's transaction, and the loser re-reads and
    continues. Narrow on purpose: no advisory lock, no retry loop, no
    SELECT FOR UPDATE. The unique index is already the arbiter; this only
    translates its verdict into the right answer.

    A recovery that finds nothing is a genuine integrity failure and is
    re-raised untouched.
    """
    Evaluation = env["hospital.patient.evaluation"]

    evaluation = latest_evaluation_for(env, appointment)
    if evaluation:
        return _assert_startable(evaluation)

    values = {
        "patient_id": appointment.patient_id.id,
        "appointment_id": appointment.id,
        # assigned_nurse_id is deliberately ABSENT, not sent as False.
        #
        # The field carries default=lambda self: self.env.user, and Odoo applies
        # a default only when the key is missing from vals. Passing an explicit
        # False would suppress it and create an unowned evaluation --
        # action_start_evaluation would then claim it anyway, but the record
        # would be momentarily invisible to the very nurse creating it.
    }
    # Set once, at creation. hospital.patient.evaluation.write() refuses to
    # repoint encounter_id afterwards, so this is the only chance to link the
    # triage to the episode create_visit() opened.
    if appointment.encounter_id:
        values["encounter_id"] = appointment.encounter_id.id

    try:
        with env.cr.savepoint():
            return Evaluation.create(values)
    except IntegrityError:
        # The savepoint rolled back; the cursor is usable again. Drop the ORM
        # cache so the re-read is a real SELECT and not the phantom record the
        # failed create left behind.
        env.invalidate_all()
        evaluation = latest_evaluation_for(env, appointment)
        if not evaluation:
            raise
        _logger.info(
            "Front desk start-triage lost the unique(appointment_id) race for "
            "appointment %s; continuing with evaluation %s.",
            appointment.id,
            evaluation.id,
        )
        return _assert_startable(evaluation)


def _worklist_domain(env, day, department_id, doctor_id, search):
    """ONE domain, evaluated once in SQL.

    Deliberately does NOT filter by stage. The counters and the rows are both
    derived from a single fetch of the day, so narrowing the SQL window by the
    requested stage would make the counters describe a different population than
    the one they are counting -- the exact way a worklist starts lying.
    """
    start, end = hospital_day_bounds_utc(env, day)
    domain = [
        ("appointment_date", ">=", start),
        ("appointment_date", "<", end),
    ]
    if department_id:
        domain.append(("department_id", "=", department_id))
    if doctor_id:
        domain.append(("doctor_id", "=", doctor_id))
    if search:
        domain = expression.AND(
            [
                domain,
                [
                    "|",
                    ("patient_id.name", "ilike", search),
                    ("patient_id.identification_code", "ilike", search),
                ],
            ]
        )
    return domain


class YoyaEmrFrontDeskController(http.Controller):

    @http.route(
        "/yoya-emr/api/v1/front-desk/worklist",
        type="http", auth="user", methods=["GET"], csrf=False,
    )
    @front_desk_endpoint
    def worklist(self, **params):
        env = request.env
        _require_front_desk(env)

        day = (
            parse_date(params["date"])
            if params.get("date")
            else fields.Date.context_today(env["hospital.appointment"])
        )
        stages = _requested_stages(params.get("stage"))
        limit = _limit_param(params.get("limit"))
        department_id = (
            parse_int_param("department_id", params["department_id"])
            if params.get("department_id")
            else None
        )
        doctor_id = (
            parse_int_param("doctor_id", params["doctor_id"])
            if params.get("doctor_id")
            else None
        )
        search = (params.get("q") or "").strip() or None

        domain = _worklist_domain(env, day, department_id, doctor_id, search)

        Appointment = env["hospital.appointment"]
        # One search, one bounded read. Record rules apply normally: a plain
        # Hospital Nurse sees their departments, a Front Desk Nurse sees the
        # whole entrance queue.
        appointments = Appointment.search(
            domain, order="appointment_date asc, id asc", limit=limit + 1
        )
        truncated = len(appointments) > limit
        if truncated:
            appointments = appointments[:limit]

        prefetch_worklist(appointments)
        capabilities = front_desk_capability_flags(env)

        all_rows = [
            serialize_worklist_row(appointment, capabilities)
            for appointment in appointments
        ]
        # Counters describe the whole fetched day; rows are the requested slice.
        counts = worklist_counts(all_rows)
        requested = set(stages)
        rows = [row for row in all_rows if row["queue_stage"] in requested]

        return success_response(
            {
                "rows": rows,
                "counts": counts,
                "capabilities": capabilities,
                "filters": {
                    "date": day.isoformat(),
                    "stage": stages,
                    "department_id": department_id,
                    "doctor_id": doctor_id,
                    "q": search,
                    "limit": limit,
                },
                "meta": {
                    "row_count": len(rows),
                    "window_count": len(all_rows),
                    "truncated": truncated,
                    "stages": list(STAGE_KEYS),
                },
            }
        )

    @http.route(
        "/yoya-emr/api/v1/front-desk/visits/<int:appointment_id>",
        type="http", auth="user", methods=["GET"], csrf=False,
    )
    @front_desk_endpoint
    def visit_detail(self, appointment_id, **params):
        env = request.env
        _require_front_desk(env)
        return _visit_response(_load_visit(env, appointment_id))

    # ------------------------------------------------------------------
    # Start Triage
    # ------------------------------------------------------------------
    @http.route(
        "/yoya-emr/api/v1/front-desk/visits/<int:appointment_id>/start-triage",
        type="http", auth="user", methods=["POST"], csrf=False,
    )
    @front_desk_endpoint
    def start_triage(self, appointment_id, **params):
        """Claim the nursing evaluation. intake -> triage, with no payment gate.

        started_at is stamped by action_start_evaluation() and never written
        here; the queue stage is derived from it by
        hospital.appointment._resolve_front_desk_stage and is never written at
        all. Safe to call repeatedly: the action keeps the original timestamp
        and the original owner.
        """
        env = request.env
        _require_front_desk(env)
        _require_triage(env)

        appointment = _load_visit(env, appointment_id)
        if appointment.state != "confirmed":
            raise ApiError(
                "invalid_workflow_state",
                "Visit %s is %s. Triage can only be started once the patient is "
                "checked in."
                % (appointment.appointment_code or appointment.id, appointment.state),
                409,
            )

        _evaluation_to_start(env, appointment).action_start_evaluation()
        return _visit_response(appointment)

    # ------------------------------------------------------------------
    # Doctor assignment
    # ------------------------------------------------------------------
    @http.route(
        "/yoya-emr/api/v1/front-desk/visits/<int:appointment_id>/doctor",
        type="http", auth="user", methods=["POST"], csrf=False,
    )
    @front_desk_endpoint
    def assign_doctor(self, appointment_id, **params):
        """Assign or change the doctor. Body: {"doctor_id": <int>}.

        The appointment/encounter/evaluation synchronization rule lives in
        hospital.reception.workflow.assign_doctor(); this route only resolves
        the two records and hands them over.
        """
        env = request.env
        _require_front_desk(env)
        _require_intake(env)

        appointment = _load_visit(env, appointment_id)

        body = read_json_body()
        unknown = set(body) - {"doctor_id"}
        if unknown:
            raise ApiError(
                "unknown_field",
                "Unrecognised fields: %s." % ", ".join(sorted(unknown)),
                400,
            )

        doctor_id = coerce_optional_id("doctor_id", body.get("doctor_id"))
        if not doctor_id:
            raise ApiError("invalid_field", "'doctor_id' is required.", 400)

        doctor = env["hospital.doctor"].search([("id", "=", doctor_id)], limit=1)
        if not doctor:
            raise ApiError("doctor_not_found", "Doctor not found.", 404)

        env["hospital.reception.workflow"].assign_doctor(appointment, doctor)
        return _visit_response(appointment)
