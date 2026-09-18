"""Radiology Desk API: the imaging department's queue, one request, and the two
transitions the desk may perform.

    GET  /yoya-emr/api/v1/radiology/session
    GET  /yoya-emr/api/v1/radiology/worklist
    GET  /yoya-emr/api/v1/radiology/requests/<id>
    POST /yoya-emr/api/v1/radiology/requests/<id>/schedule  action_schedule()
    POST /yoya-emr/api/v1/radiology/requests/<id>/start     action_mark_in_progress()

EXACTLY TWO WRITES (Slice 2), AND THE CONTROLLER DECIDES NOTHING ABOUT WORKFLOW.
Each resolves the request through the caller's own record rules, locks its row,
re-checks the DESK POLICY under the lock, and calls ONE authoritative model
method inside one savepoint. No state is written here -- the request model now
refuses a direct state write from every channel -- and nothing calls sudo().
Reporting, images, validation, release, cancellation and stock consumption are
model methods this module still does not call and routes it does not register.

THREE INDEPENDENT CONTROLS, IN THIS ORDER
-----------------------------------------
  1. auth="user"            an unauthenticated caller never reaches the handler
  2. _require_radiology_desk the ROLE gate: may this user open the desk at all
  3. Odoo record rules      which rows the caller's own ORM lets them read

The second is not decoration on top of the third. Receptionist, Nurse and the
DPO hold a read ACL on every Radiology model with no record rule narrowing it,
and the Doctor holds read/write on their own requests, so the ORM alone would
hand a populated queue to four roles that have no business at this workstation.
/radiology/* answers 403 to every role outside RAD_DESK_GROUPS -- including the
session route, so the shell can explain a refusal without receiving a successful
payload for an excluded role.

WHAT IS DERIVED, AND WHERE
--------------------------
Every lane is DERIVED by services/rad_desk_serializers.rad_desk_lane() from the
request state, the operational result's state and hospital_billing's
`billing_blocked` boolean. That ONE function decides the row's lane, the lane
filter and the lane counts, so the three can never disagree. Nothing derived is
written back to Odoo.
"""
import functools
import logging

from odoo import http
from odoo.exceptions import AccessError, UserError, ValidationError
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
from ..services.rad_desk_serializers import (
    RAD_DESK_ACTIVE_LANES,
    RAD_DESK_ANOMALY_STATES,
    RAD_DESK_LANE_STATES,
    RAD_DESK_LANES,
    rad_desk_lane,
    selection_options,
    serialize_request_detail,
    serialize_session,
    serialize_worklist,
)
from ..services.reception_scope import (
    may_rad_desk,
    rad_desk_capability_flags,
    rad_desk_role_flags,
)

_logger = logging.getLogger(__name__)

RAD_API = "/yoya-emr/api/v1/radiology"

WORKLIST_LIMIT_DEFAULT = 100
WORKLIST_LIMIT_MAX = 300

# How many requested / scheduled / in-progress requests the summary will
# classify one by one before it stops claiming exact counts for their lanes.
#
# Those three states are the department's LIVE work and are bounded by nature.
# Completed and cancelled are not -- they grow forever -- which is why they are
# counted in SQL and never scanned. The per-row pass exists because two of the
# splits cannot be SQL: `billing_blocked` is a non-stored compute that asks the
# billing engine per encounter, and the result-state split needs the one
# operational result of each request.
#
# Past the cap the work-lane counts come back null and `meta.summary_exact` is
# False. A dash on a badge is honest; a guessed number is not.
SUMMARY_WORK_SCAN_MAX = 1000

# The states the worklist and summary ever read. Draft is never among them: a
# draft request is not imaging work, and the desk does not show it.
WORK_STATES = ("requested", "scheduled", "in_progress")
SUMMARY_STATES = WORK_STATES + ("completed", "cancelled")


class TransitionResponseError(Exception):
    """A transition ran, but its confirmation could not be built.

    Raised INSIDE the savepoint, so by the time the envelope answers, the
    transition, its audit rows, its charge moves and its encounter side effect
    have all been rolled back and the message may honestly say nothing changed.
    A separate type so a serialization AccessError is never reported as a
    denial -- the reason laboratory.TransitionResponseError exists.
    """

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message


