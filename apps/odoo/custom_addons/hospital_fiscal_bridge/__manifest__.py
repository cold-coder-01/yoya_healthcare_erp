{
    "name": "Hospital Fiscal POS Bridge",
    "summary": "Secure bridge between hospital billing and external fiscal POS terminals (SUNMI / Zoorya ET)",
    "description": """
Hospital Fiscal POS Bridge
==========================
Connects the hospital billing system to an external fiscal POS / payment
terminal (e.g. SUNMI P3 MIX running Zoorya ET).

The central Odoo hospital ERP remains the single source of truth for
patients, bills, payments and accounting. The terminal is treated as a
stateless, secure fiscal payment gateway that can only:

1. Look up a fiscal transaction by exact reference/barcode.
2. Receive the payable amount and minimal bill information.
3. Process the fiscal receipt/payment externally.
4. Send a success/failure callback to Odoo.
5. Let Odoo create the official hospital payment history record.

The terminal never creates bill lines, edits patient records, calculates
insurance rules, posts accounting entries or writes bill states directly.
""",
    "version": "18.0.1.0.0",
    "category": "Healthcare",
    "author": "Synergy Tech soln",
    "license": "LGPL-3",
    "depends": [
        "hospital_billing",
        "mail",
    ],
    "data": [
        "security/ir.model.access.csv",
        "data/fiscal_sequence.xml",
        "views/fiscal_device_views.xml",
        "views/fiscal_transaction_views.xml",
        "views/fiscal_payment_log_views.xml",
        "views/patient_bill_fiscal_views.xml",
        "views/fiscal_menus.xml",
    ],
    "assets": {
        "web.assets_backend": [
            "hospital_fiscal_bridge/static/src/scss/fiscal_transaction_form.scss",
            "hospital_fiscal_bridge/static/src/scss/fiscal_device_form.scss",
        ],
    },
    "application": True,
    "installable": True,
}
