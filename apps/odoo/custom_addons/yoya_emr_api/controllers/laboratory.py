"""Laboratory Desk API: the bench's work queue, one request, its transitions,
and result entry.

EVERY STATE CHANGE IS ONE AUTHORITATIVE MODEL METHOD, AND THE CONTROLLER DECIDES
NOTHING ABOUT WORKFLOW. The mutations:

    .../requests/<id>/collect           action_mark_sample_collected()
    .../requests/<id>/start-processing  action_mark_in_progress()
    .../requests/<id>/result            find-or-create the ONE operational
                                        result (plain ORM create)
    .../results/<id>/save               allow-listed entry fields, draft only
    .../results/<id>/enter              the same fields, then
                                        action_mark_entered(), atomically
    .../results/<id>/validate           action_validate(), atomically, with
                                        its billing refusals sanitized

No state is ever written here, no state machine is restated, and nothing calls
sudo(). Release, cancellation and reset-to-draft are further model methods that
this API deliberately does NOT expose.

RESULT ENTRY ADDS DESK POLICY, NOT BUSINESS RULES. The model accepts a result
against a request that is sample_collected OR in_progress, and allows several
results per request. The Laboratory Desk is narrower on purpose -- entry only
while the request is in_progress, and exactly one operational result -- and
those two restrictions live here, in the desk's own API, exactly as
LAB_DESK_GROUPS is narrower than the ORM's ACLs. What counts as a COMPLETE
result stays the model's: action_mark_entered() decides it and this module
forwards its refusal.

THE TWO ARE NOT SYMMETRICAL, and the difference is money. Collection runs
hospital_billing's clearance gate and can be refused with a sentence that names
an amount for some roles, so its refusal is classified and sanitised
(_collection_refusal). Starting processing touches no charge at all -- laboratory
has no billing override for action_mark_in_progress -- so Odoo's own sentence is
forwarded unchanged.

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
from odoo.service.model import PG_CONCURRENCY_EXCEPTIONS_TO_RETRY

from ..services.api_response import (
    ApiError,
    api_error_response,
    error_response,
    parse_date,
    parse_int_param,
    read_json_body,
    success_response,
)
from ..services.lab_desk_serializers import (
    LAB_DESK_ACTIVE_STATUSES,
    LAB_DESK_STATUS_STATE,
    LAB_DESK_STATUSES,
    lab_desk_results,
    lab_desk_status,
    serialize_operational_result,
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

# How many `requested` rows the summary will classify before giving up on the
# awaiting/ready split.
#
# The split is the one count that cannot be done in SQL: it is hospital_billing's
# `billing_blocked`, a non-stored compute that asks the billing engine per
# encounter. Every other status is settled by one GROUP BY.
#
# Past this cap the two split counts are reported as null rather than guessed,
# and `meta.summary_exact` says so. A wrong number on a badge the bench works
# from is worse than an honest dash.
SUMMARY_CLEARANCE_SCAN_MAX = 1000


class TransitionResponseError(Exception):
    """A transition succeeded, but its confirmation could not be built.

    A separate type on purpose, and the reason is the same one cashier.py's
    PaymentResponseError documents: without it, an AccessError raised while
    SERIALIZING the result is indistinguishable from one raised by the desk
    gate, and the bench would be told it was not authorized to do a thing it
    had in fact just done.

    By the time this reaches the handler the savepoint has already rolled the
    transition back, so the message may honestly say nothing was changed. Each
    caller supplies its own code and sentence, so "the collection failed" and
    "starting processing failed" stay distinguishable to the client.
    """

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message


def lab_endpoint(func):
    """Stable error envelope for the Laboratory Desk. Never leaks a traceback.

    Ordering mirrors controllers/doctor.doctor_endpoint, and it matters for the
    same reason: in Odoo both AccessError and ValidationError subclass
    UserError, so the broad handler has to come last or it swallows the two
    specific ones and reports an authorization failure as a workflow refusal.

    TransitionResponseError sits ABOVE AccessError for exactly that reason --
    it is a plain Exception, but placing it first keeps the "response failed"
    case from ever being reported as a denial.

    CONCURRENCY FAILURES ARE RE-RAISED, NEVER ENVELOPED. A PostgreSQL
    serialization failure, lock timeout or deadlock is not an error the bench
    should see: Odoo's HTTP layer (odoo.service.model.retrying) rolls the whole
    request back and replays it in a fresh transaction. Result find-or-create
    depends on that replay -- see _open_operational_result -- so the broad
    handler below must never swallow one into a 500.
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
            # Already logged with its cause at the raise site, and the savepoint
            # has already rolled the transition back.
            return error_response(error.code, error.message, 500)
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


def _load_request(env, request_id):
    """Resolve one laboratory request through the CALLER'S OWN record rules.

    Shared by the detail read and the collection mutation so the two can never
    disagree about which requests a caller may reach.

    RESOLVED BY search(), NOT browse().exists(). The difference is the whole
    point: browse() applies no record rule, and exists() checks the row in raw
    SQL, so a hidden record would come back present and fail later with an
    AccessError -- a 403 that says "this id is real, but not yours". search()
    applies the caller's own rules in the query, so an unreadable request is
    simply absent and answers 404 like any id that is not theirs to see. It
    also inherits the ORM's active_test, so an archived request is unreachable.
    """
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
    return record


# THE ONE SENTENCE THE BENCH IS TOLD WHEN MONEY BLOCKS A COLLECTION.
#
# WHY THE MODEL'S OWN WORDING IS NOT FORWARDED HERE, unlike every other refusal
# in this module. hospital_billing._clearance_error builds its detail line from
# the caller's groups:
#
#     may_see_cash -> "<test> -- unpaid, 900.00 due"
#     otherwise    -> "<test> -- payment not cleared (see front desk)"
#
# and `may_see_cash` is true for receptionist, accountant, MANAGER and SYSTEM
# ADMINISTRATOR. The last two are in LAB_DESK_GROUPS, so forwarding that
# sentence verbatim would put an amount on the Laboratory Desk for exactly
# those two roles -- a confidentiality rule that held for a lab technician and
# silently broke for their manager.
#
# This message is therefore FIXED and role-independent. It says what the bench
# can act on: the request is not cleared, and the cashier is who clears it.
CLEARANCE_REFUSED_MESSAGE = (
    "This request is not financially cleared, so the sample cannot be "
    "collected yet. The patient settles it at the cashier; the request moves "
    "to Ready for collection by itself once that is done. Nothing has been "
    "changed."
)


