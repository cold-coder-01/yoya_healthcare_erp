"""Laboratory Desk API: the bench's work queue and one request, READ ONLY.

SLICE 1 IMPLEMENTS NO TRANSITION. There is no POST route in this module, and
that is the whole design. Sample collection, result entry, validation and
release are authoritative model methods on hospital.laboratory.request and
hospital.laboratory.result; they arrive in later slices, each calling exactly
one of those methods. Nothing here writes, and nothing here calls sudo().

THREE INDEPENDENT CONTROLS, IN THIS ORDER
-----------------------------------------
  1. auth="user"          an unauthenticated caller never reaches the handler
  2. _require_lab_desk()  the ROLE gate: may this user open the bench at all
  3. Odoo record rules    which rows the caller's own ORM lets them read

The second is not decoration on top of the third, and it is the reason this
module exists rather than a couple of routes bolted onto controllers/doctor.py.
hospital_management ships a read ACL on hospital.laboratory.request and
hospital.laboratory.result for the NURSE, the RECEPTIONIST and the DPO, with no
record rule narrowing any of them -- so the ORM alone would happily serve the
whole hospital's bench queue to the front desk. That exposure is pre-existing
and is deliberately NOT changed here (touching those rules is a separate
decision with its own blast radius). What this module does is refuse to
inherit it: /lab/* answers 403 for every role outside LAB_DESK_GROUPS, whatever
the ACL would have permitted.

So a nurse is refused by the gate, not by an empty list. Those two are very
different answers -- "not your workstation" versus "no work today" -- and
returning the wrong one is how a desk quietly becomes a second door into data
somebody already decided the front office should not be browsing.

The session route uses the same role gate. The shell can explain a 403 without
receiving a successful session payload for an excluded role.

WHAT IS DERIVED, AND WHERE
--------------------------
`awaiting_clearance` and `ready_for_collection` are NOT database states.
hospital.laboratory.request.state has exactly six values and neither is among
them; both are `requested`, split by hospital_billing's `billing_blocked`
compute. That derivation lives in services/lab_desk_serializers.lab_desk_status
-- ONE definition, used by the queue, by the detail panel and by the status
filter -- so the API and the browser can never disagree about what the bench is
looking at. Nothing derived is ever written back to Odoo.
"""
import functools
import logging

from odoo import http
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
from ..services.lab_desk_serializers import (
    LAB_DESK_ACTIVE_STATUSES,
    LAB_DESK_STATUS_STATE,
    LAB_DESK_STATUSES,
    lab_desk_status,
    serialize_request_detail,
    serialize_session,
    serialize_worklist,
)
from ..services.reception_scope import (
    lab_desk_capability_flags,
    may_lab_desk,
)

_logger = logging.getLogger(__name__)

LAB_API = "/yoya-emr/api/v1/lab"

WORKLIST_LIMIT_DEFAULT = 100
WORKLIST_LIMIT_MAX = 300


