# Hospital Pharmacy

**Module:** `hospital_pharmacy`
**Version:** 18.0.1.0.0
**License:** LGPL-3
**Depends:** `hospital_management`

---

## Overview

The Hospital Pharmacy module provides a medicine catalog and prescription dispensing workflow for the Ethiopian Hospital ERP system. It integrates with the existing `hospital_management` module to link dispensing records to patients, physicians, appointments, and prescriptions.

This module is a **foundation release**. It does not yet include stock/inventory deduction, batch/expiry tracking, billing, purchase orders, or insurance claims. These capabilities are planned for later phases.

---

## Dependencies

- `hospital_management` (Ethiopian Hospital ERP core)

---

## Features

- **Medicine Catalog** — Maintain a structured list of medicines with dosage form, route, strength, generic name, and brand name.
- **Pharmacy Dispense Records** — Create dispense records linked to a patient, prescription, physician, and appointment.
- **Dispense Lines** — Track individual medicines being dispensed with prescribed quantity, dispensed quantity, dosage, frequency, duration, route, and instructions.
- **Workflow** — Draft → Ready → Partially Dispensed → Dispensed (with cancel and reset options).
- **Prescription Integration** — Smart button on prescription form to create or view related pharmacy dispenses.
- **Patient Integration** — Smart button and notebook tab on patient form showing all pharmacy dispenses.
- **Audit Logging** — All create, update, archive, state change, and blocked delete attempts are logged to `hospital.audit.log`.
- **PDF Report** — Pharmacy Dispense Slip with YOYA Hospital header, medicine lines, and signature fields.
- **Role-Based Access** — Permissions follow existing hospital_management security groups.

---

## Models

### `hospital.pharmacy.medicine`
Medicine catalog entry.

| Field | Type | Description |
|-------|------|-------------|
| name | Char | Medicine name (required) |
| code | Char | Short code (e.g. MED-PARA) |
| category | Char | Free-text category |
| dosage_form | Selection | Tablet, Capsule, Syrup, Injection, Cream, Ointment, Drops, Inhaler, Solution, Other |
| strength | Char | Strength (e.g. 500mg) |
| generic_name | Char | Generic/INN name |
| brand_name | Char | Brand/trade name |
| route | Selection | Oral, IV, IM, Topical, Ophthalmic, Otic, Inhalation, Subcutaneous, Other |
| description | Text | Extended description |
| active | Boolean | Archive flag |

**Display name format:** `[CODE] Medicine Name Strength` (e.g. `[MED-PARA] Paracetamol 500mg`)

---

### `hospital.pharmacy.dispense`
A dispensing record created from (or linked to) a prescription.

| Field | Type | Description |
|-------|------|-------------|
| name | Char | Sequence reference (DISP00001) — auto-assigned |
| patient_id | Many2one | Patient (required) |
| prescription_id | Many2one | Source prescription |
| physician_id | Many2one | Prescribing physician |
| pharmacist_id | Many2one | Dispensing pharmacist (defaults to current user) |
| appointment_id | Many2one | Related appointment |
| dispense_date | Datetime | Date/time of dispense |
| priority | Selection | Routine, Urgent, Emergency |
| state | Selection | Draft, Ready, Partially Dispensed, Dispensed, Cancelled |
| line_ids | One2many | Medicine lines |
| notes | Text | Additional notes |
| active | Boolean | Archive flag |

**Sequence:** `DISP00001` (prefix `DISP`, 5-digit padding)

---

### `hospital.pharmacy.dispense.line`
Individual medicine line within a dispense record.

| Field | Type | Description |
|-------|------|-------------|
| medicine_id | Many2one | Medicine (required) |
| prescribed_quantity | Float | Quantity prescribed |
| dispensed_quantity | Float | Quantity actually dispensed |
| dosage | Char | Dosage (e.g. 500mg) |
| frequency | Char | Frequency (e.g. TID) |
| duration | Char | Duration (e.g. 7 days) |
| route | Char | Route of administration |
| instruction | Text | Special instructions |
| sequence | Integer | Ordering |

---

## Workflow

```
Draft
  │
  ▼ [Mark Ready]
Ready
  │
  ├─▶ [Mark Partially Dispensed] → Partially Dispensed
  │                                        │
  └─▶ [Mark Dispensed] ◀───────────────────┘
         │
         ▼
      Dispensed

Any of Draft / Ready / Partially Dispensed:
  ▼ [Cancel]
Cancelled
  ▼ [Reset to Draft]
Draft
```

- Normal users cannot delete dispense records — they must cancel or archive.
- Only System Administrators can hard-delete records.

---

## Menu Structure

```
Pharmacy (app launcher, sequence 37)
├── Pharmacy Dispenses
├── Medicines
└── Configuration
    └── Medicines
```

The `Configuration` menu is visible only to Pharmacists, Managers, and System Administrators.

---

## Security Role Summary

| Role | Medicine | Dispense | Dispense Line |
|------|----------|----------|---------------|
| Receptionist | Read | Read | Read |
| Doctor | Read | Create/Read/Write | Create/Read/Write |
| Nurse | Read | Read | Read |
| Pharmacist | Create/Read/Write | Create/Read/Write | Create/Read/Write |
| Lab Technician | None | None | None |
| Accountant | None | Read | Read |
| Manager | Create/Read/Write | Create/Read/Write | Create/Read/Write |
| Data Protection Officer | Read | Read | Read |
| System Administrator | Full | Full | Full |

