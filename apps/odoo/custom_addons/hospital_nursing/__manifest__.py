{
    "name": "Hospital Nursing",
    "summary": "Inpatient nursing foundation — rounds, notes, care plans, and medication administration",
    "version": "18.0.1.0.0",
    "category": "Healthcare",
    "author": "Synergy Tech solns",
    "license": "LGPL-3",
    "depends": [
        "hospital_management",
        "hospital_admission",
        "hospital_pharmacy",
        "mail",
    ],
    "data": [
        "security/ir.model.access.csv",
        "data/nursing_sequence.xml",
        "views/nursing_round_views.xml",
        "views/nursing_note_views.xml",
        "views/care_plan_views.xml",
        "views/medication_administration_views.xml",
        "views/patient_nursing_views.xml",
        "views/nursing_menus.xml",
        "reports/nursing_summary_template.xml",
        "reports/nursing_summary_report.xml",
    ],
    "assets": {
        "web.assets_backend": [
            "hospital_nursing/static/src/scss/nursing_theme.scss",
        ],
    },
    "application": True,
    "installable": True,
}
