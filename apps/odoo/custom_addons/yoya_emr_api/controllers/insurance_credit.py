"""Insurance/Credit API: review a sponsored visit and authorize the share.

TRANSACTION RULE, inherited from controllers/cashier.py and for the same
reason: an endpoint decorator that catches an exception and returns JSON also
tells Odoo's dispatcher the request succeeded, and the dispatcher then commits.
That once produced confirmed receipts sitting behind HTTP 403s.

So the mutation AND the construction of its success response run inside one
env.cr.savepoint(). An exception anywhere in that block rolls the authorization
back BEFORE the error handler builds a response. An officer is never told
"failed" after a sponsor share was committed.
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
    coerce_number,
    error_response,
    parse_date,
    parse_int_param,
    read_json_body,
    success_response,
)
from ..services.insurance_credit_serializers import (
    charge_needs_decision,
    serialize_officer_visit_detail,
    serialize_officer_worklist_row,
)
from ..services.reception_scope import (
    hospital_day_bounds_utc,
    insurance_credit_capability_flags,
    may_insurance_credit,
)

_logger = logging.getLogger(__name__)

WORKLIST_LIMIT_DEFAULT = 100
WORKLIST_LIMIT_MAX = 300


class AuthorizationResponseError(Exception):
    """The authorization applied, but its response could not be built.

    A distinct type so an AccessError raised while SERIALIZING is never
    mistaken for the authorization path denying the caller.
    """


def insurance_credit_endpoint(func):
    """Stable error envelope. Never leaks tracebacks.

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
        except AuthorizationResponseError:
            return error_response(
                "authorization_response_failed",
                "The authorization was not recorded because its confirmation "
                "could not be produced. Nothing was authorized. Please retry.",
                500,
            )
        except AccessError:
            _logger.warning(
                "Insurance/Credit endpoint %s denied for uid=%s",
                func.__name__,
                request.env.uid,
            )
            return error_response(
                "authorization_not_permitted",
                "You are not authorized to review or authorize sponsor "
                "responsibility for this visit.",
                403,
            )
        except ValidationError as error:
            return _authorization_validation_response(str(error))
        except UserError as error:
            return _authorization_validation_response(str(error))
        except Exception:
            _logger.exception(
                "Unexpected error in YOYA insurance/credit endpoint %s",
                func.__name__,
            )
            return error_response("internal_error", "An unexpected error occurred.", 500)

    return wrapper


def _authorization_validation_response(message):
    """Map the engine's refusals onto codes the desk can branch on."""
    lower = (message or "").lower()
    if "permits at most" in lower or "may have changed" in lower:
        # The proposal went stale: the desk must refetch, not resubmit.
        return error_response("permitted_amount_exceeded", message, 409)
    if "already carries an authorized sponsor share" in lower:
        return error_response("already_authorized", message, 409)
    if "requires a documented reason" in lower:
        return error_response("reason_required", message, 400)
    if "not valid today" in lower or "no payer eligibility" in lower:
        return error_response("eligibility_invalid", message, 400)
    if "frozen" in lower:
        return error_response("decision_frozen", message, 409)
    return error_response("authorization_validation_failed", message, 400)


def _require_insurance_credit(env):
    if not may_insurance_credit(env):
        raise ApiError(
            "insurance_credit_not_authorized",
            "The Insurance/Credit Desk requires the Insurance/Credit Officer, "
            "Hospital Manager or Hospital System Administrator role.",
            403,
        )


def _get_or_404(env, model, record_id, label):
    record = env[model].search([("id", "=", record_id)], limit=1)
    if not record:
        raise ApiError("visit_not_found", "%s not found." % label, 404)
    return record


def _worklist_limit(raw):
    if not raw:
        return WORKLIST_LIMIT_DEFAULT
    value = parse_int_param("limit", raw)
    if value < 1 or value > WORKLIST_LIMIT_MAX:
        raise ApiError(
            "invalid_limit", "'limit' must be between 1 and %s." % WORKLIST_LIMIT_MAX, 400
        )
    return value


def _candidate_domain(env, day, search):
    """Candidates on STORED columns only; membership is decided in Python.

    Queue membership depends on per-charge responsibility_state and on the
    evaluator, neither of which is expressible as a domain on the appointment.
    So the SQL narrows to one day of live visits and the rest is resolved after.
    """
    start, end = hospital_day_bounds_utc(env, day)
    domain = [
        ("appointment_date", ">=", start),
        ("appointment_date", "<", end),
        ("state", "in", ["confirmed", "in_consultation", "done"]),
    ]
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


def _needs_review(appointment):
    """THE queue predicate.

    A visit is in the Insurance/Credit queue when a sponsor decision is
    genuinely outstanding:

      * an eligibility is selected (otherwise there is no sponsor to decide),
      * that eligibility is valid enough to evaluate,
      * live billable charges exist,
      * at least one of them has no live sponsor row.

    Deliberately NOT financial clearance. Clearance answers "may the doctor
    start"; this answers "has anyone decided what the sponsor covers". A fully
    denied visit is financially unclear and yet needs no officer at all.
    """
    encounter = appointment.encounter_id
    if not encounter or not encounter.patient_payer_id:
        return False
    if not encounter.patient_payer_id.is_valid_today:
        return False
    account = encounter.billing_account_id
    if not account:
        return False
    return any(charge_needs_decision(charge) for charge in account.charge_line_ids)