def _run_transition(env, record, request_id, action, failure_code, failure_message):
    """Run ONE authoritative model transition and serialize the result. ATOMIC.

    Shared by every Laboratory Desk mutation so they cannot drift apart in the
    two places that matter: what is inside the savepoint, and what happens when
    the response cannot be built.

    THE SAVEPOINT COVERS THE TRANSITION AND ITS RESPONSE, and both halves are
    load-bearing:

      * a model method may write before it refuses -- collection's clearance
        gate persists financial_clearance_state before raising -- so catching
        its UserError without a savepoint would commit that write while telling
        the bench nothing happened;
      * a failure while SERIALIZING must not leave a committed transition
        behind a message saying it failed, or the technician repeats an action
        that already succeeded.

    THE CONTROLLER STILL DECIDES NOTHING. `action` is a bound method on the
    record -- action_mark_sample_collected, action_mark_in_progress -- and this
    helper neither inspects state nor writes it. A refusal propagates as the
    model raised it; the caller decides how to phrase it.

    Returns the serialized request as it stands AFTER the transition, so the
    status the bench renders is the derived one rather than a value the client
    guessed from its own click.
    """
    with env.cr.savepoint():
        action()
        try:
            record.invalidate_recordset()
            return serialize_request_detail(record)
        except Exception as error:
            _logger.exception(
                "Laboratory %s response failed for request=%s uid=%s; "
                "rolling the transition back",
                failure_code,
                request_id,
                env.uid,
            )
            raise TransitionResponseError(failure_code, failure_message) from error


def _collection_refusal(record, error):
    """Turn a refused collection into a safe ApiError. CLASSIFY, never decide.

    The model has already refused by the time this runs, and the savepoint has
    already rolled the attempt back. The only question left is WHICH sentence
    the bench may safely be shown, and there are exactly two kinds:

      FINANCIAL  hospital_billing._clearance_error, whose text is role-
                 dependent and CAN carry an amount (see
                 CLEARANCE_REFUSED_MESSAGE). Replaced wholesale with the fixed
                 wording -- the original is never forwarded, never logged into
                 the response, and never reaches the browser.

      WORKFLOW   the base model's "Only requested lab requests can be marked as
                 sample collected", or _assert_all_lines_charged's "N ordered
                 test(s) have no valid charge". Both name tests and states, no
                 money, and both tell the technician something true and useful
                 -- so Odoo's own sentence is forwarded, as everywhere else in
                 this module.

    THE CLASSIFIER IS THE AUTHORITATIVE BOOLEAN, not a string match. Sniffing
    the message for digits would break the moment the wording changed, and
    would fail open -- the wrong direction. `billing_blocked` is the same
    compute Slice 1 already serializes, re-read after the rollback.

    A request that is still `requested` and still blocked can only have been
    refused by the clearance gate: that gate runs first, and the base method
    would have accepted `requested`.
    """
    record.invalidate_recordset()
    still_requested = record.state == "requested"
    if still_requested and record.billing_blocked:
        return ApiError(
            "lab_not_financially_cleared", CLEARANCE_REFUSED_MESSAGE, 422
        )
    return ApiError("invalid_workflow_state", str(error), 422)


# ----------------------------------------------------------------------
# Result entry (Slice 3)
# ----------------------------------------------------------------------

# LABORATORY DESK POLICY, narrower than the model. hospital.laboratory.result
# accepts a request in sample_collected OR in_progress
# (RESULT_ELIGIBLE_REQUEST_STATES); the bench enters results only once
# processing has started, so a result is never typed against a sample nobody
# has put on the bench. Not a business rule, and deliberately not restated as
# one: the model's own constraint still runs on create.
RESULT_ENTRY_REQUEST_STATE = "in_progress"

# THE WRITE ALLOW-LIST. Everything else on the result -- request, patient,
# physician, technician, date, state, the line structure, the test, the
# specimen, the sequence -- is either derived by the model or is workflow, and
# is refused as input rather than silently dropped, so a client that sends it
# learns immediately that it is not being honoured.
EDITABLE_RESULT_HEADER_FIELDS = ("interpretation", "remarks")
EDITABLE_RESULT_LINE_FIELDS = (
    "result_value",
    "unit",
    "reference_range",
    "abnormal_flag",
    "notes",
)

RESULT_ENTRY_CLOSED_MESSAGE = (
    "Results are entered on the Laboratory Desk only while the request is In "
    "progress. Request %s is %s. Nothing has been changed."
)
RESULT_AMBIGUOUS_MESSAGE = (
    "Request %s has %s result records, so the Laboratory Desk cannot tell which "
    "one to use. Nothing has been changed. Ask a laboratory manager to review "
    "this request."
)
RESULT_FINAL_MESSAGE = (
    "Request %s already has a %s result (%s). A new result cannot be started "
    "from the Laboratory Desk."
)
RESULT_CANCELLED_MESSAGE = (
    "Request %s has a cancelled result (%s). The Laboratory Desk does not start "
    "a replacement result. Nothing has been changed. Ask a laboratory manager "
    "to review this request."
)
RESULT_NOT_EDITABLE_MESSAGE = (
    "Result %s is %s and can no longer be edited from the Laboratory Desk. "
    "Nothing has been changed."
)

# Validation (Slice 3B). Same desk policy as entry, worded for the act.
RESULT_VALIDATION_CLOSED_MESSAGE = (
    "Results are validated on the Laboratory Desk only while the request is In "
    "progress. Request %s is %s. Nothing has been changed."
)
RESULT_NOT_VALIDATABLE_MESSAGE = (
    "Result %s is %s. Only an entered result can be validated. Nothing has "
    "been changed."
)