def lab_endpoint(func):
    """Stable error envelope for the Laboratory Desk. Never leaks a traceback.

    Ordering mirrors controllers/doctor.doctor_endpoint, and it matters for the
    same reason: in Odoo both AccessError and ValidationError subclass
    UserError, so the broad handler has to come last or it swallows the two
    specific ones and reports an authorization failure as a workflow refusal.

    Slice 1 has no mutation, so there is no response-failure type and no
    savepoint here -- a read that fails to serialize has nothing to roll back.
    Those branches belong with the slices that introduce a write.
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
        except AccessError as error:
            # Reaching here means the ORM refused a row the role gate had
            # already admitted -- a record rule, not the desk gate. Odoo's own
            # sentence is forwarded because it names which boundary refused.
            _logger.warning(
                "Laboratory desk endpoint %s denied for uid=%s",
                func.__name__,
                request.env.uid,
            )
            return error_response("access_denied", str(error), 403)
        except ValidationError as error:
            return error_response("validation_error", str(error), 400)
        except UserError as error:
            return error_response("invalid_workflow_state", str(error), 422)
        except Exception:
            _logger.exception(
                "Unexpected error in YOYA laboratory endpoint %s", func.__name__
            )
            return error_response(
                "internal_error", "An unexpected error occurred.", 500
            )

    return wrapper


def _require_lab_desk(env):
    """Fail fast for LAB_DESK_GROUPS. THE desk gate.

    Deliberately raised BEFORE any record is touched, and deliberately a 403
    rather than an empty list: a nurse who holds a read ACL on the laboratory
    models would otherwise get a populated queue, and a role that has no
    business at this workstation would have been handed one by the ORM.
    """
    if not may_lab_desk(env):
        raise ApiError(
            "lab_desk_not_authorized",
            "Laboratory Desk access requires the Hospital Lab Technician, "
            "Hospital Manager or Hospital System Administrator role.",
            403,
        )


def _limit_param(raw):
    if raw in (None, "", False):
        return WORKLIST_LIMIT_DEFAULT
    limit = parse_int_param("limit", raw)
    if limit < 1 or limit > WORKLIST_LIMIT_MAX:
        raise ApiError(
            "invalid_limit",
            "'limit' must be between 1 and %s." % WORKLIST_LIMIT_MAX,
            400,
        )
    return limit


def _requested_statuses(raw):
    """The bench statuses asked for, validated against the allowlist.

    An unknown value is a 400, never a silently empty queue: a client asking
    for a status this API does not have is a bug in the client, and answering
    "no work" would hide it behind a screen that looks merely quiet.
    """
    if not raw:
        return LAB_DESK_ACTIVE_STATUSES
    requested = tuple(part.strip() for part in raw.split(",") if part.strip())
    unknown = [item for item in requested if item not in LAB_DESK_STATUSES]
    if unknown:
        raise ApiError(
            "invalid_status",
            "Unknown laboratory status(es): %s. Valid values: %s."
            % (", ".join(sorted(unknown)), ", ".join(LAB_DESK_STATUSES)),
            400,
        )
    return requested or LAB_DESK_ACTIVE_STATUSES


def _worklist_domain(statuses, day, search):
    """ONE domain over STORED columns, evaluated once in SQL.

    THE STATUS FILTER NARROWS IN THE DATABASE, via LAB_DESK_STATUS_STATE. Two
    display statuses share the `requested` state, so this can only get as far
    as the state; the caller refines `awaiting_clearance` versus
    `ready_for_collection` per row afterwards, because `billing_blocked` is a
    non-stored compute and cannot appear in a domain at all.

    THE DEFAULT IS ACTIVE BENCH WORK, not "everything". Draft, completed and
    cancelled requests are reachable only by asking for them by name, so the
    ordinary queue is the work in front of the technician.

    `active` IS LEFT TO THE ORM. Odoo's own active_test excludes archived
    requests from every search, and restating it here would be a second
    definition that could drift from it.
    """
    states = sorted({LAB_DESK_STATUS_STATE[status] for status in statuses})
    domain = [("state", "in", states)]

    if day:
        # request_date is a Date, so this is a plain equality on a stored
        # column -- no timezone conversion, and none is wanted: the laboratory
        # request records the ORDERING DAY as a calendar date, not an instant.
        domain.append(("request_date", "=", day))

    if search:
        # Scalar identity fields only. Deliberately NOT the clinical note or
        # the instructions: a bench search box is for finding a specimen's
        # paperwork, and making free-text clinical narrative searchable across
        # every patient in the hospital is a different feature with a different
        # confidentiality question behind it.
        domain = expression.AND(
            [
                domain,
                [
                    "|", "|",
                    ("name", "ilike", search),
                    ("patient_id.name", "ilike", search),
                    ("patient_id.identification_code", "ilike", search),
                ],
            ]
        )
    return domain


def _prefetch_worklist(requests):
    """Warm the caches the serializer walks, in one read per relation.

    Odoo prefetches a field across a whole recordset the first time it is
    touched on any record of it, so reading each relation once here is what
    keeps a hundred-row queue at a fixed number of queries instead of one per
    row per field.

    `billing_blocked` IS DELIBERATELY NOT PREFETCHED, because it cannot be: it
    is a non-stored compute that asks hospital.billing.engine for a clearance
    verdict per encounter, and there is no batched form of that question. That
    is precisely why the worklist is bounded by `limit` and why the default
    filter is active bench work rather than the whole table.
    """
    if not requests:
        return
    requests.mapped("patient_id.name")
    requests.mapped("physician_id.name")
    requests.mapped("encounter_id.department_id.name")
    requests.mapped("line_ids.test_id.name")
    requests.mapped("result_ids.state")


def _status_counts(rows):
    """One count per bench status, over the rows actually returned.

    Every status is present with a zero rather than omitted, so a client can
    render a fixed set of tabs without branching on which keys came back.
    """
    counts = {status: 0 for status in LAB_DESK_STATUSES}
    for row in rows:
        counts[row["status"]] = counts.get(row["status"], 0) + 1
    return counts


class YoyaEmrLaboratoryController(http.Controller):

    # ------------------------------------------------------------------
    # 1. Session
    # ------------------------------------------------------------------
    @http.route(
        "%s/session" % LAB_API,
        type="http", auth="user", methods=["GET"], csrf=False,
    )
    @lab_endpoint
    def lab_session(self, **params):
        """Who is signed in and whether the bench opens for them.

        All /lab/* routes require a bench role, including this identity lookup.
        """
        env = request.env
        _require_lab_desk(env)
        return success_response(serialize_session(env, lab_desk_capability_flags(env)))

    # ------------------------------------------------------------------
    # 2. Worklist
    # ------------------------------------------------------------------
    @http.route(
        "%s/worklist" % LAB_API,
        type="http", auth="user", methods=["GET"], csrf=False,
    )
    @lab_endpoint
    def worklist(self, **params):
        """The bench queue.

        TWO PASSES, and they are not interchangeable:

          SQL     bounded by stored columns (state, request_date, patient) and
                  by the caller's own record rules. This is the security and
                  cost boundary.
          Python  billing_blocked resolved per row, splitting `requested` into
                  awaiting_clearance / ready_for_collection. It cannot be a
                  domain: the field is not stored.

        ORDERED OLDEST FIRST. A bench queue is FIFO -- the specimen that has
        waited longest is the one at risk -- so this is `request_date asc, id
        asc` rather than the model's newest-first default. Priority is carried
        on every row as a badge and is deliberately NOT the sort key: `priority`
        is a Selection, so ordering by it in SQL would sort alphabetically
        (routine, stat, urgent) and quietly put STAT in the middle.

        `truncated` MEANS THE WINDOW WAS FULL, and it is honest in both passes.
        The SQL fetch asks for limit + 1; when a status filter then splits the
        `requested` state, the page can come back shorter than `limit` with
        `truncated` still true. That is the correct answer -- there IS more
        beyond this window -- and the client's response is to narrow the
        filters, which is what the banner says.

        AWAITING CLEARANCE IS NEVER SILENTLY DROPPED. It is in the default
        status set, because an order the bench cannot yet draw is still the
        bench's business: the technician is the person the patient asks.
        """
        env = request.env
        _require_lab_desk(env)

        statuses = _requested_statuses(params.get("status"))
        limit = _limit_param(params.get("limit"))
        # THE DAY IS OPTIONAL, unlike the Doctor and Cashier worklists. Those
        # queues are appointment-driven and a visit belongs to one day; bench
        # work does not expire at midnight, and a specimen ordered yesterday is
        # still uncollected this morning. Defaulting to today would hide it.
        day = parse_date(params["date"]) if params.get("date") else None
        search = (params.get("q") or "").strip() or None

        domain = _worklist_domain(statuses, day, search)

        candidates = env["hospital.laboratory.request"].search(
            domain, order="request_date asc, id asc", limit=limit + 1
        )
        truncated = len(candidates) > limit
        if truncated:
            candidates = candidates[:limit]

        _prefetch_worklist(candidates)

        wanted = set(statuses)
        # The Python refinement. `serialize_worklist` recomputes the status per
        # row through the SAME lab_desk_status(), so the filter and the label a
        # row carries cannot disagree.
        rows = candidates.filtered(lambda req: lab_desk_status(req) in wanted)

        capabilities = lab_desk_capability_flags(env)
        payload = serialize_worklist(
            rows,
            filters={
                "date": day.isoformat() if day else None,
                "status": list(statuses),
                "q": search,
                "limit": limit,
            },
            meta={
                "row_count": len(rows),
                "truncated": truncated,
                "statuses": list(LAB_DESK_STATUSES),
                "default_statuses": list(LAB_DESK_ACTIVE_STATUSES),
            },
            capabilities=capabilities,
        )
        # Counts describe exactly the rows the client received, so a tab count
        # and the list under it can never describe different populations.
        payload["counts"] = _status_counts(payload["rows"])
        return success_response(payload)

    # ------------------------------------------------------------------
    # 3. Request detail
    # ------------------------------------------------------------------
    @http.route(
        "%s/requests/<int:request_id>" % LAB_API,
        type="http", auth="user", methods=["GET"], csrf=False,
    )
    @lab_endpoint
    def request_detail(self, request_id, **params):
        """One laboratory request, read-only.

        THE GATE RUNS BEFORE THE RECORD IS TOUCHED. A caller outside
        LAB_DESK_GROUPS gets 403 without this endpoint disclosing whether
        `request_id` exists at all.

        Past the gate, a request that does not exist and one the caller's
        record rules hide are BOTH `lab_request_not_found`, 404. The two are
        deliberately indistinguishable: answering 403 for the second would turn
        this route into a probe that confirms which request ids are real.

        RESOLVED BY search(), NOT browse().exists(). The difference is the
        whole point: browse() applies no record rule, and exists() checks the
        row in raw SQL, so a hidden record would come back present and fail
        later with an AccessError -- a 403 that says "this id is real, but not
        yours". search() applies the caller's own rules in the query, so an
        unreadable request is simply absent and answers 404 like any other id
        that is not theirs to see. It also inherits the ORM's active_test, so
        an archived request is not reachable here either.
        """
        env = request.env
        _require_lab_desk(env)

        if request_id <= 0:
            raise ApiError(
                "invalid_request_id", "Laboratory request ID is invalid.", 400
            )

        record = env["hospital.laboratory.request"].search(
            [("id", "=", request_id)], limit=1
        )
        if not record:
            raise ApiError(
                "lab_request_not_found", "Laboratory request not found.", 404
            )

        return success_response(
            {
                "request": serialize_request_detail(record),
                "capabilities": lab_desk_capability_flags(env),
            }
        )
