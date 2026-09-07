{
    "name": "Hospital Radiology",
    "summary": "Radiology request, result and imaging workflow for Ethiopian Hospital ERP",
    "description": """
Hospital Radiology
==================

Radiology requests, results and the report a radiologist signs.

Adds ``hospital.radiology.image``: JPEG, PNG and PDF files attached to a
result, with the type read from the file's own bytes rather than its name, a
25 MB ceiling, and server-derived mimetype and size. The image set may be
managed while the result is draft or entered, and is frozen from validated
onward against create, write, unlink and archive alike, because a report whose
evidence can still be replaced is signed off in name only.
""",
    "version": "18.0.1.1.0",
    "category": "Healthcare",
    "author": "Ethiopian Hospital ERP",
    "license": "LGPL-3",
    "depends": ["hospital_management"],
    "data": [
        "security/ir.model.access.csv",
        "data/radiology_sequence.xml",
        "views/radiology_request_views.xml",
        "views/radiology_result_views.xml",
        "views/radiology_patient_views.xml",
        "views/radiology_menus.xml",
        "reports/radiology_request_template.xml",
        "reports/radiology_request_report.xml",
        "reports/radiology_result_template.xml",
        "reports/radiology_result_report.xml",
    ],
    "application": True,
    "installable": True,
}
