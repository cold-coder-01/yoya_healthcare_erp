# Hospital Admission Module

**Technical Name:** `hospital_admission`
**Version:** 18.0.1.0.0
**Category:** Healthcare
**License:** LGPL-3

---

## Overview

The `hospital_admission` module provides the inpatient admission foundation for the Ethiopian Hospital ERP built on Odoo 18 Community. It handles ward, room, and bed catalog management, patient admission lifecycle (draft → admitted → transferred → discharged), bed occupancy enforcement, patient transfers, and discharge. It extends the existing `hospital.patient` model with admission smart buttons and a notebook tab.

---

## Dependencies

| Module | Purpose |
|---|---|
| `hospital_management` | Patient, doctor, appointment, diagnosis, department, security groups |
| `hospital_billing` | Bill linkage on admission record |
| `mail` | `mail.thread` / `mail.activity.mixin` for chatter and state tracking |

Does **not** depend on: `stock`, `account`, `hr`, `website`.

---

## Features

- Ward, Room, and Bed catalog with full CRUD
- Bed state management: Available, Occupied, Cleaning, Maintenance, Blocked
- Hospital Admission record with sequence `ADM00001`
- Admission workflow: Draft → Admitted → Transferred → Discharged / Cancelled
- Bed occupancy enforcement on admission confirmation
- Transfer wizard (`hospital.admission.transfer.wizard`) for safe bed-to-bed moves
- Transfer history log (`hospital.admission.transfer`) per admission
- Patient smart button showing admission count
- Patient notebook tab listing all admissions
- Admission Summary PDF report using shared YOYA Hospital report header
- Full audit logging using `hospital.audit.log`
- Role-based access control using existing `hospital_management` security groups

---

## Models

### `hospital.ward`
Hospital ward catalog.

| Field | Type | Description |
|---|---|---|
| `name` | Char | Ward name (required) |
| `code` | Char | Short code, e.g. MED-WARD |
| `ward_type` | Selection | general, private, emergency, maternity, pediatric, surgical, medical, icu, isolation, other |
| `floor` | Char | Floor location |
| `department_id` | Many2one | Linked hospital department |
| `description` | Text | Notes |
| `active` | Boolean | Archive support |

Display name: `[CODE] Ward Name` if code set, otherwise `Ward Name`.

---

### `hospital.room`
Room catalog under a ward.

| Field | Type | Description |
|---|---|---|
| `name` | Char | Room name (required) |
| `code` | Char | Short code |
| `ward_id` | Many2one | Parent ward (required) |
| `room_type` | Selection | shared, private, isolation, icu, emergency, other |
| `floor` | Char | Floor location |
| `description` | Text | Notes |
| `active` | Boolean | Archive support |

Display name: `Ward / Room`.

---

### `hospital.bed`
Bed catalog under a room.

| Field | Type | Description |
|---|---|---|
| `name` | Char | Bed name (required) |
| `code` | Char | Short code, e.g. BED-101A |
| `room_id` | Many2one | Parent room (required) |
| `ward_id` | Many2one | Related from room (stored) |
| `bed_type` | Selection | standard, pediatric, icu, maternity, emergency, isolation, other |
| `state` | Selection | available, occupied, cleaning, maintenance, blocked |
| `current_admission_id` | Many2one | Active admission occupying this bed |
| `active` | Boolean | Archive support |

Default state: `available`.
Display name: `CODE - Room Name`.

---

### `hospital.admission`
Main inpatient admission record.

| Field | Type | Description |
|---|---|---|
| `name` | Char | Sequence reference, e.g. ADM00001 |
| `patient_id` | Many2one | Patient (required) |
| `physician_id` | Many2one | Admitting physician |
| `appointment_id` | Many2one | Linked outpatient appointment |
| `diagnosis_id` | Many2one | Linked diagnosis |
| `admission_date` | Datetime | Date/time of admission |
| `expected_discharge_date` | Datetime | Planned discharge date |
| `discharge_date` | Datetime | Actual discharge date |
| `ward_id` | Many2one | Assigned ward |
| `room_id` | Many2one | Assigned room |
| `bed_id` | Many2one | Assigned bed |
| `admission_reason` | Text | Clinical reason for admission |
| `discharge_summary` | Text | Clinical discharge notes |
| `state` | Selection | draft, admitted, transferred, discharged, cancelled |
| `bill_id` | Many2one | Linked bill (manual, future automation) |
| `transfer_ids` | One2many | Transfer history records |
| `notes` | Text | General notes |
| `active` | Boolean | Archive support |

---

### `hospital.admission.transfer`
Transfer record created each time a patient is moved to a new bed.

