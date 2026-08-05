"""Response envelope, access checks and value coercion for the clinical API.

Kept separate from controllers/appointment.py so the pre-existing demo routes
are not disturbed. The envelope shape is intentionally identical to the demo
controller's, so both generations of endpoint look the same to the BFF.
"""
import json
import logging

from odoo.http import request

_logger = logging.getLogger(__name__)

PAIN_LEVELS = tuple(str(level) for level in range(11))
TRIAGE_PRIORITIES = ("routine", "urgent", "emergency")


class ApiError(Exception):
    """Raised inside a handler to produce a deterministic error response."""

    def __init__(self, code, message, status=400, extra=None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.extra = extra or {}


def json_response(payload, status=200):
    return request.make_json_response(payload, status=status)


def success_response(data, status=200):
    return json_response({"success": True, "data": data}, status=status)


def error_response(code, message, status, extra=None):
    error = {"code": code, "message": message}
    if extra:
        error.update(extra)
    return json_response({"success": False, "error": error}, status=status)


def api_error_response(error):
    return error_response(error.code, error.message, error.status, error.extra)


# ----------------------------------------------------------------------
# Access
# ----------------------------------------------------------------------
def check_access(records, operation):
    """Odoo 18 exposes check_access(); older builds split it in two."""
    checker = getattr(records, "check_access", None)
    if checker:
        checker(operation)
        return
    records.check_access_rights(operation)
    records.check_access_rule(operation)


# ----------------------------------------------------------------------
# Serialisation primitives
# ----------------------------------------------------------------------
def datetime_value(value):
    return value.isoformat() if value else None


def date_value(value):
    return value.isoformat() if value else None


def m2o_value(record):
    """{'id':..,'name':..} or None. Never raises on an empty recordset."""
    if not record:
        return None
    return {"id": record.id, "name": record.display_name}


def selection_value(value):
    """Selection fields read as False when unset; normalise to None."""
    return value if value else None


def float_value(value):
    # Float fields default to 0.0, which the clinical layer treats as
    # "not recorded". Preserve the raw number and let the client decide.
    return float(value) if value is not None else None


# ----------------------------------------------------------------------
# Request payload
# ----------------------------------------------------------------------
def read_json_body():
    """Parse a JSON body from a type='http' route.

    type='http' does not parse JSON for us. Odoo 17+ offers get_json_data();
    fall back to the raw body so this keeps working if that helper moves.
    """
    getter = getattr(request, "get_json_data", None)
    if getter:
        try:
            payload = getter()
        except Exception:
            raise ApiError("invalid_json", "Request body must be valid JSON.", 400)
    else:
        raw = request.httprequest.get_data() or b""
        if not raw.strip():
            payload = {}
        else:
            try:
                payload = json.loads(raw.decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                raise ApiError("invalid_json", "Request body must be valid JSON.", 400)
    if payload in (None, ""):
        payload = {}
    if not isinstance(payload, dict):
        raise ApiError("invalid_json", "Request body must be a JSON object.", 400)
    return payload


def coerce_number(field_name, value):
    """Accept JSON numbers only. bool is rejected despite subclassing int."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ApiError(
            "invalid_field",
            f"'{field_name}' must be a number.",
            400,
        )
    return float(value)


def coerce_optional_id(field_name, value):
    if value in (None, False):
        return False
    if isinstance(value, bool) or not isinstance(value, int):
        raise ApiError(
            "invalid_field",
            f"'{field_name}' must be an integer id or null.",
            400,
        )
    return value


def coerce_text(field_name, value):
    if value in (None, False):
        return False
    if not isinstance(value, str):
        raise ApiError("invalid_field", f"'{field_name}' must be a string.", 400)
    return value


def coerce_pain_level(value):
    """pain_level is a Selection keyed by the STRINGS '0'..'10'."""
    if value in (None, False, ""):
        return False
    if not isinstance(value, str):
        raise ApiError(
            "invalid_field",
            "'pain_level' must be a string such as \"5\", not a number.",
            400,
        )
    if value not in PAIN_LEVELS:
        raise ApiError(
            "invalid_field",
            "'pain_level' must be one of %s." % ", ".join(PAIN_LEVELS),
            400,
        )
    return value


def coerce_triage_priority(value):
    if value in (None, False, ""):
        return False
    if not isinstance(value, str) or value not in TRIAGE_PRIORITIES:
        raise ApiError(
            "invalid_field",
            "'triage_priority' must be one of %s." % ", ".join(TRIAGE_PRIORITIES),
            400,
        )
    return value


def parse_date(value):
    from datetime import datetime

    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        raise ApiError("invalid_parameter", "'date' must be formatted YYYY-MM-DD.", 400)


def parse_int_param(name, value):
    try:
        return int(value)
    except (TypeError, ValueError):
        raise ApiError("invalid_parameter", f"'{name}' must be an integer.", 400)
