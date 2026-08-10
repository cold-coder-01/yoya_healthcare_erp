"""Front Desk API: the nurse-staffed hospital entrance.

Read-only in this phase. Registration, triage and payment already have
authoritative endpoints; this adds the consolidated worklist and the
selected-visit panel the dense workstation needs, and nothing else.

The controller is deliberately thin: it validates query parameters, composes ONE
bounded domain, hands the recordset to the serializer, and returns. It makes no
workflow decision, writes nothing, and never calls sudo().
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

        # search() rather than browse(): a record the user's rules exclude must
        # be a 404, not an AccessError that leaks its existence.
        appointment = env["hospital.appointment"].search(
            [("id", "=", appointment_id)], limit=1
        )
        if not appointment:
            raise ApiError("visit_not_found", "Visit not found.", 404)

        prefetch_worklist(appointment)
        capabilities = front_desk_capability_flags(env)
        return success_response(
            serialize_front_desk_visit(appointment, capabilities)
        )
