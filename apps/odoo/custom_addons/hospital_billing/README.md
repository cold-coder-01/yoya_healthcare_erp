# Hospital Billing

## Task 32B-1 — financial-state terminology

Encounter charges now expose five independent lifecycle fields:

* `operational_funding_state` — receipt allocation only (`Funded` is not paid).
* `accounting_receipt_state` — whether receipt accounting has been posted.
* `invoice_state` — invoice lifecycle, unchanged technically.
* `settlement_state` — reserved for posted/reconciled receivables.
* `fiscal_state` — independent fiscal-event lifecycle.

The historical `payment_state` values (`unpaid`, `partially_paid`, `paid`,
`refunded`) are preserved for compatibility but relabelled as operational funding
and hidden from normal charge views. Existing formulas for advance held, applied
advance and patient credit are unchanged. Task 32B-1 creates no invoice, accounting
entry, reconciliation or fiscal record.

**Module:** `hospital_billing`
**Version:** 18.0.1.0.0
**License:** LGPL-3
**Author:** Ethiopian Hospital ERP

---

## Overview

`hospital_billing` is the patient billing foundation module for the Ethiopian Hospital ERP system built on Odoo 18 Community. It provides a service catalog, patient bill management, a full payment workflow, PDF receipt generation, and integration with the existing patient record via smart button and notebook tab.

This is a **foundation phase** module. Accounting integration, official invoice creation, and automatic charge generation from lab/radiology/pharmacy will be added in later phases.

---

## Dependencies

| Module | Purpose |
|---|---|
| `hospital_management` | Patient, doctor, appointment, audit log, security groups |
| `hospital_pharmacy` | Required dependency (auto-charge integration: future phase) |
| `hospital_radiology` | Required dependency (auto-charge integration: future phase) |

---

## Features

- **Billing Service Catalog** — Create reusable service charge entries (consultation, lab, radiology, pharmacy, procedure, admission, nursing, other) with code, type, default price, and currency.
- **Patient Bill** — Full bill header with BILL00001 sequence, patient, physician, appointment linkage, cashier assignment, multi-payment method support, and line-level discount.
- **Bill Line** — Itemised charge lines with service lookup, source type, quantity, unit price, discount percentage, and computed subtotal.
- **Payment Workflow** — Draft → Confirmed → Partially Paid → Paid, with Cancel and Reset to Draft transitions.
- **Computed Totals** — Subtotal, total discount, total, amount due automatically computed from bill lines.
- **Patient Integration** — Smart button (Bills count) and Bills notebook tab on the patient form.
- **PDF Receipt** — Patient Bill / Receipt PDF using the shared YOYA Hospital report header.
- **Audit Logging** — All create, update, state change, archive, and blocked delete attempts are logged to `hospital.audit.log`.
- **Role-Based Access** — Nine hospital security roles with appropriate read/write/create/delete permissions.

---

## Models

### `hospital.billing.service`
Service charge catalog. Each service has a code, type, default price, and optional description. Display name renders as `[CODE] Name` when a code is set.

| Field | Type | Notes |
|---|---|---|
| `name` | Char | Required |
| `code` | Char | Optional short code, e.g. LAB-CBC |
| `service_type` | Selection | consultation, laboratory, radiology, pharmacy, procedure, admission, nursing, other |
| `default_price` | Float | Pre-fills bill line unit price on selection |
| `currency_id` | Many2one res.currency | Defaults to company currency |
| `description` | Text | Optional |
| `active` | Boolean | Archive support |

### `hospital.patient.bill`
Main bill header. Sequence: `BILL00001`.