def rad_endpoint(func):
    """Stable error envelope for the Radiology Desk. Never leaks a traceback.

    Ordering mirrors controllers/laboratory.lab_endpoint: in Odoo AccessError and
    ValidationError both subclass UserError, so the broad handler comes last.
    Concurrency failures are re-raised so Odoo's own retry can replay the whole
    request in a fresh transaction -- the row lock taken by a transition relies
    on that replay.
    """

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
        except TransitionResponseError as error:
            return error_response(error.code, error.message, 500)
        except AccessError as error:
            # The role gate admitted the caller and the ORM still refused a row
            # -- a record rule, not the desk gate.
            _logger.warning(
                "Radiology desk endpoint %s denied for uid=%s",
                func.__name__,
                request.env.uid,
            )
            return error_response("access_denied", str(error), 403)
        except ValidationError as error:
            return error_response("validation_error", str(error), 400)
        except UserError as error:
            return error_response("invalid_request", str(error), 422)
        except Exception:
            _logger.exception(
                "Unexpected error in YOYA radiology endpoint %s", func.__name__
            )
            return error_response(
                "internal_error", "An unexpected error occurred.", 500
            )

    return wrapper


def _require_radiology_desk(env):
    """Fail fast for RAD_DESK_GROUPS. THE desk gate.

    Raised BEFORE any record is touched, and a 403 rather than an empty list: a
    nurse holding a read ACL on the radiology models would otherwise receive a
    populated queue, and "not your workstation" would read as "no work today".
    """
    if not may_rad_desk(env):
        raise ApiError(
            "radiology_desk_not_authorized",
            "Radiology Desk access requires the Hospital Radiology Technician, "
            "Hospital Radiologist, Hospital Manager or Hospital System "
            "Administrator role.",
            403,
        )


def _load_request(env, request_id):
    """Resolve one radiology request through the CALLER's own record rules.

    search(), not browse().exists(): browse applies no record rule, so a hidden
    record would come back present and fail later with an AccessError that
    confirms the id is real. search() makes an unreadable request simply absent,
    and inherits active_test, so an archived request is unreachable too. Both
    answer the same 404.
    """
    if request_id <= 0:
        raise ApiError(
            "invalid_request_id", "Radiology request ID is invalid.", 400
        )
    record = env["hospital.radiology.request"].search(
        [("id", "=", request_id)], limit=1
    )
    if not record:
        raise ApiError(
            "radiology_request_not_found", "Radiology request not found.", 404
        )
    return record


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


def _requested_lanes(raw):
    """The lanes asked for, validated against the allowlist.

    An unknown lane is a 400, never a silently empty queue. `draft` is not a
    lane anyone may ask for.
    """
    if not raw:
        return RAD_DESK_ACTIVE_LANES
    requested = tuple(part.strip() for part in raw.split(",") if part.strip())
    unknown = [item for item in requested if item not in RAD_DESK_LANES]
    if unknown:
        raise ApiError(
            "invalid_status",
            "Unknown radiology lane(s): %s. Valid values: %s."
            % (", ".join(sorted(unknown)), ", ".join(RAD_DESK_LANES)),
            400,
        )
    return requested or RAD_DESK_ACTIVE_LANES


def _modality_param(env, raw):
    """A modality from the MODEL's own selection, or None.

    Validated against hospital.radiology.exam.modality as installed, so a value
    added to that selection is accepted without touching this module and an
    invented one is refused.
    """
    value = (raw or "").strip()
    if not value:
        return None
    allowed = [
        option["value"]
        for option in selection_options(env["hospital.radiology.exam"], "modality")
    ]
    if value not in allowed:
        raise ApiError(
            "invalid_modality",
            "Unknown modality: %s. Valid values: %s." % (value, ", ".join(allowed)),
            400,
        )
    return value


