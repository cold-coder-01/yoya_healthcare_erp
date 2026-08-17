{
    "name": "Hospital Billing",
    "summary": "Patient billing foundation for Ethiopian Hospital ERP",
    "version": "18.0.1.11.0",
    "category": "Healthcare",
    "author": "Synergy Tech soln",
    "license": "LGPL-3",
    # 'mail' is already present transitively through hospital_management, but the
    # payer and agreement models inherit mail.thread directly, so it is declared
    # explicitly rather than relied upon.
    "depends": ["hospital_management", "hospital_pharmacy", "hospital_radiology", "uom", "mail"],
    "data": [
        "security/hospital_billing_groups.xml",
        "security/ir.model.access.csv",
        "security/hospital_billing_payer_rules.xml",
        "data/billing_sequence.xml",
        "views/payer_views.xml",
        "views/payer_agreement_views.xml",
        "views/payer_benefit_rule_views.xml",
        "views/patient_payer_views.xml",
        "views/billing_service_views.xml",
        "views/billing_account_views.xml",
        "views/charge_line_views.xml",
        "views/charge_receipt_views.xml",
        "views/appointment_billing_views.xml",
        "views/laboratory_billing_views.xml",
        "views/radiology_billing_views.xml",
        "views/pharmacy_billing_views.xml",
        "views/patient_bill_views.xml",
        "views/patient_bill_payment_wizard_views.xml",
        "views/patient_billing_views.xml",
        "views/billing_menus.xml",
        "reports/patient_bill_template.xml",
        "reports/patient_bill_report.xml",
    ],
    "assets": {
        "web.assets_backend": [
            "hospital_billing/static/src/scss/billing_theme.scss",
        ],
    },
    # Installs the ACTIVE agreement and patient-eligibility overlap EXCLUDE
    # constraints, which the ORM cannot express. Degrades loudly when btree_gist
    # is unavailable; Python advisory-lock guards remain authoritative.
    "post_init_hook": "post_init_hook",
    "application": True,
    "installable": True,
}
