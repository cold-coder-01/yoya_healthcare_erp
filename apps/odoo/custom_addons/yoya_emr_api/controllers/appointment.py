import logging

from odoo import http
from odoo.exceptions import AccessError, UserError
from odoo.http import request


_logger = logging.getLogger(__name__)


def _json_response(payload, status=200):
    return request.make_json_response(payload, status=status)


def _error_response(code, message, status):
    return _json_response(
        {
            "success": False,
            "error": {
                "code": code,
                "message": message,
            },
        },
        status=status,
    )


def _datetime_value(value):
    return value.isoformat() if value else False


def _check_access(record, operation):
    check_access = getattr(record, "check_access", None)
    if check_access:
        check_access(operation)
        return
    record.check_access_rights(operation)
    record.check_access_rule(operation)


def _serialize_appointment(appointment):
    patient = appointment.patient_id
    return {
        "id": appointment.id,
        "name": appointment.name,
        "patient_id": patient.id,
        "patient_name": patient.name,
        "patient_number": patient.patient_number,
        "appointment_date": _datetime_value(appointment.appointment_date),
        "doctor_name": appointment.doctor_name,
        "reason": appointment.reason,
        "state": appointment.state,
        "started_at": _datetime_value(appointment.started_at),
        "completed_at": _datetime_value(appointment.completed_at),
    }


class YoyaEmrApiAppointmentController(http.Controller):
    @http.route(
        "/yoya-emr/api/v1/appointments",
        type="http",
        auth="user",
        methods=["GET"],
        csrf=False,
    )
    def list_appointments(self):
        try:
            Appointment = request.env["yoya.emr.appointment"]
            appointments = Appointment.search([], order="appointment_date desc")
            return _json_response(
                {
                    "success": True,
                    "data": {
                        "appointments": [
                            _serialize_appointment(appointment)
                            for appointment in appointments
                        ],
                    },
                }
            )
        except AccessError:
            return _error_response("access_denied", "Access denied.", 403)
        except Exception:
            _logger.exception("Unexpected error while listing YOYA EMR appointments")
            return _error_response(
                "internal_error",
                "An unexpected error occurred.",
                500,
            )

    @http.route(
        "/yoya-emr/api/v1/appointments/<int:appointment_id>",
        type="http",
        auth="user",
        methods=["GET"],
        csrf=False,
    )
    def get_appointment(self, appointment_id):
        try:
            appointment = self._get_existing_appointment(appointment_id)
            if not appointment:
                return _error_response("not_found", "Appointment not found.", 404)
            _check_access(appointment, "read")
            return _json_response(
                {
                    "success": True,
                    "data": {
                        "appointment": _serialize_appointment(appointment),
                    },
                }
            )
        except AccessError:
            return _error_response("access_denied", "Access denied.", 403)
        except Exception:
            _logger.exception(
                "Unexpected error while reading YOYA EMR appointment %s",
                appointment_id,
            )
            return _error_response(
                "internal_error",
                "An unexpected error occurred.",
                500,
            )

    @http.route(
        "/yoya-emr/api/v1/appointments/<int:appointment_id>/start",
        type="http",
        auth="user",
        methods=["POST"],
        csrf=False,
    )
    def start_consultation(self, appointment_id):
        try:
            appointment = self._get_existing_appointment(appointment_id)
            if not appointment:
                return _error_response("not_found", "Appointment not found.", 404)
            _check_access(appointment, "write")
            appointment.action_start_consultation()
            return _json_response(
                {
                    "success": True,
                    "data": {
                        "appointment": _serialize_appointment(appointment),
                    },
                }
            )
        except UserError as error:
            return _error_response("invalid_transition", str(error), 400)
        except AccessError:
            return _error_response("access_denied", "Access denied.", 403)
        except Exception:
            _logger.exception(
                "Unexpected error while starting YOYA EMR appointment %s",
                appointment_id,
            )
            return _error_response(
                "internal_error",
                "An unexpected error occurred.",
                500,
            )

    def _get_existing_appointment(self, appointment_id):
        return request.env["yoya.emr.appointment"].browse(appointment_id).exists()
