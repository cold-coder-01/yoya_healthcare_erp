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

Owns ``hospital.consultation``, the physician's consultation note: one record
per encounter, opened only for a visit that has already reached
``in_consultation``, with a copy-once presenting complaint seeded from the
completed nursing triage, an optimistic-concurrency token on every save and a
post-completion clinical freeze.

Extends ``hospital.patient.diagnosis`` with the encounter and consultation
anchors it lacks, a diagnostic certainty axis independent of clinical status,
a one-primary-per-consultation invariant enforced by a partial unique index,
and the record rules that model shipped without.

Extends ``hospital.laboratory.request`` with a consultation anchor and a
per-consultation idempotency token, so the Doctor Desk can place a laboratory
order that runs the EXISTING ``action_confirm_request()`` workflow -- billing,
coverage and clearance all stay where they already are.

Extends ``hospital.radiology.request`` the same way, and adds the record rules
the radiology models shipped without entirely, so a doctor reaches their own
imaging orders and results rather than the whole hospital's.

This module never modifies ``hospital_management``, ``hospital_billing`` or
``hospital_radiology``.
""",
    "version": "18.0.1.4.0",
    "category": "Healthcare",
    "author": "YOYA Healthcare",
    "license": "LGPL-3",
    "depends": [
        "hospital_management",
        "hospital_billing",
        # Already reached transitively -- hospital_billing hard-depends on it --
        # but named explicitly because this module now inherits its models and
        # references its model_ids by external id in the security XML. A
        # transitive dependency is a fact about another module's manifest, and
        # relying on one for a load-order guarantee is how an unrelated edit
        # over there turns into a ParseError over here.
        "hospital_radiology",
    ],
    # Access rows before record rules, matching yoya_reception_bridge: the ACL
    # decides WHETHER a group may touch the model at all, the rules decide WHICH
    # rows, and loading them in that order keeps the security story readable in
    # the manifest as well as in the files.
    "data": [
        "security/ir.model.access.csv",
        "security/yoya_clinical_bridge_security.xml",
        "data/consultation_sequence.xml",
        "views/res_users_views.xml",
        "views/patient_evaluation_views.xml",
        "views/consultation_views.xml",
    ],
    "installable": True,
    "application": False,
    "auto_install": False,
}
