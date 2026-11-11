{
    "name": "Hospital Dashboard",
    "summary": "Executive command center: operational overview and real-time hospital activity",
    "description": """
Hospital Dashboard / Executive Command Center
=============================================
Read-only operational dashboard for the YOYA General Hospital ERP.

Summarizes patient activity, appointments, admissions, bed occupancy,
clinical operations (laboratory, radiology, procedures, operation theatre,
nursing, pharmacy), billing and collections, insurance claims, fiscal POS
transactions, inventory alerts and recent activity in a single command
center page.

The dashboard never writes to any clinical, billing, fiscal or inventory
record. It only aggregates data and offers navigation shortcuts into the
existing hospital modules. Optional modules are detected at runtime, so the
dashboard degrades gracefully when a module is not installed.
""",
    "version": "18.0.1.0.0",
    "category": "Healthcare",
    "author": "Synergy Tech soln",
    "license": "LGPL-3",
    "depends": [
        "web",
        "hospital_management",
    ],
    "data": [
        "views/hospital_dashboard_menus.xml",
    ],
    "assets": {
        "web.assets_backend": [
            "hospital_dashboard/static/src/scss/hospital_dashboard.scss",
            "hospital_dashboard/static/src/js/hospital_dashboard.js",
            "hospital_dashboard/static/src/xml/hospital_dashboard.xml",
        ],
    },
    "application": True,
    "installable": True,
}
