"""Clinical endpoints for the UAT patient-evaluation workflow.

These routes drive the real hospital_management schema. The demo routes in
controllers/appointment.py are untouched and keep working against
yoya.emr.appointment.

No route here uses sudo(). Every read is scoped twice: by an explicit domain
from services/clinical_scope.py, and by whatever record rules the ORM applies
on top.
"""
import functools
import logging
from datetime import datetime, time

import pytz

from odoo import http
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.http import request

from ..services.api_response import (
    ApiError,
    api_error_response,
    coerce_number,
    coerce_optional_id,
    coerce_pain_level,
    coerce_text,
    coerce_triage_priority,
    error_response,
    parse_date,
    parse_int_param,
    read_json_body,
    success_response,
)
from ..services.clinical_scope import (
    find_appointment_in_scope,
    hospital_groups,
    latest_evaluation_for,
    scoped_appointment_domain,
)
from ..services.clinical_serializers import (
    serialize_appointment_core,
    serialize_billing_clearance,
    serialize_encounter,
    serialize_evaluation,
    serialize_patient_detail,
    serialize_previous_vitals,
    serialize_queue_row,
    serialize_session,
)

_logger = logging.getLogger(__name__)

QUEUE_STATES = ("confirmed", "in_consultation")
QUEUE_LIMIT = 500

# Writable through the save endpoint, and nothing else.
SAVE_ALLOWED_FIELDS = {
    "physician_id",
    "encounter_id",
    "assigned_nurse_id",
    "triage_priority",
    "chief_complaint",
    "triage_notes",
    "weight",
    "height",
    "temperature",
    "heart_rate",
    "respiratory_rate",
    "systolic_bp",
    "diastolic_bp",
    "spo2",
    "rbs",
    "head_circumference",
    "pain_level",
    "pain_note",
}

# Rejected explicitly rather than silently dropped, so a mistaken client gets
# told. bmi/bmi_state are computed, the timestamps and state belong to the
# workflow methods, and patient/appointment are derived from the URL.
SAVE_PROTECTED_FIELDS = {
    "id",
    "name",
    "state",
    "bmi",
    "bmi_state",
    "started_at",
    "completed_at",
    "patient_id",
    "appointment_id",
}

NUMERIC_FIELDS = {
    "weight",
    "height",
    "temperature",
    "heart_rate",
    "respiratory_rate",
    "systolic_bp",
    "diastolic_bp",
    "spo2",
    "rbs",
    "head_circumference",
}
ID_FIELDS = {"physician_id", "encounter_id", "assigned_nurse_id"}
TEXT_FIELDS = {"chief_complaint", "triage_notes", "pain_note"}


def clinical_endpoint(func):
    """Uniform error mapping. Never leaks a traceback to the client.

    Ordering matters: in Odoo, AccessError and ValidationError both subclass
    UserError, so the broad handler has to come last.
    """

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except ApiError as error:
            return api_error_response(error)
        except AccessError:
            return error_response(
                "access_denied",
                "You are not allowed to access this record.",
                403,
            )
        except ValidationError as error:
            return error_response("validation_error", str(error), 400)
        except UserError as error:
            return error_response("business_error", str(error), 400)
        except Exception:
            _logger.exception(
                "Unexpected error in YOYA clinical endpoint %s", func.__name__
            )
            return error_response(
                "internal_error", "An unexpected error occurred.", 500
            )

    return wrapper


def _day_bounds_utc(env, day):
    """Naive UTC bounds for a calendar day in the user's timezone."""
    tz = pytz.timezone(env.user.tz or "UTC")
    start = tz.localize(datetime.combine(day, time.min)).astimezone(pytz.UTC)
    end = tz.localize(datetime.combine(day, time.max)).astimezone(pytz.UTC)
    return start.replace(tzinfo=None), end.replace(tzinfo=None)


def _require_appointment(env, appointment_id):
    appointment, reason = find_appointment_in_scope(env, appointment_id)
    if reason == "not_found":
        raise ApiError("not_found", "Appointment not found.", 404)
    if reason == "out_of_scope":
        raise ApiError(
            "out_of_scope",
            "This appointment is outside your clinical scope.",
            403,
        )
    return appointment


