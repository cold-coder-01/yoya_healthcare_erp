"""Secured receptionist API (Phase 2).

Every mutation delegates to hospital.reception.workflow. This controller
creates no patient, appointment, encounter, card issuance or charge of its
own, and writes no workflow state: doing any of that here would bypass the
atomicity, clearance persistence, check-in and idempotency the workflow
provides.

No route uses sudo(). Authorization is decided explicitly in
services/reception_scope.py before any record is touched, and the models
enforce it again underneath.
"""
import functools
import logging

from odoo import http
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.http import request

from ..services.api_response import (
    ApiError,
    api_error_response,
    error_response,
    parse_date,
    parse_int_param,
    read_json_body,
    success_response,
)
from ..services.reception_scope import (
    EMERGENCY_GROUPS,
    RECEPTION_GROUPS,
    capability_flags,
    hospital_day_bounds_utc,
    hospital_today,
    may_authorize_payer,
    may_emergency_bypass,
    may_reception,
    reception_queue_domain,
    role_flags,
    search_text_domain,
)
from ..services.reception_serializers import (
    build_card_summaries,
    serialize_department,
    serialize_doctor,
    serialize_patient_reception,
    serialize_queue_row,
    serialize_visit_detail,
)

_logger = logging.getLogger(__name__)

QUEUE_LIMIT = 500
PATIENT_SEARCH_LIMIT = 25
MIN_SEARCH_LENGTH = 2

VISIT_TYPES = ("routine", "emergency", "follow_up", "referral")

# Strict subset of hospital.reception.workflow.PATIENT_REGISTRATION_FIELDS.
# Every name here was verified to exist on hospital.patient as a plain
# scalar. identification_code is absent by design: the MRN is
# sequence-assigned and must never be client-chosen.
PATIENT_VALUE_FIELDS = frozenset(
    {
        "name",
        "date_of_birth",
        "gender",
        "phone",
        "mobile",
        "address",
        "city",
        # NOTE: on hospital.patient, 'state' is a Char address field
        # (province/region), NOT a workflow state -- hospital.patient has no
        # workflow state field at all. It is an address field and is allowed
        # as such. The appointment/encounter/charge state fields named in the
        # never-accept list live on other models and are unreachable here.
        "state",
        "zip_code",
        "country",
        "emergency_contact_name",
        "emergency_contact_phone",
        "blood_group",
    }
)

# Named explicitly so a client sending one gets a precise error instead of a
# silent drop.
PATIENT_FORBIDDEN_FIELDS = frozenset(
    {
        "identification_code",
        "active",
        "company_id",
        "create_uid",
        "create_date",
        "write_uid",
        "write_date",
        "encounter_id",
        "appointment_id",
        "state",
        "charge_line_id",
        "charge_ids",
    }
)

GENDERS = ("male", "female")
BLOOD_GROUPS = (
    "a_positive", "a_negative", "b_positive", "b_negative",
    "ab_positive", "ab_negative", "o_positive", "o_negative", "unknown",
)


