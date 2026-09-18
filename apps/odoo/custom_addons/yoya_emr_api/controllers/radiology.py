"""Radiology Desk API: the imaging department's queue, one request, the two
request transitions, and report drafting and entry.

    GET  /yoya-emr/api/v1/radiology/session
    GET  /yoya-emr/api/v1/radiology/worklist
    GET  /yoya-emr/api/v1/radiology/requests/<id>
    POST /yoya-emr/api/v1/radiology/requests/<id>/schedule  action_schedule()
    POST /yoya-emr/api/v1/radiology/requests/<id>/start     action_mark_in_progress()
    POST /yoya-emr/api/v1/radiology/requests/<id>/report    find or create THE report
    POST /yoya-emr/api/v1/radiology/results/<id>/save       write allow-listed text
    POST /yoya-emr/api/v1/radiology/results/<id>/enter      save + action_mark_entered()
    POST /yoya-emr/api/v1/radiology/results/<id>/images     upload ONE file
    GET  /yoya-emr/api/v1/radiology/results/<id>/images/<image_id>         its bytes
    POST /yoya-emr/api/v1/radiology/results/<id>/images/<image_id>/remove  unlink it
    POST /yoya-emr/api/v1/radiology/results/<id>/validate   action_validate()
    POST /yoya-emr/api/v1/radiology/results/<id>/release    action_release()

THE CONTROLLER DECIDES NOTHING ABOUT WORKFLOW. Each write resolves its record
through the caller's own record rules, locks the row, re-checks the DESK POLICY
under the lock, and calls authoritative model methods inside one savepoint. No
state is written here -- the request and result models refuse a direct state
write from every channel -- and nothing calls sudo(). Cancellation, reset,
amendment and stock consumption are model methods this module still does not
call and routes it does not register.

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
import base64
import functools
import logging
import os

from odoo import http
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.http import request
from odoo.osv import expression
from odoo.service.model import PG_CONCURRENCY_EXCEPTIONS_TO_RETRY
from odoo.tools.mimetypes import guess_mimetype

from ..services.api_response import (
    ApiError,
    api_error_response,
    error_response,
    parse_date,
    parse_int_param,
    read_json_body,
    success_response,
)
from ..services.rad_desk_serializers import (
    RAD_DESK_ACTIVE_LANES,
    RAD_DESK_ANOMALY_STATES,
    RAD_DESK_LANE_STATES,
    RAD_DESK_LANES,
    operational_results,
    rad_desk_lane,
    selection_options,
    serialize_image_metadata,
    serialize_operational_result,
    serialize_request_detail,
    serialize_session,
    serialize_worklist,
)
from ..services.reception_scope import (
    may_author_rad_report,
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


# ---------------------------------------------------------------------------
# Report drafting and entry (Slice 3)
# ---------------------------------------------------------------------------
# A report is opened, and written, only while its study is In progress.
REPORT_REQUEST_STATE = "in_progress"

# THE WRITE ALLOW-LIST. Everything else on a report -- request, patient,
# ordering physician, reporting radiologist, date, state, archive flag, images,
# the exam, the ordered study, the contrast flag, the sequence -- is derived by
# the model or is workflow, and is REFUSED as input rather than silently
# dropped, so a client that sends it learns at once it is not honoured.
# contrast_used is not here: it is copied from the order, and nothing in the
# current workflow asks a radiologist to correct it.
EDITABLE_REPORT_FIELDS = ("findings", "impression", "recommendations")
EDITABLE_REPORT_LINE_FIELDS = ("result_summary", "notes")

REPORT_NOT_AVAILABLE_MESSAGE = (
    "A radiology report is written only while the study is In progress. "
    "Request %s is %s. Nothing has been changed."
)
REPORT_AMBIGUOUS_MESSAGE = (
    "Request %s has %s active radiology reports, so the Radiology Desk cannot "
    "tell which one to use. Nothing has been changed. Ask a hospital manager to "
    "review this request."
)
REPORT_NOT_EDITABLE_MESSAGE = (
    "Radiology report %s is %s and can no longer be edited from the Radiology "
    "Desk. Nothing has been changed."
)
REPORT_STATE_CONFLICT_MESSAGE = (
    "Radiology report %s changed while this action was being processed and is "
    "now %s. Nothing has been changed by this action. Refresh and check the "
    "report."
)
REPORT_AUTHOR_REQUIRED_MESSAGE = (
    "Only a Radiologist, Hospital Manager or Hospital System Administrator can "
    "write or enter a radiology report. Nothing has been changed."
)
REPORT_OPEN_RESPONSE_FAILED_MESSAGE = (
    "The radiology report could not be opened because the confirmation could "
    "not be produced. Nothing was changed. Please retry."
)
REPORT_SAVE_RESPONSE_FAILED_MESSAGE = (
    "The radiology report was not saved because the confirmation could not be "
    "produced. Nothing was changed. Please retry."
)
REPORT_ENTER_RESPONSE_FAILED_MESSAGE = (
    "The radiology report was not marked entered because the confirmation "
    "could not be produced. Nothing was changed. Please retry."
)


def _require_report_author(env):
    """The second gate of the two report WRITES. Checked before any record."""
    if not may_author_rad_report(env):
        raise ApiError(
            "radiology_report_author_required", REPORT_AUTHOR_REQUIRED_MESSAGE, 403
        )


def _load_result(env, result_id):
    """Resolve one report through the CALLER'S OWN record rules.

    search(), not browse().exists(), for the reason _load_request gives. An
    archived report is unreachable too.
    """
    if result_id <= 0:
        raise ApiError("invalid_result_id", "Radiology report ID is invalid.", 400)
    result = env["hospital.radiology.result"].search([("id", "=", result_id)], limit=1)
    if not result:
        raise ApiError(
            "radiology_result_not_found", "Radiology report not found.", 404
        )
    return result


def _lock_result_row(env, result):
    env.flush_all()
    env.cr.execute(
        "SELECT id FROM hospital_radiology_result WHERE id = %s FOR UPDATE",
        (result.id,),
    )
    result.invalidate_recordset()


def _assert_report_available(record):
    """Desk policy for every report route: the study is In progress and has at
    least one active study to report on."""
    if record.state != REPORT_REQUEST_STATE:
        raise ApiError(
            "radiology_report_not_available",
            REPORT_NOT_AVAILABLE_MESSAGE % (record.name, _state_label(record)),
            422,
        )
    if not record.line_ids.filtered(lambda line: line.state != "cancelled"):
        raise ApiError(
            "radiology_request_no_active_study",
            NO_ACTIVE_STUDY_MESSAGE % (record.name, "reported"),
            422,
        )


def _assert_single_report(record, results):
    """Refuse to guess when a request carries more than one active report."""
    if len(results) > 1:
        raise ApiError(
            "radiology_result_ambiguous",
            REPORT_AMBIGUOUS_MESSAGE % (record.name, len(results)),
            409,
        )


def _open_operational_report(env, record):
    """Find THE one operational report of an in-progress request, under the
    request's row lock. Returns it, or an empty recordset when one may be
    created. Every refusal is raised before anything is written.

    WHY THE LOCK IS NOT ENOUGH, and why _create_operational_report touches the
    row: see laboratory._open_operational_result. Odoo runs every transaction at
    REPEATABLE READ with the snapshot taken at its first query, so a second
    click waiting on this lock would, once it got it, still see no report and
    create another. The creator's no-op UPDATE turns that stale snapshot into a
    serialization failure, rad_endpoint re-raises it, Odoo replays the request,
    and the replay finds the first report.
    """
    _lock_row(env, record)
    _assert_report_available(record)
    results = operational_results(record)
    _assert_single_report(record, results)
    return results


def _create_operational_report(env, record):
    """Create the report, after marking the locked request row as modified.

    Only ever called by the lock holder after _open_operational_report found no
    report. A plain ORM create: the model assigns the RADRES sequence, derives
    patient and ordering physician from the request, builds one line per active
    study, and records NO radiologist -- that is set when a report author marks
    it entered.
    """
    env.cr.execute(
        "UPDATE hospital_radiology_request SET write_date = write_date "
        "WHERE id = %s",
        (record.id,),
    )
    return env["hospital.radiology.result"].create({"request_id": record.id})


def _text_value(field_name, value):
    """A string, or null to clear. Stored as typed, never trimmed: whether
    whitespace counts as a report is action_mark_entered's decision."""
    if value is None or value == "":
        return False
    if not isinstance(value, str):
        raise ApiError(
            "invalid_field", "'%s' must be a string or null." % field_name, 400
        )
    return value


