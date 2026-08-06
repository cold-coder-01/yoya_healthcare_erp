{
    "name": "YOYA Reception Bridge",
    "summary": "Receptionist-to-triage workflow foundation: security hardening, "
    "patient card issuance and visit registration",
    "description": """
YOYA Reception Bridge (Phase 1)
===============================

Phase 1A - security hardening
    * Emergency bypass on hospital.encounter becomes an authorized act.
    * action_start_consultation is restricted to the assigned doctor,
      hospital managers and system administrators.
    * A dedicated cashier group is introduced.
    * Record rules are added for the new models and, conservatively, for
      hospital.appointment and hospital.encounter.

Phase 1B - patient card issuance
    * hospital.patient.card.issue with a full issuance lifecycle.
    * hospital.billing.service gains a 'registration' service type and a
      never-guess default-card-service resolver.

Phase 1C - reception workflow foundation
    * Visit metadata on hospital.appointment.
    * Emergency danger-sign screening on hospital.encounter.
    * hospital.reception.workflow, the authoritative registration service.

This module never edits hospital_management or hospital_billing. Every
charge is raised through hospital.billing.engine.create_or_update_charge so
idempotency, the commercial snapshot and audit stay in one place.
""",
    "version": "18.0.1.0.0",
    "category": "Healthcare",
    "author": "YOYA Healthcare",
    "license": "LGPL-3",
    "depends": [
        "hospital_management",
        "hospital_billing",
        "yoya_clinical_bridge",
    ],
    "data": [
        "security/yoya_reception_groups.xml",
        "security/ir.model.access.csv",
        "security/yoya_reception_rules.xml",
        "data/reception_sequence.xml",
        "data/emergency_danger_sign_data.xml",
        "views/emergency_danger_sign_views.xml",
        "views/patient_card_issue_views.xml",
        "views/billing_service_views.xml",
        "views/hospital_appointment_views.xml",
        "views/hospital_encounter_views.xml",
        "views/hospital_patient_views.xml",
        "views/reception_menus.xml",
    ],
    "installable": True,
    "application": False,
    "auto_install": False,
}