def _filter_domain(day, search, modality):
    """The COMMON filters -- date, search, modality -- over stored columns.

    Shared by the rows and the summary, so the two cannot describe different
    scopes.

    `request_date` is a Date: the ordering day. There is no scheduled date on
    the model, so none is invented.

    THE SEARCH COVERS IDENTITY FIELDS ONLY: request code, patient name, chart
    number, exam name and code, ordering doctor. Not clinical indication, not
    report text, and nothing financial -- a queue search box is for finding a
    study, not for searching narrative across every patient in the hospital.

    `active` IS LEFT TO THE ORM, whose active_test already excludes archived
    requests from every search.
    """
    domain = []
    if day:
        domain.append(("request_date", "=", day))
    if modality:
        # A request with ANY active study of this modality.
        domain.append(
            (
                "line_ids",
                "any",
                [("state", "!=", "cancelled"), ("exam_id.modality", "=", modality)],
            )
        )
    if search:
        domain = expression.AND(
            [
                domain,
                [
                    "|", "|", "|", "|", "|",
                    ("name", "ilike", search),
                    ("patient_id.name", "ilike", search),
                    ("patient_id.identification_code", "ilike", search),
                    ("line_ids.exam_id.name", "ilike", search),
                    ("line_ids.exam_id.code", "ilike", search),
                    ("physician_id.name", "ilike", search),
                ],
            ]
        )
    return domain


def _conflicted_request_ids(env, filters):
    """Ids of requests in scope with MORE THAN ONE operational report.

    One SQL GROUP BY over hospital.radiology.result, run as the caller, so it
    counts exactly what operational_results() sees per row: active results (the
    ORM's active_test) that are not cancelled, under the caller's record rules.

    WHY THIS EXISTS AS SQL. A conflict can sit on a COMPLETED request, and
    completed requests grow forever. Finding them by scanning rows would either
    be unbounded or would fill the worklist's paged window with completed rows
    that are then discarded. Normally the set is empty.
    """
    scope = expression.AND([filters, [("state", "in", list(RAD_DESK_ANOMALY_STATES))]])
    groups = env["hospital.radiology.result"]._read_group(
        [("state", "!=", "cancelled"), ("request_id", "any", scope)],
        ["request_id"],
        ["__count"],
        having=[("__count", ">", 1)],
    )
    return [record.id for record, _count in groups]


def _lane_domain(lanes, conflicted_ids):
    """The SQL shape of the requested lanes, before the per-row refinement."""
    parts = []
    states = sorted(
        {
            state
            for lane in lanes
            if lane != "anomaly"
            for state in RAD_DESK_LANE_STATES[lane]
        }
    )
    if states:
        parts.append([("state", "in", states)])
    if "anomaly" in lanes:
        parts.append([("state", "=", "in_progress")])
        if conflicted_ids:
            parts.append(
                [
                    ("state", "in", list(RAD_DESK_ANOMALY_STATES)),
                    ("id", "in", conflicted_ids),
                ]
            )
    return expression.OR(parts)


def _prefetch(requests):
    """Warm the caches the serializer walks, one read per relation.

    `billing_blocked` cannot be prefetched: it is a non-stored compute that asks
    the billing engine per encounter. That is why the queue is bounded by
    `limit` and the summary's per-row pass is capped.
    """
    if not requests:
        return
    requests.mapped("patient_id.name")
    requests.mapped("patient_id.identification_code")
    requests.mapped("physician_id.name")
    requests.mapped("line_ids.exam_id.name")
    requests.mapped("result_ids.state")
    requests.mapped("result_ids.image_ids")


