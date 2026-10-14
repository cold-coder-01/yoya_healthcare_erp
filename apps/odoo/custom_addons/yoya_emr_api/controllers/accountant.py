"""ACCOUNTANT DESK API: inpatient refund follow-up and settlement review.

    GET  /yoya-emr/api/v1/accountant/session
    GET  /yoya-emr/api/v1/accountant/worklist?lane=&q=
    GET  /yoya-emr/api/v1/accountant/admissions/<id>
    POST /yoya-emr/api/v1/accountant/admissions/<id>/refund
    POST /yoya-emr/api/v1/accountant/admissions/<id>/post-accounting
    POST /yoya-emr/api/v1/accountant/admissions/<id>/post-legacy-accounting

Accountant, Hospital Manager, System Administrator (hospital_billing's
ACCOUNTING_GROUPS). Every figure is the settlement authority's; the refund is
hospital.admission._cashier_record_refund(), the existing accounting act --
no second ledger, no new formula, no payment intake, no clinical act. Posting
is hospital.admission._accounting_post() / _accounting_post_legacy(): the
accounting bridge's domain authority -- this file reproduces no accounting
rule and names no account or journal, only a business payment method. The
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
from .cashier import REFUND_REQUIRED_KEYS, SETTLEMENT_STATUS

_logger = logging.getLogger(__name__)

POST_ACCOUNTING_BODY_KEYS = frozenset({"idempotency_key"})
LEGACY_REQUIRED_KEYS = frozenset({"idempotency_key", "payment_method"})
LEGACY_OPTIONAL_KEYS = frozenset({"payment_reference", "accounting_date"})
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
        included. Body: {amount, reason, idempotency_key, payment_method}
        plus payment_reference for a non-cash method. Atomic with its
        accounting: the stay's accounting close and the refund entry post in
        the same savepoint, or nothing is refunded.

        The model re-derives everything under the admission's row lock: the
        caller's accounting role, that care is over, the settlement state,
        the refundable balance (amount may not exceed it; a smaller amount is
        a partial refund and the rest stays due) and the token (a replay
        returns the first result and refunds nothing twice).
        """
        env = request.env
        _require_accountant_desk(env)
        body = read_json_body()
        if (
            not isinstance(body, dict)
            or not REFUND_REQUIRED_KEYS <= set(body)
            or set(body) - REFUND_REQUIRED_KEYS - {"payment_reference"}
        ):
            raise ApiError(
                "refund_validation_failed",
                "A refund body is exactly: amount, reason, idempotency_key, "
                "payment_method and, for a non-cash refund, payment_reference.",
                400,
            )
        amount = coerce_number("amount", body.get("amount"))
        admission = env["hospital.admission"]._accounting_find(admission_id)
        with env.cr.savepoint():
            admission, replayed = admission._cashier_record_refund(
                amount, body.get("reason"), body.get("idempotency_key"),
                payment_method=body.get("payment_method"),
                payment_reference=body.get("payment_reference"),
            )
            payload = _detail(env, admission)
        payload["replayed"] = bool(replayed)
        return success_response(payload)

    @http.route(
        "/yoya-emr/api/v1/accountant/admissions/<int:admission_id>/post-accounting",
        type="http", auth="user", methods=["POST"], csrf=False,
    )
    @accountant_endpoint
    def accountant_post_accounting(self, admission_id, **params):
        """Post a DISCHARGED stay's accounting: unposted receipts, delivered
        care invoice, advance application. Body: exactly {idempotency_key}.
        A replay returns the first result and posts nothing twice."""
        env = request.env
        _require_accountant_desk(env)
        body = read_json_body()
        if not isinstance(body, dict) or set(body) != POST_ACCOUNTING_BODY_KEYS:
            raise ApiError(
                "accounting_validation_failed",
                "A post-accounting body is exactly: idempotency_key.",
                400,
            )
        admission = env["hospital.admission"]._accounting_find(admission_id)
        with env.cr.savepoint():
            operation, replayed = admission._accounting_post(body.get("idempotency_key"))
            payload = _detail(env, admission)
        payload["replayed"] = bool(replayed)
        payload["posted_reference"] = operation.sudo().name
        return success_response(payload)

    @http.route(
        "/yoya-emr/api/v1/accountant/admissions/<int:admission_id>/post-legacy-accounting",
        type="http", auth="user", methods=["POST"], csrf=False,
    )
    @accountant_endpoint
    def accountant_post_legacy_accounting(self, admission_id, **params):
        """Post the accounting of refunds recorded before refund accounting
        existed. Body: {idempotency_key, payment_method} plus
        payment_reference (non-cash) and accounting_date (YYYY-MM-DD,
        optional, not in the future). The method is the Accountant's
        statement -- never assumed."""
        env = request.env
        _require_accountant_desk(env)
        body = read_json_body()
        if (
            not isinstance(body, dict)
            or not LEGACY_REQUIRED_KEYS <= set(body)
            or set(body) - LEGACY_REQUIRED_KEYS - LEGACY_OPTIONAL_KEYS
        ):
            raise ApiError(
                "accounting_validation_failed",
                "A legacy-accounting body is exactly: idempotency_key, payment_method "
                "and, where needed, payment_reference and accounting_date.",
                400,
            )
        admission = env["hospital.admission"]._accounting_find(admission_id)
        with env.cr.savepoint():
            operation, replayed = admission._accounting_post_legacy(
                body.get("idempotency_key"),
                body.get("payment_method"),
                payment_reference=body.get("payment_reference"),
                accounting_date=body.get("accounting_date"),
            )
            payload = _detail(env, admission)
        payload["replayed"] = bool(replayed)
        payload["posted_reference"] = operation.sudo().name
        return success_response(payload)