def _check_report_payload_shape(body):
    """Refuse any key outside the allow-list before a record is touched."""
    allowed = set(EDITABLE_REPORT_FIELDS) | {"lines"}
    unknown = sorted(key for key in body if key not in allowed)
    if unknown:
        raise ApiError(
            "radiology_report_field_not_allowed",
            "These report fields cannot be written from the Radiology Desk: %s."
            % ", ".join(unknown),
            400,
        )
    lines = body.get("lines", [])
    if lines is None:
        lines = []
    if not isinstance(lines, list):
        raise ApiError("invalid_field", "'lines' must be a list.", 400)
    line_allowed = set(EDITABLE_REPORT_LINE_FIELDS) | {"id"}
    for entry in lines:
        if not isinstance(entry, dict):
            raise ApiError(
                "invalid_field", "Each entry in 'lines' must be an object.", 400
            )
        unknown = sorted(key for key in entry if key not in line_allowed)
        if unknown:
            raise ApiError(
                "radiology_report_field_not_allowed",
                "These report line fields cannot be written from the Radiology "
                "Desk: %s." % ", ".join(unknown),
                400,
            )
        line_id = entry.get("id")
        if isinstance(line_id, bool) or not isinstance(line_id, int):
            raise ApiError(
                "invalid_field", "Each report line needs its integer 'id'.", 400
            )
    return lines