def _coerce_decisions(body):
    decisions = body.get("decisions")
    if not isinstance(decisions, list) or not decisions:
        raise ApiError(
            "authorization_validation_failed",
            "'decisions' must be a non-empty list.",
            400,
        )
    if len(decisions) > 100:
        raise ApiError(
            "authorization_validation_failed",
            "At most 100 charges may be authorized in one request.",
            400,
        )

    coerced = []
    seen = set()
    for entry in decisions:
        if not isinstance(entry, dict):
            raise ApiError(
                "authorization_validation_failed",
                "Each decision must be an object.",
                400,
            )
        charge_id = parse_int_param("charge_id", entry.get("charge_id"))
        if charge_id in seen:
            raise ApiError(
                "authorization_validation_failed",
                "Charge %s appears twice in one request." % charge_id,
                400,
            )
        seen.add(charge_id)
        amount = coerce_number("amount", entry.get("amount"))
        if amount < 0:
            raise ApiError(
                "authorization_validation_failed",
                "An authorized sponsor amount cannot be negative.",
                400,
            )
        reason = entry.get("reason")
        if reason is not None and not isinstance(reason, str):
            raise ApiError(
                "authorization_validation_failed", "'reason' must be a string.", 400
            )
        reference = entry.get("authorization_reference")
        if reference is not None and not isinstance(reference, str):
            raise ApiError(
                "authorization_validation_failed",
                "'authorization_reference' must be a string.",
                400,
            )
        coerced.append(
            {
                "charge_id": charge_id,
                "amount": amount,
                "reason": (reason or "").strip(),
                "authorization_reference": (reference or "").strip() or None,
            }
        )
    return coerced


class YoyaEmrInsuranceCreditController(http.Controller):

    @http.route(
        "/yoya-emr/api/v1/insurance-credit/worklist",
        type="http", auth="user", methods=["GET"], csrf=False,
    )
    @insurance_credit_endpoint
    def worklist(self, **params):
        """Visits waiting on a sponsor decision."""
        env = request.env
        _require_insurance_credit(env)

        day = (
            parse_date(params["date"])
            if params.get("date")
            else fields.Date.context_today(env["hospital.appointment"])
        )
        limit = _worklist_limit(params.get("limit"))
        search = (params.get("q") or "").strip() or None
        include_resolved = params.get("include_resolved") in ("1", "true", "True")

        Appointment = env["hospital.appointment"]
        candidates = Appointment.search(
            _candidate_domain(env, day, search),
            order="appointment_date asc, id asc",
        )

        # ONE bounded elevation, for the derived stage only. The officer holds
        # no ACL on hospital.patient.evaluation and the stage traverses it.
        elevated = candidates.sudo()
        stage_by_id = {a.id: a.front_desk_stage for a in elevated}

        selected = []
        for appointment in elevated:
            if _needs_review(appointment) or (
                include_resolved and appointment.encounter_id.patient_payer_id
            ):
                selected.append(appointment)

        truncated = len(selected) > limit
        rows = [
            serialize_officer_worklist_row(env, appointment, stage_by_id.get(appointment.id))
            for appointment in selected[:limit]
        ]

        return success_response(
            {
                "date": str(day),
                "rows": rows,
                "counts": {
                    "review_required": sum(
                        1 for row in rows if row["review_status"] == "review_required"
                    ),
                    "resolved": sum(
                        1 for row in rows if row["review_status"] == "authorized"
                    ),
                },
                "truncated": truncated,
                "capabilities": insurance_credit_capability_flags(env),
            }
        )

    @http.route(
        "/yoya-emr/api/v1/insurance-credit/visits/<int:appointment_id>",
        type="http", auth="user", methods=["GET"], csrf=False,
    )
    @insurance_credit_endpoint
    def visit_detail(self, appointment_id, **params):
        env = request.env
        _require_insurance_credit(env)
        appointment = _get_or_404(env, "hospital.appointment", appointment_id, "Visit")
        return success_response(
            serialize_officer_visit_detail(env, appointment.sudo())
        )

    @http.route(
        "/yoya-emr/api/v1/insurance-credit/visits/<int:appointment_id>/authorize",
        type="http", auth="user", methods=["POST"], csrf=False,
    )
    @insurance_credit_endpoint
    def authorize(self, appointment_id, **kwargs):
        """Authorize sponsor shares for selected charges, atomically."""
        env = request.env
        _require_insurance_credit(env)

        body = read_json_body()
        decisions = _coerce_decisions(body)

        idempotency_key = body.get("idempotency_key")
        if idempotency_key is not None:
            if not isinstance(idempotency_key, str) or not idempotency_key.strip():
                raise ApiError(
                    "authorization_validation_failed",
                    "'idempotency_key' must be a non-empty string when supplied.",
                    400,
                )
            idempotency_key = idempotency_key.strip()[:96]

        appointment = _get_or_404(env, "hospital.appointment", appointment_id, "Visit")
        encounter = appointment.encounter_id
        if not encounter:
            raise ApiError(
                "authorization_validation_failed",
                "Visit %s has no encounter yet." % (appointment.appointment_code or appointment.id),
                400,
            )
        account = encounter.billing_account_id
        if not account:
            raise ApiError(
                "authorization_validation_failed",
                "Encounter %s has no billing account." % encounter.name,
                400,
            )

        # ONE atomic unit: the authorization AND its serialized response. See
        # the module docstring; this is the cashier's savepoint discipline.
        with env.cr.savepoint():
            env["hospital.billing.engine"].authorize_visit_coverage(
                account, decisions, request_token=idempotency_key
            )
            try:
                account.invalidate_recordset()
                appointment.invalidate_recordset()
                response = success_response(
                    serialize_officer_visit_detail(env, appointment.sudo())
                )
            except Exception as error:
                _logger.exception(
                    "Insurance/Credit authorization response failed for "
                    "appointment=%s uid=%s; rolling the authorization back",
                    appointment_id,
                    env.uid,
                )
                raise AuthorizationResponseError(str(error)) from error

        return response
