"""Pharmacy Desk API (Pharmacy Slice 1): READ ONLY.

    GET /yoya-emr/api/v1/pharmacy/session
    GET /yoya-emr/api/v1/pharmacy/worklist
    GET /yoya-emr/api/v1/pharmacy/dispenses/<id>

NO MUTATION ROUTE EXISTS. Preparing quantities, Mark Ready, Validate Dispense and
cancellation are later slices; this module registers no POST and calls no
workflow method. Nothing here calls sudo(): every record is read through the
caller's own ACLs and record rules. The two places that need elevated reads --
billing clearance and mapping validity -- are model methods owned by
hospital_billing and hospital_inventory that return booleans only.

THREE INDEPENDENT CONTROLS, IN THIS ORDER
-----------------------------------------
  1. auth="user"               an unauthenticated caller never reaches a handler
  2. _require_pharmacy_desk    the ROLE gate (reception_scope.PHARMACY_DESK_GROUPS)
  3. Odoo record rules         which rows the caller's own ORM lets them read

The role gate is not decoration: Receptionist, Nurse, Accountant and the DPO
hold a read ACL on the dispense models with no record rule narrowing it, so the
ORM alone would hand them a populated counter queue.

WHERE THE LANES COME FROM
-------------------------
services/pharmacy_desk_serializers.classify(). Lanes need per-record facts
(billing and stock verdicts, quantity reconciliation) that are not SQL, so the
worklist classifies EVERY dispense in the filter scope once, up to
SUMMARY_SCAN_MAX, and derives the rows, the lane filter and the lane counts from
that single pass. The counts therefore always match what the rows would show.
"""
import functools
import logging

from odoo import http
from odoo.exceptions import AccessError, UserError
from odoo.http import request
from odoo.osv import expression
from odoo.service.model import PG_CONCURRENCY_EXCEPTIONS_TO_RETRY

from ..services.api_response import (
    ApiError,
    api_error_response,
    error_response,
    parse_date,
    parse_int_param,
    success_response,
)
from ..services.pharmacy_desk_serializers import (
    PHARMACY_DESK_ACTIVE_LANES,
    PHARMACY_DESK_LANES,
    PRIORITY_RANK,
    classify,
    serialize_dispense_detail,
    serialize_queue_row,
    serialize_session,
    serialize_worklist,
)
from ..services.reception_scope import (
    hospital_day_bounds_utc,
    may_pharmacy_desk,
    pharmacy_desk_capability_flags,
    pharmacy_desk_role_flags,
)

_logger = logging.getLogger(__name__)

PHARMACY_API = "/yoya-emr/api/v1/pharmacy"

WORKLIST_LIMIT_DEFAULT = 100
WORKLIST_LIMIT_MAX = 300

# How many dispenses one worklist call will classify. Classification asks the
# billing and stock modules per record, so it is bounded. Past the cap the
# counts come back null, `summary_exact` is False and `truncated` is True: a
# dash on a badge is honest, a guessed number is not. The client's answer is
# to narrow by date or search.
SUMMARY_SCAN_MAX = 500

# FIXED SENTENCES for failures. An exception's own text is never forwarded: a
# billing refusal can name amounts, and an access error can name records.
ACCESS_DENIED_MESSAGE = "Access to this pharmacy record was refused."
LOAD_FAILED_MESSAGE = "The pharmacy desk could not load this information."


def pharmacy_endpoint(func):
    """Stable error envelope for the Pharmacy Desk. Never leaks a traceback,
    an amount or a record name through an error message."""

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
                "Pharmacy desk endpoint %s denied for uid=%s", func.__name__, request.env.uid
            )
            return error_response("access_denied", ACCESS_DENIED_MESSAGE, 403)
        except UserError:
            _logger.warning(
                "Pharmacy desk endpoint %s refused for uid=%s", func.__name__,
                request.env.uid, exc_info=True,
            )
            return error_response("invalid_request", LOAD_FAILED_MESSAGE, 422)
        except Exception:
            _logger.exception("Unexpected error in YOYA pharmacy endpoint %s", func.__name__)
            return error_response("internal_error", "An unexpected error occurred.", 500)

    return wrapper


def _require_pharmacy_desk(env):
    """THE desk gate, raised BEFORE any record is touched."""
    if not may_pharmacy_desk(env):
        raise ApiError(
            "pharmacy_desk_not_authorized",
            "Pharmacy Desk access requires the Hospital Pharmacist, Hospital "
            "Manager or Hospital System Administrator role.",
            403,
        )


def _load_dispense(env, dispense_id):
    """search(), not browse(): a record the caller's rules hide, an archived
    one and a missing one are all the same 404."""
    if dispense_id <= 0:
        raise ApiError("invalid_dispense_id", "Pharmacy dispense ID is invalid.", 400)
    record = env["hospital.pharmacy.dispense"].search([("id", "=", dispense_id)], limit=1)
    if not record:
        raise ApiError("pharmacy_dispense_not_found", "Pharmacy dispense not found.", 404)
    return record


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
        return PHARMACY_DESK_ACTIVE_LANES
    requested = tuple(part.strip() for part in raw.split(",") if part.strip())
    unknown = [item for item in requested if item not in PHARMACY_DESK_LANES]
    if unknown:
        raise ApiError(
            "invalid_status",
            "Unknown pharmacy lane(s): %s. Valid values: %s."
            % (", ".join(sorted(unknown)), ", ".join(PHARMACY_DESK_LANES)),
            400,
        )
    return requested or PHARMACY_DESK_ACTIVE_LANES