| Field | Type | Notes |
|---|---|---|
| `name` | Char | Auto sequence BILL00001, readonly |
| `patient_id` | Many2one hospital.patient | Required |
| `appointment_id` | Many2one hospital.appointment | Optional |
| `physician_id` | Many2one hospital.doctor | Optional |
| `bill_date` | Date | Default today |
| `cashier_id` | Many2one res.users | Default current user |
| `currency_id` | Many2one res.currency | Default company currency |
| `line_ids` | One2many bill.line | Bill charge lines |
| `amount_untaxed` | Float | Computed: sum of line subtotals |
| `discount_amount` | Float | Computed: gross minus subtotal |
| `amount_total` | Float | Computed: same as amount_untaxed (foundation phase, no tax) |
| `payment_ids` | One2many patient.bill.payment | Individual payment records |
| `payment_count` | Integer | Computed count of payments |
| `amount_paid` | Float | Computed stored: sum of payment_ids amounts |
| `amount_due` | Float | Computed: amount_total minus amount_paid |
| `payment_method` | Selection | cash, bank_transfer, mobile_money, card, insurance, other |
| `payment_reference` | Char | Optional reference number |
| `notes` | Text | Internal notes |
| `state` | Selection | draft, confirmed, partially_paid, paid, cancelled |
| `active` | Boolean | Archive support |

### `hospital.patient.bill.payment`
Individual payment record linked to a bill. Sequence: `PAY00001`.

| Field | Type | Notes |
|---|---|---|
| `name` | Char | Auto sequence PAY00001, readonly |
| `bill_id` | Many2one patient.bill | Required, cascade delete |
| `payment_date` | Datetime | Default now |
| `amount` | Float | Payment amount, must be > 0 and ≤ remaining due |
| `currency_id` | Many2one res.currency | Related from bill |
| `payment_method` | Selection | cash, bank_transfer, mobile_money, card, insurance, other |
| `payment_reference` | Char | Optional reference |
| `cashier_id` | Many2one res.users | Default current user |
| `notes` | Text | Optional |
| `active` | Boolean | Archive support |

Deletion is blocked for all roles except System Administrator (UserError + audit log).

### `hospital.patient.bill.payment.wizard`
Transient model powering the Register Payment dialog. Validates amount > 0 and amount ≤ remaining due before creating a `hospital.patient.bill.payment` record.

### `hospital.patient.bill.line`
Itemised charge lines linked to a bill.

| Field | Type | Notes |
|---|---|---|
| `bill_id` | Many2one patient.bill | Required, cascade delete |
| `service_id` | Many2one billing.service | Optional; auto-fills description, price, type |
| `description` | Char | Required |
| `source_type` | Selection | consultation, laboratory, radiology, pharmacy, procedure, admission, other |
| `quantity` | Float | Default 1.0 |
| `unit_price` | Float | Per unit price |
| `discount` | Float | Percentage discount, default 0 |
| `subtotal` | Float | Computed: qty × unit_price × (1 − discount/100) |
| `source_model` | Char | For future auto-charge linking |
| `source_record_id` | Integer | For future auto-charge linking |
| `sequence` | Integer | Line ordering, default 10 |

---

## Workflow

```
draft → confirmed → partially_paid → paid
            ↓              ↓
         cancelled ←───────┘
            ↓
          draft (reset)
```

| Action | From State | To State | Method |
|---|---|---|---|
| Confirm Bill | draft | confirmed | `action_confirm` |
| Register Payment | confirmed / partially_paid | partially_paid or paid | `action_register_payment` |
| Mark as Paid | confirmed / partially_paid | paid | `action_mark_paid` |
| Cancel | draft / confirmed / partially_paid | cancelled | `action_cancel` |
| Reset to Draft | cancelled | draft | `action_reset_to_draft` |

**Payment state logic:**
- `amount_paid == 0` and confirmed → stays `confirmed`
- `amount_paid > 0` and `amount_paid < amount_total` → `partially_paid`
- `amount_paid >= amount_total` → `paid`

---

## Menu Structure

```
Billing (App Launcher, sequence 50)
├── Patient Bills
├── Billing Services  (manager/accountant/admin only)
└── Configuration     (manager/accountant/admin only)
    └── Billing Services
```

Patient-specific bills are accessed through the smart button or Bills tab on the Patient form — not via a top-level menu item.

---

