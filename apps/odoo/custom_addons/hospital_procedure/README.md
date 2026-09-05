# Hospital Procedure

Clinical service execution foundation for the **Ethiopian Hospital ERP** (Odoo 18 Community).

## Overview

`hospital_procedure` adds the missing clinical service layer to the Hospital ERP:
procedures, bedside services, minor treatments, and nursing/doctor-performed
clinical actions. It provides a procedure/service catalog and a procedure
request/execution record with a full clinical workflow, clinical locking,
audit logging, and a printable Procedure Summary.

Typical clinical services covered:

- Wound dressing
- Injection service
- Nebulization
- Suture removal
- Catheterization
- IV cannulation
- Oxygen therapy
- ECG service
- Physiotherapy session
- Drain removal
- Minor procedure
- Blood transfusion assistance
- Other billable / non-billable clinical procedures

This is a **foundation module only**. See *Known Limitations* below.

## Dependencies

- `hospital_management`
- `hospital_admission`
- `hospital_nursing`
- `hospital_billing`
- `mail`

## Features

- Procedure / clinical service catalog (`hospital.procedure.type`).
- Procedure request / execution record (`hospital.procedure.request`) with a
  6-state clinical workflow.
- Automatic `PROC#####` sequence reference.
- Requirement enforcement: admission required and/or doctor approval required
  per procedure type.
- Clinical locking of completed/cancelled records (UI readonly + server-side
  write protection).
- Mail thread / activities (chatter) with tracked fields.
- Patient smart button + Procedures tab.
- Admission smart button.
- Procedure Summary PDF using the shared YOYA Hospital report header.
- Audit logging via `hospital.audit.log` (graceful if unavailable).

## Models

### `hospital.procedure.type` — Procedure / Service Catalog
- `name` (required), `code`, `category`, `default_price`, `currency_id`,
  `requires_doctor_approval`, `requires_admission`, `description`, `active`.
- Display name: `[CODE] Procedure Name` when a code exists, otherwise just the name.

### `hospital.procedure.request` — Procedure Request / Execution
- Context: `patient_id` (required), `admission_id`, `appointment_id`,
  `physician_id`, `requested_by`.
- Procedure: `procedure_type_id` (required), `category` (related),
  `request_datetime`, `scheduled_datetime`.
- Execution: `start_datetime`, `performed_datetime`, `performed_by`.
- Documentation: `clinical_indication`, `procedure_notes`, `outcome`, `complications`.
- Price context: `default_price` (related), `currency_id`, `notes`.
- Billing (Task 28B): `bill_id`, `bill_count`, `billing_state`, `billed_amount`,
  `amount_paid`, `amount_due`.
- Workflow: `state`, `active`.

### Patient / Admission integration
- `hospital.patient`: `procedure_request_ids`, `procedure_request_count`,
  `action_view_procedure_requests()`.
- `hospital.admission`: `procedure_request_ids`, `procedure_request_count`,
  `action_view_admission_procedures()`.

## Workflow

| Action | Transition | Side effects |
| --- | --- | --- |
| Submit Request | draft → requested | — |
| Schedule | requested → scheduled | — |
| Start Procedure | requested/scheduled → in_progress | sets `start_datetime` if empty |
| Mark Done | requested/scheduled/in_progress → done | sets `performed_datetime` and `performed_by` if empty |
| Cancel | draft/requested/scheduled/in_progress → cancelled | — |
| Reset to Draft | cancelled → draft | — |

Validation:
- `patient_id` and `procedure_type_id` are required.
- If the procedure type **requires admission**, `admission_id` must be set.
- If the procedure type **requires doctor approval**, `physician_id` must be set before Mark Done.
- `start_datetime` / `performed_datetime` cannot precede `request_datetime`.

## Clinical Locking Rules

Once a record is **done** or **cancelled**, clinical fields are locked:

- UI: fields are rendered readonly in those states.
- Server: `write()` checks `PROTECTED_PROCEDURE_FIELDS`; changing any protected
  field while `state in ("done", "cancelled")` raises:
  *"Completed or cancelled procedure records are locked. Reset to Draft before
  making corrections."*

Workflow/system fields (`state`, chatter, write metadata) are intentionally
excluded so the workflow keeps functioning. To correct a locked record, use
**Reset to Draft** (cancelled records only) first.

## Task 28B — Procedure Billing Automation

A **completed** procedure can be turned into a normal `hospital.patient.bill` in
the existing `hospital_billing` engine. This is **not** a second billing system:
no Odoo invoices, no accounting entries, and no Walnut Accounting integration are
created. The procedure simply generates one patient bill with one procedure line,
following the same safe pattern used by Admission Billing.

### Billing rules

1. Only a procedure in state `done` can be billed. Otherwise:
   *"Procedure bill can only be generated after the procedure is completed."*
2. If a bill already exists (`bill_id` set):
   *"This procedure has already been billed."*
3. `procedure_type_id.default_price` must be greater than 0. Otherwise:
   *"Procedure price is zero. Set a default price before billing."*
4. `patient_id` must exist.
5. One bill per procedure (no batch billing, no auto-billing on Mark Done).

### Generate Procedure Bill button

Header button **Generate Procedure Bill** (`action_generate_procedure_bill`):

- Visible only when `state == 'done'` and `bill_id` is empty.
- Restricted to Receptionist, Accountant, Manager, and System Administrator.
- Creates a `hospital.patient.bill` with:
  - `patient_id`, `physician_id`, `appointment_id` from the procedure,
  - `bill_date` = today, `cashier_id` = current user,
  - `currency_id` = procedure currency or company currency,
  - `notes` = `Generated from procedure PROC#####`.
