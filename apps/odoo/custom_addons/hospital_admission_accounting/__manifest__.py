{
    "name": "Hospital Admission Accounting Bridge",
    "summary": "Posts the inpatient settlement (advances, delivered care, advance "
    "application and refunds) to Odoo Accounting",
    "description": """
Hospital Admission Accounting Bridge
====================================
The bridge between the operational inpatient lifecycle (hospital_admission)
and the hospital accounting bridge (hospital_billing_accounting).

* Patient advances and every other patient receipt: Dr cash/bank, Cr Patient
  Advance Liability (never revenue).
* Delivered care: a standard customer invoice from delivered quantity only
  (Dr Patient Receivable, Cr Healthcare Revenue).
* Advance application: Dr Patient Advance Liability, Cr Patient Receivable,
  capped by the settlement authority's own advance-applied figure.
* Refund: Dr Patient Advance Liability, Cr cash/bank, atomic with the
  operational refund.

No second settlement formula, no daily accrual, no payer accounting.
""",
    "version": "18.0.1.0.0",
    "category": "Healthcare",
    "author": "YOYA Healthcare",
    "license": "LGPL-3",
    "depends": ["hospital_admission", "hospital_billing_accounting"],
    "data": [
        "security/ir.model.access.csv",
        "security/admission_accounting_security.xml",
        "data/admission_accounting_sequence.xml",
        "views/admission_accounting_operation_views.xml",
    ],
    "auto_install": True,
    "application": False,
    "installable": True,
}