def _report_write_values(result, body):
    """Translate an allow-listed body into ONE parent write.

    Lines are written through the PARENT as (1, id, vals) commands, so the
    report's own write() -- its freeze, its authorship check and its audit entry
    -- sees them. Every line id must belong to THIS report; a foreign id is
    refused, never ignored.
    """
    lines = _check_report_payload_shape(body)
    values = {}
    for field_name in EDITABLE_REPORT_FIELDS:
        if field_name in body:
            values[field_name] = _text_value(field_name, body[field_name])
    own_line_ids = set(result.line_ids.ids)
    seen = set()
    commands = []
    for entry in lines:
        line_id = entry["id"]
        if line_id not in own_line_ids:
            raise ApiError(
                "radiology_result_line_not_found",
                "Report line %s does not belong to report %s." % (line_id, result.name),
                400,
            )
        if line_id in seen:
            raise ApiError(
                "invalid_field", "Report line %s appears more than once." % line_id, 400
            )
        seen.add(line_id)
        line_values = {
            field_name: _text_value(field_name, entry[field_name])
            for field_name in EDITABLE_REPORT_LINE_FIELDS
            if field_name in entry
        }
        if line_values:
            commands.append((1, line_id, line_values))
    if commands:
        values["line_ids"] = commands
    return values


def _editable_report(env, result, seen_state):
    """Lock, then apply the desk's entry policy. Writes nothing.

    LOCK ORDER: REQUEST, THEN REPORT -- the order the open route takes, so two
    desk actions on one study cannot deadlock. The report lock serializes save
    against enter: the later request waits, finds the row modified by the
    committed change, and is replayed by Odoo against the entered report, which
    this function then refuses.
    """
    record = result.request_id
    _lock_row(env, record)
    _lock_result_row(env, result)
    if result.state != seen_state:
        raise ApiError(
            "radiology_result_state_conflict",
            REPORT_STATE_CONFLICT_MESSAGE % (result.name, _state_label(result)),
            409,
        )
    _assert_report_available(record)
    _assert_single_report(record, operational_results(record))
    if result.state != "draft":
        raise ApiError(
            "radiology_result_not_editable",
            REPORT_NOT_EDITABLE_MESSAGE % (result.name, _state_label(result)),
            409,
        )


def _report_payload(result):
    return {
        "result": serialize_operational_result(result),
        "request": serialize_request_detail(result.request_id),
    }


def _run_report_action(env, result, action, failure_message):
    """Run a report mutation and build its response inside ONE savepoint.

    The same contract as _run_transition: a model refusal leaves nothing
    half-written, and a failure while serializing leaves no committed change
    behind a message saying it failed.
    """
    with env.cr.savepoint():
        action()
        try:
            env.invalidate_all()
            return _report_payload(result)
        except Exception as error:
            _logger.exception(
                "Radiology report response failed for result=%s uid=%s; "
                "rolling the change back",
                result.id,
                env.uid,
            )
            raise TransitionResponseError(
                "radiology_report_response_failed", failure_message
            ) from error


# ---------------------------------------------------------------------------
# Images (Slice 4)
# ---------------------------------------------------------------------------
# THE MODEL IS THE AUTHORITY. hospital.radiology.image sniffs the bytes, caps
# the size, derives mimetype and size, records the uploader, forces private
# storage and freezes the set from `validated` onward -- under sudo too. These
# routes add the desk's gate, a row lock and one savepoint, and pre-check the
# file with the MODEL'S OWN constants only so each refusal can carry its own
# code; the model re-checks everything on create.
#
# NOTHING ABOUT THE FILE IS TAKEN FROM THE CLIENT except the bytes, the name it
# is displayed under (sanitized), and an optional caption. The browser's
# Content-Type, a claimed size, an image_type, a result id in the body -- any
# other form field is refused, not ignored.
IMAGE_FORM_FIELDS = ("caption",)
IMAGE_CAPTION_MAX = 200
IMAGE_FILENAME_MAX = 200

IMAGE_NOT_EDITABLE_MESSAGE = (
    "Radiology report %s is %s, so its images can no longer be changed. "
    "Nothing has been changed."
)
IMAGE_NOT_FOUND_MESSAGE = "Image not found."
IMAGE_ONE_FILE_MESSAGE = "Send exactly one file, in the 'file' field."
IMAGE_EMPTY_MESSAGE = "The file is empty. Nothing has been uploaded."
IMAGE_TOO_LARGE_MESSAGE = (
    "This file is larger than %d MB, the limit for one radiology file. Nothing "
    "has been uploaded."
)
IMAGE_UNSUPPORTED_MESSAGE = (
    "This file is not a JPEG, PNG or PDF. The file's content decides this, not "
    "its name. Nothing has been uploaded."
)
IMAGE_UPLOAD_RESPONSE_FAILED_MESSAGE = (
    "The file was not uploaded because the confirmation could not be produced. "
    "Nothing was changed. Please retry."
)
IMAGE_REMOVE_RESPONSE_FAILED_MESSAGE = (
    "The file was not removed because the confirmation could not be produced. "
    "Nothing was changed. Please retry."
)


