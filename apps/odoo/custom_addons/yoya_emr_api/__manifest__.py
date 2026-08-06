{
    "name": "YOYA EMR API",
    "version": "18.0.1.1.0",
    "license": "LGPL-3",
    "depends": [
        "base",
        "web",
        # Demo workflow. Kept so the existing /yoya-emr/api/v1/appointments
        # routes continue to resolve against yoya.emr.appointment.
        "yoya_emr_demo",
        # Real UAT clinical schema.
        "hospital_management",
        # Supplies appointment.billing_blocked / billing_clearance_message and
        # the financial clearance gate on action_start_consultation.
        "hospital_billing",
        # Supplies the triage fields, the encounter link and the evaluation
        # record rules the clinical endpoints depend on.
        "yoya_clinical_bridge",
        # Supplies hospital.reception.workflow, hospital.patient.card.issue,
        # the encounter reception_* clearance fields and the cashier /
        # emergency-authorizer groups the reception endpoints depend on.
        "yoya_reception_bridge",
    ],
    "data": [],
    "installable": True,
    "application": False,
    "auto_install": False,
}