- Adds one bill line: description `Procedure Type - PROC#####`, qty 1,
  `unit_price` = procedure default price, `source_type` = `procedure`,
  `source_model`/`source_record_id` back-references. The subtotal/total are
  computed by the existing billing logic.
- Links the new bill back to the procedure (`bill_id`) and returns an action
  opening the bill form.

### Patient Bill smart button

Smart button **Patient Bill** (`action_view_procedure_bill`) appears once
`bill_id` exists and opens the linked bill.

### Billing status fields / Billing section

The form's **Billing** notebook page shows:

- `billing_state` — `not_billed` / `billed` / `partially_paid` / `paid`,
  computed read-only from the linked bill.
- `bill_id`, `default_price`, `currency_id`.
- `billed_amount`, `amount_paid`, `amount_due` — computed read-only from the
  linked bill (procedure never writes payment fields directly).

`billing_state` derivation: no bill → `not_billed`; bill paid or amount due ≤ 0
with a positive total → `paid`; partial payment present → `partially_paid`;
otherwise → `billed`.

### Source type handling

`hospital.patient.bill.line.source_type` already includes a `procedure`
selection value, so the existing value is reused — no `selection_add` extension
was needed.

### Clinical locking interaction

`bill_id` and the computed billing fields are **not** in
`PROTECTED_PROCEDURE_FIELDS`, so billing linkage is allowed after the procedure
is `done` without weakening clinical locking on protected clinical fields.

## Menu Structure

```
Procedures (app)
├── Procedure Requests
├── Procedure Types
└── Configuration
    └── Procedure Types
```

Patient and Admission forms expose a **Procedures** smart button; the Patient
form also gains a **Procedures** notebook tab.

## Security Role Summary

Uses existing `hospital_management` groups (no new groups defined).

| Role | Procedure Request | Procedure Type |
| --- | --- | --- |
| Nurse | read/write/create | read |
| Doctor | read/write/create | read |
| Receptionist | read/write/create | read |
| Pharmacist | read | read |
| Lab Technician | read | read |
| Accountant | read | read |
| Manager | read/write/create | read/write/create |
| Data Protection Officer | read | read |
| System Administrator | full (incl. delete) | full (incl. delete) |

No normal user has delete access on clinical procedure records. `unlink()` is
overridden to block deletion for non–system-administrators and to log the
attempt.

## Report

**Procedure Summary** (`qweb-pdf`) bound to `hospital.procedure.request`,
rendered with the shared `hospital_management.hospital_report_header`. Includes
patient, admission, appointment, physician, procedure type, category, requested
by, performed by, all timestamps, clinical documentation, the price reference,
and a **Billing Summary** (billing status, bill reference, procedure price,
amount paid, amount due).

## Install Notes

1. Ensure `hospital_management`, `hospital_admission`, and `hospital_nursing`
   are installed/upgraded.
2. Install `hospital_procedure`.
3. Hard refresh the browser; the **Procedures** app appears in the app launcher.

## Manual Test Checklist

1. Install/upgrade `hospital_management`, `hospital_admission`,
   `hospital_nursing`, `hospital_procedure`.
2. Open the **Procedures** app.
3. Create a Procedure Type (e.g. Wound Dressing, code `WD-001`, category Wound
   Care, price 300, Requires Admission = Yes, Requires Doctor Approval = No).
4. Create a Procedure Request for a patient + admission and procedure type.
5. Submit Request → Schedule → Start Procedure → Mark Done.
6. Confirm `performed_datetime` and `performed_by` are filled.
7. Try editing procedure notes/outcome after Done → expect readonly / UserError.
8. Print the Procedure Summary PDF.
9. Confirm the Patient **Procedures** smart button count.
10. Confirm the Admission **Procedures** smart button count.
11. Confirm `requires_admission` and `requires_doctor_approval` validations.

### Billing (Task 28B)

12. Confirm `hospital_billing` is installed and upgrade `hospital_procedure`.
13. Open a **Done** procedure whose type has Default Price 300; confirm the
    Billing tab shows **Not Billed** and Price 300.
14. A non-completed procedure must **not** show Generate Procedure Bill.
15. A done procedure with price 0 → Generate Procedure Bill raises the
    zero-price UserError.
16. Click **Generate Procedure Bill** → a new Patient Bill opens with one line
    `Wound Dressing - PROC00001`, qty 1, unit price 300, total 300.
17. Return to the procedure → the **Patient Bill** smart button appears and
    opens the bill; `bill_id` is linked.
18. Click Generate Procedure Bill again → blocked with duplicate-billing
    UserError.
19. Register a partial payment (e.g. 100) → procedure `billing_state` becomes
    `partially_paid`.
20. Pay the remaining 200 → procedure `billing_state` becomes `paid`.
21. Print the Procedure Summary PDF → Billing Summary section renders.

## Known Limitations

- One bill per procedure (re-billing blocked once `bill_id` is set).
- No batch procedure billing yet.
- No automatic billing on Mark Done (manual button only).
- No procedure materials / stock costing.
- No stock / material deduction yet.
- No operation theatre integration yet.
- No advanced consent linkage yet.
- No insurance / claim integration.
- No Walnut Accounting integration.
- No Odoo invoice or accounting entry generation.
- No procedure scheduling calendar view yet.

## Next Recommended Task

Procedure materials/stock costing and batch procedure billing, followed by a
procedure scheduling calendar view (still no accounting/insurance integration).
