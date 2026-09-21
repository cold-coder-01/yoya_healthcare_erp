"""Admissions Desk API (Admissions Slice 1). READ ONLY.

    GET /yoya-emr/api/v1/admissions/session
    GET /yoya-emr/api/v1/admissions/worklist   ?lane= &ward_id= &q= &limit=
    GET /yoya-emr/api/v1/admissions/<id>
    GET /yoya-emr/api/v1/admissions/wards
    GET /yoya-emr/api/v1/admissions/beds       ?ward_id= &room_id= &state= &q=

NO ROUTE HERE WRITES. Admit, assign bed, transfer, discharge and cancel are not
registered; every capability flag for them is False. Nothing here creates an
audit row either -- viewing is not an act the audit trail records.

THREE INDEPENDENT CONTROLS, IN THIS ORDER
-----------------------------------------
  1. auth="user"                an unauthenticated caller never reaches a handler
  2. _require_admissions_desk   the ROLE gate (reception_scope.ADMISSIONS_DESK_GROUPS)
  3. Odoo record rules          which admissions the caller's own ORM returns

The gate runs BEFORE any record is read, so a refused caller learns nothing --
not a count, not whether an id exists.

NO sudo() SEARCH OF ADMISSIONS. The only elevated reads are the encounter
integrity facts and the amount-free clearance verdict, both inside
services/admissions_desk_serializers.py and documented there.

WHERE THE LANES AND COUNTS COME FROM
------------------------------------
services/admissions_desk_serializers.classify_batch(): every admission in the
filter scope is classified ONCE, up to SUMMARY_SCAN_MAX, and the rows, the lane
filter and the lane counts are all derived from that one pass.
"""
import functools
import logging

from odoo import http
from odoo.exceptions import AccessError, UserError
from odoo.http import request
from odoo.osv import expression
from odoo.service.model import PG_CONCURRENCY_EXCEPTIONS_TO_RETRY

from ..services.admissions_desk_serializers import (
    ACTIVE_LANES,
    BED_STATES,
    LANE_ORDER,
    bed_row_text,
    build_bed_board,
    build_ward_rollups,
    classify_batch,
    serialize_detail,
    serialize_row,
    serialize_session,
    transfer_counts,
)
from ..services.api_response import (
    ApiError,
    api_error_response,
    error_response,
    parse_int_param,
    success_response,
)
from ..services.reception_scope import (
    admissions_desk_capability_flags,
    admissions_desk_role_flags,
    may_admissions_desk,
)

_logger = logging.getLogger(__name__)

ADMISSIONS_API = "/yoya-emr/api/v1/admissions"

WORKLIST_LIMIT_DEFAULT = 100
WORKLIST_LIMIT_MAX = 300

# How many admissions one worklist call will classify. Past the cap the counts
# come back null and `summary_exact` is False: a dash on a badge is honest, a
# guessed number is not. The client's answer is to narrow by ward or search.
SUMMARY_SCAN_MAX = 1000

ACCESS_DENIED_MESSAGE = "Access to this admission record was refused."
LOAD_FAILED_MESSAGE = "The admissions desk could not load this information."


def admissions_endpoint(func):
    """Stable error envelope. Never leaks a traceback or a record name."""

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        try:
            if request.env.user._is_public():
                return error_response(
                    "authentication_required", "Authentication is required.", 401
                )
            return func(*args, **kwargs)
        except PG_CONCURRENCY_EXCEPTIONS_TO_RETRY:
            raise
        except ApiError as error:
            return api_error_response(error)
        except AccessError:
            _logger.warning(
                "Admissions desk endpoint %s denied for uid=%s", func.__name__, request.env.uid
            )
            return error_response("access_denied", ACCESS_DENIED_MESSAGE, 403)
        except UserError:
            _logger.warning(
                "Admissions desk endpoint %s refused for uid=%s", func.__name__,
                request.env.uid, exc_info=True,
            )
            return error_response("invalid_request", LOAD_FAILED_MESSAGE, 422)
        except Exception:
            _logger.exception("Unexpected error in YOYA admissions endpoint %s", func.__name__)
            return error_response("internal_error", "An unexpected error occurred.", 500)

    return wrapper


