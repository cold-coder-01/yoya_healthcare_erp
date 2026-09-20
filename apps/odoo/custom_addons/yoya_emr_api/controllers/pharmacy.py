"""Pharmacy Desk API.

    GET  /yoya-emr/api/v1/pharmacy/session
    GET  /yoya-emr/api/v1/pharmacy/worklist
    GET  /yoya-emr/api/v1/pharmacy/dispenses/<id>
    POST /yoya-emr/api/v1/pharmacy/dispenses/<id>/prepare    (Slice 2)
    POST /yoya-emr/api/v1/pharmacy/dispenses/<id>/validate   (Slice 2)

THE CONTROLLER DECIDES NOTHING ABOUT WORKFLOW. Each mutation checks the role,
resolves the dispense through the caller's own record rules, and hands the
payload to the model's private _desk_prepare() / _desk_validate(), which own
locking, idempotency, revision checks, billing, stock and state. The controller
writes no field itself. Cancellation, returns and substitution are not
registered. Nothing here calls sudo(): every record is read through the
caller's own ACLs and record rules. The places that need elevated reads --
billing clearance and mapping validity -- are model methods owned by
hospital_billing and hospital_inventory that return booleans only.

ONE SAVEPOINT PER MUTATION. The model call, the response serialization and the
operation row all happen inside it; business refusals are mapped to fixed codes
OUTSIDE it, after everything has rolled back. Serialization and lock failures
are re-raised untouched so Odoo's HTTP layer replays the whole request in a
fresh transaction -- where the idempotency record or the new revision answers
it.

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
import uuid

from psycopg2 import IntegrityError

from odoo import http
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.http import request
from odoo.osv import expression
from odoo.service.model import PG_CONCURRENCY_EXCEPTIONS_TO_RETRY

from odoo.addons.hospital_pharmacy.models.pharmacy_authority import (
    MUTATION_ERROR_MESSAGES,
    PharmacyWorkflowError,
)

from ..services.api_response import (
    ApiError,
    api_error_response,
    error_response,
    parse_date,
    parse_int_param,
    read_json_body,
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


# ---------------------------------------------------------------------------
# Mutations (Pharmacy Slice 2)
# ---------------------------------------------------------------------------
MUTATION_STATUS = {
    "pharmacy_desk_not_authorized": 403,
    "pharmacy_dispense_not_found": 404,
    "pharmacy_invalid_payload": 400,
    "pharmacy_operation_token_required": 400,
    "pharmacy_idempotency_conflict": 409,
    "pharmacy_dispense_revision_conflict": 409,
    "pharmacy_dispense_state_conflict": 409,
    "pharmacy_dispense_needs_review": 409,
    "pharmacy_concurrent_conflict": 409,
    "pharmacy_quantity_invalid": 422,
    "pharmacy_duplicate_line": 422,
    "pharmacy_no_positive_increment": 422,
    "pharmacy_billing_mapping_missing": 422,
    "pharmacy_inventory_mapping_missing": 422,
    "pharmacy_charge_conflict": 422,
    "pharmacy_billing_blocked": 422,
    "pharmacy_stock_insufficient": 422,
    "pharmacy_mutation_response_failed": 500,
}

PREPARE_KEYS = frozenset({"operation_token", "expected_revision", "lines"})
VALIDATE_KEYS = frozenset({"operation_token", "expected_revision"})


def _mutation_error(code):
    return ApiError(code, MUTATION_ERROR_MESSAGES[code], MUTATION_STATUS[code])


def _require_pharmacy_mutation(env):
    """The role gate, BEFORE the dispense is resolved: a refused caller learns
    nothing about whether the id exists."""
    if not may_pharmacy_desk(env):
        raise _mutation_error("pharmacy_desk_not_authorized")


def _mutation_body(required_keys):
    """Exactly the allowed keys; anything else is refused before a row is read.

    A missing or blank token has its own code so a client can tell "you forgot
    the token" from "the request is malformed". Everything else about the token,
    the revision and the lines is validated by the model.
    """
    try:
        body = read_json_body()
    except ApiError:
        raise _mutation_error("pharmacy_invalid_payload") from None
    token = body.get("operation_token")
    if token is None or (isinstance(token, str) and not token.strip()):
        raise _mutation_error("pharmacy_operation_token_required")
    if set(body) != required_keys:
        raise _mutation_error("pharmacy_invalid_payload")
    return body


def _load_dispense_for_mutation(env, dispense_id):
    if dispense_id <= 0:
        raise _mutation_error("pharmacy_dispense_not_found")
    record = env["hospital.pharmacy.dispense"].search([("id", "=", dispense_id)], limit=1)
    if not record:
        raise _mutation_error("pharmacy_dispense_not_found")
    return record


def _desk_policy(dispense):
    """THE DESK POLICY, run by the model under its locks: a record the queue
    shows as `anomaly` is never mutated, whatever its state says."""
    lane, _reason, _facts = classify(dispense)
    if lane == "anomaly":
        raise PharmacyWorkflowError("pharmacy_dispense_needs_review")


def _canonical_token(raw):
    try:
        return str(uuid.UUID(raw.strip()))
    except (AttributeError, ValueError):
        return raw


def _run_mutation(env, record, operation_type, token, call):
    """ONE savepoint: model workflow + response serialization + operation row.

    Mapped AFTER the savepoint has rolled back, so a refusal can say "nothing
    was changed" and mean it. No exception text is ever forwarded.
    """

    def serialize(dispense):
        return serialize_dispense_detail(dispense, may_mutate=True)

    try:
        with env.cr.savepoint():
            payload, replayed = call(record, _desk_policy, serialize)
    except PG_CONCURRENCY_EXCEPTIONS_TO_RETRY:
        raise
    except PharmacyWorkflowError as error:
        raise _mutation_error(error.code) from None
    except IntegrityError:
        # A unique constraint lost a race the locks did not cover (a charge
        # source key, a token on another dispense). Rolled back; refresh.
        _logger.warning("Pharmacy %s integrity conflict on dispense=%s", operation_type, record.id)
        raise _mutation_error("pharmacy_concurrent_conflict") from None
    except AccessError:
        raise _mutation_error("pharmacy_desk_not_authorized") from None
    except (UserError, ValidationError):
        # A deeper layer refused in its own words -- which may name amounts or
        # batches. Logged server-side, answered with the fixed sentence.
        _logger.warning(
            "Pharmacy %s refused for dispense=%s uid=%s", operation_type, record.id,
            env.uid, exc_info=True,
        )
        raise _mutation_error("pharmacy_mutation_response_failed") from None
    except Exception:
        _logger.exception("Pharmacy %s failed for dispense=%s", operation_type, record.id)
        raise _mutation_error("pharmacy_mutation_response_failed") from None

    return success_response({
        "dispense": payload,
        "capabilities": pharmacy_desk_capability_flags(env),
        "workflow_revision": payload["workflow_revision"],
        "operation": {"type": operation_type, "token": token, "replayed": bool(replayed)},
    })


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
        may_mutate = may_pharmacy_desk(env)
        rows = [
            serialize_queue_row(record, result, may_mutate)
            for record, result in matching[:limit]
        ]

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
            "dispense": serialize_dispense_detail(record, may_mutate=may_pharmacy_desk(env)),
            "capabilities": pharmacy_desk_capability_flags(env),
        })

    @http.route(
        "%s/dispenses/<int:dispense_id>/prepare" % PHARMACY_API,
        type="http", auth="user", methods=["POST"], csrf=False,
    )
    @pharmacy_endpoint
    def pharmacy_dispense_prepare(self, dispense_id, **params):
        """Set every line's CUMULATIVE intended quantity, bill it, make Ready.

        Body: {"operation_token": uuid, "expected_revision": int,
               "lines": [{"line_id": int, "intended_quantity": number}, ...]}
        Every current line exactly once. Nothing else is accepted.
        """
        env = request.env
        _require_pharmacy_mutation(env)
        body = _mutation_body(PREPARE_KEYS)
        record = _load_dispense_for_mutation(env, dispense_id)
        return _run_mutation(
            env, record, "prepare", _canonical_token(body["operation_token"]),
            lambda dispense, policy, serialize: dispense._desk_prepare(
                body["lines"], body["operation_token"], body["expected_revision"],
                policy_check=policy, serialize=serialize,
            ),
        )

    @http.route(
        "%s/dispenses/<int:dispense_id>/validate" % PHARMACY_API,
        type="http", auth="user", methods=["POST"], csrf=False,
    )
    @pharmacy_endpoint
    def pharmacy_dispense_validate(self, dispense_id, **params):
        """Hand over exactly the prepared increment. No quantities accepted.

        Body: {"operation_token": uuid, "expected_revision": int}
        """
        env = request.env
        _require_pharmacy_mutation(env)
        body = _mutation_body(VALIDATE_KEYS)
        record = _load_dispense_for_mutation(env, dispense_id)
        return _run_mutation(
            env, record, "validate", _canonical_token(body["operation_token"]),
            lambda dispense, policy, serialize: dispense._desk_validate(
                body["operation_token"], body["expected_revision"],
                policy_check=policy, serialize=serialize,
            ),
        )