def _worklist_summary(env, filters, conflicted_ids):
    """One count per lane over the WHOLE filter scope. Never the page.

    THE LANE IS IGNORED, the date, search and modality are not. Counts answer
    "what is in this scope", so selecting one lane never zeroes the others, and
    narrowing the date or modality narrows every badge the way the user sees.

      completed, cancelled  SQL GROUP BY on state. Exact at any volume. A
                            completed request with a result conflict is moved
                            to `anomaly`, using the same conflicted ids.
      the live work lanes   requested + scheduled + in_progress, classified per
                            row through rad_desk_lane() -- the function the rows
                            use -- because clearance and result state are not
                            SQL. Capped; past the cap they are null.

    RECORD RULES APPLY. Every query runs as the caller, so the summary can never
    count a request the caller could not read.
    """
    Request = env["hospital.radiology.request"]
    scope = expression.AND([filters, [("state", "in", list(SUMMARY_STATES))]])
    by_state = dict(Request._read_group(scope, ["state"], ["__count"]))

    summary = {lane: 0 for lane in RAD_DESK_LANES}

    completed_conflicts = 0
    if conflicted_ids:
        completed_conflicts = Request.search_count(
            expression.AND(
                [scope, [("state", "=", "completed"), ("id", "in", conflicted_ids)]]
            )
        )
    summary["completed"] = by_state.get("completed", 0) - completed_conflicts
    summary["cancelled"] = by_state.get("cancelled", 0)

    work_total = sum(by_state.get(state, 0) for state in WORK_STATES)
    exact = work_total <= SUMMARY_WORK_SCAN_MAX
    if not exact:
        _logger.warning(
            "Radiology summary: %s live requests exceed the %s scan cap; the "
            "work-lane counts are reported as unavailable.",
            work_total,
            SUMMARY_WORK_SCAN_MAX,
        )
        for lane in RAD_DESK_ACTIVE_LANES:
            summary[lane] = None
        summary["active"] = None
        summary["work_total"] = work_total
        return summary, exact

    if work_total:
        work = Request.search(
            expression.AND([scope, [("state", "in", list(WORK_STATES))]])
        )
        _prefetch(work)
        for record in work:
            lane, _reason = rad_desk_lane(record)
            summary[lane] += 1
    summary["anomaly"] += completed_conflicts
    summary["active"] = sum(summary[lane] for lane in RAD_DESK_ACTIVE_LANES)
    summary["work_total"] = work_total
    return summary, exact


# ---------------------------------------------------------------------------
# Transitions (Slice 2)
# ---------------------------------------------------------------------------
# FIXED, ROLE-INDEPENDENT SENTENCES. hospital_billing's radiology clearance
# refusal names the patient-payable amount and currency for EVERY role, and the
# billing engine's integrity refusals name charges and encounters. None of that
# text is ever forwarded: a Manager or System Administrator on this desk gets
# exactly the words a technician gets. The original is logged server-side.
SCHEDULE_NOT_ALLOWED_MESSAGE = (
    "Only a requested radiology study can be scheduled. Request %s is %s. "
    "Nothing has been changed."
)
SCHEDULE_BLOCKED_MESSAGE = (
    "The radiology study is awaiting financial clearance and cannot be "
    "scheduled from the Radiology Desk."
)
START_NOT_ALLOWED_MESSAGE = (
    "Only a scheduled radiology study can be started. Request %s is %s. "
    "Nothing has been changed."
)
START_BLOCKED_MESSAGE = (
    "The radiology study cannot be started because financial clearance has "
    "not been confirmed."
)
START_FAILED_MESSAGE = (
    "The radiology study could not be started. Nothing was changed. Ask a "
    "hospital manager to review this request."
)
NO_ACTIVE_STUDY_MESSAGE = (
    "Request %s has no active study, so it cannot be %s. Nothing has been "
    "changed. Ask a hospital manager to review this request."
)
NEEDS_REVIEW_MESSAGE = (
    "Request %s needs review before workflow actions can continue. Nothing has "
    "been changed."
)
STATE_CONFLICT_MESSAGE = (
    "Request %s changed while this action was being processed and is now %s. "
    "Nothing has been changed by this action. Refresh and check the request."
)
SCHEDULE_RESPONSE_FAILED_MESSAGE = (
    "The radiology study was not scheduled because the confirmation could not "
    "be produced. Nothing was changed. Please retry."
)
START_RESPONSE_FAILED_MESSAGE = (
    "The radiology study was not started because the confirmation could not "
    "be produced. Nothing was changed. Please retry."
)


def _lock_row(env, record):
    """Flush pending ORM writes, then take this request's row lock.

    The flush comes first so the lock is taken on a row that reflects this
    transaction's own writes; the table name is a literal, never input.
    """
    env.flush_all()
    env.cr.execute(
        "SELECT id FROM hospital_radiology_request WHERE id = %s FOR UPDATE",
        (record.id,),
    )
    record.invalidate_recordset()