def _require_admissions_desk(env):
    """THE desk gate, raised BEFORE any record is touched."""
    if not may_admissions_desk(env):
        raise ApiError(
            "admissions_desk_not_authorized",
            "The Admissions Desk is open to reception, nursing, doctors, hospital "
            "managers and system administrators.",
            403,
        )


def _optional_id(name, raw):
    if raw in (None, "", False):
        return None
    value = parse_int_param(name, raw)
    if value <= 0:
        raise ApiError("invalid_%s" % name, "'%s' must be a positive id." % name, 400)
    return value


def _limit_param(raw):
    if raw in (None, "", False):
        return WORKLIST_LIMIT_DEFAULT
    limit = parse_int_param("limit", raw)
    if limit < 1 or limit > WORKLIST_LIMIT_MAX:
        raise ApiError(
            "invalid_limit", "'limit' must be between 1 and %s." % WORKLIST_LIMIT_MAX, 400
        )
    return limit


def _requested_lanes(raw):
    if not raw:
        return ACTIVE_LANES
    requested = tuple(part.strip() for part in raw.split(",") if part.strip())
    if requested == ("all",):
        return LANE_ORDER
    unknown = [lane for lane in requested if lane not in LANE_ORDER]
    if unknown:
        raise ApiError(
            "invalid_lane",
            "Unknown admission lane(s): %s. Valid values: %s, or all."
            % (", ".join(sorted(unknown)), ", ".join(LANE_ORDER)),
            400,
        )
    return requested or ACTIVE_LANES


def _worklist_domain(ward_id, search):
    """Ward and search over stored identity columns. Nothing financial.
    Evaluated under the caller's record rules like any other search."""
    domain = []
    if ward_id:
        domain.append(("ward_id", "=", ward_id))
    if search:
        domain = expression.AND([
            domain,
            [
                "|", "|", "|", "|", "|", "|", "|", "|", "|",
                ("name", "ilike", search),
                ("patient_id.name", "ilike", search),
                ("patient_id.identification_code", "ilike", search),
                ("physician_id.name", "ilike", search),
                ("bed_id.code", "ilike", search),
                ("bed_id.name", "ilike", search),
                ("room_id.name", "ilike", search),
                ("room_id.code", "ilike", search),
                ("ward_id.name", "ilike", search),
                ("ward_id.code", "ilike", search),
            ],
        ])
    return domain


def _sort_key(item):
    """Lane order first, then the most recent admission first."""
    admission, lane, _reasons, _encounter = item
    stamp = admission.admission_date or admission.create_date
    return (LANE_ORDER.index(lane), -(stamp.timestamp() if stamp else 0), -admission.id)