# THE ONE SENTENCE A VALIDATION REFUSAL FROM BEYOND THE COMPLETENESS GATE GETS.
#
# WHY NOTHING PAST THAT GATE IS FORWARDED. action_validate() is overridden by
# hospital_billing: after the clinical transition it resolves each result line
# to its ordered request line and delivers that line's charge through
# hospital.billing.engine. Every refusal on that path is written for billing
# staff -- "It cannot be validated against a charge", "Charge CHRG... is
# invoiced", engine and charge-line constraint text -- and the charge model
# carries constraints whose messages name sums. None of it is safe to show a
# bench, and none of it is something a technician can act on.
#
# FIXED AND ROLE-INDEPENDENT, for the reason CLEARANCE_REFUSED_MESSAGE gives: a
# manager and a system administrator are Lab Desk roles too, and must not
# receive richer financial text than the technician beside them. The original
# is logged server-side and never reaches the response.
VALIDATION_BLOCKED_MESSAGE = (
    "The laboratory result could not be validated. Nothing was changed. Ask a "
    "laboratory manager to review this request."
)


def _lock_row(env, table, record_id):
    """Take the row lock for one record, AFTER flushing pending ORM writes.

    The flush is not optional: the ORM defers writes, and a raw SELECT ... FOR
    UPDATE issued before it would lock a row that does not yet reflect what this
    same transaction has done -- the ordering consultation._assert_version
    documents for the same reason. `table` is always a literal from this
    module, never input.
    """
    env.flush_all()
    env.cr.execute(
        "SELECT id FROM %s WHERE id = %%s FOR UPDATE" % table, (record_id,)
    )


def _load_result(env, result_id):
    """Resolve one result through the CALLER'S OWN record rules.

    search(), not browse().exists(), for the reason _load_request gives: a
    result the caller may not read is simply absent and answers 404, like any
    id that is not theirs to see. Archived results are unreachable too.
    """
    if result_id <= 0:
        raise ApiError("invalid_result_id", "Laboratory result ID is invalid.", 400)
    result = env["hospital.laboratory.result"].search(
        [("id", "=", result_id)], limit=1
    )
    if not result:
        raise ApiError("lab_result_not_found", "Laboratory result not found.", 404)
    return result


def _state_label(record):
    labels = dict(record._fields["state"]._description_selection(record.env))
    return labels.get(record.state, record.state)


def _assert_entry_open(record):
    """Desk policy: the request must be in_progress. See RESULT_ENTRY_REQUEST_STATE."""
    if record.state != RESULT_ENTRY_REQUEST_STATE:
        raise ApiError(
            "lab_result_entry_not_available",
            RESULT_ENTRY_CLOSED_MESSAGE % (record.name, _state_label(record)),
            422,
        )


def _assert_single_result(record, results):
    """Refuse to guess when a request carries more than one result."""
    if len(results) > 1:
        raise ApiError(
            "lab_result_ambiguous",
            RESULT_AMBIGUOUS_MESSAGE % (record.name, len(results)),
            409,
        )


def _open_operational_result(env, record):
    """Find or create THE one operational result of an in_progress request.

    WHY THIS NEEDS MORE THAN A ROW LOCK. The model deliberately allows several
    results per request, so no constraint stops a double-click creating two --
    and two active results covering the same ordered line block the request's
    completion permanently (the known B3 gap). The guard therefore has to live
    here, and it has to survive two requests racing.

    Odoo runs every transaction at REPEATABLE READ, and the snapshot is taken at
    the transaction's FIRST query -- long before this function, while the
    session and the request were being loaded. So a plain SELECT ... FOR UPDATE
    is not enough on its own:

        A locks the request, finds no result, creates one, commits.
        B was waiting on the lock; it now acquires it -- but B's snapshot still
        predates A's commit, so B's search sees NO result and creates a second.

    THE FIX IS TO MAKE THE WINNER MODIFY THE ROW IT LOCKED. Immediately before
    creating, the winner issues a no-op UPDATE on the request row
    (write_date = write_date: no value changes, no ORM write, no audit entry).
    PostgreSQL then refuses B's lock with a serialization failure, because the
    row it wants was modified by a transaction that committed after B's
    snapshot. lab_endpoint re-raises that failure, Odoo's HTTP layer
    (odoo.service.model.retrying) replays B in a fresh transaction, and the
    replay finds A's result and returns it. Two clicks, one result.

    Only the CREATE path touches the row (see _create_operational_result).
    Resuming an existing result never needs to: a result that already exists is
    visible to every snapshot taken after its creator committed, and the creator
    touched the row when it made it.

    Returns the existing operational result, or an empty recordset when one may
    be created. Every refusal is raised before anything is written.
    """
    _lock_row(env, "hospital_laboratory_request", record.id)
    record.invalidate_recordset()
    _assert_entry_open(record)

    results = lab_desk_results(record)
    _assert_single_result(record, results)

    if results:
        result = results
        if result.state == "cancelled":
            raise ApiError(
                "lab_result_cancelled",
                RESULT_CANCELLED_MESSAGE % (record.name, result.name),
                409,
            )
        if result.state not in ("draft", "entered"):
            raise ApiError(
                "lab_result_already_final",
                RESULT_FINAL_MESSAGE
                % (record.name, _state_label(result).lower(), result.name),
                409,
            )
    return results


def _create_operational_result(env, record):
    """Create the result, after marking the locked request row as modified.

    Only ever called by the lock holder, after _open_operational_result has
    found no result. The no-op UPDATE is what turns a concurrent creator's
    stale snapshot into a serialization failure Odoo replays; see that
    function's docstring.
    """
    env.cr.execute(
        "UPDATE hospital_laboratory_request SET write_date = write_date "
        "WHERE id = %s",
        (record.id,),
    )
    # Plain ORM create, exactly as the model expects: it assigns the LABRES
    # sequence, copies patient and physician from the request, defaults the
    # technician to the caller, and builds one line per ordered request line.
    return env["hospital.laboratory.result"].create({"request_id": record.id})