No role (except System Administrator) can delete dispense records or lines.

---

## Prescription Integration

From the prescription form view (`hospital.prescription`), a smart button labeled **Dispensing** appears. Clicking it:
- If no dispense exists for this prescription: creates a draft dispense (pre-filling patient, physician, and appointment) and opens it.
- If one dispense exists: opens it directly.
- If multiple dispenses exist: opens the list filtered to this prescription.

**Note:** Prescription lines use a plain `medicine_name` Char field and do not have a Many2one to the medicine catalog. Therefore, medicine lines on the dispense are **not auto-populated** from prescription lines — the pharmacist must add medicine lines manually and select medicines from the catalog.

---

## Patient Integration

On the patient form (`hospital.patient`):
- A **Pharmacy Dispenses** smart button shows the count of dispenses and opens the latest one.
- A **Pharmacy Dispenses** notebook tab lists all dispense records for the patient in read-only mode.

---

## Reports

### Pharmacy Dispense Slip (`hospital_pharmacy.report_pharmacy_dispense`)
PDF report accessible from the Pharmacy Dispense form via the Print menu.

Includes:
- YOYA Hospital report header (shared from `hospital_management`)
- Dispense reference, date, patient, physician, pharmacist, priority, status
- Medicine lines table (medicine, dosage, frequency, duration, route, prescribed/dispensed qty, instructions)
- Notes section (if any)
- Signature fields for pharmacist and patient/guardian

---

## Installation

1. Ensure `hospital_management` is installed.
2. Place `hospital_pharmacy` in the `custom_addons` directory.
3. Restart Odoo server.
4. Go to Apps → Update Apps List.
5. Search for "Hospital Pharmacy" and click Install.

---

## Manual Test Checklist

- [ ] Module installs without error
- [ ] Pharmacy app appears in the home menu
- [ ] Medicine catalog can be opened and a new medicine created
- [ ] Display name shows as `[CODE] Name Strength`
- [ ] Pharmacy Dispenses list opens
- [ ] New dispense can be created manually with patient and medicine lines
- [ ] Sequence `DISP00001` is assigned on save
- [ ] `Mark Ready` button moves state from Draft → Ready
- [ ] `Mark Partially Dispensed` moves Ready → Partially Dispensed
- [ ] `Mark Dispensed` moves Ready or Partially Dispensed → Dispensed
- [ ] `Cancel` works from Draft, Ready, or Partially Dispensed
- [ ] `Reset to Draft` works from Cancelled
- [ ] Deleting a dispense as a non-admin shows a UserError
- [ ] Open patient HMS0001 — Pharmacy Dispenses smart button shows correct count
- [ ] Smart button opens the latest dispense (form view)
- [ ] Pharmacy Dispenses notebook tab is visible on patient form
- [ ] Open a prescription — Dispensing smart button is visible
- [ ] Clicking Dispensing (count=0) creates a draft dispense and opens it
- [ ] Prescription dispense is pre-filled with patient, physician, appointment
- [ ] PDF report prints without RPC error
- [ ] Report shows correct patient, medicines, and signature fields
- [ ] Audit log entries are created for create, update, state change, and delete attempt

---

## Known Limitations

1. **No stock deduction** — Dispensing does not decrement inventory. Stock integration is a planned future phase.
2. **No batch/expiry tracking** — No lot/serial number or expiry date fields. Planned for a future phase.
3. **No supplier/purchase workflow** — No purchase orders or supplier price lists. Planned for a future phase.
4. **No billing/accounting integration** — No invoice generation or payment linkage. Planned for a future phase.
5. **No insurance claims** — Planned for a future phase.
6. **Prescription lines not auto-mapped** — Because `hospital.prescription.line` uses a plain `medicine_name` Char field (not a Many2one to the medicine catalog), medicine lines cannot be automatically copied from prescription lines. The pharmacist must add lines manually.

---

## UI Design

The pharmacy dispense form follows **UI Pattern V1** — the same emerald-green design system used across the hospital ERP (patient, appointment, consent, evaluation, radiology modules).

Scoped SCSS file: `hospital_pharmacy/static/src/scss/pharmacy_theme.scss`  
Wrapper class: `.hospital_pharmacy_dispense_profile` (applied to both `<form>` and `<sheet>`)

Layout structure:

1. **Hero header** — emerald icon card (`fa-medkit`) + "DISPENSE" label + sequence reference (DISP00001)
2. **KPI summary row** — five cards: Patient, Prescription, Physician, Pharmacist, Status (readonly display)
3. **Two-column info grid**
   - Left card — *Dispense Information*: Appointment, Prescription, Dispense Date, Priority, Active
   - Right card — *Dispense Summary*: Patient, Physician, Pharmacist, State (badge), Created By
4. **Notebook tabs** — Medicines (editable one2many table) + Notes (freetext with "PHARMACY NOTES" section title)

Statusbar active state and workflow buttons are styled in primary emerald (`#006A4F`).  
All styles are fully scoped and do not affect other Odoo forms.

---

## Next Recommended Task

**Task 25 — Pharmacy Stock Integration**
- Link `hospital.pharmacy.medicine` to `product.product` or `stock.quant`
- Deduct stock on dispense confirmation
- Add lot/serial number and expiry date tracking to dispense lines
- Depends on `stock` module

Or:

**Task 25 — Pharmacy Billing Integration**
- Generate draft invoices from dispensed records
- Link to `account.move`
- Depends on `account` module