class YoyaEmrAdmissionsController(http.Controller):

    @http.route("%s/session" % ADMISSIONS_API, type="http", auth="user", methods=["GET"], csrf=False)
    @admissions_endpoint
    def admissions_session(self, **params):
        env = request.env
        _require_admissions_desk(env)
        return success_response(
            serialize_session(
                env, admissions_desk_role_flags(env), admissions_desk_capability_flags(env)
            )
        )

    @http.route("%s/worklist" % ADMISSIONS_API, type="http", auth="user", methods=["GET"], csrf=False)
    @admissions_endpoint
    def admissions_worklist(self, **params):
        """The census. The summary describes the WHOLE ward + search scope and
        ignores the selected lane, so choosing a lane never zeroes the others."""
        env = request.env
        _require_admissions_desk(env)

        lanes = _requested_lanes(params.get("lane"))
        limit = _limit_param(params.get("limit"))
        ward_id = _optional_id("ward_id", params.get("ward_id"))
        search = (params.get("q") or "").strip() or None

        # active_test=False: an archived admission is still part of the census
        # history and must be counted in its lane, not silently dropped.
        scope = env["hospital.admission"].with_context(active_test=False).search(
            _worklist_domain(ward_id, search), order="id desc", limit=SUMMARY_SCAN_MAX + 1
        )
        exact = len(scope) <= SUMMARY_SCAN_MAX
        if not exact:
            _logger.warning(
                "Admissions worklist: more than %s admissions in scope; counts reported as unavailable.",
                SUMMARY_SCAN_MAX,
            )
            scope = scope[:SUMMARY_SCAN_MAX]

        classified = classify_batch(scope)

        summary = {lane: 0 for lane in LANE_ORDER}
        for _admission, lane, _reasons, _encounter in classified:
            summary[lane] += 1
        summary["active"] = sum(summary[lane] for lane in ACTIVE_LANES)
        summary["total"] = len(classified)
        if not exact:
            summary = {key: None for key in summary}

        wanted = set(lanes)
        matching = sorted((item for item in classified if item[1] in wanted), key=_sort_key)
        page = matching[:limit]
        truncated = (not exact) or len(matching) > limit

        admissions = env["hospital.admission"].browse([item[0].id for item in page])
        counts = transfer_counts(admissions)
        rows = [
            serialize_row(admission, lane, reasons, encounter, counts.get(admission.id, 0))
            for admission, lane, reasons, encounter in page
        ]

        return success_response({
            "rows": rows,
            "summary": summary,
            "filters": {
                "lane": list(lanes),
                "ward_id": ward_id,
                "q": search,
                "limit": limit,
            },
            "meta": {
                "row_count": len(rows),
                "total_visible": len(classified) if exact else None,
                "truncated": truncated,
                "summary_exact": exact,
                "lanes": list(LANE_ORDER),
                "default_lanes": list(ACTIVE_LANES),
                "summary_filters": ["ward_id", "q"],
            },
            "capabilities": admissions_desk_capability_flags(env),
        })

    @http.route("%s/wards" % ADMISSIONS_API, type="http", auth="user", methods=["GET"], csrf=False)
    @admissions_endpoint
    def admissions_wards(self, **params):
        """Ward -> room hierarchy with bed rollups summed from the bed board."""
        env = request.env
        _require_admissions_desk(env)
        bed_rows = build_bed_board(env)
        return success_response({
            "wards": build_ward_rollups(env, bed_rows),
            "capabilities": admissions_desk_capability_flags(env),
        })

    @http.route("%s/beds" % ADMISSIONS_API, type="http", auth="user", methods=["GET"], csrf=False)
    @admissions_endpoint
    def admissions_beds(self, **params):
        env = request.env
        _require_admissions_desk(env)

        ward_id = _optional_id("ward_id", params.get("ward_id"))
        room_id = _optional_id("room_id", params.get("room_id"))
        state = (params.get("state") or "").strip() or None
        if state and state not in BED_STATES + ("needs_review",):
            raise ApiError(
                "invalid_state",
                "Unknown bed state. Valid values: %s, needs_review." % ", ".join(BED_STATES),
                400,
            )
        search = (params.get("q") or "").strip().lower() or None

        domain = []
        if ward_id:
            domain.append(("ward_id", "=", ward_id))
        if room_id:
            domain.append(("room_id", "=", room_id))
        if state and state != "needs_review":
            domain.append(("state", "=", state))

        rows = build_bed_board(env, domain)
        if state == "needs_review":
            rows = [row for row in rows if row["needs_review"]]
        # AFTER redaction, over what each row already shows -- see
        # admissions_desk_serializers "BED BOARD PRIVACY".
        if search:
            rows = [row for row in rows if search in bed_row_text(row)]

        return success_response({
            "beds": rows,
            "filters": {"ward_id": ward_id, "room_id": room_id, "state": state, "q": search},
            "meta": {"row_count": len(rows)},
            "capabilities": admissions_desk_capability_flags(env),
        })

    # Registered LAST and typed <int:>, so /session, /worklist, /wards and /beds
    # can never be read as an admission id.
    @http.route(
        "%s/<int:admission_id>" % ADMISSIONS_API,
        type="http", auth="user", methods=["GET"], csrf=False,
    )
    @admissions_endpoint
    def admissions_detail(self, admission_id, **params):
        """One admission. search(), not browse(): a record the caller's rules
        hide and a missing one are the same 404."""
        env = request.env
        _require_admissions_desk(env)
        if admission_id <= 0:
            raise ApiError("invalid_admission_id", "Admission ID is invalid.", 400)
        record = env["hospital.admission"].with_context(active_test=False).search(
            [("id", "=", admission_id)], limit=1
        )
        if not record:
            raise ApiError("admission_not_found", "Admission not found.", 404)
        return success_response({
            "admission": serialize_detail(record),
            "capabilities": admissions_desk_capability_flags(env),
        })
