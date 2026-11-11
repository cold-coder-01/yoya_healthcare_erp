{
    "name": "Hospital Insurance Accounting Bridge",
    "summary": "Reclassify insurance / corporate payer share from patient "
    "receivable to a dedicated payer receivable account",
    "description": """
Hospital Insurance Accounting Bridge
====================================
A clean accounting bridge that reclassifies the payer (insurance / corporate /
government / NGO / donor / credit) share of an already-posted patient bill from
the generic Patient Receivable account to a dedicated payer receivable account.

It does NOT re-post revenue and does NOT create payer payments. It only moves
the payer share between receivable accounts via an explicit, balanced manual
journal entry triggered by an accountant.

    Dr Corporate Receivable   4,000
        Cr Patient Receivable   4,000
""",
    "version": "18.0.1.1.0",
    "category": "Healthcare",
    "author": "Synergy Tech soln",
    "license": "LGPL-3",
    "depends": [
        "hospital_insurance",
        "hospital_billing_accounting",
        "om_account_accountant",
        "mail",
    ],
    "data": [
        "security/ir.model.access.csv",
        "data/insurance_accounting_sequence.xml",
        "views/insurance_accounting_config_views.xml",
        "views/insurance_accounting_log_views.xml",
        "views/insurance_payer_payment_wizard_views.xml",
        "views/insurance_rejection_resolution_wizard_views.xml",
        "views/insurance_claim_accounting_views.xml",
        "views/patient_bill_split_views.xml",
        "views/patient_bill_payment_wizard_views.xml",
        "views/insurance_accounting_menus.xml",
    ],
    "application": False,
    "installable": True,
}