def _safe_filename(raw):
    """The name a file is stored and displayed under.

    The basename only -- a browser may send a full client path -- with control
    characters, quotes and path separators removed rather than escaped, and a
    bounded length. It is a LABEL: nothing about the file's type is read from
    it.
    """
    name = (raw or "").replace("\\", "/").rsplit("/", 1)[-1]
    forbidden = set('"\r\n\t<>')
    cleaned = "".join(
        character for character in name
        if character.isprintable() and character not in forbidden
    ).strip()
    return cleaned[:IMAGE_FILENAME_MAX] or "radiology-file"


def _read_upload(env):
    """(raw bytes, sniffed mimetype, safe filename, caption) from the request.

    Read with a hard ceiling of MAX_FILE_SIZE + 1 bytes, so an oversized upload
    is refused without the whole of it being held in memory.
    """
    httprequest = request.httprequest
    unknown = sorted(
        key for key in httprequest.form.keys() if key not in IMAGE_FORM_FIELDS
    )
    if unknown:
        raise ApiError(
            "radiology_image_field_not_allowed",
            "These fields cannot be sent with a radiology file: %s. The type and "
            "size are read from the file itself." % ", ".join(unknown),
            400,
        )
    others = sorted(key for key in httprequest.files.keys() if key != "file")
    files = httprequest.files.getlist("file")
    if others or len(files) != 1:
        raise ApiError("radiology_image_invalid_file", IMAGE_ONE_FILE_MESSAGE, 400)

    Image = env["hospital.radiology.image"]
    storage = files[0]
    raw = storage.stream.read(Image.MAX_FILE_SIZE + 1)
    if not raw:
        raise ApiError("radiology_image_invalid_file", IMAGE_EMPTY_MESSAGE, 400)
    if len(raw) > Image.MAX_FILE_SIZE:
        raise ApiError(
            "radiology_image_too_large",
            IMAGE_TOO_LARGE_MESSAGE % (Image.MAX_FILE_SIZE // (1024 * 1024)),
            413,
        )
    mimetype = guess_mimetype(raw)
    if mimetype not in Image.ALLOWED_MIMETYPES:
        raise ApiError(
            "radiology_image_unsupported_type", IMAGE_UNSUPPORTED_MESSAGE, 415
        )

    caption = httprequest.form.get("caption")
    if caption is not None:
        caption = caption.strip()
        if len(caption) > IMAGE_CAPTION_MAX:
            raise ApiError(
                "invalid_field",
                "'caption' must be at most %d characters." % IMAGE_CAPTION_MAX,
                400,
            )
    return raw, mimetype, _safe_filename(storage.filename), caption or None


def _load_image(env, result, image_id):
    """ONE image of ONE report, through the caller's own record rules.

    The result AND the image id must match, and archived images are absent, so
    a guessed id, another report's image and an archived file all answer the
    same 404.
    """
    if image_id <= 0:
        raise ApiError("radiology_image_not_found", IMAGE_NOT_FOUND_MESSAGE, 404)
    image = env["hospital.radiology.image"].search(
        [("id", "=", image_id), ("result_id", "=", result.id), ("active", "=", True)],
        limit=1,
    )
    if not image:
        raise ApiError("radiology_image_not_found", IMAGE_NOT_FOUND_MESSAGE, 404)
    return image


def _mutable_images(env, result):
    """Lock the request, then the report -- the Slice 3 lock order -- and
    re-check under the lock that the image set may still change."""
    _lock_row(env, result.request_id)
    _lock_result_row(env, result)
    Image = env["hospital.radiology.image"]
    if result.state not in Image.MUTABLE_RESULT_STATES:
        raise ApiError(
            "radiology_image_not_editable",
            IMAGE_NOT_EDITABLE_MESSAGE % (result.name, _state_label(result)),
            409,
        )


def _run_image_action(env, result, action, failure_message):
    """An image mutation and its response in ONE savepoint.

    The image row, its backing ir.attachment and its audit row are all written
    inside it, so a failure while building the confirmation leaves none of
    them behind -- and a removal that cannot be confirmed is undone.
    """
    with env.cr.savepoint():
        outcome = action()
        try:
            env.invalidate_all()
            payload = _report_payload(result)
            if outcome is not None:
                payload["image"] = serialize_image_metadata(outcome)
            return payload
        except Exception as error:
            _logger.exception(
                "Radiology image response failed for result=%s uid=%s; "
                "rolling the change back",
                result.id,
                env.uid,
            )
            raise TransitionResponseError(
                "radiology_image_response_failed", failure_message
            ) from error


# ---------------------------------------------------------------------------
# Validation and release (Slice 5)
# ---------------------------------------------------------------------------
# THE MODEL DOES THE WORK. action_validate() and action_release() -- with
# hospital_billing's overrides -- re-check that the study has started and is
# financially cleared, deliver the charge at release, and complete the request
# through _sync_completion_from_results(). Nothing here restates any of that.
# The desk adds the author gate, the two row locks, the one-report rule, a
# completeness pre-check whose sentence carries no money, and one savepoint.
#
# EVERY REFUSAL FROM THE ACTION ITSELF IS A FIXED SENTENCE. hospital_billing's
# clearance refusal names the patient-payable amount and currency, and its
# integrity refusals name charges; none of it reaches this desk, for any role.
# The original is logged by type only.
REPORT_TRANSITION_REQUEST_STATE = "in_progress"

REPORT_NOT_VALIDATABLE_MESSAGE = (
    "Only an entered radiology report of a study in progress can be validated. "
    "Report %s is %s and request %s is %s. Nothing has been changed."
)
REPORT_NOT_RELEASABLE_MESSAGE = (
    "Only a validated radiology report of a study in progress can be released. "
    "Report %s is %s and request %s is %s. Nothing has been changed."
)
REPORT_VALIDATION_BLOCKED_MESSAGE = (
    "The radiology report could not be validated because financial clearance "
    "has not been confirmed. Nothing was changed. Ask a hospital manager to "
    "review this request."
)
REPORT_RELEASE_BLOCKED_MESSAGE = (
    "The radiology report could not be released. Nothing was changed. Ask a "
    "hospital manager to review this request."
)
REPORT_VALIDATE_RESPONSE_FAILED_MESSAGE = (
    "The radiology report was not validated because the confirmation could not "
    "be produced. Nothing was changed. Please retry."
)
REPORT_RELEASE_RESPONSE_FAILED_MESSAGE = (
    "The radiology report was not released because the confirmation could not "
    "be produced. Nothing was changed. Please retry."
)


def _transitionable_report(env, result, seen_state, *, expected_state,
                           not_allowed_code, not_allowed_message):
    """Lock, then apply the desk's validation / release policy. Writes nothing.

    LOCK ORDER: REQUEST, THEN REPORT -- the order every report route takes.
    Under the locks, in order:

      1. the report moved since it was loaded     -> 409 state conflict
      2. wrong report state or request state      -> 409 not validatable/releasable
      3. more than one active report, or this one
         is not the request's operational report  -> 409 ambiguous
      4. the report is no longer complete          -> 422 incomplete (model's words)
    """
    record = result.request_id
    _lock_row(env, record)
    _lock_result_row(env, result)
    if result.state != seen_state:
        raise ApiError(
            "radiology_result_state_conflict",
            REPORT_STATE_CONFLICT_MESSAGE % (result.name, _state_label(result)),
            409,
        )
    if result.state != expected_state or record.state != REPORT_TRANSITION_REQUEST_STATE:
        raise ApiError(
            not_allowed_code,
            not_allowed_message
            % (result.name, _state_label(result), record.name, _state_label(record)),
            409,
        )
    results = operational_results(record)
    _assert_single_report(record, results)
    if results != result:
        raise ApiError(
            "radiology_result_ambiguous",
            REPORT_AMBIGUOUS_MESSAGE % (record.name, len(results)),
            409,
        )
    # The model's own completeness rule, asked BEFORE the action so its
    # sentence -- which carries no money -- can be shown; the action re-checks.
    problems = result._entry_problems()
    if problems:
        raise ApiError(
            "radiology_report_incomplete",
            "Radiology report %s is not complete: %s." % (result.name, "; ".join(problems)),
            422,
        )


def _run_report_transition(env, result, action, *, blocked_code, blocked_message,
                           failure_message):
    """ONE authoritative model method and its response, in ONE savepoint.

    The savepoint covers the state change and its audit row and -- at release
    -- hospital_billing's charge delivery and the request's completion. Any
    refusal from the model rolls all of it back and becomes the fixed,
    money-free sentence; a failure while building the response rolls it back
    too and says nothing was changed.
    """
    try:
        with env.cr.savepoint():
            action()
            try:
                env.invalidate_all()
                payload = _report_payload(result)
                payload["request_completed"] = (
                    result.request_id.state == "completed"
                )
                return payload
            except Exception as error:
                _logger.exception(
                    "Radiology report transition response failed for result=%s "
                    "uid=%s; rolling the transition back",
                    result.id,
                    env.uid,
                )
                raise TransitionResponseError(
                    "radiology_result_transition_response_failed", failure_message
                ) from error
    except (TransitionResponseError, AccessError, *PG_CONCURRENCY_EXCEPTIONS_TO_RETRY):
        raise
    except Exception as error:  # noqa: BLE001 -- mapped to a fixed sentence
        _logger.warning(
            "Radiology report transition refused for result=%s uid=%s: %s",
            result.id, env.uid, type(error).__name__,
        )
        raise ApiError(blocked_code, blocked_message, 422) from error


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

    # ------------------------------------------------------------------
    # 6. Report: find or create the operational report (Slice 3)
    # ------------------------------------------------------------------
    @http.route(
        "%s/requests/<int:request_id>/report" % RAD_API,
        type="http", auth="user", methods=["POST"], csrf=False,
    )
    @rad_endpoint
    def radiology_report_open(self, request_id, **params):
        """Return the request's ONE operational report, creating it if absent.

        What "Open report" calls, for every desk role: the technician opens the
        report container too. Idempotent: the first call creates a draft, every
        later call returns that same record -- draft to continue, entered or
        later to read. The body is ignored: nothing about a report is chosen by
        the client at creation.

        REFUSALS, each raised before anything is written:

          radiology_report_not_available     422  request is not in_progress
          radiology_request_no_active_study  422  nothing left to report on
          radiology_result_ambiguous         409  more than one active report
        """
        env = request.env
        _require_radiology_desk(env)
        record = _load_request(env, request_id)
        existing = _open_operational_report(env, record)
        capabilities = rad_desk_capability_flags(env)

        if existing:
            payload = _report_payload(existing)
            payload.update({"created": False, "capabilities": capabilities})
            return success_response(payload)

        try:
            with env.cr.savepoint():
                created = _create_operational_report(env, record)
                try:
                    env.invalidate_all()
                    payload = _report_payload(created)
                except Exception as error:
                    _logger.exception(
                        "Radiology report create response failed for request=%s "
                        "uid=%s; rolling the creation back",
                        request_id,
                        env.uid,
                    )
                    raise TransitionResponseError(
                        "radiology_report_response_failed",
                        REPORT_OPEN_RESPONSE_FAILED_MESSAGE,
                    ) from error
        except (TransitionResponseError, AccessError, *PG_CONCURRENCY_EXCEPTIONS_TO_RETRY):
            raise
        except (UserError, ValidationError) as error:
            # The model's own creation guards (request state, one operational
            # report, patient match). Rolled back by the savepoint; the result
            # model carries no billing text.
            raise ApiError("radiology_report_not_available", str(error), 422) from error

        payload.update({"created": True, "capabilities": capabilities})
        return success_response(payload)

    # ------------------------------------------------------------------
    # 7. Report: save a draft (Slice 3)
    # ------------------------------------------------------------------
    @http.route(
        "%s/results/<int:result_id>/save" % RAD_API,
        type="http", auth="user", methods=["POST"], csrf=False,
    )
    @rad_endpoint
    def radiology_report_save(self, result_id, **params):
        """Persist allow-listed report text on a DRAFT report. No state moves.

        Body (every key optional):

            {"findings": str|null, "impression": str|null,
             "recommendations": str|null,
             "lines": [{"id": int, "result_summary": str|null, "notes": str|null}]}

        Any other key is a 400, not a silent drop. INCOMPLETE DRAFTS SAVE: a
        draft is unfinished by definition, and completeness is the entry gate's
        decision, not this route's.
        """
        env = request.env
        _require_radiology_desk(env)
        _require_report_author(env)
        body = read_json_body()
        _check_report_payload_shape(body)

        result = _load_result(env, result_id)
        seen_state = result.state
        _editable_report(env, result, seen_state)
        values = _report_write_values(result, body)

        def save():
            if values:
                result.write(values)

        try:
            payload = _run_report_action(
                env, result, save, REPORT_SAVE_RESPONSE_FAILED_MESSAGE
            )
        except (TransitionResponseError, AccessError, *PG_CONCURRENCY_EXCEPTIONS_TO_RETRY):
            raise
        except (UserError, ValidationError) as error:
            raise ApiError("radiology_result_not_editable", str(error), 409) from error

        payload["capabilities"] = rad_desk_capability_flags(env)
        return success_response(payload)

    # ------------------------------------------------------------------
    # 8. Report: save and mark entered, atomically (Slice 3)
    # ------------------------------------------------------------------
    @http.route(
        "%s/results/<int:result_id>/enter" % RAD_API,
        type="http", auth="user", methods=["POST"], csrf=False,
    )
    @rad_endpoint
    def radiology_report_enter(self, result_id, **params):
        """Write the latest text, then action_mark_entered(). ONE savepoint.

        One act to the radiologist, so one request: either the report is entered
        with exactly the text on screen, or nothing has changed and the draft is
        as it was before the click. The body is the /save shape and may be
        empty.

        The completeness rule is not restated here: action_mark_entered()
        decides it (findings or impression non-blank; every line linked to a
        study of this request, none twice) and its refusal is forwarded as
        radiology_report_incomplete. The same method records the entering author
        as the reporting radiologist. No money is involved: hospital_billing
        overrides validate and release, not entry.
        """
        env = request.env
        _require_radiology_desk(env)
        _require_report_author(env)
        body = read_json_body()
        _check_report_payload_shape(body)

        result = _load_result(env, result_id)
        seen_state = result.state
        _editable_report(env, result, seen_state)
        values = _report_write_values(result, body)

        def save_and_enter():
            if values:
                result.write(values)
            result.action_mark_entered()

        try:
            payload = _run_report_action(
                env, result, save_and_enter, REPORT_ENTER_RESPONSE_FAILED_MESSAGE
            )
        except (TransitionResponseError, AccessError, *PG_CONCURRENCY_EXCEPTIONS_TO_RETRY):
            raise
        except ValidationError as error:
            raise ApiError("radiology_report_incomplete", str(error), 422) from error
        except UserError as error:
            raise ApiError("radiology_result_not_editable", str(error), 409) from error

        payload["capabilities"] = rad_desk_capability_flags(env)
        return success_response(payload)

    # ------------------------------------------------------------------
    # 9. Images: upload one file (Slice 4)
    # ------------------------------------------------------------------
    @http.route(
        "%s/results/<int:result_id>/images" % RAD_API,
        type="http", auth="user", methods=["POST"], csrf=False,
    )
    @rad_endpoint
    def radiology_image_upload(self, result_id, **params):
        """Attach ONE file to a draft or entered report. multipart/form-data.

        Form: `file` (exactly one) and an optional `caption`. Anything else is
        refused. The bytes decide the type: JPEG, PNG or PDF, sniffed; the
        size is counted here and capped at the model's 25 MB. image_type is
        derived too -- "report" for a PDF, "image" otherwise.

        REFUSALS, each before anything is written:

          radiology_image_invalid_file      400  no file, two files, empty
          radiology_image_field_not_allowed 400  any other form field
          radiology_image_too_large         413  over 25 MB
          radiology_image_unsupported_type  415  not JPEG, PNG or PDF by content
          radiology_result_not_found        404  missing, hidden or archived
          radiology_image_not_editable      409  report validated or later
        """
        env = request.env
        _require_radiology_desk(env)
        result = _load_result(env, result_id)
        raw, mimetype, filename, caption = _read_upload(env)
        _mutable_images(env, result)

        values = {
            "result_id": result.id,
            "name": os.path.splitext(filename)[0] or filename,
            "filename": filename,
            "file": base64.b64encode(raw),
            "image_type": "report" if mimetype == "application/pdf" else "image",
        }
        if caption:
            values["caption"] = caption

        def upload():
            return env["hospital.radiology.image"].create(values)

        try:
            payload = _run_image_action(
                env, result, upload, IMAGE_UPLOAD_RESPONSE_FAILED_MESSAGE
            )
        except (TransitionResponseError, AccessError, *PG_CONCURRENCY_EXCEPTIONS_TO_RETRY):
            raise
        except ValidationError as error:
            # The model's own inspection, re-run on create. Money-free.
            raise ApiError("radiology_image_invalid_file", str(error), 400) from error
        except UserError as error:
            # The freeze, reached if the report moved on concurrently.
            raise ApiError("radiology_image_not_editable", str(error), 409) from error

        payload["capabilities"] = rad_desk_capability_flags(env)
        return success_response(payload)

    # ------------------------------------------------------------------
    # 10. Images: one file's BYTES (Slice 4)
    # ------------------------------------------------------------------
    @http.route(
        "%s/results/<int:result_id>/images/<int:image_id>" % RAD_API,
        type="http", auth="user", methods=["GET"], csrf=False,
    )
    @rad_endpoint
    def radiology_image_content(self, result_id, image_id, **params):
        """The bytes of one image of one report, for the Radiology Desk.

        A DESK ROUTE, NOT THE DOCTOR'S. The Doctor's loader serves RELEASED
        images only and scopes them to a visit; the desk works on draft and
        entered reports, so it has its own route with its own gate.

        THE ID IS A hospital.radiology.image ID, never an ir.attachment id, and
        it must belong to THIS report. No sudo: the search and the stream run
        as the caller, so the record rules and the attachment's own access
        check both apply.

        EVERY MISS IS THE SAME 404: a missing, hidden or archived report, an
        image of another report, an archived image, a guessed id.

        NEVER CACHED: `private, no-store`, and the type is the one the model
        sniffed at upload, never re-guessed.
        """
        env = request.env
        _require_radiology_desk(env)
        result = env["hospital.radiology.result"].search(
            [("id", "=", result_id)], limit=1
        ) if result_id > 0 else env["hospital.radiology.result"]
        if not result:
            raise ApiError("radiology_image_not_found", IMAGE_NOT_FOUND_MESSAGE, 404)
        image = _load_image(env, result, image_id)

        as_attachment = params.get("disposition") == "attachment"
        stream = env["ir.binary"]._get_stream_from(
            image,
            "file",
            filename=_safe_filename(image.filename),
            mimetype=image.mimetype or None,
        )
        response = stream.get_response(as_attachment=as_attachment)
        response.headers["Cache-Control"] = "private, no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    # ------------------------------------------------------------------
    # 11. Images: remove one file (Slice 4)
    # ------------------------------------------------------------------
    @http.route(
        "%s/results/<int:result_id>/images/<int:image_id>/remove" % RAD_API,
        type="http", auth="user", methods=["POST"], csrf=False,
    )
    @rad_endpoint
    def radiology_image_remove(self, result_id, image_id, **params):
        """Remove a wrong upload from a draft or entered report. Bodiless.

        A REAL UNLINK, the model's own removal path and the one the Odoo form's
        image list already uses; the backing attachment goes with it. The
        model logs a BLOCKED removal and lets a permitted one pass unlogged --
        this repository's convention (see hospital.radiology.image.unlink).

        REFUSALS: radiology_image_not_found 404 (not this report's, archived, or
        already removed -- so a second click is safe), radiology_image_not_editable
        409 (report validated or later).
        """
        env = request.env
        _require_radiology_desk(env)
        result = _load_result(env, result_id)
        _mutable_images(env, result)
        image = _load_image(env, result, image_id)

        def remove():
            image.unlink()

        try:
            payload = _run_image_action(
                env, result, remove, IMAGE_REMOVE_RESPONSE_FAILED_MESSAGE
            )
        except (TransitionResponseError, AccessError, *PG_CONCURRENCY_EXCEPTIONS_TO_RETRY):
            raise
        except (UserError, ValidationError) as error:
            raise ApiError("radiology_image_not_editable", str(error), 409) from error

        payload["capabilities"] = rad_desk_capability_flags(env)
        return success_response(payload)

    # ------------------------------------------------------------------
    # 12. Report: validate (Slice 5)
    # ------------------------------------------------------------------
    @http.route(
        "%s/results/<int:result_id>/validate" % RAD_API,
        type="http", auth="user", methods=["POST"], csrf=False,
    )
    @rad_endpoint
    def radiology_report_validate(self, result_id, **params):
        """entered -> validated, through action_validate(). Bodiless.

        hospital_billing's override re-checks that the study was started and is
        financially cleared; the model re-checks completeness. Validation
        delivers nothing and completes nothing: the request stays in progress
        and the report moves to Awaiting release. From here the image set is
        frozen by the image model itself.

        REFUSALS: radiology_report_author_required 403, radiology_result_not_found
        404, radiology_result_state_conflict 409, radiology_result_not_validatable
        409, radiology_result_ambiguous 409, radiology_report_incomplete 422,
        radiology_report_validation_blocked 422 (fixed, money-free).
        """
        env = request.env
        _require_radiology_desk(env)
        _require_report_author(env)
        result = _load_result(env, result_id)
        _transitionable_report(
            env, result, result.state,
            expected_state="entered",
            not_allowed_code="radiology_result_not_validatable",
            not_allowed_message=REPORT_NOT_VALIDATABLE_MESSAGE,
        )
        payload = _run_report_transition(
            env, result, result.action_validate,
            blocked_code="radiology_report_validation_blocked",
            blocked_message=REPORT_VALIDATION_BLOCKED_MESSAGE,
            failure_message=REPORT_VALIDATE_RESPONSE_FAILED_MESSAGE,
        )
        payload["capabilities"] = rad_desk_capability_flags(env)
        return success_response(payload)

    # ------------------------------------------------------------------
    # 13. Report: release (Slice 5)
    # ------------------------------------------------------------------
    @http.route(
        "%s/results/<int:result_id>/release" % RAD_API,
        type="http", auth="user", methods=["POST"], csrf=False,
    )
    @rad_endpoint
    def radiology_report_release(self, result_id, **params):
        """validated -> released, through action_release(). Bodiless.

        THE CLINICAL HANDOFF. hospital_billing's override re-checks clearance,
        resolves each report line to its ordered study, delivers each charge
        once, and runs the request's completion rule -- which completes the
        request when every active study has a released report. The Doctor Desk
        shows the report and its images from this moment, through its own
        released-only serializers; nothing here touches them.

        `request_completed` in the response says which of the two outcomes
        happened, read from the request the release left behind.

        REFUSALS: as validate, with radiology_result_not_releasable and
        radiology_report_release_blocked 422 (fixed, money-free).
        """
        env = request.env
        _require_radiology_desk(env)
        _require_report_author(env)
        result = _load_result(env, result_id)
        _transitionable_report(
            env, result, result.state,
            expected_state="validated",
            not_allowed_code="radiology_result_not_releasable",
            not_allowed_message=REPORT_NOT_RELEASABLE_MESSAGE,
        )
        payload = _run_report_transition(
            env, result, result.action_release,
            blocked_code="radiology_report_release_blocked",
            blocked_message=REPORT_RELEASE_BLOCKED_MESSAGE,
            failure_message=REPORT_RELEASE_RESPONSE_FAILED_MESSAGE,
        )
        payload["capabilities"] = rad_desk_capability_flags(env)
        return success_response(payload)
