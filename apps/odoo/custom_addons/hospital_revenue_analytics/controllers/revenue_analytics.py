"""REVENUE ANALYTICS API: read-only Finance and Executive revenue intelligence.

    GET /yoya-emr/api/v1/revenue-analytics/session
    GET /yoya-emr/api/v1/revenue-analytics/summary       Finance, Executive
    GET /yoya-emr/api/v1/revenue-analytics/trend         Finance, Executive
    GET /yoya-emr/api/v1/revenue-analytics/doctors       Finance, Executive
    GET /yoya-emr/api/v1/revenue-analytics/services      Finance, Executive
    GET /yoya-emr/api/v1/revenue-analytics/departments   Finance, Executive
    GET /yoya-emr/api/v1/revenue-analytics/unbilled      Finance
    GET /yoya-emr/api/v1/revenue-analytics/data-quality  Finance

Shared query parameters: date_from, date_to (YYYY-MM-DD, local calendar
dates), company, doctor, department (an id or "unassigned"), category,
basis=recognized|delivered. Extra: grain (trend), sort (doctors), group
(services).

THIN ON PURPOSE. Every figure, every bound and every access decision is
hospital.revenue.analytics's; this file only maps HTTP to it and errors to the
API envelope. There is no POST route: nothing here can change anything.
"""
import functools
import logging

from odoo import http
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.http import request

from odoo.addons.yoya_emr_api.services.api_response import error_response, success_response

from ..models.revenue_analytics import RevenueAnalyticsError
from ..services.revenue_analytics_serializers import serialize_payload, serialize_session

_logger = logging.getLogger(__name__)

BASE = "/yoya-emr/api/v1/revenue-analytics"
FILTER_KEYS = ("date_from", "date_to", "company", "doctor", "department", "category", "basis")


def analytics_endpoint(func):
    """Stable error envelope; never leaks a traceback."""

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        try:
            if request.env.user._is_public():
                return error_response("authentication_required", "Authentication is required.", 401)
            return func(*args, **kwargs)
        except RevenueAnalyticsError as error:
            return error_response(error.code, error.message, error.status)
        except AccessError:
            _logger.warning("Revenue analytics %s denied for uid=%s", func.__name__, request.env.uid)
            return error_response(
                "revenue_analytics_not_authorized",
                "Revenue analytics requires the Hospital Finance Analytics or Hospital "
                "Executive role. This view is not available to your account.",
                403,
            )
        except (UserError, ValidationError):
            _logger.warning("Revenue analytics %s refused", func.__name__, exc_info=True)
            return error_response(
                "revenue_analytics_refused", "The analytics request could not be completed.", 409
            )
        except Exception:
            _logger.exception("Revenue analytics %s failed", func.__name__)
            return error_response(
                "revenue_analytics_failed", "The analytics request failed.", 500
            )

    return wrapper


def _filters(params):
    return {key: params.get(key) for key in FILTER_KEYS}


def _service():
    return request.env["hospital.revenue.analytics"]


def _respond(data):
    return success_response(serialize_payload(data, _service().access_level()))


class YoyaRevenueAnalyticsController(http.Controller):

    @http.route(BASE + "/session", type="http", auth="user", methods=["GET"], csrf=False)
    @analytics_endpoint
    def analytics_session(self, **params):
        """Who is signed in and what the dashboard may show them. Not gated,
        so the page can explain a refusal instead of failing blind."""
        env = request.env
        return success_response(serialize_session(_service().session(), env.user, env.company))

    @http.route(BASE + "/summary", type="http", auth="user", methods=["GET"], csrf=False)
    @analytics_endpoint
    def analytics_summary(self, **params):
        return _respond(_service().summary(_filters(params)))

    @http.route(BASE + "/trend", type="http", auth="user", methods=["GET"], csrf=False)
    @analytics_endpoint
    def analytics_trend(self, grain=None, **params):
        return _respond(_service().trend(_filters(params), grain=grain))

    @http.route(BASE + "/doctors", type="http", auth="user", methods=["GET"], csrf=False)
    @analytics_endpoint
    def analytics_doctors(self, sort=None, **params):
        return _respond(_service().doctors(_filters(params), sort=sort))

    @http.route(BASE + "/services", type="http", auth="user", methods=["GET"], csrf=False)
    @analytics_endpoint
    def analytics_services(self, group=None, **params):
        return _respond(_service().services(_filters(params), group=group))

    @http.route(BASE + "/departments", type="http", auth="user", methods=["GET"], csrf=False)
    @analytics_endpoint
    def analytics_departments(self, **params):
        return _respond(_service().departments(_filters(params)))

    @http.route(BASE + "/unbilled", type="http", auth="user", methods=["GET"], csrf=False)
    @analytics_endpoint
    def analytics_unbilled(self, **params):
        return _respond(_service().unbilled(_filters(params)))

    @http.route(BASE + "/data-quality", type="http", auth="user", methods=["GET"], csrf=False)
    @analytics_endpoint
    def analytics_data_quality(self, **params):
        return _respond(_service().data_quality(_filters(params)))
