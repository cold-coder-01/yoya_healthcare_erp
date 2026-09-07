{
    "name": "Hospital Pharmacy Fiscal Bridge",
    "summary": "Connect pharmacy dispensing with the fiscal POS bridge: "
    "auto inventory consumption after successful fiscal payment",
    "description": """
Hospital Pharmacy Fiscal Bridge
===============================
Connector between hospital_pharmacy, hospital_fiscal_bridge and
hospital_inventory. No fiscal, billing or stock logic is duplicated here —
this module only wires the existing workflows together.

Business flow
-------------
1. Pharmacist prepares the dispense (Ready / Partially Dispensed).
2. Pharmacist clicks "Prepare Fiscal Payment": Odoo creates a patient bill
   from the dispensed lines and a fiscal transaction (FISCxxxxx) on it.
3. Patient pays on the SUNMI fiscal terminal. The terminal only looks up
   the fiscal reference and sends the success callback — it never touches
   stock, bills or dispense records.
4. On a validated success callback (exact amount, no duplicate receipt),
   Odoo automatically marks the dispense as Dispensed, creates the
   inventory consumption, allocates batches FEFO and consumes the stock.
5. If the automatic consumption fails, the fiscal payment is kept, the
   error is stored on the dispense and a manager-only
   "Retry Inventory Consumption" recovery button appears.

Odoo stays the single source of truth for dispense state, payments,
stock on hand, COGS/valuation and the audit trail.
""",
    "version": "18.0.1.0.0",
    "category": "Healthcare",
    "author": "Synergy Tech soln",
    "license": "LGPL-3",
    "depends": [
        "hospital_pharmacy",
        "hospital_fiscal_bridge",
        "hospital_inventory",
        "hospital_billing",
    ],
    "data": [
        "security/ir.model.access.csv",
        "views/pharmacy_medicine_fiscal_views.xml",
        "views/pharmacy_dispense_fiscal_views.xml",
        "views/fiscal_transaction_source_views.xml",
    ],
    "application": False,
    "installable": True,
    "auto_install": False,
}
