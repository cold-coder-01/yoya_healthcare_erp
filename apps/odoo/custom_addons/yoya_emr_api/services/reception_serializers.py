"""Reception-safe serializers.

The governing constraint: a receptionist must never trigger a read of a
protected clinical or stock relation. hospital.patient carries
_compute_related_record_counts (ten search_count queries touching
prescriptions, lab requests, diagnoses, documents...) and
_compute_patient_overview (four more), plus many2many links into medical
alerts and, via other installed modules, inventory. Opening the full Odoo
patient form as a receptionist traverses those.

Nothing here reads any of them. Every function below touches only stored
scalar identity fields, plus reception/billing state the front desk is
entitled to see.
"""
from .api_response import (
    date_value,
    datetime_value,
    float_value,
    m2o_value,
    selection_value,
)

# Explicit allowlist. Anything not named here is never serialized to a
# reception client, and the validator asserts this list contains no known
# clinical/stock field.
PATIENT_SEARCH_FIELDS = (
    "id",
    "identification_code",
    "name",
    "date_of_birth",
    "age",
    "gender",
    "phone",
    "mobile",
    "blood_group",
)


def serialize_patient_reception(patient, card_summary=None):
    """Scalar identity only. No counts, no overview, no relations."""
    payload = {
        "id": patient.id,
        "identification_code": patient.identification_code,
        "name": patient.name,
        "date_of_birth": date_value(patient.date_of_birth),
        # Stored compute on hospital.patient: a plain column read.
        "age": patient.age,
        "gender": selection_value(patient.gender),
        "phone": patient.phone or None,
        "mobile": patient.mobile or None,
        "blood_group": selection_value(patient.blood_group),
    }
    summary = card_summary or {}
    payload["has_existing_first_card"] = summary.get("has_existing_first_card", False)
    payload["latest_card_status"] = summary.get("latest_card_status")
    return payload


def build_card_summaries(env, patients):
    """One batched query for every patient's first-card status.

    Deliberately batched: a per-patient lookup inside the search serializer
    would be 25 extra queries on every keystroke.
    """
    if not patients:
        return {}
    CardIssue = env["hospital.patient.card.issue"]
    cards = CardIssue.with_context(active_test=False).search(
        [("patient_id", "in", patients.ids)],
        order="patient_id, create_date desc, id desc",
    )
    summaries = {}
    for card in cards:
        entry = summaries.setdefault(
            card.patient_id.id,
            {"has_existing_first_card": False, "latest_card_status": None},
        )
        if entry["latest_card_status"] is None:
            entry["latest_card_status"] = card.state
        if card.issue_reason == "first" and card.state != "cancelled":
            entry["has_existing_first_card"] = True
    return summaries


def serialize_card_issue(card):
    if not card:
        return None
    return {
        "id": card.id,
        "name": card.name,
        "state": card.state,
        "issue_reason": card.issue_reason,
        "card_number": card.card_number or None,
        "service": m2o_value(card.service_id),
        "charge_line_id": card.charge_line_id.id if card.charge_line_id else None,
        "charge_amount": float_value(card.charge_amount),
        "charge_payment_state": selection_value(card.charge_payment_state),
        "waiver_reason": card.waiver_reason or None,
        "issued_at": datetime_value(card.issued_at),
    }


def serialize_reception_clearance(summary):
    """The AUTHORITATIVE reception clearance.

    Sourced from hospital.encounter._reception_clearance_summary(), which
    calls the billing engine WITHOUT a charge filter, so consultation AND
    patient-card charges both participate.
    """
    return {
        "required": float_value(summary.get("required", 0.0)),
        "received": float_value(summary.get("paid", 0.0)),
        "outstanding": float_value(summary.get("outstanding", 0.0)),
        "ok": bool(summary.get("cleared")),
        "state": summary.get("state"),
        "message": summary.get("reason") or None,
    }


def serialize_consultation_clearance(appointment):
    """Consultation-only clearance, reported SEPARATELY and never mixed in.

    hospital_billing deliberately scopes appointment.billing_blocked and
    billing_clearance_message to the consultation charge alone. That is the
    right gate for starting a consultation and the WRONG one for releasing a
    patient to triage, so it is surfaced under its own key with its own
    amounts and must never be used for a reception decision.
    """
    charge = appointment.consultation_charge_id
    return {
        "required": float_value(charge.amount_estimated) if charge else 0.0,
        "outstanding": float_value(charge.amount_due_for_clearance) if charge else 0.0,
        "blocked": bool(appointment.billing_blocked),
        "message": appointment.billing_clearance_message or None,
        "scope_note": "Consultation charge only. Not a reception clearance signal.",
    }


def _charge_category(charge):
    """Coarse provenance label, derived from the engine's own source_model."""
    source = charge.source_model or ""
    if source == "hospital.patient.card.issue":
        return "patient_card"
    if source == "hospital.appointment":
        return "consultation"
    return source or "other"


def serialize_charge_line(charge):
    return {
        "id": charge.id,
        "name": charge.name,
        "description": charge.description,
        "service": m2o_value(charge.service_id),
        "amount": float_value(charge.amount_estimated),
        "received": float_value(charge.amount_received),
        "outstanding": float_value(charge.amount_due_for_clearance),
        "charge_state": selection_value(charge.charge_state),
        "payment_state": selection_value(charge.payment_state),
        "authorization_state": selection_value(charge.authorization_state),
        "billing_basis": selection_value(charge.billing_basis),
        "source_category": _charge_category(charge),
        "source_event": charge.source_event or None,
    }