| Field | Type | Description |
|---|---|---|
| `admission_id` | Many2one | Parent admission (cascade delete) |
| `transfer_date` | Datetime | Date/time of transfer |
| `from_ward_id` | Many2one | Previous ward |
| `from_room_id` | Many2one | Previous room |
| `from_bed_id` | Many2one | Previous bed |
| `to_ward_id` | Many2one | New ward (required) |
| `to_room_id` | Many2one | New room (required) |
| `to_bed_id` | Many2one | New bed (required) |
| `reason` | Text | Transfer reason |
| `transferred_by` | Many2one | User who performed transfer |

---

### `hospital.admission.transfer.wizard` (TransientModel)
Wizard for safe bed-to-bed patient transfers invoked from the admission form.

---

## Admission Workflow

```
Draft
  │
  ▼ [Confirm Admission]
Admitted ──────────────────────────────────────────► Cancelled
  │                  [Cancel]                              │
  ▼ [Transfer Patient]                                     │
Transferred ────────────────────────────────────────► Cancelled
  │                  [Cancel]                              │
  ▼ [Discharge]                                            │
Discharged                                   [Reset to Draft]
                                                          │
                                                         Draft
```

Button visibility rules:
- **Confirm Admission**: visible in `draft`
- **Transfer Patient**: visible in `admitted`, `transferred`
- **Discharge**: visible in `admitted`, `transferred`
- **Cancel**: visible in `draft`, `admitted`, `transferred`
- **Reset to Draft**: visible in `cancelled` (Manager / System Administrator only)

---

## Bed Occupancy Rules

| Action | Effect |
|---|---|
| Confirm Admission (bed selected) | Bed state → `occupied`, `current_admission_id` set |
| Confirm Admission (bed `occupied`) | **Blocked** — `UserError` raised |
| Confirm Admission (bed `maintenance`, `blocked`, `cleaning`) | **Blocked** — `UserError` raised |
| Discharge | Bed state → `available`, `current_admission_id` cleared |
| Cancel (from admitted/transferred) | Bed state → `available`, `current_admission_id` cleared |
| Transfer (wizard) | Old bed → `available`; New bed → `occupied` |
| Transfer to non-available bed | **Blocked** — `UserError` raised |
| Delete occupied bed | **Blocked** — `AccessError` raised + audit log |

---

## Transfer Rules

1. Transfer is only allowed when admission state is `admitted` or `transferred`.
2. The new bed must be in `available` state; any other state raises `UserError`.
3. The old bed is freed (`available`, `current_admission_id` cleared).
4. The new bed is occupied (`occupied`, `current_admission_id` set).
5. A `hospital.admission.transfer` record is created with full from/to detail.
6. The admission `ward_id`, `room_id`, `bed_id` are updated to the new location.
7. Admission state becomes `transferred`.
8. All bed state changes are audit-logged.

---

## Menu Structure

```
Admissions (App launcher)
├── Admissions
├── Transfers
├── Wards
├── Rooms
├── Beds
└── Configuration
    ├── Wards
    ├── Rooms
    └── Beds
```

---

## Security Role Summary

| Role | Ward/Room/Bed | Admission | Transfer Record |
|---|---|---|---|
| Receptionist | Read | Read/Write/Create | Read |
| Doctor | Read | Read/Write | Read |
| Nurse | Read | Read/Write/Create | Read/Write/Create |
| Pharmacist | Read | Read | Read |
| Lab Technician | Read | Read | Read |
| Accountant | Read | Read | Read |
| Manager | Read/Write/Create | Read/Write/Create | Read/Write/Create |
| Data Protection Officer | Read | Read | Read |
| System Administrator | Full | Full | Full |

- No normal user can **delete** admission records in state other than Draft or Cancelled.
- Deletion of occupied beds is blocked.
- All security groups are sourced from `hospital_management`.

---

## Report

**Admission Summary PDF**

- Triggered from the admission form via Print menu.
- Uses shared `hospital_management.hospital_report_header` (YOYA Hospital branding).
- Sections: Patient & Clinical Information, Admission Details, Bed Assignment, Admission Reason, Discharge Summary, Transfer History.

---

## Installation

1. Ensure `hospital_management` and `hospital_billing` are installed and up to date.
2. Place `hospital_admission` in your Odoo addons path.
3. Restart Odoo server.
4. Go to **Apps → Update App List**.
5. Search for **Hospital Admission** and click **Install**.

Upgrade command:
```
python odoo-bin -u hospital_admission -d <your_database>
```

---

## Manual Test Checklist

