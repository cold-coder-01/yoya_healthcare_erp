{
    "name": "YOYA Clinical Bridge",
    "summary": "Triage and encounter linkage for the hospital patient evaluation workflow",
    "description": """
YOYA Clinical Bridge
====================

Extends ``hospital.patient.evaluation`` with the triage concepts the base
evaluation record is missing: an encounter link, a chief complaint, a triage
priority, triage notes, nurse assignment and start/completion timestamps.

Also adds clinical range validation on the vital signs, one-evaluation-per-
appointment enforcement, a post-completion edit lock with a manager-only
reopen path, and department/assignment scoped record rules.

This module never modifies ``hospital_management`` or ``hospital_billing``.
""",
    "version": "18.0.1.0.0",
    "category": "Healthcare",
    "author": "YOYA Healthcare",
    "license": "LGPL-3",
    "depends": [
        "hospital_management",
        "hospital_billing",
    ],
    "data": [
        "security/yoya_clinical_bridge_security.xml",
        "views/res_users_views.xml",
        "views/patient_evaluation_views.xml",
    ],
    "installable": True,
    "application": False,
    "auto_install": False,
}
