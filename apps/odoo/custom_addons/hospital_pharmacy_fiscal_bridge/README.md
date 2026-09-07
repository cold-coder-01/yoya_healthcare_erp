# Hospital Pharmacy Fiscal Bridge

Connector module that links pharmacy dispensing with the fiscal POS bridge,
patient billing, and hospital inventory consumption.

This module does not duplicate fiscal, billing, or stock logic. It wires the
existing workflows together so Odoo remains the source of truth for dispense
state, payment state, stock movement, valuation, and audit history.

## Purpose

The module enforces this business rule:

Pharmacy medicines can only be dispensed after a successful fiscal payment.

After the payment is confirmed by the fiscal terminal, the module automatically:

1. Marks the pharmacy dispense as dispensed.
2. Creates or reuses the inventory consumption record.
3. Allocates batches using FEFO.
4. Consumes inventory stock.
5. Records chatter and audit log messages.

If inventory automation fails after payment, the payment remains valid and a
manager can retry inventory consumption after fixing the cause.

## Dependencies

This module depends on:

- `hospital_pharmacy`
- `hospital_fiscal_bridge`
- `hospital_inventory`
- `hospital_billing`

## Main Workflow

1. A pharmacist prepares a pharmacy dispense in `Ready` or `Partially Dispensed`
   state.
2. The pharmacist clicks `Prepare Fiscal Payment`.
3. Odoo creates or refreshes a patient bill from the dispensed medicine lines.
4. Odoo creates a fiscal transaction linked to the dispense.
5. The patient pays on the fiscal POS terminal using the fiscal reference.
6. The fiscal bridge validates the terminal callback.
7. On successful payment, this module dispatches the success event back to the
   pharmacy dispense.
8. The dispense is validated and inventory consumption is created and consumed.

## Important Rules

- The fiscal terminal never decides the payable amount.
- The payable amount is computed in Odoo from dispensed quantities and unit
  prices.
- Prescribed quantities are not charged unless they are dispensed.
- A dispense cannot be manually validated before fiscal payment, except through
  a manager or system administrator emergency override context.
- Duplicate active fiscal payment requests are avoided.
- Duplicate terminal callbacks do not re-run the post-payment automation.
- A successful fiscal payment is never rolled back because inventory automation
  failed.

## Models Extended

### `hospital.pharmacy.dispense`

Adds fiscal and inventory bridge fields:

- `amount_payable`
- `fiscal_bill_id`
- `fiscal_transaction_ids`
- `fiscal_transaction_id`
- `fiscal_transaction_count`
- `fiscal_payment_state`
- `fiscal_reference`
- `fiscal_receipt_number`
- `inventory_consumption_id`
- `inventory_consumption_state`
- `auto_consumption_error`

Main methods:

- `action_prepare_fiscal_payment()`
- `action_mark_dispensed()`
- `_on_fiscal_payment_success(transaction)`
- `_auto_fulfill_inventory_consumption()`
- `_auto_assign_consumption_batches(consumption)`
- `action_retry_inventory_consumption()`
- `action_view_fiscal_transactions()`

### `hospital.fiscal.transaction`

Adds source document fields:

- `source_model`
- `source_record_id`
- `source_reference`
- `pharmacy_dispense_id`

Extends terminal payment success handling through:

- `action_mark_paid_from_terminal(payload)`
- `_dispatch_source_payment_success()`

When a callback is accepted, the transaction notifies the linked source record
through `_on_fiscal_payment_success(transaction)`.

### `hospital.pharmacy.medicine`

Adds:

- `sale_price`

This is the catalog selling price used to default dispense line unit prices.

### `hospital.pharmacy.dispense.line`

Adds:

- `unit_price`
- `price_subtotal`

`price_subtotal` is computed as:

```text
dispensed_quantity x unit_price
```

## User Interface

### Pharmacy Dispense

The dispense form is extended with:

- `Prepare Fiscal Payment` button.
- `Retry Inventory Consumption` button for managers and system administrators.
- Fiscal payment smart button.
- Fiscal Payment notebook page.
- Fiscal payment status fields.
- Inventory consumption status fields.
- Auto consumption error display.
- Unit price and subtotal on medicine lines.
- Chatter for fiscal and inventory audit messages.

The normal `Validate Dispense` button is hidden until fiscal payment is paid.

### Fiscal Transaction

The fiscal transaction form is extended with:

- `Open Pharmacy Dispense` button.
- Source document information.
- Technical source model and record ID for administrators.

### Pharmacy Medicine

The medicine form and list views show `Sale Price`.

## Access

Pharmacists receive read-only access to fiscal bridge records needed for the
workflow:

- Fiscal transactions
- Fiscal transaction lines
- Fiscal payment logs
- Fiscal devices

Inventory retry and administrative recovery actions are limited to managers and
system administrators.

## Failure And Recovery

If payment succeeds but automatic inventory consumption fails:

1. The fiscal payment remains recorded.
2. The error is stored on the dispense in `auto_consumption_error`.
3. The dispense shows a warning on the Fiscal Payment page.
4. A manager or system administrator can use `Retry Inventory Consumption`.

Common causes include:

- Missing stock.
- Expired or unavailable batches.
- Missing inventory item configuration.
- Dispense lines changed after fiscal payment was prepared.

## Files

- `__manifest__.py` - module metadata and dependencies.
- `models/pharmacy_dispense.py` - main bridge workflow.
- `models/fiscal_transaction.py` - source callback dispatch.
- `models/pharmacy_medicine.py` - sale price and dispense line pricing.
- `views/pharmacy_dispense_fiscal_views.xml` - dispense UI integration.
- `views/fiscal_transaction_source_views.xml` - fiscal source UI integration.
- `views/pharmacy_medicine_fiscal_views.xml` - medicine sale price UI.
- `security/ir.model.access.csv` - pharmacist read access to fiscal records.

## Operational Notes

- Keep sale prices configured before preparing fiscal payment.
- Do not edit dispense lines after a fiscal payment request is prepared unless a
  new payment flow is intentionally required.
- Investigate `auto_consumption_error` before retrying inventory consumption.
- Use the fiscal transaction reference as the patient-facing payment reference.
- Use the linked patient bill as the official billing and payment anchor.
