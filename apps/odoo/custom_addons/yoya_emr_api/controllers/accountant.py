"""ACCOUNTANT DESK API: inpatient refund follow-up and settlement review.

    GET  /yoya-emr/api/v1/accountant/session
    GET  /yoya-emr/api/v1/accountant/worklist?lane=&q=
    GET  /yoya-emr/api/v1/accountant/admissions/<id>
    POST /yoya-emr/api/v1/accountant/admissions/<id>/refund

Accountant, Hospital Manager, System Administrator (hospital_billing's
ACCOUNTING_GROUPS). Every figure is the settlement authority's; the refund is
hospital.admission._cashier_record_refund(), the existing accounting act --
no second ledger, no new formula, no payment intake, no clinical act. The
worklist is NOT date-driven: a refund owed on a stay discharged last week is
still here. No sudo() in this file: the model methods decide visibility by the
caller's own record rules.
"""
import functools
import logging

from odoo import http
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.http import request

from odoo.addons.hospital_admission.models.admission_accounting import ACCOUNTING_LANES
from odoo.addons.hospital_admission.models.admission_cashier import CashierSettlementError
from odoo.addons.hospital_admission.models.admission_financials import (
    FINANCIAL_REVIEW_MESSAGES,
)

from ..services.accountant_serializers import (
    serialize_accountant_detail,
    serialize_accountant_row,
)
from ..services.api_response import (
    ApiError,
    api_error_response,
    coerce_number,
    error_response,
    read_json_body,
    success_response,
)
from ..services.reception_scope import accountant_capability_flags, may_accountant_desk
from .cashier import SETTLEMENT_STATUS

_logger = logging.getLogger(__name__)

REFUND_BODY_KEYS = frozenset({"amount", "reason", "idempotency_key"})
SEARCH_MAX_LENGTH = 80


def accountant_endpoint(func):
    """Stable error envelope; never leaks a traceback."""

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        try:
            if request.env.user._is_public():
                return error_response("authentication_required", "Authentication is required.", 401)
            return func(*args, **kwargs)
        except ApiError as error:
            return api_error_response(error)
        except CashierSettlementError as error:
            return api_error_response(_settlement_error(error))
        except AccessError:
            _logger.warning("Accountant endpoint %s denied for uid=%s", func.__name__, request.env.uid)
            return error_response(
                "accountant_desk_not_authorized",
                "The Accountant Desk requires the Accountant, Hospital Manager or "
                "Hospital System Administrator role.",
                403,
            )
        except (UserError, ValidationError):
            _logger.warning("Accountant endpoint %s refused", func.__name__, exc_info=True)
            return error_response(
                "accountant_action_refused",
                "The action could not be completed. Nothing was changed.",
                409,
            )
        except Exception:
            _logger.exception("Accountant endpoint %s failed", func.__name__)
            return error_response(
                "accountant_request_failed",
                "The Accountant Desk request failed. Nothing was changed.",
                500,
            )

    return wrapper


def _settlement_error(error):
    return ApiError(error.code, str(error), SETTLEMENT_STATUS.get(error.code, 400))


def _require_accountant_desk(env):
    if not may_accountant_desk(env):
        raise ApiError(
            "accountant_desk_not_authorized",
            "The Accountant Desk requires the Accountant, Hospital Manager or "
            "Hospital System Administrator role.",
            403,
        )


def _lanes(raw):
    if not raw or raw == "all":
        return ACCOUNTING_LANES
    lanes = tuple(part.strip() for part in raw.split(",") if part.strip())
    unknown = [lane for lane in lanes if lane not in ACCOUNTING_LANES]
    if unknown or not lanes:
        raise ApiError(
            "invalid_lane",
            "'lane' must be one of: all, %s." % ", ".join(ACCOUNTING_LANES),
            400,
        )
    return lanes


def _detail(env, admission):
    env.invalidate_all()
    return serialize_accountant_detail(
        env, admission._accounting_detail(), FINANCIAL_REVIEW_MESSAGES, may_accountant_desk(env)
    )


class YoyaEmrAccountantController(http.Controller):

    @http.route(
        "/yoya-emr/api/v1/accountant/session",
        type="http", auth="user", methods=["GET"], csrf=False,
    )
    @accountant_endpoint
    def accountant_session(self, **params):
        """Who is signed in and what this desk may offer them. Not gated, so
        the page can say "not your workstation" instead of failing blind."""
        env = request.env
        user = env.user
        return success_response({
            "user": {"id": user.id, "name": user.name, "login": user.login},
            "company": {"id": user.company_id.id, "name": user.company_id.name},
            "capabilities": accountant_capability_flags(env),
        })

    @http.route(
        "/yoya-emr/api/v1/accountant/worklist",
        type="http", auth="user", methods=["GET"], csrf=False,
    )
    @accountant_endpoint
    def accountant_worklist(self, lane=None, q=None, **params):
        env = request.env
        _require_accountant_desk(env)
        lanes = _lanes(lane)
        search = (q or "").strip()[:SEARCH_MAX_LENGTH] or None
        # Counts are over EVERY lane, so the tabs stay honest under a filter.
        facts, truncated = env["hospital.admission"]._accounting_census(search=search)
        counts = {name: 0 for name in ACCOUNTING_LANES}
        for item in facts:
            counts[item["accounting_lane"]] += 1
        rows = [serialize_accountant_row(item) for item in facts if item["accounting_lane"] in lanes]
        return success_response({
            "lanes": list(lanes),
            "counts": counts,
            "rows": rows,
            "truncated": truncated,
        })

    @http.route(
        "/yoya-emr/api/v1/accountant/admissions/<int:admission_id>",
        type="http", auth="user", methods=["GET"], csrf=False,
    )
    @accountant_endpoint
    def accountant_detail(self, admission_id, **params):
        env = request.env
        _require_accountant_desk(env)
        admission = env["hospital.admission"]._accounting_find(admission_id)
        return success_response(_detail(env, admission))

    @http.route(
        "/yoya-emr/api/v1/accountant/admissions/<int:admission_id>/refund",
        type="http", auth="user", methods=["POST"], csrf=False,
    )
    @accountant_endpoint
    def accountant_refund(self, admission_id, **params):
        """Record a refund of unapplied patient credit -- discharged stays
        included. Body: exactly {amount, reason, idempotency_key}.

        The model re-derives everything under the admission's row lock: the
        caller's accounting role, that care is over, the settlement state,
        the refundable balance (amount may not exceed it; a smaller amount is
        a partial refund and the rest stays due) and the token (a replay
        returns the first result and refunds nothing twice).
        """
        env = request.env
        _require_accountant_desk(env)
        body = read_json_body()
        if not isinstance(body, dict) or set(body) != REFUND_BODY_KEYS:
            raise ApiError(
                "refund_validation_failed",
                "A refund body is exactly: amount, reason, idempotency_key.",
                400,
            )
        amount = coerce_number("amount", body.get("amount"))
        admission = env["hospital.admission"]._accounting_find(admission_id)
        with env.cr.savepoint():
            admission, replayed = admission._cashier_record_refund(
                amount, body.get("reason"), body.get("idempotency_key")
            )
            payload = _detail(env, admission)
        payload["replayed"] = bool(replayed)
        return success_response(payload)