## Security Role Summary

| Role | Billing Service | Patient Bill | Bill Line | Bill Payment |
|---|---|---|---|---|
| Receptionist | Read | Read/Create/Write | Read/Create/Write | Read/Create |
| Doctor | Read | Read | Read | Read |
| Nurse | Read | Read | Read | Read |
| Pharmacist | Read | Read | Read | Read |
| Lab Technician | Read | Read | Read | Read |
| Accountant | Read/Create/Write | Read/Create/Write | Read/Create/Write | Read/Create/Write |
| Manager | Read/Create/Write | Read/Create/Write | Read/Create/Write | Read/Create/Write |
| Data Protection Officer | Read | Read | Read | Read |
| System Administrator | Full | Full | Full | Full |

**No role except System Administrator can delete billing records via the UI.**
Deletion attempts by non-admins are blocked at the model level with a UserError and logged in `hospital.audit.log`.

---

## Reports

### Patient Bill / Receipt
- **Report name:** `hospital_billing.report_patient_bill`
- **Type:** QWeb PDF
- **Accessible from:** Print menu on Patient Bill form
- **Header:** Shared YOYA Hospital header (`hospital_management.hospital_report_header`)
- **Content:** Patient info, bill lines table, payment history table (PAY00001, date, method, cashier, amount), subtotal/discount/total/paid/due summary, notes, footer

---

## Installation

1. Ensure the following modules are installed and working:
   - `hospital_management`
   - `hospital_pharmacy`
   - `hospital_radiology`

2. Place this module in your `custom_addons` directory.

3. Update the apps list in Odoo and install **Hospital Billing**.

4. The **Billing** app icon will appear on the main dashboard.

---

## Manual Test Checklist

- [ ] Module installs without errors
- [ ] Billing app icon appears on dashboard
- [ ] Can open Patient Bills list
- [ ] Can open Billing Services list
- [ ] Configuration → Billing Services is accessible to manager/admin
- [ ] Create service: Consultation Fee, code CONS-GEN, type consultation, price 300
- [ ] Create service: Complete Blood Count, code LAB-CBC, type laboratory, price 250
- [ ] Create service: Brain CT Scan, code RAD-CT-BRAIN, type radiology, price 2500
- [ ] Service display name shows `[LAB-CBC] Complete Blood Count`
- [ ] Create Patient Bill for HMS0001
- [ ] Bill sequence generates BILL00001
- [ ] Add bill line; selecting service auto-fills description, price, type
- [ ] Bill line subtotal computes: 30 × 5 × (1 - 0) = 150
- [ ] Bill total sums all line subtotals
- [ ] Discount amount computes when discount % > 0
- [ ] Confirm Bill button changes state to confirmed
- [ ] Register Payment wizard opens with remaining amount pre-filled
- [ ] Payment 1 (1,000 ETB): Amount Paid = 1,000, Amount Due = 2,050, state = partially_paid
- [ ] Payment 2 (2,050 ETB): Amount Paid = 3,050, Amount Due = 0, state = paid
- [ ] amount_paid is cumulative (computed from payment_ids), not overwritten
- [ ] amount_due = amount_total - sum(payment_ids.amount)
- [ ] Payment reference PAY00001, PAY00002 auto-generated
- [ ] Payments tab shows full payment history list
- [ ] Enter full amount → state becomes paid
- [ ] Cancel works from draft / confirmed / partially_paid
- [ ] Reset to Draft works from cancelled
- [ ] Print button generates PDF without RPC error
- [ ] PDF shows YOYA Hospital header with bill reference and date
- [ ] PDF shows bill lines table with subtotal and totals
- [ ] Patient form shows Bills smart button with correct count
- [ ] Clicking Bills smart button opens the bill
- [ ] Bills tab on patient form shows bill list
- [ ] Receptionist cannot delete a bill (UserError)
- [ ] Delete attempt is logged in Audit Logs
- [ ] System Administrator can delete a bill (no UserError)
- [ ] Archive a bill — active becomes False, visible with Archived filter
- [ ] Create/update/state change logged in Audit Logs