- [ ] Module installs without error
- [ ] **Admissions** app launcher appears
- [ ] Create Ward: Name=`Medical Ward`, Code=`MED-WARD`, Type=`Medical`
- [ ] Create Room: Name=`Room 101`, Code=`R101`, Ward=`Medical Ward`, Type=`Shared`
- [ ] Create Bed: Name=`Bed 101-A`, Code=`BED-101A`, Room=`Room 101`, Type=`Standard`, State=`Available`
- [ ] Create Admission: Patient=`HMS0001`, Physician=`Dr. Hana Bekele`, Ward=`Medical Ward`, Room=`Room 101`, Bed=`Bed 101-A`
- [ ] Click **Confirm Admission** → state becomes `Admitted`, sequence `ADM00001` assigned
- [ ] Verify `Bed 101-A` state is now `Occupied`
- [ ] Attempt second admission with same bed → `UserError` raised
- [ ] Create `Bed 101-B` in Room 101 (state=Available)
- [ ] Click **Transfer Patient** → select `Bed 101-B` → confirm
- [ ] Verify `Bed 101-A` → `Available`, `Bed 101-B` → `Occupied`
- [ ] Transfer tab on admission shows transfer record
- [ ] Click **Discharge** → state becomes `Discharged`
- [ ] Verify `Bed 101-B` → `Available`
- [ ] Print **Admission Summary** PDF → renders without RPC error
- [ ] Open patient `HMS0001` → **Admissions** smart button shows count `1`
- [ ] Patient **Admissions** tab shows the admission record

---

## Task 26B — Admission Billing Automation

Discharge-based billing bridge from `hospital_admission` to `hospital_billing`.

### Pricing Configuration

| Model | Field | Purpose |
|---|---|---|
| `hospital.ward` | `admission_fee` | One-time fee charged on admission |
| `hospital.ward` | `daily_ward_rate` | Default daily bed rate |
| `hospital.room` | `daily_room_rate` | Overrides ward rate if > 0 |
| `hospital.bed` | `daily_bed_rate` | Overrides room and ward rate if > 0 |

**Rate priority:** Bed rate → Room rate → Ward rate.

### Billing Fields on Admission

| Field | Description |
|---|---|
| `stay_days` | Computed: ceiling of hours / 24, minimum 1 |
| `admission_fee_amount` | Ward admission fee |
| `daily_rate_amount` | Effective daily rate (bed/room/ward priority) |
| `bed_charge_amount` | `daily_rate_amount × stay_days` |
| `total_admission_charge` | `admission_fee_amount + bed_charge_amount` |
| `billing_state` | not_billed / billed / partially_paid / paid |
| `bill_id` | Linked `hospital.patient.bill` |
| `bill_count` | 0 or 1 |
| `currency_id` | Company currency |

### Bill Generation Rules

1. Only available when `state == discharged`.
2. Blocked if `bill_id` already exists (no duplicates).
3. `discharge_date` must be set.
4. Generates a `hospital.patient.bill` with two lines:
   - **Admission Fee** (if fee > 0)
   - **Bed Stay Charge** (if daily rate > 0, quantity = stay_days)
5. Both lines have `source_type = admission`.
6. Bill is auto-linked to admission via `bill_id`.
7. Action opens the new bill form.

### Stay Days Calculation

```
stay_days = ceil((discharge_date - admission_date).total_seconds() / 3600 / 24)
minimum = 1 day
```

For this version, the **final bed/room/ward rate** is used for the entire stay (no per-transfer segment split).

### UI Changes

- **Generate Admission Bill** button: visible when `discharged` and `bill_id` is empty.
- **Bill** smart button: visible when `bill_id` is set, opens the linked bill.
- **Billing tab** in admission notebook: shows all preview fields.
- Pricing section added to Ward, Room, and Bed forms.

---

## Chatter / Mail Compatibility

`hospital.admission` inherits `mail.thread` and `mail.activity.mixin`. This enables:
- Chatter block on the admission form for clinical audit trail.
- `tracking=True` on key fields: `state`, `patient_id`, `physician_id`, `ward_id`, `room_id`, `bed_id`, `admission_date`, `expected_discharge_date`, `discharge_date`.
- Activities (reminders, to-dos) linked to admissions.

The `mail` module is listed as an explicit dependency in the manifest. `hospital.ward`, `hospital.room`, `hospital.bed`, and `hospital.admission.transfer` do **not** inherit mail mixins as their views contain no chatter block.

---

## Known Limitations

- Discharge-based billing is implemented (Task 26B). No running daily billing cron yet.
- No nursing notes module yet.
- No medication administration record (MAR) yet.
- No surgery / operation theatre integration yet.
- No insurance workflow yet.
- No bed cleaning workflow automation yet (no transition from cleaning → available).
- No accounting integration yet.

---

## Next Recommended Task

**Task 27 — Nursing Notes & Inpatient Vitals**
Add nursing observation notes and inpatient vital sign records linked to `hospital.admission`, with nurse-only write access and a dedicated tab on the admission form.