def _state_label(record):
    labels = dict(record._fields["state"]._description_selection(record.env))
    return labels.get(record.state, record.state)


def _assert_transition_policy(record, seen_state, *, expected_state, expected_lane,
                              verb, not_allowed_code, not_allowed_message):
    """THE DESK POLICY, re-checked under the row lock. Decides only whether the
    desk may ASK; the model method still decides whether the transition happens.

    Order matters, and each refusal names what the user can act on:

      1. the state moved since this request was loaded   -> 409 state conflict
      2. the request is not in the expected state         -> 409 not allowed
      3. no active study                                  -> 422
      4. a report conflict or other anomaly               -> 409 needs review
      5. financially blocked                              -> 422 (fixed wording)

    The lane comes from rad_desk_lane(), the function the queue already shows,
    so the button, the badge and this check can never disagree.
    """
    if record.state != seen_state:
        raise ApiError(
            "radiology_request_state_conflict",
            STATE_CONFLICT_MESSAGE % (record.name, _state_label(record)),
            409,
        )
    if record.state != expected_state:
        raise ApiError(
            not_allowed_code,
            not_allowed_message % (record.name, _state_label(record)),
            409,
        )
    if not record.line_ids.filtered(lambda line: line.state != "cancelled"):
        raise ApiError(
            "radiology_request_no_active_study",
            NO_ACTIVE_STUDY_MESSAGE % (record.name, verb),
            422,
        )
    lane, _reason = rad_desk_lane(record)
    if lane == "anomaly":
        raise ApiError(
            "radiology_request_needs_review",
            NEEDS_REVIEW_MESSAGE % record.name,
            409,
        )
    if lane != expected_lane:
        # The only other lane these two states can be in is awaiting_clearance.
        return False
    return True


def _run_transition(env, record, action, failure_code, failure_message):
    """Run ONE authoritative model method and serialize the result. ATOMIC.

    THE SAVEPOINT COVERS EVERYTHING: the state change and its audit row, every
    charge move and the encounter start hospital_billing performs, the
    clearance state the gate persists before refusing, AND the serialized
    response. Any exception -- a refusal from the model, a crash in a side
    effect, a failure while building the payload -- rolls all of it back.
    """
    with env.cr.savepoint():
        action()
        try:
            record.invalidate_recordset()
            return serialize_request_detail(record)
        except Exception as error:
            _logger.exception(
                "Radiology %s response failed for request=%s uid=%s; rolling the "
                "transition back",
                failure_code,
                record.id,
                env.uid,
            )
            raise TransitionResponseError(failure_code, failure_message) from error


