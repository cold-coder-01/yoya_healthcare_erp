{
    "name": "Hospital Billing Accounting Bridge",
    "summary": "Bridge that posts hospital patient bills and payments into Odoo Accounting",
    "description": """
Hospital Billing Accounting Bridge
==================================
A separate bridge module that connects the existing Hospital Billing system to
Odoo 18 Accounting Community (om_account_accountant).

It does NOT create a second billing system. It only maps hospital bill source
types and payment methods to accounting accounts/journals and posts manual
journal entries through explicit button clicks.
""",
    "version": "18.0.1.3.0",
    "category": "Healthcare",
    "author": "Synergy Tech soln",
    "license": "LGPL-3",
    "depends": ["hospital_billing", "hospital_inventory", "om_account_accountant", "mail"],
    "data": [
        "security/accounting_security.xml",
        "security/ir.model.access.csv",
        "data/billing_accounting_sequence.xml",
        "views/invoice_batch_views.xml",
        "views/billing_invoice_views.xml",
        "views/patient_advance_accounting_views.xml",
        "views/billing_accounting_config_views.xml",
        "views/accounting_posting_log_views.xml",
        "views/patient_bill_accounting_views.xml",
        "views/billing_accounting_menus.xml",
    ],
    "application": False,
    "installable": True,
}



