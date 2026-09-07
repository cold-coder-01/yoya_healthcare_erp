{
    "name": "Hospital Pharmacy",
    "summary": "Pharmacy medicine catalog and prescription dispensing workflow for Ethiopian Hospital ERP",
    "version": "18.0.1.1.0",
    "category": "Healthcare",
    "author": "Ethiopian Hospital ERP",
    "license": "LGPL-3",
    "depends": ["hospital_management"],
    "data": [
        "security/ir.model.access.csv",
        "data/pharmacy_sequence.xml",
        "views/pharmacy_medicine_views.xml",
        "views/pharmacy_dispense_views.xml",
        "views/pharmacy_patient_views.xml",
        "views/pharmacy_menus.xml",
        "reports/pharmacy_dispense_template.xml",
        "reports/pharmacy_dispense_report.xml",
    ],
    "assets": {
        "web.assets_backend": [
            "hospital_pharmacy/static/src/scss/pharmacy_theme.scss",
        ],
    },
    "application": True,
    "installable": True,
}