def _build_save_values(body):
    provided = set(body)

    protected = provided & SAVE_PROTECTED_FIELDS
    if protected:
        raise ApiError(
            "protected_field",
            "These fields cannot be written directly: %s."
            % ", ".join(sorted(protected)),
            400,
        )

    unknown = provided - SAVE_ALLOWED_FIELDS
    if unknown:
        raise ApiError(
            "unknown_field",
            "Unrecognised fields: %s." % ", ".join(sorted(unknown)),
            400,
        )

    values = {}
    for name in provided:
        raw = body[name]
        if name in NUMERIC_FIELDS:
            values[name] = coerce_number(name, raw)
        elif name in ID_FIELDS:
            values[name] = coerce_optional_id(name, raw)
        elif name in TEXT_FIELDS:
            values[name] = coerce_text(name, raw)
        elif name == "pain_level":
            values[name] = coerce_pain_level(raw)
        elif name == "triage_priority":
            values[name] = coerce_triage_priority(raw)
    return values


class YoyaEmrClinicalController(http.Controller):

    # ------------------------------------------------------------------
    # 1. Session
    # ------------------------------------------------------------------
    @http.route(
        "/yoya-emr/api/v1/clinical/session",
        type="http",
        auth="user",
        methods=["GET"],
        csrf=False,
    )
    @clinical_endpoint
    def clinical_session(self, **kwargs):
        env = request.env
        return success_response(serialize_session(env, hospital_groups(env)))

    # ------------------------------------------------------------------
    # 2. Evaluation queue
    # ------------------------------------------------------------------
    @http.route(
        "/yoya-emr/api/v1/clinical/evaluation-queue",
        type="http",
        auth="user",
        methods=["GET"],
        csrf=False,
    )
    @clinical_endpoint
    def evaluation_queue(self, date=None, state=None, department_id=None, doctor_id=None, **kwargs):
        env = request.env

        if state is not None:
            if state not in QUEUE_STATES:
                raise ApiError(
                    "invalid_parameter",
                    "'state' must be one of %s." % ", ".join(QUEUE_STATES),
                    400,
                )
            states = [state]
        else:
            states = list(QUEUE_STATES)

        if date:
            day = parse_date(date)
        else:
            day = datetime.now(pytz.timezone(env.user.tz or "UTC")).date()
        start_utc, end_utc = _day_bounds_utc(env, day)

        base_domain = [
            ("state", "in", states),
            ("appointment_date", ">=", start_utc),
            ("appointment_date", "<=", end_utc),
        ]
        if department_id is not None:
            base_domain.append(
                ("department_id", "=", parse_int_param("department_id", department_id))
            )
        if doctor_id is not None:
            base_domain.append(
                ("doctor_id", "=", parse_int_param("doctor_id", doctor_id))
            )

        domain = scoped_appointment_domain(env, base_domain)
        appointments = env["hospital.appointment"].search(
            domain, order="appointment_date asc, id asc", limit=QUEUE_LIMIT
        )

        rows = [
            serialize_queue_row(appointment, latest_evaluation_for(env, appointment))
            for appointment in appointments
        ]
        return success_response(
            {
                "date": day.isoformat(),
                "states": states,
                "count": len(rows),
                "truncated": len(rows) >= QUEUE_LIMIT,
                "queue": rows,
            }
        )

    # ------------------------------------------------------------------
    # 3. Evaluation detail by appointment
    # ------------------------------------------------------------------
    @http.route(
        "/yoya-emr/api/v1/clinical/evaluations/by-appointment/<int:appointment_id>",
        type="http",
        auth="user",
        methods=["GET"],
        csrf=False,
    )
    @clinical_endpoint
    def evaluation_detail(self, appointment_id, **kwargs):
        env = request.env
        appointment = _require_appointment(env, appointment_id)
        evaluation = latest_evaluation_for(env, appointment)
        patient = appointment.patient_id

        payload = {
            "appointment": serialize_appointment_core(appointment),
            "patient": serialize_patient_detail(patient),
            "previous_vitals": serialize_previous_vitals(patient),
            "encounter": serialize_encounter(appointment.encounter_id),
            "evaluation": serialize_evaluation(evaluation),
        }
        payload["appointment"].update(serialize_billing_clearance(appointment))
        return success_response(payload)

    # ------------------------------------------------------------------
    # 4. Save / upsert evaluation
    # ------------------------------------------------------------------
    @http.route(
        "/yoya-emr/api/v1/clinical/evaluations/<int:appointment_id>/save",
        type="http",
        auth="user",
        methods=["POST"],
        csrf=False,
    )
    @clinical_endpoint
    def save_evaluation(self, appointment_id, **kwargs):
        env = request.env
        appointment = _require_appointment(env, appointment_id)
        values = _build_save_values(read_json_body())

        evaluation = latest_evaluation_for(env, appointment)

        if evaluation:
            if evaluation.state != "draft":
                raise ApiError(
                    "evaluation_locked",
                    "Evaluation %s is %s and can no longer be edited."
                    % (evaluation.name, evaluation.state),
                    409,
                )
            if values:
                evaluation.write(values)
        else:
            create_values = dict(values)
            create_values.update(
                {
                    "patient_id": appointment.patient_id.id,
                    "appointment_id": appointment.id,
                }
            )
            evaluation = env["hospital.patient.evaluation"].create(create_values)

        return success_response({"evaluation": serialize_evaluation(evaluation)})

    # ------------------------------------------------------------------
    # 5. Complete evaluation
    # ------------------------------------------------------------------
    @http.route(
        "/yoya-emr/api/v1/clinical/evaluations/<int:evaluation_id>/complete",
        type="http",
        auth="user",
        methods=["POST"],
        csrf=False,
    )
    @clinical_endpoint
    def complete_evaluation(self, evaluation_id, **kwargs):
        env = request.env
        # search() applies record rules, so an evaluation outside the caller's
        # scope is indistinguishable from one that does not exist. That is the
        # safer of the two answers.
        evaluation = env["hospital.patient.evaluation"].search(
            [("id", "=", evaluation_id)], limit=1
        )
        if not evaluation:
            raise ApiError("not_found", "Evaluation not found.", 404)

        if evaluation.state != "draft":
            raise ApiError(
                "invalid_transition",
                "Evaluation %s is %s and cannot be completed."
                % (evaluation.name, evaluation.state),
                409,
            )

        # Authoritative method: stamps completed_at and writes the audit log.
        evaluation.action_done()
        return success_response({"evaluation": serialize_evaluation(evaluation)})

    # ------------------------------------------------------------------
    # 6. Start consultation
    # ------------------------------------------------------------------
    @http.route(
        "/yoya-emr/api/v1/clinical/appointments/<int:appointment_id>/start-consultation",
        type="http",
        auth="user",
        methods=["POST"],
        csrf=False,
    )
    @clinical_endpoint
    def start_consultation(self, appointment_id, **kwargs):
        env = request.env
        appointment = _require_appointment(env, appointment_id)

        evaluation = latest_evaluation_for(env, appointment)
        if not evaluation or evaluation.state != "done":
            raise ApiError(
                "evaluation_not_completed",
                "The evaluation for this appointment must be completed before "
                "the consultation can start.",
                409,
            )

        try:
            # hospital_billing overrides this to enforce financial clearance
            # before care commences. Never write state directly.
            appointment.action_start_consultation()
        except AccessError:
            raise
        except UserError as error:
            clearance = serialize_billing_clearance(appointment)
            if clearance["billing_blocked"] or clearance["billing_clearance_message"]:
                raise ApiError(
                    "billing_clearance_required",
                    str(error),
                    409,
                    extra=clearance,
                )
            raise ApiError("invalid_transition", str(error), 409)

        return success_response(
            {
                "appointment": serialize_appointment_core(appointment),
                "encounter": serialize_encounter(appointment.encounter_id),
            }
        )
