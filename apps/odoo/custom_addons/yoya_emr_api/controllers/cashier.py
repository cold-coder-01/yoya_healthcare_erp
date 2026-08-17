"""Cashier API for operational payment intake.

The browser never talks to Odoo directly. This controller validates the HTTP
shape, derives the billing account from the visit, and delegates the actual
money movement to hospital_billing's canonical payment wizard path.

TRANSACTION RULE (the reason this file has a savepoint in it)
------------------------------------------------------------
The mutation and the construction of its success response are ONE unit. They
have to be, because an endpoint decorator that catches an exception and
returns JSON also tells Odoo's dispatcher that the request succeeded -- and
the dispatcher then commits. That combination previously produced confirmed
receipts sitting behind HTTP 403 responses: money taken, client told it was
refused.

So everything from record_operational_payment() through the serialized
response body runs inside env.cr.savepoint(). Any exception in that block
rolls the payment back BEFORE the error handler produces a response. There is
no manual unwinding of receipt or allocation rows anywhere in here; the
savepoint is the mechanism.
"""
import functools
import logging

from odoo import http
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.http import request

from odoo.addons.hospital_billing.models.charge_receipt import (
    PAYMENT_METHODS,
    REFERENCE_REQUIRED,
)

from odoo import fields
from odoo.osv import expression

from ..services.api_response import (
    ApiError,
    api_error_response,
    coerce_number,
    error_response,
    parse_date,
    parse_int_param,
    read_json_body,
    success_response,
)
from ..services.cashier_serializers import (
    serialize_cashier_payment_result,
    serialize_cashier_visit_detail,
    serialize_cashier_worklist_row,
)
from ..services.reception_scope import (
    cashier_capability_flags,
    hospital_day_bounds_utc,
    may_cashier_desk,
    may_record_payment,
)

_logger = logging.getLogger(__name__)

PAYMENT_METHOD_KEYS = {key for key, _label in PAYMENT_METHODS}

# Which derived stages the Cashier Desk is about. 'awaiting_cashier' is the
# authoritative membership test -- it already means "triage is complete AND
# money still stands between this patient and the doctor", and it already
# excludes emergency bypass and fully-authorized sponsorship because
# _is_payment_blocking() resolves both to False.
#
# 'ready_doctor' is offered as an opt-in lane so a cashier can confirm a visit
# they just settled. It is never the default.
CASHIER_STAGES = ("awaiting_cashier", "ready_doctor")
DEFAULT_CASHIER_STAGES = ("awaiting_cashier",)

WORKLIST_LIMIT_DEFAULT = 100
WORKLIST_LIMIT_MAX = 300


class PaymentResponseError(Exception):
    """The payment was authorized and applied, but its response failed.

    A separate type on purpose. Without it, an AccessError raised while
    SERIALIZING the result is indistinguishable from an AccessError raised by
    the intake guard, and the client gets told it was not authorized to record
    a payment that it was in fact authorized to record -- and which, before
    the savepoint, had already been committed.
    """