def _text_value(field_name, value):
    """A string, or null to clear. An empty string is stored as empty."""
    if value is None or value == "":
        return False
    if not isinstance(value, str):
        raise ApiError("invalid_field", "'%s' must be a string or null." % field_name, 400)
    # Stored as typed. Deliberately NOT stripped: whether whitespace counts as a
    # result is action_mark_entered's decision, and trimming here would make a
    # blank-looking draft silently different from what the technician sees.
    return value


def _check_result_payload_shape(body):
    """Refuse any key outside the allow-list before a record is touched."""
    allowed = set(EDITABLE_RESULT_HEADER_FIELDS) | {"lines"}
    unknown = sorted(key for key in body if key not in allowed)
    if unknown:
        raise ApiError(
            "lab_result_field_not_allowed",
            "These result fields cannot be written from the Laboratory Desk: %s."
            % ", ".join(unknown),
            400,
        )
    lines = body.get("lines", [])
    if lines is None:
        lines = []
    if not isinstance(lines, list):
        raise ApiError("invalid_field", "'lines' must be a list.", 400)
    line_allowed = set(EDITABLE_RESULT_LINE_FIELDS) | {"id"}
    for entry in lines:
        if not isinstance(entry, dict):
            raise ApiError("invalid_field", "Each entry in 'lines' must be an object.", 400)
        unknown = sorted(key for key in entry if key not in line_allowed)
        if unknown:
            raise ApiError(
                "lab_result_field_not_allowed",
                "These result line fields cannot be written from the Laboratory "
                "Desk: %s." % ", ".join(unknown),
                400,
            )
        line_id = entry.get("id")
        if isinstance(line_id, bool) or not isinstance(line_id, int):
            raise ApiError(
                "invalid_field", "Each result line needs its integer 'id'.", 400
            )
    return lines


def _result_write_values(env, result, body):
    """Translate an allow-listed body into ONE parent write.

    LINES ARE WRITTEN THROUGH THE PARENT, as (1, id, vals) commands on
    `line_ids`, rather than line by line. hospital.laboratory.result.write() is
    where the audit entry "Laboratory result updated." is created; a direct
    line.write() records nothing at all.

    Every line id must belong to THIS result. A foreign id is refused, never
    ignored: a (1, id, vals) command naming another result's line would
    otherwise write through into a record the request never asked about.
    """
    lines = _check_result_payload_shape(body)
    values = {}
    for field_name in EDITABLE_RESULT_HEADER_FIELDS:
        if field_name in body:
            values[field_name] = _text_value(field_name, body[field_name])

    own_line_ids = set(result.line_ids.ids)
    flag_values = {
        value
        for value, _label in env["hospital.laboratory.result.line"]
        ._fields["abnormal_flag"]
        ._description_selection(env)
    }
    seen = set()
    commands = []
    for entry in lines:
        line_id = entry["id"]
        if line_id not in own_line_ids:
            raise ApiError(
                "lab_result_line_not_found",
                "Result line %s does not belong to result %s." % (line_id, result.name),
                400,
            )
        if line_id in seen:
            raise ApiError(
                "invalid_field",
                "Result line %s appears more than once." % line_id,
                400,
            )
        seen.add(line_id)
        line_values = {}
        for field_name in EDITABLE_RESULT_LINE_FIELDS:
            if field_name not in entry:
                continue
            if field_name == "abnormal_flag":
                flag = entry[field_name]
                # The model's own selection, and never null: the field defaults
                # to "normal" and the desk offers no invented "unassessed" value.
                if flag not in flag_values:
                    raise ApiError(
                        "invalid_field",
                        "'abnormal_flag' must be one of: %s."
                        % ", ".join(sorted(flag_values)),
                        400,
                    )
                line_values[field_name] = flag
            else:
                line_values[field_name] = _text_value(field_name, entry[field_name])
        if line_values:
            commands.append((1, line_id, line_values))
    if commands:
        values["line_ids"] = commands
    return values


def _editable_result(env, result):
    """Lock the result and apply the desk's entry policy to it.

    THE LOCK SERIALIZES SAVE AGAINST ENTER. Without it a save racing a Mark
    entered could write a blank value into a result the moment after it became
    entered -- result lines are not frozen until validation. With it, the
    later request waits, finds the row modified by the committed state change,
    and is replayed by Odoo against the entered result, which this function
    then refuses.
    """
    _lock_row(env, "hospital_laboratory_result", result.id)
    result.invalidate_recordset()
    request_record = result.request_id
    _assert_entry_open(request_record)
    _assert_single_result(request_record, lab_desk_results(request_record))
    if result.state != "draft":
        raise ApiError(
            "lab_result_not_editable",
            RESULT_NOT_EDITABLE_MESSAGE % (result.name, _state_label(result)),
            409,
        )


def _run_result_action(env, result, action, failure_code, failure_message):
    """Run a result mutation and build its response inside ONE savepoint.

    The same contract as _run_transition, for the same two reasons: a model
    refusal must leave nothing half-written behind it, and a failure while
    serializing must not leave a committed change behind a message saying it
    failed. For Mark entered that is the difference between "values saved but
    not entered" and a clean "nothing changed" -- the only outcome the bench
    can act on.
    """
    with env.cr.savepoint():
        action()
        try:
            env.invalidate_all()
            return {
                "result": serialize_operational_result(result),
                "request": serialize_request_detail(result.request_id),
            }
        except Exception as error:
            _logger.exception(
                "Laboratory %s response failed for result=%s uid=%s; "
                "rolling the change back",
                failure_code,
                result.id,
                env.uid,
            )
            raise TransitionResponseError(failure_code, failure_message) from error