def serialize_charge_lines(encounter):
    account = encounter.billing_account_id
    if not account:
        return []
    return [serialize_charge_line(line) for line in account.charge_line_ids]


def serialize_danger_sign(sign):
    return {
        "id": sign.id,
        "name": sign.name,
        "code": sign.code or None,
        "severity": sign.severity,
        "requires_immediate_triage": sign.requires_immediate_triage,
    }


def serialize_emergency_summary(encounter):
    if not encounter:
        return None
    return {
        "emergency_bypass": bool(encounter.emergency_bypass),
        "reason": encounter.emergency_bypass_reason or None,
        "authorized_by": m2o_value(encounter.emergency_bypass_authorized_by),
        "authorized_at": datetime_value(encounter.emergency_bypass_at),
        "screened_by": m2o_value(encounter.emergency_screened_by_id),
        "screened_at": datetime_value(encounter.emergency_screened_at),
        "highest_danger_severity": selection_value(
            encounter.highest_danger_severity
        ),
    }


def serialize_encounter_reception(encounter):
    if not encounter:
        return None
    return {
        "id": encounter.id,
        "name": encounter.name,
        "state": encounter.state,
        "encounter_type": selection_value(encounter.encounter_type),
        "payer_type": selection_value(encounter.payer_type),
        "payer": m2o_value(encounter.payer_id),
    }


def serialize_appointment_reception(appointment):
    return {
        "id": appointment.id,
        "appointment_code": appointment.appointment_code,
        "appointment_date": datetime_value(appointment.appointment_date),
        "state": appointment.state,
        "visit_type": selection_value(appointment.visit_type),
        "reason": appointment.reason or None,
        "doctor": m2o_value(appointment.doctor_id),
        "department": m2o_value(appointment.department_id),
        "triage_destination": m2o_value(appointment.triage_destination_id),
        "reception_workflow_managed": bool(appointment.reception_workflow_managed),
        "clinical_queue_stage": selection_value(appointment.clinical_queue_stage),
        "registered_by": m2o_value(appointment.registered_by_id),
        "registered_at": datetime_value(appointment.registered_at),
    }


def permitted_actions(env, appointment, encounter, clearance, capabilities):
    """What the CURRENT user may do to THIS visit, right now."""
    is_confirmed = appointment.state == "confirmed"
    bypassed = bool(encounter and encounter.emergency_bypass)
    return {
        "send_to_triage": bool(
            capabilities["send_to_triage"]
            and is_confirmed
            and clearance.get("ok")
        ),
        "emergency_bypass": bool(
            capabilities["emergency_bypass"] and encounter and not bypassed
        ),
        "authorize_payer": bool(
            capabilities["payer_authorization"]
            and encounter
            and encounter.payer_type != "self_pay"
        ),
        "record_payment": False,
        "blocked_reason": (
            None
            if clearance.get("ok")
            else clearance.get("message") or "Outstanding reception clearance."
        ),
    }


def serialize_visit_detail(env, appointment, capabilities):
    """The full reception-safe visit payload."""
    encounter = appointment.encounter_id
    clearance_summary = (
        encounter._reception_clearance_summary() if encounter else {}
    )
    clearance = serialize_reception_clearance(clearance_summary)

    card = env["hospital.patient.card.issue"].search(
        [("patient_id", "=", appointment.patient_id.id)]
        + ([("encounter_id", "=", encounter.id)] if encounter else []),
        order="create_date desc, id desc",
        limit=1,
    )

    return {
        "patient": serialize_patient_reception(appointment.patient_id),
        "appointment": serialize_appointment_reception(appointment),
        "encounter": serialize_encounter_reception(encounter),
        "card_issue": serialize_card_issue(card),
        "reception_clearance": clearance,
        "consultation_clearance": serialize_consultation_clearance(appointment),
        "charge_lines": serialize_charge_lines(encounter) if encounter else [],
        "emergency": serialize_emergency_summary(encounter),
        "danger_signs": [
            serialize_danger_sign(sign) for sign in encounter.danger_sign_ids
        ]
        if encounter
        else [],
        "permitted_actions": permitted_actions(
            env, appointment, encounter, clearance, capabilities
        ),
    }


def serialize_queue_row(env, appointment, card_summary=None):
    encounter = appointment.encounter_id
    summary = encounter._reception_clearance_summary() if encounter else {}
    clearance = serialize_reception_clearance(summary)
    return {
        "id": appointment.id,
        "appointment_code": appointment.appointment_code,
        "appointment_date": datetime_value(appointment.appointment_date),
        "patient": {
            "id": appointment.patient_id.id,
            "name": appointment.patient_id.name,
            "identification_code": appointment.patient_id.identification_code,
        },
        "visit_type": selection_value(appointment.visit_type),
        "department": m2o_value(appointment.department_id),
        "doctor": m2o_value(appointment.doctor_id),
        "card_status": (card_summary or {}).get("latest_card_status"),
        "reception_clearance": clearance,
        "clinical_queue_stage": selection_value(appointment.clinical_queue_stage),
        "emergency": bool(encounter and encounter.emergency_bypass)
        or appointment.visit_type == "emergency",
        "permitted_actions": {
            "send_to_triage": bool(
                appointment.state == "confirmed" and clearance["ok"]
            ),
            "open": True,
        },
    }


def serialize_department(department):
    return {
        "id": department.id,
        "name": department.name,
        "code": department.code or None,
    }


def serialize_doctor(doctor):
    """Real stored fields only. No availability or on-duty invention."""
    return {
        "id": doctor.id,
        "name": doctor.name,
        "department": m2o_value(doctor.department_id),
        "user_linked": bool(doctor.user_id),
        "active": doctor.active,
    }
