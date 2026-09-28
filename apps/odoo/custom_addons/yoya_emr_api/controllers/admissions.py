"""Admissions Desk API (Admissions Slices 1-4).

    GET  /yoya-emr/api/v1/admissions/session
    GET  /yoya-emr/api/v1/admissions/worklist   ?lane= &ward_id= &q= &limit=
    GET  /yoya-emr/api/v1/admissions/<id>
    GET  /yoya-emr/api/v1/admissions/wards
    GET  /yoya-emr/api/v1/admissions/beds       ?ward_id= &room_id= &state= &q=
    POST /yoya-emr/api/v1/admissions/<id>/admit           (Slice 2)
    POST /yoya-emr/api/v1/admissions/<id>/transfer        (Slice 3)
    POST /yoya-emr/api/v1/admissions/<id>/cancel-request  (Slice 3)
    POST /yoya-emr/api/v1/admissions/<id>/finalize-discharge (Slice 4)

FOUR WRITES. Admit assigns the bed and confirms the admission as a single
atomic act; there is no separate assign-bed route, so a bed is never "held" by
a draft. Transfer moves an admitted patient to another bed. Cancel-request
withdraws a DRAFT request, from either desk. Finalize-discharge (Slice 4) is
the administrative discharge of a patient the doctor has declared medically
ready -- the doctor's own act lives on the Doctor Desk. The Doctor Desk's
admission REQUEST and DISCHARGE REQUEST live in controllers/doctor.py, beside
the visit they belong to. Viewing creates no audit row.

THE CONTROLLER DECIDES NOTHING ABOUT WORKFLOW. Each write route checks the
role, resolves the admission through the caller's own record rules, and hands
the payload to the model's own desk method (_desk_admit, _desk_transfer,
_desk_cancel_request, _desk_finalize_discharge), which owns locking, idempotency, the revision check,
the bed checks and the Slice 0 transition. The controller writes no field and
calls no sudo().

The detail payload carries `financial` (Slice 3): the inpatient financial
state as a key, three booleans and fixed review sentences. No amount.

ONE SAVEPOINT PER MUTATION: the model call and the response serialization run
inside it; refusals are mapped to fixed codes OUTSIDE it, after everything has
rolled back. Serialization and lock failures are re-raised untouched so the
HTTP layer replays the request in a fresh transaction, where the operation
token answers it.

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

from psycopg2 import IntegrityError

from odoo import http
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.http import request
from odoo.osv import expression
from odoo.service.model import PG_CONCURRENCY_EXCEPTIONS_TO_RETRY

from odoo.addons.hospital_admission.models.admission_authority import (
    AdmissionDeskError,
)

from ..services.admission_mutations import (
    ADMIT_KEYS,
    CANCEL_REQUEST_KEYS,
    FINALIZE_DISCHARGE_KEYS,
    TRANSFER_KEYS,
    canonical_token,
    desk_error,
    integrity_code,
    mutation_body,
)
from ..services.admissions_desk_serializers import (
    ACTIVE_LANES,
    BED_STATES,
    LANE_ORDER,
    bed_row_text,
    build_bed_board,
    build_ward_rollups,
    classify_batch,
    serialize_detail,
    serialize_doctor_admission,
    serialize_row,
    serialize_session,
    transfer_counts,
)
from ..services.api_response import (
    ApiError,
    api_error_response,
    error_response,
    parse_int_param,
    read_json_body,
    success_response,
)
from ..services.reception_scope import (
    admissions_desk_capability_flags,
    admissions_desk_role_flags,
    may_admissions_admit,
    may_admissions_cancel_request,
    may_admissions_desk,
    may_admissions_finalize_discharge,
    may_admissions_transfer,
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


def _load_admission_for_mutation(env, admission_id):
    """search(), not browse(): hidden and missing are the same 404."""
    if admission_id <= 0:
        raise desk_error("admission_not_found")
    record = env["hospital.admission"].with_context(active_test=False).search(
        [("id", "=", admission_id)], limit=1
    )
    if not record:
        raise desk_error("admission_not_found")
    return record


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
        may_admit = may_admissions_admit(env)
        may_transfer = may_admissions_transfer(env)
        may_cancel = may_admissions_cancel_request(env)
        rows = [
            serialize_row(
                admission, lane, reasons, encounter, counts.get(admission.id, 0),
                may_admit=may_admit, may_transfer=may_transfer, may_cancel=may_cancel,
                may_finalize=may_admissions_finalize_discharge(env),
            )
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
            "admission": serialize_detail(
                record,
                may_admit=may_admissions_admit(env),
                may_transfer=may_admissions_transfer(env),
                may_cancel=may_admissions_cancel_request(env),
                may_finalize=may_admissions_finalize_discharge(env),
            ),
            "capabilities": admissions_desk_capability_flags(env),
        })

    # ------------------------------------------------------------------
    # Slice 2: admit (assign bed + confirm, atomically)
    # ------------------------------------------------------------------
    @http.route(
        "%s/<int:admission_id>/admit" % ADMISSIONS_API,
        type="http", auth="user", methods=["POST"], csrf=False,
    )
    @admissions_endpoint
    def admissions_admit(self, admission_id, **params):
        """Put a draft admission into a bed.

        Body: {"operation_token": uuid, "expected_revision": int, "bed_id": int}
        Exactly those keys. The ward and room are derived from the bed.
        """
        env = request.env
        # THE ROLE GATE, before the admission is resolved: a refused caller
        # learns nothing about whether the id exists.
        if not may_admissions_admit(env):
            raise desk_error("admission_not_authorized")
        body = mutation_body(ADMIT_KEYS)
        record = _load_admission_for_mutation(env, admission_id)
        return _run_desk_mutation(
            env, record, "admit", body,
            lambda: record._desk_admit(
                body["bed_id"], body["operation_token"], body["expected_revision"]
            ),
        )

    # ------------------------------------------------------------------
    # Slice 3: transfer to another bed
    # ------------------------------------------------------------------
    @http.route(
        "%s/<int:admission_id>/transfer" % ADMISSIONS_API,
        type="http", auth="user", methods=["POST"], csrf=False,
    )
    @admissions_endpoint
    def admissions_transfer(self, admission_id, **params):
        """Move an admitted patient to another bed.

        Body: {"operation_token": uuid, "expected_revision": int, "bed_id": int,
        "reason": text}. Exactly those keys. The destination ward and room are
        derived from the bed. hospital.admission._desk_transfer() owns the
        locks, the replay, the revision, the bed checks and the Slice 0
        transition (release, occupy, immutable history with the destination's
        rate snapshot).
        """
        env = request.env
        if not may_admissions_transfer(env):
            raise desk_error("admission_not_authorized")
        body = mutation_body(TRANSFER_KEYS)
        record = _load_admission_for_mutation(env, admission_id)
        return _run_desk_mutation(
            env, record, "transfer", body,
            lambda: record._desk_transfer(
                body["bed_id"], body["reason"], body["operation_token"], body["expected_revision"]
            ),
        )

    # ------------------------------------------------------------------
    # Slice 3: cancel a DRAFT admission request
    # ------------------------------------------------------------------
    @http.route(
        "%s/<int:admission_id>/cancel-request" % ADMISSIONS_API,
        type="http", auth="user", methods=["POST"], csrf=False,
    )
    @admissions_endpoint
    def admissions_cancel_request(self, admission_id, **params):
        """Withdraw an admission request that has not been admitted.

        Body: {"operation_token": uuid, "expected_revision": int}. Exactly those
        keys. The admissions clerk and oversight may cancel any request; a
        doctor only their own (hospital.admission._desk_may_cancel_request).
        Used by BOTH desks, so the response also carries the Doctor Desk's own
        summary of the visit when the caller can read that visit.
        """
        env = request.env
        if not may_admissions_cancel_request(env):
            raise desk_error("admission_not_authorized")
        body = mutation_body(CANCEL_REQUEST_KEYS)
        record = _load_admission_for_mutation(env, admission_id)
        return _run_desk_mutation(
            env, record, "cancel_request", body,
            lambda: record._desk_cancel_request(
                body["operation_token"], body["expected_revision"]
            ),
            with_doctor_summary=True,
        )

    # ------------------------------------------------------------------
    # Slice 4: administrative final discharge
    # ------------------------------------------------------------------
    @http.route(
        "%s/<int:admission_id>/finalize-discharge" % ADMISSIONS_API,
        type="http", auth="user", methods=["POST"], csrf=False,
    )
    @admissions_endpoint
    def admissions_finalize_discharge(self, admission_id, **params):
        """Discharge a medically ready patient: post the final stay, apply the
        settlement gate, release the bed and complete the visit -- one act.

        Body: {"operation_token": uuid, "expected_revision": int}. Exactly
        those keys. Everything else -- readiness, bed ownership, the visit, the
        money -- is re-derived by hospital.admission._desk_finalize_discharge().

        A refusal for an unsettled account also POSTS THE STAY TO DATE, in its
        own savepoint after the refused one has rolled back: a bed-day that
        started since the cashier last collected must exist as a charge before
        the cashier can collect it. That posting is idempotent, moves no bed and
        changes no workflow state (hospital.admission._desk_post_stay_to_date).
        """
        env = request.env
        if not may_admissions_finalize_discharge(env):
            raise desk_error("admission_not_authorized")
        body = mutation_body(FINALIZE_DISCHARGE_KEYS)
        record = _load_admission_for_mutation(env, admission_id)

        def post_stay_on_settlement_refusal(code):
            if code != "admission_settlement_required":
                return
            try:
                with env.cr.savepoint():
                    record._desk_post_stay_to_date()
            except PG_CONCURRENCY_EXCEPTIONS_TO_RETRY:
                raise
            except Exception:
                _logger.warning(
                    "Admissions: posting the stay to date failed for admission=%s", record.id,
                    exc_info=True,
                )

        return _run_desk_mutation(
            env, record, "final_discharge", body,
            lambda: record._desk_finalize_discharge(
                body["operation_token"], body["expected_revision"]
            ),
            on_refusal=post_stay_on_settlement_refusal,
        )


def _run_desk_mutation(
    env, record, operation_type, body, call, with_doctor_summary=False, on_refusal=None,
):
    """THE one shape every Admissions Desk mutation runs in.

    ONE SAVEPOINT: the model call and the response serialization run inside
    it, so a refusal anywhere rolls every write back. Refusals are mapped to
    fixed codes OUTSIDE it. Serialization and lock failures are re-raised
    untouched so the HTTP layer replays the request in a fresh transaction,
    where the operation token answers it.
    """
    doctor_summary = None
    try:
        with env.cr.savepoint():
            admission, replayed = call()
            payload = serialize_detail(
                admission,
                may_admit=may_admissions_admit(env),
                may_transfer=may_admissions_transfer(env),
                may_cancel=may_admissions_cancel_request(env),
                may_finalize=may_admissions_finalize_discharge(env),
            )
            if with_doctor_summary:
                # Under the CALLER's rules, and without sudo(): the id is a
                # column of the admission the caller already reads, and the
                # appointment itself is searched through their own rules. A
                # clerk who cannot read it simply gets no doctor summary.
                appointment_id = admission.appointment_id.id
                appointment = env["hospital.appointment"].search(
                    [("id", "=", appointment_id)], limit=1
                ) if appointment_id else None
                if appointment:
                    doctor_summary = serialize_doctor_admission(appointment)
    except PG_CONCURRENCY_EXCEPTIONS_TO_RETRY:
        raise
    except AdmissionDeskError as error:
        if on_refusal:
            on_refusal(error.code)
        raise desk_error(error.code) from None
    except IntegrityError as error:
        raise desk_error(integrity_code(error)) from None
    except AccessError:
        raise desk_error("admission_not_authorized") from None
    except (UserError, ValidationError):
        # A deeper layer refused in its own words. Logged, answered with a
        # fixed sentence; the savepoint has already rolled everything back.
        _logger.warning(
            "Admissions %s refused for admission=%s uid=%s", operation_type, record.id, env.uid,
            exc_info=True,
        )
        raise desk_error("admission_integrity_error") from None
    except Exception:
        _logger.exception("Admissions %s failed for admission=%s", operation_type, record.id)
        raise desk_error("admission_mutation_failed") from None

    data = {
        "admission": payload,
        "capabilities": admissions_desk_capability_flags(env),
        "workflow_revision": payload["workflow_revision"],
        "operation": {
            "type": operation_type,
            "token": canonical_token(body["operation_token"]),
            "replayed": bool(replayed),
        },
    }
    if with_doctor_summary:
        data["doctor_admission"] = doctor_summary
    return success_response(data)