def _validatable_result(env, result):
    """Lock, then apply the Laboratory Desk's validation policy. Writes nothing.

    LOCK ORDER: REQUEST, THEN RESULT. The request row is the one completion
    (and, later, release) serializes on -- _evaluate_completion takes it FOR
    UPDATE -- so every desk action that holds both takes the request first and
    two of them can never wait on each other in opposite orders.

    WHAT IS RE-CHECKED UNDER THE LOCK, all four before anything is written:

      1. the request is in_progress   -- B2: validating on sample_collected
                                         delivers the charge of a result that
                                         can then never be released
      2. the request has exactly ONE  -- B3 and the cancelled-sibling gap: a
         result                          second result (cancelled or not) makes
                                         completion impossible later
      3. that result is THIS result   -- the id in the URL is the operational one
      4. this result is entered       -- the model's own source state

    The first two are desk policy, deliberately narrower than the model, and
    they are what make B2 and B3 unreachable through the Laboratory Desk.
    """
    request_record = result.request_id
    _lock_row(env, "hospital_laboratory_request", request_record.id)
    _lock_row(env, "hospital_laboratory_result", result.id)
    request_record.invalidate_recordset()
    result.invalidate_recordset()

    if request_record.state != RESULT_ENTRY_REQUEST_STATE:
        raise ApiError(
            "lab_result_entry_not_available",
            RESULT_VALIDATION_CLOSED_MESSAGE
            % (request_record.name, _state_label(request_record)),
            422,
        )
    results = lab_desk_results(request_record)
    _assert_single_result(request_record, results)
    if results != result:
        raise ApiError(
            "lab_result_ambiguous",
            RESULT_AMBIGUOUS_MESSAGE % (request_record.name, len(results)),
            409,
        )
    if result.state != "entered":
        raise ApiError(
            "lab_result_not_validatable",
            RESULT_NOT_VALIDATABLE_MESSAGE % (result.name, _state_label(result)),
            409,
        )
    return request_record


def _run_validation(env, result):
    """Validate ONE result and build its response inside ONE savepoint.

    THREE GATES, EACH WITH ITS OWN ANSWER, all inside the same savepoint:

      A. COMPLETENESS, with linkage tolerated -- the model's own
         _check_lines_consistent(require_linkage=False, require_complete=True).
         Its refusal names tests and the request only, so it is forwarded as
         `lab_result_incomplete`: "no result value entered for: CBC" is
         something the technician can act on. Nothing is restated here.

      B. LINKAGE -- the same method with require_linkage=True. Having passed A,
         a refusal here can only be a result line that is not tied to its
         ordered request line: the condition hospital_billing then turns into
         "cannot be validated against a charge". Fixed message.

      C. action_validate() and an explicit flush. The base method writes
         `validated`; hospital_billing's override delivers the charge through
         the engine under sudo. ANY refusal from here on -- billing engine,
         charge constraint, line resolution, even an access error on a billing
         record -- is replaced with VALIDATION_BLOCKED_MESSAGE. The flush is
         what makes a stored-field or constraint failure surface HERE, inside
         the mapping, rather than at savepoint exit where it would escape as
         an unclassified error.

    ROLLBACK IS THE SAVEPOINT'S. hospital.laboratory.result.action_validate()
    writes the result state BEFORE it delivers, and has no rollback of its own.
    A failure in delivery, or in building the response, therefore rolls back
    the result state, the charge's qty_delivered / delivery_state /
    delivered_at, and every audit row written on the way -- there is never a
    validated result or a delivered charge behind an error.

    Concurrency failures are re-raised untouched so Odoo can replay the request.
    """
    try:
        with env.cr.savepoint():
            result._check_lines_consistent(require_linkage=False, require_complete=True)
            try:
                result._check_lines_consistent(require_linkage=True, require_complete=True)
            except ValidationError as error:
                _logger.warning(
                    "Laboratory validation refused on linkage for result=%s uid=%s: %s",
                    result.id, env.uid, error,
                )
                raise ApiError(
                    "lab_result_validation_blocked", VALIDATION_BLOCKED_MESSAGE, 422
                ) from error

            try:
                result.action_validate()
                env.flush_all()
            except PG_CONCURRENCY_EXCEPTIONS_TO_RETRY:
                raise
            except UserError as error:
                # UserError covers ValidationError and AccessError too.
                _logger.warning(
                    "Laboratory validation blocked for result=%s uid=%s: %s",
                    result.id, env.uid, error,
                )
                raise ApiError(
                    "lab_result_validation_blocked", VALIDATION_BLOCKED_MESSAGE, 422
                ) from error

            try:
                env.invalidate_all()
                return {
                    "result": serialize_operational_result(result),
                    "request": serialize_request_detail(result.request_id),
                }
            except PG_CONCURRENCY_EXCEPTIONS_TO_RETRY:
                raise
            except Exception as error:
                _logger.exception(
                    "Laboratory validation response failed for result=%s uid=%s; "
                    "rolling the validation back",
                    result.id,
                    env.uid,
                )
                raise TransitionResponseError(
                    "lab_result_validate_response_failed",
                    "The result was not validated because the confirmation could "
                    "not be produced. Nothing was changed. Please retry.",
                ) from error
    except ValidationError as error:
        # Gate A: the model's completeness refusal. Tests and request only.
        raise ApiError("lab_result_incomplete", str(error), 422) from error


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