def cashier_endpoint(func):
    """Stable cashier-payment error envelope. Never leaks tracebacks.

    Ordering matters: AccessError and ValidationError both subclass UserError
    in Odoo, so the broad handler comes last.
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
        except PaymentResponseError:
            # Already logged with its cause at the raise site, and the
            # savepoint has already rolled the payment back.
            return error_response(
                "payment_response_failed",
                "The payment was not recorded because its confirmation could "
                "not be produced. Nothing was charged. Please retry.",
                500,
            )
        except AccessError:
            # Reaching here means the AUTHORIZATION path denied the caller --
            # either reading this visit or the operational-intake guard in
            # hospital_billing. Response-building failures cannot land here;
            # they are PaymentResponseError above.
            _logger.warning(
                "Cashier endpoint %s denied for uid=%s",
                func.__name__,
                request.env.uid,
            )
            return error_response(
                "payment_not_authorized",
                "You are not authorized to record operational payment for this visit.",
                403,
            )
        except ValidationError as error:
            return _payment_validation_response(str(error))
        except UserError as error:
            return _payment_validation_response(str(error))
        except Exception:
            _logger.exception(
                "Unexpected error in YOYA cashier endpoint %s", func.__name__
            )
            return error_response(
                "internal_error", "An unexpected error occurred.", 500
            )

    return wrapper


def _payment_validation_response(message):
    lower = (message or "").lower()
    if "idempotency key" in lower and "different payment" in lower:
        return error_response("idempotency_conflict", message, 409)
    if "greater than zero" in lower or "positive amount" in lower:
        return error_response("invalid_amount", message, 400)
    if "requires an external reference" in lower:
        return error_response("payment_reference_required", message, 400)
    if "no payable charges" in lower or "allocate a positive amount" in lower:
        return error_response("no_payable_charges", message, 400)
    # THE ENFORCE-MODE CEILING.
    #
    # hospital_billing refuses an allocation that exceeds the patient's residual
    # with "...has been allocated to the patient, but the patient is responsible
    # for only...". That message matches none of the older phrases, so it used
    # to fall through to the generic branch and the desk could not tell a
    # ceiling breach from a malformed request -- the difference between "collect
    # less" and "fix your payload".
    #
    # Matched on the model's own wording rather than by changing that wording:
    # the ValidationError text is read by hospital_billing's tests, and this is
    # the API contract layer, which is where an HTTP error code belongs.
    if (
        "overpayment" in lower
        or "may allocate at most" in lower
        or "the patient is responsible for only" in lower
    ):
        return error_response("overpayment_not_allowed", message, 400)
    return error_response("payment_validation_failed", message, 400)


def _get_or_404(env, model, record_id, label):
    record = env[model].search([("id", "=", record_id)], limit=1)
    if not record:
        raise ApiError("visit_not_found", "%s not found." % label, 404)
    return record


def _coerce_optional_text(body, field_name):
    value = body.get(field_name)
    if value in (None, False, ""):
        return None
    if not isinstance(value, str):
        raise ApiError(
            "payment_validation_failed", "'%s' must be a string." % field_name, 400
        )
    return value.strip() or None


def _coerce_payment_body():
    body = read_json_body()

    amount = coerce_number("amount", body.get("amount"))
    if amount <= 0:
        raise ApiError("invalid_amount", "Payment amount must be greater than zero.", 400)

    payment_method = body.get("payment_method")
    if not isinstance(payment_method, str) or payment_method not in PAYMENT_METHOD_KEYS:
        raise ApiError(
            "payment_validation_failed",
            "'payment_method' must be one of %s." % ", ".join(sorted(PAYMENT_METHOD_KEYS)),
            400,
        )

    payment_reference = _coerce_optional_text(body, "payment_reference")
    if payment_method in REFERENCE_REQUIRED and not payment_reference:
        raise ApiError(
            "payment_reference_required",
            "A %s payment requires an external reference."
            % dict(PAYMENT_METHODS)[payment_method],
            400,
        )

    note = _coerce_optional_text(body, "note")

    idempotency_key = body.get("idempotency_key")
    if not isinstance(idempotency_key, str) or not idempotency_key.strip():
        raise ApiError(
            "payment_validation_failed",
            "'idempotency_key' is required and must be a non-empty string.",
            400,
        )
    idempotency_key = idempotency_key.strip()
    if len(idempotency_key) > 128:
        raise ApiError(
            "payment_validation_failed",
            "'idempotency_key' must be 128 characters or fewer.",
            400,
        )

    forbidden = set(body) & {"billing_account_id", "charge_line_id", "charge_line_ids", "encounter_id"}
    if forbidden:
        raise ApiError(
            "payment_validation_failed",
            "These fields cannot be supplied for cashier payment: %s."
            % ", ".join(sorted(forbidden)),
            400,
        )

    return {
        "amount": amount,
        "payment_method": payment_method,
        "payment_reference": payment_reference,
        "note": note,
        "intake_token": idempotency_key,
    }


def _require_cashier_desk(env):
    if not may_cashier_desk(env):
        raise ApiError(
            "cashier_desk_not_authorized",
            "The Cashier Desk requires the Hospital Cashier, Accountant, "
            "Hospital Manager or Hospital System Administrator role.",
            403,
        )


def _requested_lanes(raw):
    """Stage filter, validated against the allowlist. Unknown values are 400."""
    if not raw:
        return DEFAULT_CASHIER_STAGES
    requested = tuple(part.strip() for part in raw.split(",") if part.strip())
    unknown = [stage for stage in requested if stage not in CASHIER_STAGES]
    if unknown:
        raise ApiError(
            "invalid_stage",
            "Unknown cashier stage(s): %s. Valid values: %s."
            % (", ".join(sorted(unknown)), ", ".join(CASHIER_STAGES)),
            400,
        )
    return requested or DEFAULT_CASHIER_STAGES


def _worklist_limit(raw):
    if not raw:
        return WORKLIST_LIMIT_DEFAULT
    value = parse_int_param("limit", raw)
    if value < 1 or value > WORKLIST_LIMIT_MAX:
        raise ApiError(
            "invalid_limit",
            "'limit' must be between 1 and %s." % WORKLIST_LIMIT_MAX,
            400,
        )
    return value


def _cashier_candidate_domain(env, day, department_id, search):
    """Candidates, selected on STORED fields only.

    front_desk_stage is a non-stored compute, so it cannot appear in a domain at
    all -- and it traverses evaluation_ids, which a Cashier may not read. The
    stage is therefore resolved in Python, under sudo(), AFTER this narrows the
    set to one day's confirmed visits. Selecting broadly and filtering in Python
    is the only correct order here; it is also why the day window is mandatory.
    """
    start, end = hospital_day_bounds_utc(env, day)
    domain = [
        ("appointment_date", ">=", start),
        ("appointment_date", "<", end),
        # Cancelled and draft visits are never a cashier's business, and this is
        # a stored column so it costs nothing to exclude in SQL.
        ("state", "in", ["confirmed", "in_consultation", "done"]),
    ]
    if department_id:
        domain.append(("department_id", "=", department_id))
    if search:
        domain = expression.AND(
            [
                domain,
                [
                    "|", "|",
                    ("patient_id.name", "ilike", search),
                    ("patient_id.identification_code", "ilike", search),
                    ("appointment_code", "ilike", search),
                ],
            ]
        )
    return domain


class YoyaEmrCashierController(http.Controller):

    @http.route(
        "/yoya-emr/api/v1/cashier/worklist",
        type="http", auth="user", methods=["GET"], csrf=False,
    )
    @cashier_endpoint
    def worklist(self, **params):
        """The Cashier queue.

        THE ACCESS-CONTROL SHAPE OF THIS ENDPOINT IS THE WHOLE DESIGN.

        A Hospital Cashier holds read ACLs on patient, appointment, encounter,
        billing account, charge line, receipt and allocation -- and NOT on
        hospital.patient.evaluation. Both front_desk_stage and
        clinical_queue_stage are non-stored computes that traverse
        evaluation_ids, so reading either as the cashier raises AccessError.
        That is the same failure that once returned 403 to a cashier whose
        payment had already been committed.

        So: select as the CASHIER (record rules apply normally), resolve the
        stage on a sudo() copy of exactly those records, and serialize only
        cashier-safe fields. The sudo() is bounded to one field on one
        recordset; no evaluation data is read, returned or logged.
        """
        env = request.env
        _require_cashier_desk(env)

        day = (
            parse_date(params["date"])
            if params.get("date")
            else fields.Date.context_today(env["hospital.appointment"])
        )
        stages = _requested_lanes(params.get("stage"))
        limit = _worklist_limit(params.get("limit"))
        department_id = (
            parse_int_param("department_id", params["department_id"])
            if params.get("department_id")
            else None
        )
        search = (params.get("q") or "").strip() or None

        Appointment = env["hospital.appointment"]
        candidates = Appointment.search(
            _cashier_candidate_domain(env, day, department_id, search),
            order="appointment_date asc, id asc",
        )

        # ONE bounded elevation: the derived stage, and nothing else. Reading it
        # on the whole recordset batches the compute rather than going per row.
        elevated = candidates.sudo()
        stage_by_id = {
            appointment.id: appointment.front_desk_stage
            for appointment in elevated
        }

        selected = [
            appointment
            for appointment in candidates
            if stage_by_id.get(appointment.id) in stages
        ]
        truncated = len(selected) > limit
        rows = [
            serialize_cashier_worklist_row(
                appointment, stage_by_id.get(appointment.id)
            )
            for appointment in selected[:limit]
        ]

        # Counters describe the SAME day window the rows were drawn from, so the
        # queue and its totals can never describe different populations.
        counts = {"awaiting_cashier": 0, "ready_doctor": 0}
        for appointment in candidates:
            stage = stage_by_id.get(appointment.id)
            if stage in counts:
                counts[stage] += 1
        lane_counts = {"collect": 0, "partial": 0, "blocked": 0, "cleared": 0}
        for row in rows:
            lane_counts[row["lane"]] = lane_counts.get(row["lane"], 0) + 1

        return success_response(
            {
                "date": str(day),
                "stages": list(stages),
                "rows": rows,
                "counts": counts,
                "lane_counts": lane_counts,
                "truncated": truncated,
                "capabilities": cashier_capability_flags(env),
            }
        )

    @http.route(
        "/yoya-emr/api/v1/cashier/visits/<int:appointment_id>",
        type="http", auth="user", methods=["GET"], csrf=False,
    )
    @cashier_endpoint
    def visit_detail(self, appointment_id, **params):
        """Canonical cashier visit payload.

        Returns exactly what a successful payment returns, minus the receipt, so
        the client has one shape to render and refreshing after payment is a
        re-render rather than a second mapping.
        """
        env = request.env
        _require_cashier_desk(env)

        appointment = _get_or_404(env, "hospital.appointment", appointment_id, "Visit")
        return success_response(serialize_cashier_visit_detail(env, appointment))

    @http.route(
        "/yoya-emr/api/v1/cashier/visits/<int:appointment_id>/payment",
        type="http", auth="user", methods=["POST"], csrf=False,
    )
    @cashier_endpoint
    def record_visit_payment(self, appointment_id, **kwargs):
        env = request.env
        if not may_record_payment(env):
            raise ApiError(
                "payment_not_authorized",
                "Recording operational payment requires the Hospital Cashier, "
                "Accountant, Hospital Manager or Hospital System Administrator role.",
                403,
            )

        body = _coerce_payment_body()
        appointment = _get_or_404(env, "hospital.appointment", appointment_id, "Visit")
        encounter = appointment.encounter_id
        if not encounter:
            raise ApiError(
                "payment_validation_failed",
                "Visit %s has no encounter yet." % (appointment.appointment_code or appointment.id),
                400,
            )

        account = encounter.billing_account_id
        if not account:
            raise ApiError(
                "payment_validation_failed",
                "Encounter %s has no billing account." % encounter.name,
                400,
            )

        if account.encounter_id != encounter:
            raise ApiError(
                "payment_validation_failed",
                "The billing account does not belong to this encounter.",
                400,
            )
        if account.patient_id != appointment.patient_id:
            raise ApiError(
                "payment_validation_failed",
                "The billing account does not belong to this patient.",
                400,
            )
        if account.company_id and encounter.company_id and account.company_id != encounter.company_id:
            raise ApiError(
                "payment_validation_failed",
                "The billing account company does not match this encounter.",
                400,
            )

        # ONE atomic unit: mutation + payload + serialized response body.
        #
        # env.cr.savepoint() is a _FlushingSavepoint: it flushes on entry, and
        # on any exception it clears the pending ORM state and issues
        # ROLLBACK TO SAVEPOINT before re-raising. So by the time the decorator
        # above builds an error response, the receipt and its allocations are
        # gone. The response object is built in here too, so a failure while
        # encoding it cannot commit the payment either.
        with env.cr.savepoint():
            receipt = account.record_operational_payment(**body)

            try:
                account.invalidate_recordset()
                appointment.invalidate_recordset()
                receipt.invalidate_recordset()
                response = success_response(
                    serialize_cashier_payment_result(env, appointment, receipt)
                )
            except Exception as error:
                # Log the real cause here -- the client never sees it. Without
                # this the failure was completely silent: the old AccessError
                # branch returned JSON and logged nothing at all.
                _logger.exception(
                    "Cashier payment response failed for appointment=%s uid=%s; "
                    "rolling the payment back",
                    appointment_id,
                    env.uid,
                )
                raise PaymentResponseError(str(error)) from error

        return response