def _filter_domain(env, day, search):
    """Date and search over stored columns. IDENTITY FIELDS ONLY in search:
    codes, patient, chart number, prescriber, medicine. Nothing financial."""
    domain = []
    if day:
        start, end = hospital_day_bounds_utc(env, day)
        domain += [("dispense_date", ">=", start), ("dispense_date", "<=", end)]
    if search:
        domain = expression.AND([
            domain,
            [
                "|", "|", "|", "|", "|",
                ("name", "ilike", search),
                ("prescription_id.name", "ilike", search),
                ("patient_id.name", "ilike", search),
                ("patient_id.identification_code", "ilike", search),
                ("prescription_id.physician_id.name", "ilike", search),
                ("line_ids.medicine_id.name", "ilike", search),
            ],
        ])
    return domain


def _sort_key(pair):
    record = pair[0]
    return (
        PRIORITY_RANK.get(record.priority, 2),
        record.dispense_date or record.create_date,
        record.id,
    )


class YoyaEmrPharmacyController(http.Controller):

    @http.route("%s/session" % PHARMACY_API, type="http", auth="user", methods=["GET"], csrf=False)
    @pharmacy_endpoint
    def pharmacy_session(self, **params):
        env = request.env
        _require_pharmacy_desk(env)
        return success_response(
            serialize_session(env, pharmacy_desk_role_flags(env), pharmacy_desk_capability_flags(env))
        )

    @http.route("%s/worklist" % PHARMACY_API, type="http", auth="user", methods=["GET"], csrf=False)
    @pharmacy_endpoint
    def pharmacy_worklist(self, **params):
        """The counter queue.

        ORDERED BY URGENCY, THEN OLDEST FIRST. `priority` is a Selection, so it
        is ranked in Python (emergency, urgent, routine) rather than sorted
        alphabetically in SQL.

        The summary describes the WHOLE date + search scope and ignores the
        selected lane, so choosing a lane never zeroes the other badges.
        """
        env = request.env
        _require_pharmacy_desk(env)

        lanes = _requested_lanes(params.get("status"))
        limit = _limit_param(params.get("limit"))
        day = parse_date(params["date"]) if params.get("date") else None
        search = (params.get("q") or "").strip() or None

        scope = env["hospital.pharmacy.dispense"].search(
            _filter_domain(env, day, search), order="id desc", limit=SUMMARY_SCAN_MAX + 1
        )
        exact = len(scope) <= SUMMARY_SCAN_MAX
        if not exact:
            _logger.warning(
                "Pharmacy worklist: more than %s dispenses in scope; counts reported as unavailable.",
                SUMMARY_SCAN_MAX,
            )
            scope = scope[:SUMMARY_SCAN_MAX]

        classified = [(record, classify(record)) for record in scope]

        summary = {lane: 0 for lane in PHARMACY_DESK_LANES}
        for _record, (lane, _reason, _facts) in classified:
            summary[lane] = summary.get(lane, 0) + 1
        summary["active"] = sum(summary[lane] for lane in PHARMACY_DESK_ACTIVE_LANES)
        summary["total"] = len(classified)
        if not exact:
            summary = {key: None for key in summary}

        wanted = set(lanes)
        matching = sorted(
            (pair for pair in classified if pair[1][0] in wanted), key=_sort_key
        )
        truncated = (not exact) or len(matching) > limit
        rows = [serialize_queue_row(record, result) for record, result in matching[:limit]]

        return success_response(
            serialize_worklist(
                rows,
                summary,
                filters={
                    "date": day.isoformat() if day else None,
                    "status": list(lanes),
                    "q": search,
                    "limit": limit,
                },
                meta={
                    "row_count": len(rows),
                    "truncated": truncated,
                    "summary_exact": exact,
                    "lanes": list(PHARMACY_DESK_LANES),
                    "default_lanes": list(PHARMACY_DESK_ACTIVE_LANES),
                    "summary_filters": ["date", "q"],
                },
                capabilities=pharmacy_desk_capability_flags(env),
            )
        )

    @http.route(
        "%s/dispenses/<int:dispense_id>" % PHARMACY_API,
        type="http", auth="user", methods=["GET"], csrf=False,
    )
    @pharmacy_endpoint
    def pharmacy_dispense_detail(self, dispense_id, **params):
        """One dispense, read-only. The gate runs before the record is touched,
        so a refused caller learns nothing about whether the id exists."""
        env = request.env
        _require_pharmacy_desk(env)
        record = _load_dispense(env, dispense_id)
        return success_response({
            "dispense": serialize_dispense_detail(record),
            "capabilities": pharmacy_desk_capability_flags(env),
        })