def _worklist_summary(env, day, search):
    """One count per bench status over the WHOLE date+search scope.

    THE DEFECT THIS REPLACES. The previous version counted the rows the client
    had just been handed. Those rows are narrowed twice over -- to the selected
    lane's states, and then to `limit` -- so every lane the technician had not
    clicked read zero, and a lane whose rows fell outside the first page read
    zero even when it was selected. A badge that only becomes true once you
    click it is worse than no badge.

    THE LANE IS DELIBERATELY IGNORED HERE. Counts answer "what is in this date
    and search scope", so selecting Ready for collection must not zero the
    other seven. Only the two COMMON filters narrow them, exactly as the user
    sees them: the ordered day (or Any day) and the search box.

    TWO QUERIES, AND THE SPLIT IS THE REASON THERE ARE TWO.

      1. _read_group by `state` -- one SQL GROUP BY. It settles draft,
         sample_collected, in_progress, completed and cancelled exactly, and
         gives the `requested` TOTAL.

      2. `requested` alone cannot be grouped further, because the split into
         awaiting_clearance / ready_for_collection is hospital_billing's
         `billing_blocked` -- a non-stored compute that asks the billing engine
         per encounter. It cannot appear in a domain or a GROUP BY at all. So
         the requested rows are read and classified through lab_desk_status(),
         THE SAME function the rows use. No status logic is duplicated, and the
         clearance rule is never reimplemented here.

    BOUNDED, AND HONEST WHEN IT CANNOT BE. Step 2 is O(requested) clearance
    evaluations, so it is capped. Past the cap the two split counts come back
    as null and `summary_exact` is False -- the desk shows a dash rather than a
    number that is wrong. Guessing would be the one outcome worse than not
    knowing.

    RECORD RULES APPLY. _read_group runs through _search, so the summary is
    scoped to the caller exactly as the rows are; it can never count a request
    the technician may not read.
    """
    Request = env["hospital.laboratory.request"]
    # Reuses the SAME domain builder as the rows, asked for every status, so
    # the date and search predicates cannot drift between the two.
    scope = _worklist_domain(LAB_DESK_STATUSES, day, search)

    by_state = dict(Request._read_group(scope, ["state"], ["__count"]))

    summary = {
        status: by_state.get(LAB_DESK_STATUS_STATE[status], 0)
        for status in LAB_DESK_STATUSES
        if status not in ("awaiting_clearance", "ready_for_collection")
    }

    requested_total = by_state.get("requested", 0)
    exact = requested_total <= SUMMARY_CLEARANCE_SCAN_MAX
    if not exact:
        _logger.warning(
            "Laboratory summary: %s requested rows exceeds the %s clearance "
            "scan cap; the awaiting/ready split is reported as unavailable.",
            requested_total,
            SUMMARY_CLEARANCE_SCAN_MAX,
        )
        summary["awaiting_clearance"] = None
        summary["ready_for_collection"] = None
        summary["active_bench"] = None
        summary["requested_total"] = requested_total
        return summary, exact

    awaiting = 0
    if requested_total:
        pending = Request.search(
            expression.AND([scope, [("state", "=", "requested")]])
        )
        # Warm the one compute the classification needs, across the whole
        # recordset rather than per row.
        pending.mapped("encounter_id")
        awaiting = sum(
            1 for req in pending if lab_desk_status(req) == "awaiting_clearance"
        )

    summary["awaiting_clearance"] = awaiting
    summary["ready_for_collection"] = requested_total - awaiting
    # ACTIVE BENCH, stated once and derived from the same numbers the lanes
    # show, so the tab and its parts can never disagree. Preserves Slice 1's
    # LAB_DESK_ACTIVE_STATUSES exactly: draft, completed and cancelled are not
    # bench work.
    summary["active_bench"] = sum(
        summary[status] for status in LAB_DESK_ACTIVE_STATUSES
    )
    summary["requested_total"] = requested_total
    return summary, exact


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

        # Computed over the date+search scope, INDEPENDENT of the selected
        # lane, so every badge is right before the technician clicks anything.
        summary, summary_exact = _worklist_summary(env, day, search)

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
                # False only when the requested rows exceeded the clearance
                # scan cap, in which case the two split counts are null.
                "summary_exact": summary_exact,
            },
            capabilities=capabilities,
        )
        # THE LANE COUNTS, AND THEY DESCRIBE THE SCOPE RATHER THAN THE PAGE.
        #
        # `summary` counts every request matching the date and search filters,
        # whatever lane is selected and however many rows fitted in this page.
        # `meta.row_count` and `meta.truncated` describe the PAGE. The two
        # answer different questions and are deliberately not the same number:
        # a page of 100 out of 173 matching reports row_count 100, truncated
        # true, and a summary that still totals 173.
        payload["summary"] = summary
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

        record = _load_request(env, request_id)

        return success_response(
            {
                "request": serialize_request_detail(record),
                "capabilities": lab_desk_capability_flags(env),
            }
        )

    # ------------------------------------------------------------------
    # 4. Sample collection
    # ------------------------------------------------------------------
    @http.route(
        "%s/requests/<int:request_id>/collect" % LAB_API,
        type="http", auth="user", methods=["POST"], csrf=False,
    )
    @lab_endpoint
    def collect_sample(self, request_id, **params):
        """Mark the sample collected. THE CONTROLLER DECIDES NOTHING.

        It resolves the request through the caller's own record rules and calls
        ONE authoritative model method:

            hospital.laboratory.request.action_mark_sample_collected()

        No state is written here. That method is where the two gates live, and
        neither is reimplemented or second-guessed:

          hospital_billing's override   financial clearance, evaluated across
                                        EVERY charge on the request BEFORE any
                                        clinical state moves, via
                                        billing_engine.check_financial_clearance
          hospital_management's base    requested -> sample_collected only

        NO CLEARANCE DECISION IS DUPLICATED. This endpoint never asks whether
        the request is cleared; it asks the model to collect, and reports what
        the model said. `billing_blocked` is read afterwards only to CLASSIFY a
        refusal that already happened, so the right sentence is chosen -- never
        to make the call.

        ATOMICITY. The transition, the charge moves it triggers and the
        serialized response are ONE savepoint. Two things make that necessary
        rather than decorative:

          * check_financial_clearance(persist=True) WRITES
            financial_clearance_state on the billing account before the refusal
            is raised. Catching that UserError without a savepoint would commit
            that write while telling the bench nothing happened.
          * a failure while serializing must not leave a collected sample
            behind a message saying the collection failed -- the technician
            would collect a second time.

        IDEMPOTENCY IS THE STATE MACHINE'S, and no token is added. The base
        method refuses anything but `requested`, so a double-clicked Collect
        makes the second call a clean 422 rather than a second transition. The
        charge moves are idempotent in the engine, and the browser disables the
        button on submit -- see the note in lab-request-panel.tsx.
        """
        env = request.env
        _require_lab_desk(env)

        record = _load_request(env, request_id)

        try:
            payload = _run_transition(
                env,
                record,
                request_id,
                record.action_mark_sample_collected,
                "lab_collect_response_failed",
                "The sample was not marked collected because the confirmation "
                "could not be produced. Nothing was changed. Please retry.",
            )
        except (UserError, ValidationError) as error:
            # The savepoint has already rolled everything back, so the record
            # below is the state the caller still has, and the message may
            # honestly say nothing changed.
            raise _collection_refusal(record, error) from error

        return success_response(
            {
                "request": payload,
                "capabilities": lab_desk_capability_flags(env),
            }
        )

    # ------------------------------------------------------------------
    # 5. Start processing
    # ------------------------------------------------------------------
    @http.route(
        "%s/requests/<int:request_id>/start-processing" % LAB_API,
        type="http", auth="user", methods=["POST"], csrf=False,
    )
    @lab_endpoint
    def start_processing(self, request_id, **params):
        """Move a collected sample onto the bench. THE CONTROLLER DECIDES NOTHING.

        It resolves the request through the caller's own record rules and calls
        ONE authoritative model method:

            hospital.laboratory.request.action_mark_in_progress()

        WHAT THAT METHOD IS, VERIFIED FROM SOURCE RATHER THAN ASSUMED. Unlike
        collection, laboratory has exactly ONE implementation of it and no
        override anywhere: hospital_billing overrides action_mark_in_progress
        only for hospital.radiology.request, not for this model. So:

          allowed source   sample_collected, and nothing else
          target           in_progress
          money            NONE. No clearance is re-checked, no charge moves,
                           no delivery is recorded. Collection already
                           commenced the work; this is a purely clinical step.
          audit            _log_state_change -> hospital.audit.log, with actor
          guards           the base write() runs _check_state_transition too,
                           and sample_collected -> in_progress is the only move
                           that state permits

        NO RESULT RECORDS ARE REQUIRED, and none is created. Result entry is a
        later slice.

        NOTHING IS SANITISED HERE, and that is deliberate rather than an
        omission. The one refusal this endpoint can produce names states only
        ("Only lab requests with samples collected can be marked as in
        progress."), so Odoo's own sentence is forwarded as everywhere else in
        this module. Collection needs its extra classifier because its refusal
        can carry an amount for a manager; this one cannot, and adding the same
        machinery would imply a risk that does not exist.

        IDEMPOTENCY IS THE STATE MACHINE'S, and no token is added. A replayed
        POST finds the request already in_progress and is refused with a clean
        422 rather than transitioning twice. The browser disables the button on
        submit as well, so the second call is not normally made at all.
        """
        env = request.env
        _require_lab_desk(env)

        record = _load_request(env, request_id)

        try:
            payload = _run_transition(
                env,
                record,
                request_id,
                record.action_mark_in_progress,
                "lab_start_processing_response_failed",
                "Processing was not started because the confirmation could not "
                "be produced. Nothing was changed. Please retry.",
            )
        except (UserError, ValidationError) as error:
            # The savepoint has already rolled the attempt back. The model's
            # sentence names the state it refused and carries no money.
            raise ApiError("invalid_workflow_state", str(error), 422) from error

        return success_response(
            {
                "request": payload,
                "capabilities": lab_desk_capability_flags(env),
            }
        )

    # ------------------------------------------------------------------
    # 6. Result entry: find or create the operational result
    # ------------------------------------------------------------------
    @http.route(
        "%s/requests/<int:request_id>/result" % LAB_API,
        type="http", auth="user", methods=["POST"], csrf=False,
    )
    @lab_endpoint
    def open_result(self, request_id, **params):
        """Return the request's ONE operational result, creating it if absent.

        What "Enter results" calls. Idempotent by design: the first call creates
        a draft, every later call returns that same record -- draft to resume,
        entered to view. See _open_operational_result for the concurrency
        argument, which is the reason this is not a plain search-then-create.

        REFUSALS, each raised before anything is written:

          lab_result_entry_not_available  422  request is not in_progress
          lab_result_ambiguous            409  more than one result exists
          lab_result_already_final        409  the result is validated/released
          lab_result_cancelled            409  the result is cancelled

        The body is ignored: nothing about a result is chosen by the client at
        creation. The model derives patient, physician, technician and lines.
        """
        env = request.env
        _require_lab_desk(env)

        record = _load_request(env, request_id)
        existing = _open_operational_result(env, record)
        capabilities = lab_desk_capability_flags(env)

        if existing:
            return success_response(
                {
                    "result": serialize_operational_result(existing),
                    "request": serialize_request_detail(record),
                    "created": False,
                    "capabilities": capabilities,
                }
            )

        try:
            with env.cr.savepoint():
                created = _create_operational_result(env, record)
                try:
                    env.invalidate_all()
                    payload = {
                        "result": serialize_operational_result(created),
                        "request": serialize_request_detail(record),
                    }
                except Exception as error:
                    _logger.exception(
                        "Laboratory result create response failed for "
                        "request=%s uid=%s; rolling the creation back",
                        request_id,
                        env.uid,
                    )
                    raise TransitionResponseError(
                        "lab_result_open_response_failed",
                        "The result could not be opened because the "
                        "confirmation could not be produced. Nothing was "
                        "changed. Please retry.",
                    ) from error
        except AccessError:
            raise
        except (UserError, ValidationError) as error:
            # The model's own creation guards (request state, patient match).
            # Rolled back by the savepoint; the sentence carries no money.
            raise ApiError("invalid_workflow_state", str(error), 422) from error

        payload["created"] = True
        payload["capabilities"] = capabilities
        return success_response(payload)

    # ------------------------------------------------------------------
    # 7. Result entry: save a draft
    # ------------------------------------------------------------------
    @http.route(
        "%s/results/<int:result_id>/save" % LAB_API,
        type="http", auth="user", methods=["POST"], csrf=False,
    )
    @lab_endpoint
    def save_result(self, result_id, **params):
        """Persist allow-listed entry fields on a DRAFT result. No state moves.

        Body (every key optional):

            {"interpretation": str|null, "remarks": str|null,
             "lines": [{"id": int, "result_value": str|null, "unit": str|null,
                        "reference_range": str|null, "abnormal_flag": str,
                        "notes": str|null}]}

        Any other key -- request_id, patient_id, state, test_id,
        request_line_id, sample_type, sequence, anything -- is a 400, not a
        silent drop. The write goes through the PARENT result so the model's
        audit entry is created.

        INCOMPLETE DRAFTS ARE ALLOWED. A blank or whitespace-only value saves,
        because a draft is by definition unfinished and the model permits it;
        completeness is action_mark_entered's gate, not this route's.
        """
        env = request.env
        _require_lab_desk(env)
        body = read_json_body()
        _check_result_payload_shape(body)

        result = _load_result(env, result_id)
        _editable_result(env, result)
        values = _result_write_values(env, result, body)

        def save():
            if values:
                result.write(values)

        try:
            payload = _run_result_action(
                env,
                result,
                save,
                "lab_result_save_response_failed",
                "The draft was not saved because the confirmation could not be "
                "produced. Nothing was changed. Please retry.",
            )
        except AccessError:
            raise
        except (UserError, ValidationError) as error:
            raise ApiError("invalid_workflow_state", str(error), 422) from error

        payload["capabilities"] = lab_desk_capability_flags(env)
        return success_response(payload)

    # ------------------------------------------------------------------
    # 8. Result entry: save and mark entered, atomically
    # ------------------------------------------------------------------
    @http.route(
        "%s/results/<int:result_id>/enter" % LAB_API,
        type="http", auth="user", methods=["POST"], csrf=False,
    )
    @lab_endpoint
    def enter_result(self, result_id, **params):
        """Write the latest values, then action_mark_entered(). ONE savepoint.

        WHY ONE ROUTE WITH A BODY, RATHER THAN THE BROWSER CALLING /save AND
        THEN /enter. "Mark entered" is one act to the technician. As two
        requests it has a failure in the middle -- values saved, transition
        refused or lost in transit -- that leaves the record different from
        both what they asked for and what they were told. Here the write, the
        model's completeness gate and the response are one savepoint: either
        the result is entered with exactly the values on screen, or nothing at
        all has changed and the draft is as it was before the click.

        The body is the same allow-listed shape /save accepts, and may be
        empty. The completeness rule is not restated: action_mark_entered()
        decides it (every ordered line reported once, every result_value
        non-blank after trimming, "0" valid) and its refusal is forwarded as
        `lab_result_incomplete`. No money is involved: hospital_billing
        overrides action_validate, not this method.
        """
        env = request.env
        _require_lab_desk(env)
        body = read_json_body()
        _check_result_payload_shape(body)

        result = _load_result(env, result_id)
        _editable_result(env, result)
        values = _result_write_values(env, result, body)

        def save_and_enter():
            if values:
                result.write(values)
            result.action_mark_entered()

        try:
            payload = _run_result_action(
                env,
                result,
                save_and_enter,
                "lab_result_enter_response_failed",
                "The result was not marked entered because the confirmation "
                "could not be produced. Nothing was changed. Please retry.",
            )
        except AccessError:
            raise
        except ValidationError as error:
            raise ApiError("lab_result_incomplete", str(error), 422) from error
        except UserError as error:
            raise ApiError("invalid_workflow_state", str(error), 422) from error

        payload["capabilities"] = lab_desk_capability_flags(env)
        return success_response(payload)

    # ------------------------------------------------------------------
    # 9. Result validation (Slice 3B)
    # ------------------------------------------------------------------
    @http.route(
        "%s/results/<int:result_id>/validate" % LAB_API,
        type="http", auth="user", methods=["POST"], csrf=False,
    )
    @lab_endpoint
    def validate_result(self, result_id, **params):
        """entered -> validated. THE CONTROLLER DECIDES NOTHING ABOUT BILLING.

        It resolves the result through the caller's own record rules, applies
        the Laboratory Desk's validation policy under lock
        (_validatable_result), and calls ONE authoritative model method:

            hospital.laboratory.result.action_validate()

        WHAT THAT METHOD DOES, VERIFIED FROM SOURCE. The base method re-runs the
        full completeness check and writes `validated`; hospital_billing's
        override then delivers each ordered test's charge through
        hospital.billing.engine -- qty_delivered 0 -> 1, delivery_state
        delivered, delivered_at set. It creates no invoice and no accounting
        entry, does not release the result, does not complete the request, and
        does not make anything visible to the Doctor Desk. No charge method is
        called from here, and no billing rule is restated.

        THE BODY IS IGNORED. Validation changes no value: an entered result is
        read-only on the desk, and correcting it is not this route's business.

        REFUSALS:

          lab_result_not_found            404  unknown or unreadable result
          lab_result_entry_not_available  422  request is not in_progress
          lab_result_ambiguous            409  the request has another result
          lab_result_not_validatable      409  the result is not entered
          lab_result_incomplete           422  the model's completeness check
          lab_result_validation_blocked   422  linkage, billing or charge refusal;
                                               FIXED text, see
                                               VALIDATION_BLOCKED_MESSAGE
          lab_result_validate_response_failed
                                          500  rolled back; nothing changed

        IDEMPOTENCY. The request and result rows are locked and the state
        re-read, so a double-click finds the result already validated (409) --
        a concurrent one is replayed by Odoo after the first commits and gets
        the same answer. The engine's delivery is idempotent as well, so a
        replay can never deliver twice.
        """
        env = request.env
        _require_lab_desk(env)

        result = _load_result(env, result_id)
        _validatable_result(env, result)
        payload = _run_validation(env, result)

        payload["capabilities"] = lab_desk_capability_flags(env)
        return success_response(payload)