---

## UI Design

### Billing UI Pattern V1 (Task 25B)

The Patient Bill form implements the hospital ERP **UI Pattern V1** design system, scoped under `.hospital_billing_profile`.

**SCSS file:** `static/src/scss/billing_theme.scss`
**Color system:** Emerald — primary `#006A4F`
**Scope class:** `hospital_billing_profile` on both `<form>` and `<sheet>`

**Layout sections:**
1. **Hero Header** — Bill icon (72×72 emerald), bill reference (28px bold), patient name subtitle, meta grid (bill date, cashier, currency, payment count), state ribbon (color-coded per state)
2. **KPI Row** — Five cards: Patient, Bill Total, Amount Paid, Amount Due (amber/green icon by state), Status badge
3. **Fully Paid Badge** — Emerald chip shown only when `state == 'paid'`
4. **Two-Column Info Grid** — Bill Information (left) and Payment Details with totals (right)
5. **Notebook Tabs** — Bill Lines, Payments (with styled history section), Notes (with card wrapper)

**State ribbon colors:**
- Draft → grey
- Confirmed → emerald outline
- Partially Paid → amber
- Paid → solid emerald
- Cancelled → red outline

**Manual UI Test Checklist (Task 25B):**
- [ ] Hero header shows bill ref, patient name, meta grid, and correct state ribbon
- [ ] KPI cards show correct values for Patient, Bill Total, Amount Paid, Amount Due, Status
- [ ] Amount Due KPI card shows amber icon when not paid, green icon when paid
- [ ] "✓ Settled" note appears under Amount Due when state = paid
- [ ] "Bill fully settled" chip is visible only when state = paid
- [ ] Two-column info grid shows Bill Information on left, Payment Details + totals on right
- [ ] Bill Lines tab shows editable list with subtotal column
- [ ] Payments tab shows Payment History section title and payment list with total
- [ ] Notes tab shows card wrapper with Internal Notes title
- [ ] Notebook tab active state has emerald top border
- [ ] State ribbon in hero updates correctly on workflow transitions
- [ ] No styling leaks outside `.hospital_billing_profile` scope
- [ ] All billing logic (payments, amounts, state) unchanged and working correctly

---

## Known Limitations (Foundation Phase)

1. **No Odoo Accounting integration** — Bills do not create `account.move` (invoice) records. Accounting integration is planned for a later phase.
2. **No official invoice creation** — Bills are standalone records, not linked to Odoo's native invoicing workflow.
3. **No automatic charge generation** — Lab, radiology, and pharmacy charges must be entered manually as bill lines. Automatic charge generation from these modules will be added in a later phase.
4. **No insurance claim workflow** — Insurance billing is tracked via `payment_method = insurance` only; claim submission and tracking are out of scope.
5. **No tax / VAT / WHT configuration** — The `amount_total` equals `amount_untaxed`. Tax logic will be added when Odoo Accounting is integrated.
6. **No payment gateway integration** — All payments are recorded manually.
7. **source_model / source_record_id fields are reserved** — These fields are scaffolded for future auto-charge linking from lab/radiology/pharmacy records but are not actively used in this phase.

---

## Next Recommended Task

**Task 26: Hospital Billing — Accounting Integration**

- Depend on `account` module
- Generate official Odoo `account.move` (customer invoice) from a confirmed Patient Bill
- Map billing services to product / income account
- Handle payment reconciliation via `account.payment`
- Add VAT / WHT configuration
- Connect bill state to invoice payment state automatically
Task 32B-2 delivered-only boundary
----------------------------------

Invoice eligibility is now based only on valid delivered quantity. Prepayment
does not make an undelivered service invoiceable, and cancelled, rejected or
emergency-bypassed charges are excluded. Standard Odoo invoice construction,
batch idempotency and quantity provenance are supplied by the accounting bridge;
receipt accounting and advance application remain later phases.