def reception_endpoint(func):
    """Stable error envelope. Never leaks a traceback.

    Ordering matters: AccessError and ValidationError both subclass UserError
    in Odoo, so the broad handler comes last. A bare UserError from the
    workflow is an invalid workflow state (422), not a validation failure.
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
        except AccessError:
            return error_response(
                "access_denied", "You are not allowed to perform this action.", 403
            )
        except ValidationError as error:
            return error_response("validation_error", str(error), 400)
        except UserError as error:
            return error_response("invalid_workflow_state", str(error), 422)
        except Exception:
            _logger.exception(
                "Unexpected error in YOYA reception endpoint %s", func.__name__
            )
            return error_response(
                "internal_error", "An unexpected error occurred.", 500
            )

    return wrapper


# ----------------------------------------------------------------------
# Guards and lookups
# ----------------------------------------------------------------------
def _require_reception(env):
    if not may_reception(env):
        raise ApiError(
            "access_denied",
            "Reception access requires the Receptionist, Hospital Manager or "
            "Hospital System Administrator role.",
            403,
        )


def _require_emergency_authorizer(env):
    if not may_emergency_bypass(env):
        raise ApiError(
            "access_denied",
            "An emergency bypass requires the Hospital Emergency Authorizer, "
            "Hospital Manager or Hospital System Administrator role.",
            403,
        )


def _get_or_404(env, model, record_id, label):
    record = env[model].search([("id", "=", record_id)], limit=1)
    if not record:
        raise ApiError("not_found", "%s not found." % label, 404)
    return record


def _load_appointment(env, appointment_id):
    return _get_or_404(env, "hospital.appointment", appointment_id, "Visit")


def _validate_visit_type(value):
    if value is None:
        return "routine"
    if value not in VISIT_TYPES:
        raise ApiError(
            "validation_error",
            "'visit_type' must be one of %s." % ", ".join(VISIT_TYPES),
            400,
        )
    return value


def _validate_doctor_department(doctor, department):
    """A doctor must belong to the department the visit is booked into."""
    if not doctor or not department:
        return
    if doctor.department_id and doctor.department_id.id != department.id:
        raise ApiError(
            "validation_error",
            "%s belongs to %s, not %s."
            % (
                doctor.display_name,
                doctor.department_id.display_name,
                department.display_name,
            ),
            400,
        )


def _build_patient_values(raw):
    if not isinstance(raw, dict):
        raise ApiError(
            "validation_error", "'patient_values' must be an object.", 400
        )

    provided = set(raw)
    forbidden = provided & PATIENT_FORBIDDEN_FIELDS
    if forbidden:
        raise ApiError(
            "validation_error",
            "These fields cannot be supplied when registering a patient: %s."
            % ", ".join(sorted(forbidden)),
            400,
        )
    unknown = provided - PATIENT_VALUE_FIELDS
    if unknown:
        raise ApiError(
            "validation_error",
            "Unrecognised patient fields: %s." % ", ".join(sorted(unknown)),
            400,
        )

    values = {}
    for key in provided:
        value = raw[key]
        if value in (None, ""):
            continue
        if key == "gender":
            if value not in GENDERS:
                raise ApiError(
                    "validation_error",
                    "'gender' must be one of %s." % ", ".join(GENDERS),
                    400,
                )
        elif key == "blood_group":
            if value not in BLOOD_GROUPS:
                raise ApiError(
                    "validation_error",
                    "'blood_group' must be one of %s." % ", ".join(BLOOD_GROUPS),
                    400,
                )
        elif not isinstance(value, str):
            raise ApiError(
                "validation_error", "'%s' must be a string." % key, 400
            )
        values[key] = value

    if not (values.get("name") or "").strip():
        raise ApiError("validation_error", "A patient name is required.", 400)
    return values


class YoyaEmrReceptionController(http.Controller):

    # ------------------------------------------------------------------
    # 1. Session
    # ------------------------------------------------------------------
    @http.route(
        "/yoya-emr/api/v1/reception/session",
        type="http", auth="user", methods=["GET"], csrf=False,
    )
    @reception_endpoint
    def reception_session(self, **kwargs):
        env = request.env
        user = env.user
        return success_response(
            {
                "user": {"id": user.id, "name": user.name, "login": user.login},
                "company": {
                    "id": user.company_id.id,
                    "name": user.company_id.name,
                    "currency": user.company_id.currency_id.name,
                },
                "roles": role_flags(env),
                "capabilities": capability_flags(env),
            }
        )

    # ------------------------------------------------------------------
    # 2. Patient search
    # ------------------------------------------------------------------
    @http.route(
        "/yoya-emr/api/v1/reception/patients/search",
        type="http", auth="user", methods=["GET"], csrf=False,
    )
    @reception_endpoint
    def patient_search(self, q=None, **kwargs):
        env = request.env
        _require_reception(env)

        term = (q or "").strip()
        if len(term) < MIN_SEARCH_LENGTH:
            raise ApiError(
                "validation_error",
                "Provide at least %d characters to search." % MIN_SEARCH_LENGTH,
                400,
            )

        domain = search_text_domain(term)
        # active_test defaults True, so archived patients are excluded.
        patients = env["hospital.patient"].search(
            domain, limit=PATIENT_SEARCH_LIMIT, order="identification_code desc"
        )
        summaries = build_card_summaries(env, patients)
        return success_response(
            {
                "query": term,
                "count": len(patients),
                "limit": PATIENT_SEARCH_LIMIT,
                "patients": [
                    serialize_patient_reception(patient, summaries.get(patient.id))
                    for patient in patients
                ],
            }
        )

    # ------------------------------------------------------------------
    # 3. Visit preview
    # ------------------------------------------------------------------
    @http.route(
        "/yoya-emr/api/v1/reception/visit-preview",
        type="http", auth="user", methods=["GET"], csrf=False,
    )
    @reception_endpoint
    def visit_preview(
        self, patient_id=None, visit_type=None, department_id=None,
        doctor_id=None, **kwargs
    ):
        env = request.env
        _require_reception(env)

        if not department_id:
            raise ApiError("validation_error", "'department_id' is required.", 400)

        visit_type = _validate_visit_type(visit_type)
        department = _get_or_404(
            env, "hospital.department",
            parse_int_param("department_id", department_id), "Department",
        )
        patient = None
        if patient_id:
            patient = _get_or_404(
                env, "hospital.patient",
                parse_int_param("patient_id", patient_id), "Patient",
            )
        doctor = None
        if doctor_id:
            doctor = _get_or_404(
                env, "hospital.doctor",
                parse_int_param("doctor_id", doctor_id), "Doctor",
            )
            _validate_doctor_department(doctor, department)

        preview = env["hospital.reception.workflow"].preview_visit(
            patient=patient,
            visit_type=visit_type,
            department=department,
            doctor=doctor,
        )
        return success_response(preview)

    # ------------------------------------------------------------------
    # 4. Create visit (atomic guided registration)
    # ------------------------------------------------------------------
    @http.route(
        "/yoya-emr/api/v1/reception/visits",
        type="http", auth="user", methods=["POST"], csrf=False,
    )
    @reception_endpoint
    def create_visit(self, **kwargs):
        env = request.env
        _require_reception(env)
        body = read_json_body()

        patient_id = body.get("patient_id")
        patient_values_raw = body.get("patient_values")
        if patient_id and patient_values_raw:
            raise ApiError(
                "validation_error",
                "Provide either 'patient_id' or 'patient_values', never both.",
                400,
            )
        if not patient_id and not patient_values_raw:
            raise ApiError(
                "validation_error",
                "Provide 'patient_id' for a returning patient or "
                "'patient_values' to register a new one.",
                400,
            )

        if not body.get("department_id"):
            raise ApiError("validation_error", "'department_id' is required.", 400)

        visit_type = _validate_visit_type(body.get("visit_type"))
        department = _get_or_404(
            env, "hospital.department",
            parse_int_param("department_id", body["department_id"]), "Department",
        )

        patient = None
        patient_values = None
        if patient_id:
            patient = _get_or_404(
                env, "hospital.patient",
                parse_int_param("patient_id", patient_id), "Patient",
            )
        else:
            patient_values = _build_patient_values(patient_values_raw)

        doctor = None
        if body.get("doctor_id"):
            doctor = _get_or_404(
                env, "hospital.doctor",
                parse_int_param("doctor_id", body["doctor_id"]), "Doctor",
            )
            _validate_doctor_department(doctor, department)

        triage_destination = None
        if body.get("triage_destination_id"):
            triage_destination = _get_or_404(
                env, "hospital.department",
                parse_int_param(
                    "triage_destination_id", body["triage_destination_id"]
                ),
                "Triage destination",
            )

        appointment_date = None
        if body.get("appointment_date"):
            appointment_date = body["appointment_date"]
            if not isinstance(appointment_date, str):
                raise ApiError(
                    "validation_error",
                    "'appointment_date' must be an ISO datetime string.",
                    400,
                )

        # Only the SUPPRESS direction is exposed. Forcing a card on a patient
        # who already holds one would attempt a second 'first' issuance, which
        # the model's partial unique index rejects; replacements are a
        # manager action on the card model, not a reception flag.
        issue_card = None
        if "issue_card" in body:
            if body["issue_card"] is not False:
                raise ApiError(
                    "validation_error",
                    "'issue_card' may only be false, to suppress first-card "
                    "issuance. Replacement cards are raised by a manager on the "
                    "card issuance record.",
                    400,
                )
            issue_card = False

        reason = body.get("reason")
        if reason is not None and not isinstance(reason, str):
            raise ApiError("validation_error", "'reason' must be a string.", 400)

        # THE only mutation. One call, one transaction.
        result = env["hospital.reception.workflow"].create_visit(
            patient=patient,
            patient_values=patient_values,
            visit_type=visit_type,
            department=department,
            doctor=doctor,
            appointment_date=appointment_date,
            reason=reason,
            triage_destination=triage_destination,
            issue_card=issue_card,
        )

        appointment = result["appointment"]
        return success_response(
            serialize_visit_detail(env, appointment, capability_flags(env)),
            status=201,
        )

    # ------------------------------------------------------------------
    # 5. Visit detail
    # ------------------------------------------------------------------
    @http.route(
        "/yoya-emr/api/v1/reception/visits/<int:appointment_id>",
        type="http", auth="user", methods=["GET"], csrf=False,
    )
    @reception_endpoint
    def visit_detail(self, appointment_id, **kwargs):
        env = request.env
        _require_reception(env)
        appointment = _load_appointment(env, appointment_id)
        return success_response(
            serialize_visit_detail(env, appointment, capability_flags(env))
        )

    # ------------------------------------------------------------------
    # 6. Reception queue
    # ------------------------------------------------------------------
    @http.route(
        "/yoya-emr/api/v1/reception/queue",
        type="http", auth="user", methods=["GET"], csrf=False,
    )
    @reception_endpoint
    def reception_queue(
        self, date=None, stage=None, department_id=None, doctor_id=None,
        visit_type=None, search=None, **kwargs
    ):
        env = request.env
        _require_reception(env)

        day = parse_date(date) if date else hospital_today(env)
        start_utc, end_utc = hospital_day_bounds_utc(env, day)

        base = [
            ("appointment_date", ">=", start_utc),
            ("appointment_date", "<=", end_utc),
        ]
        if visit_type:
            _validate_visit_type(visit_type)
        domain = reception_queue_domain(
            base_domain=base,
            department_id=parse_int_param("department_id", department_id)
            if department_id else None,
            doctor_id=parse_int_param("doctor_id", doctor_id)
            if doctor_id else None,
            visit_type=visit_type,
        )

        appointments = env["hospital.appointment"].search(
            domain, order="appointment_date asc, id asc", limit=QUEUE_LIMIT
        )

        # clinical_queue_stage is a non-stored compute, so stage filtering and
        # free-text search are applied in Python rather than in the domain.
        if stage:
            appointments = appointments.filtered(
                lambda record: record.clinical_queue_stage == stage
            )
        needle = (search or "").strip().lower()
        if needle:
            def matches(record):
                return any(
                    needle in (value or "").lower()
                    for value in (
                        record.appointment_code,
                        record.patient_id.name,
                        record.patient_id.identification_code,
                        record.doctor_id.name,
                        record.department_id.name,
                    )
                )
            appointments = appointments.filtered(matches)

        summaries = build_card_summaries(env, appointments.mapped("patient_id"))
        rows = [
            serialize_queue_row(
                env, appointment, summaries.get(appointment.patient_id.id)
            )
            for appointment in appointments
        ]
        return success_response(
            {
                "date": day.isoformat(),
                "count": len(rows),
                "limit": QUEUE_LIMIT,
                "truncated": len(rows) >= QUEUE_LIMIT,
                "queue": rows,
            }
        )

    # ------------------------------------------------------------------
    # 7. Send to triage
    # ------------------------------------------------------------------
    @http.route(
        "/yoya-emr/api/v1/reception/visits/<int:appointment_id>/send-to-triage",
        type="http", auth="user", methods=["POST"], csrf=False,
    )
    @reception_endpoint
    def send_to_triage(self, appointment_id, **kwargs):
        env = request.env
        _require_reception(env)
        appointment = _load_appointment(env, appointment_id)

        encounter = appointment.encounter_id
        if not encounter:
            raise ApiError(
                "invalid_workflow_state",
                "Visit %s has no encounter yet." % appointment.appointment_code,
                422,
            )

        # Structured pre-check so the client gets amounts, not a parsed string.
        # send_to_triage re-checks this itself; this is fail-fast, not the gate.
        summary = encounter._reception_clearance_summary()
        if not summary.get("cleared"):
            raise ApiError(
                "reception_clearance_required",
                "Reception clearance is outstanding for this visit.",
                409,
                extra={
                    "required_amount": summary.get("required", 0.0),
                    "received_amount": summary.get("paid", 0.0),
                    "outstanding_amount": summary.get("outstanding", 0.0),
                    "clearance_state": summary.get("state"),
                    "clearance_message": summary.get("reason"),
                },
            )

        try:
            env["hospital.reception.workflow"].send_to_triage(appointment)
        except UserError as error:
            raise ApiError("triage_not_ready", str(error), 409)

        return success_response(
            serialize_visit_detail(env, appointment, capability_flags(env))
        )

    # ------------------------------------------------------------------
    # 8. Emergency bypass
    # ------------------------------------------------------------------
    @http.route(
        "/yoya-emr/api/v1/reception/visits/<int:appointment_id>/emergency-bypass",
        type="http", auth="user", methods=["POST"], csrf=False,
    )
    @reception_endpoint
    def emergency_bypass(self, appointment_id, **kwargs):
        env = request.env
        _require_emergency_authorizer(env)
        appointment = _load_appointment(env, appointment_id)
        body = read_json_body()

        reason = body.get("reason")
        if not isinstance(reason, str) or not reason.strip():
            raise ApiError(
                "validation_error",
                "A documented reason is required to authorize an emergency bypass.",
                400,
            )

        danger_signs = env["hospital.emergency.danger.sign"]
        raw_ids = body.get("danger_sign_ids") or []
        if raw_ids:
            if not isinstance(raw_ids, list) or not all(
                isinstance(value, int) and not isinstance(value, bool)
                for value in raw_ids
            ):
                raise ApiError(
                    "validation_error",
                    "'danger_sign_ids' must be a list of integer ids.",
                    400,
                )
            danger_signs = env["hospital.emergency.danger.sign"].search(
                [("id", "in", raw_ids)]
            )
            missing = set(raw_ids) - set(danger_signs.ids)
            if missing:
                raise ApiError(
                    "validation_error",
                    "Unknown danger sign ids: %s."
                    % ", ".join(str(value) for value in sorted(missing)),
                    400,
                )

        env["hospital.reception.workflow"].emergency_bypass_and_send(
            appointment, reason.strip(), danger_signs=danger_signs
        )
        return success_response(
            serialize_visit_detail(env, appointment, capability_flags(env))
        )

    # ------------------------------------------------------------------
    # 9-10. Reference data
    # ------------------------------------------------------------------
    @http.route(
        "/yoya-emr/api/v1/reference/departments",
        type="http", auth="user", methods=["GET"], csrf=False,
    )
    @reception_endpoint
    def reference_departments(self, **kwargs):
        env = request.env
        departments = env["hospital.department"].search([], order="name asc")
        return success_response(
            {"departments": [serialize_department(d) for d in departments]}
        )

    @http.route(
        "/yoya-emr/api/v1/reference/doctors",
        type="http", auth="user", methods=["GET"], csrf=False,
    )
    @reception_endpoint
    def reference_doctors(self, department_id=None, **kwargs):
        env = request.env
        domain = []
        if department_id:
            domain.append(
                ("department_id", "=", parse_int_param("department_id", department_id))
            )
        doctors = env["hospital.doctor"].search(domain, order="name asc")
        return success_response(
            {"doctors": [serialize_doctor(d) for d in doctors]}
        )

    # ------------------------------------------------------------------
    # 11. Payment -- deliberately not implemented
    # ------------------------------------------------------------------
    @http.route(
        "/yoya-emr/api/v1/reception/visits/<int:appointment_id>/payment",
        type="http", auth="user", methods=["POST"], csrf=False,
    )
    @reception_endpoint
    def record_payment(self, appointment_id, **kwargs):
        """Declared and refused, rather than omitted.

        A 501 with a stable code lets the Next.js client render an honest
        "pay at the cashier desk" state instead of guessing from a 404.

        Implementing this would mean reproducing
        hospital.charge.payment.wizard.action_confirm: idempotency token,
        server-side charge re-resolution, over-application justification,
        receipt + allocation creation, audit linkage and
        _sync_payment_state. Reimplementing that would fork the money path.
        Calling it is impossible for a cashier because RECEIPT_GROUPS is
        hardcoded in hospital_billing, which is not version-controlled and
        must not be edited from here.
        """
        raise ApiError(
            "feature_unavailable",
            "Cashier payment API is not enabled until the billing module "
            "authorization boundary is version-controlled and updated.",
            501,
        )

    # ------------------------------------------------------------------
    # 12. Payer authorization
    # ------------------------------------------------------------------
    @http.route(
        "/yoya-emr/api/v1/reception/visits/<int:appointment_id>/authorize-payer",
        type="http", auth="user", methods=["POST"], csrf=False,
    )
    @reception_endpoint
    def authorize_payer(self, appointment_id, **kwargs):
        """Record payer authorization against charges on THIS encounter.

        hospital.billing.engine.authorize_charge is safe to expose: it
        asserts AUTHORIZE_GROUPS itself, is idempotent, writes an audit log
        entry, and assumes no guarantee-letter linkage. The extra work here
        is proving every submitted charge belongs to the target encounter, so
        a caller cannot authorize an unrelated patient's charge by id.
        """
        env = request.env
        if not may_authorize_payer(env):
            raise ApiError(
                "access_denied",
                "Recording payer authorization requires the Receptionist, "
                "Accountant, Hospital Manager or Hospital System "
                "Administrator role.",
                403,
            )

        appointment = _load_appointment(env, appointment_id)
        encounter = appointment.encounter_id
        if not encounter:
            raise ApiError(
                "invalid_workflow_state",
                "Visit %s has no encounter yet." % appointment.appointment_code,
                422,
            )
        if encounter.payer_type == "self_pay":
            raise ApiError(
                "invalid_workflow_state",
                "Encounter %s is Self Pay; there is no payer to authorize."
                % encounter.name,
                422,
            )

        body = read_json_body()
        raw_ids = body.get("charge_line_ids")
        if not isinstance(raw_ids, list) or not raw_ids or not all(
            isinstance(value, int) and not isinstance(value, bool)
            for value in raw_ids
        ):
            raise ApiError(
                "validation_error",
                "'charge_line_ids' must be a non-empty list of integer ids.",
                400,
            )
        reference = body.get("reference")
        if reference is not None and not isinstance(reference, str):
            raise ApiError("validation_error", "'reference' must be a string.", 400)

        account = encounter.billing_account_id
        owned_ids = set(account.charge_line_ids.ids) if account else set()
        foreign = set(raw_ids) - owned_ids
        if foreign:
            raise ApiError(
                "validation_error",
                "These charges do not belong to encounter %s: %s."
                % (
                    encounter.name,
                    ", ".join(str(value) for value in sorted(foreign)),
                ),
                400,
            )

        engine = env["hospital.billing.engine"]
        charges = env["hospital.charge.line"].browse(sorted(set(raw_ids)))
        for charge in charges:
            engine.authorize_charge(charge, reference=reference)

        return success_response(
            serialize_visit_detail(env, appointment, capability_flags(env))
        )