class YoyaEmrRadiologyController(http.Controller):

    # ------------------------------------------------------------------
    # 1. Session
    # ------------------------------------------------------------------
    @http.route(
        "%s/session" % RAD_API,
        type="http", auth="user", methods=["GET"], csrf=False,
    )
    @rad_endpoint
    def radiology_session(self, **params):
        """Who is signed in, which Radiology Desk role they hold, and whether
        the desk opens for them. Gated like every other /radiology/* route."""
        env = request.env
        _require_radiology_desk(env)
        return success_response(
            serialize_session(
                env, rad_desk_role_flags(env), rad_desk_capability_flags(env)
            )
        )

    # ------------------------------------------------------------------
    # 2. Worklist
    # ------------------------------------------------------------------
    @http.route(
        "%s/worklist" % RAD_API,
        type="http", auth="user", methods=["GET"], csrf=False,
    )
    @rad_endpoint
    def radiology_worklist(self, **params):
        """The imaging queue.

        TWO PASSES, and they are not interchangeable:

          SQL     bounded by stored columns (state, request_date, patient, exam,
                  doctor, modality), by the conflicted-report ids, and by the
                  caller's own record rules. The security and cost boundary.
          Python  rad_desk_lane() per row: the clearance split and the result
                  state split, which cannot be domains.

        ORDERED OLDEST FIRST (request_date, id), the Laboratory Desk's
        convention and for the same reason: the study that has waited longest is
        the one at risk. Priority travels on every row as a pill and is NOT the
        sort key -- `priority` is a Selection, so ordering by it in SQL sorts
        alphabetically (routine, stat, urgent) and would put STAT in the middle.

        `truncated` means the SQL window was full. After the per-row refinement
        a page can come back shorter than `limit` with `truncated` still true;
        that is the honest answer, and the client's response is to narrow the
        filters.
        """
        env = request.env
        _require_radiology_desk(env)

        lanes = _requested_lanes(params.get("status"))
        limit = _limit_param(params.get("limit"))
        # OPTIONAL, like the Laboratory Desk: imaging work does not expire at
        # midnight, and defaulting to today would hide yesterday's orders.
        day = parse_date(params["date"]) if params.get("date") else None
        search = (params.get("q") or "").strip() or None
        modality = _modality_param(env, params.get("modality"))

        filters = _filter_domain(day, search, modality)
        conflicted_ids = _conflicted_request_ids(env, filters)

        domain = expression.AND([filters, _lane_domain(lanes, conflicted_ids)])
        candidates = env["hospital.radiology.request"].search(
            domain, order="request_date asc, id asc", limit=limit + 1
        )
        truncated = len(candidates) > limit
        if truncated:
            candidates = candidates[:limit]
        _prefetch(candidates)

        wanted = set(lanes)
        rows = candidates.filtered(lambda record: rad_desk_lane(record)[0] in wanted)

        summary, summary_exact = _worklist_summary(env, filters, conflicted_ids)

        return success_response(
            serialize_worklist(
                rows,
                summary,
                filters={
                    "date": day.isoformat() if day else None,
                    "status": list(lanes),
                    "q": search,
                    "modality": modality,
                    "limit": limit,
                },
                meta={
                    # The PAGE. The summary describes the whole filter scope.
                    "row_count": len(rows),
                    "truncated": truncated,
                    "lanes": list(RAD_DESK_LANES),
                    "default_lanes": list(RAD_DESK_ACTIVE_LANES),
                    "summary_exact": summary_exact,
                    # The summary honours date, q and modality, and ignores the
                    # selected lane. Stated in the payload so no client guesses.
                    "summary_filters": ["date", "q", "modality"],
                    # The model's own modality selection, for the filter control.
                    "modalities": selection_options(
                        env["hospital.radiology.exam"], "modality"
                    ),
                },
                capabilities=rad_desk_capability_flags(env),
            )
        )

    # ------------------------------------------------------------------
    # 3. Request detail
    # ------------------------------------------------------------------
    @http.route(
        "%s/requests/<int:request_id>" % RAD_API,
        type="http", auth="user", methods=["GET"], csrf=False,
    )
    @rad_endpoint
    def radiology_request_detail(self, request_id, **params):
        """One radiology request, read-only.

        THE GATE RUNS BEFORE THE RECORD IS TOUCHED, so a caller outside
        RAD_DESK_GROUPS learns nothing about whether `request_id` exists. Past
        the gate, a request that does not exist, one the caller's rules hide and
        an archived one are all the same 404.

        IMAGE METADATA ONLY. No bytes, no URL, no attachment id.
        """
        env = request.env
        _require_radiology_desk(env)
        record = _load_request(env, request_id)
        return success_response(
            {
                "request": serialize_request_detail(record),
                "capabilities": rad_desk_capability_flags(env),
            }
        )

    # ------------------------------------------------------------------
    # 4. Schedule study (Slice 2)
    # ------------------------------------------------------------------
    @http.route(
        "%s/requests/<int:request_id>/schedule" % RAD_API,
        type="http", auth="user", methods=["POST"], csrf=False,
    )
    @rad_endpoint
    def radiology_schedule(self, request_id, **params):
        """requested -> scheduled, through hospital.radiology.request.action_schedule().

        DESK POLICY IS STRICTER THAN THE MODEL. action_schedule() itself accepts
        an unpaid request; the desk does not, because the model has no date or
        slot, so moving an unpaid study to "scheduled" would say nothing true.
        A blocked request is refused BEFORE the method is called.

        No money moves on this transition, so a refusal from the model after the
        policy passed can only be a workflow refusal (a concurrent change): it is
        reported as a state conflict, never with the model's own text.
        """
        env = request.env
        _require_radiology_desk(env)
        record = _load_request(env, request_id)
        seen_state = record.state

        _lock_row(env, record)
        clear = _assert_transition_policy(
            record,
            seen_state,
            expected_state="requested",
            expected_lane="to_schedule",
            verb="scheduled",
            not_allowed_code="radiology_request_not_schedulable",
            not_allowed_message=SCHEDULE_NOT_ALLOWED_MESSAGE,
        )
        if not clear:
            raise ApiError(
                "radiology_request_awaiting_clearance", SCHEDULE_BLOCKED_MESSAGE, 422
            )

        try:
            payload = _run_transition(
                env,
                record,
                record.action_schedule,
                "radiology_request_transition_response_failed",
                SCHEDULE_RESPONSE_FAILED_MESSAGE,
            )
        except (TransitionResponseError, *PG_CONCURRENCY_EXCEPTIONS_TO_RETRY):
            raise
        except Exception as error:  # noqa: BLE001 -- mapped to a fixed sentence
            _logger.warning(
                "Radiology schedule refused by the model for request=%s uid=%s: %s",
                record.id, env.uid, type(error).__name__,
            )
            record.invalidate_recordset()
            raise ApiError(
                "radiology_request_state_conflict",
                STATE_CONFLICT_MESSAGE % (record.name, _state_label(record)),
                409,
            ) from error

        return success_response(
            {"request": payload, "capabilities": rad_desk_capability_flags(env)}
        )

    # ------------------------------------------------------------------
    # 5. Start exam (Slice 2)
    # ------------------------------------------------------------------
    @http.route(
        "%s/requests/<int:request_id>/start" % RAD_API,
        type="http", auth="user", methods=["POST"], csrf=False,
    )
    @rad_endpoint
    def radiology_start(self, request_id, **params):
        """scheduled -> in_progress, through action_mark_in_progress().

        hospital_billing's override IS the gate and is not reimplemented: it
        asserts every active study has a live charge, runs the financial
        clearance check (persisting the clearance state), starts the encounter
        and moves the charges to in_progress. The desk refuses a request it can
        already see is blocked, then lets the model decide.

        EVERY MODEL REFUSAL BECOMES A FIXED SENTENCE. The clearance refusal names
        the patient-payable amount; the integrity refusals name charges; a legacy
        request with no encounter fails with a bare ValueError inside the
        billing engine. All of them roll back inside the savepoint and are
        classified by the authoritative `billing_blocked` boolean re-read after
        the rollback -- never by reading the message.
        """
        env = request.env
        _require_radiology_desk(env)
        record = _load_request(env, request_id)
        seen_state = record.state

        _lock_row(env, record)
        clear = _assert_transition_policy(
            record,
            seen_state,
            expected_state="scheduled",
            expected_lane="ready_to_start",
            verb="started",
            not_allowed_code="radiology_request_not_startable",
            not_allowed_message=START_NOT_ALLOWED_MESSAGE,
        )
        if not clear:
            raise ApiError(
                "radiology_request_start_blocked", START_BLOCKED_MESSAGE, 422
            )

        try:
            payload = _run_transition(
                env,
                record,
                record.action_mark_in_progress,
                "radiology_request_transition_response_failed",
                START_RESPONSE_FAILED_MESSAGE,
            )
        except (TransitionResponseError, *PG_CONCURRENCY_EXCEPTIONS_TO_RETRY):
            raise
        except Exception as error:  # noqa: BLE001 -- mapped to a fixed sentence
            _logger.warning(
                "Radiology start refused for request=%s uid=%s: %s",
                record.id, env.uid, type(error).__name__,
            )
            record.invalidate_recordset()
            if record.state != "scheduled":
                raise ApiError(
                    "radiology_request_state_conflict",
                    STATE_CONFLICT_MESSAGE % (record.name, _state_label(record)),
                    409,
                ) from error
            message = (
                START_BLOCKED_MESSAGE if record.billing_blocked else START_FAILED_MESSAGE
            )
            raise ApiError("radiology_request_start_blocked", message, 422) from error

        return success_response(
            {"request": payload, "capabilities": rad_desk_capability_flags(env)}
        )
