# Hospital Nursing Module

**Technical Name:** `hospital_nursing`
**Version:** 18.0.1.0.0
**Category:** Healthcare
**License:** LGPL-3

---

## Overview

The Hospital Nursing module provides the inpatient nursing foundation for the Ethiopian Hospital ERP (YOYA Hospital). It covers all core nursing workflows for admitted patients, including nursing rounds with vital sign monitoring, nursing notes, care plans, and medication administration records (MAR).

This module is a **foundation task** — it establishes the clinical data layer for nursing. Advanced features (barcode scanning, pharmacy stock deduction, nurse scheduling, shift handover, billing charges) are planned for future phases.

---

## Dependencies

| Module | Purpose |
|--------|---------|
| `hospital_management` | Core patient, doctor, audit log, security groups |
| `hospital_admission` | Admission records, ward/room/bed |
| `hospital_pharmacy` | Prescription linkage for MAR |
| `mail` | Chatter and activity tracking |

---

## Features

- **Nursing Rounds** — Document vital signs (temperature, pulse, BP, SpO2, blood sugar, pain), consciousness level, intake/output, observations, and actions taken per patient round
- **Nursing Notes** — Free-text notes by type (general, observation, intervention, incident, patient complaint, family communication, discharge preparation)
- **Care Plans** — Structured nursing care plans with diagnosis, goals, interventions, and evaluation; linked to admission and optionally to a physician
- **Medication Administration Records (MAR)** — Track scheduled, administered, missed, refused, and held medications per admission
- **Patient Smart Buttons** — Nursing Rounds, Notes, Care Plans, and MAR counts accessible from patient form
- **Admission Smart Buttons** — Same counters accessible from admission form
- **Nursing Tab on Patient** — Embedded lists of recent nursing records on the patient form
- **Nursing Summary PDF** — Printable report for an admission including all nursing records
- **Full Audit Logging** — All create/update/state change/delete-attempt actions logged via `hospital.audit.log`
- **Safe Unlink Protection** — Non-admin users cannot delete clinical nursing records

---

## Models

### `hospital.nursing.round`
Routine nurse round / vital sign check for an admitted patient.

| Field | Type | Description |
|-------|------|-------------|
| `name` | Char | Auto-sequence, NURR00001 |
| `patient_id` | Many2one | hospital.patient (required) |
| `admission_id` | Many2one | hospital.admission (required) |
| `nurse_id` | Many2one | res.users (default: current user) |
| `round_datetime` | Datetime | Default: now |
| `consciousness_level` | Selection | Alert / Voice Response / Pain Response / Unresponsive |
| `temperature` | Float | °C |
| `pulse_rate` | Float | bpm |
| `respiratory_rate` | Float | breaths/min |
| `systolic_bp` | Float | mmHg |
| `diastolic_bp` | Float | mmHg |
| `oxygen_saturation` | Float | % (0-100 validated) |
| `blood_sugar` | Float | mg/dL |
| `pain_level` | Selection | 0-10 |
| `intake_notes` | Text | Fluid intake notes |
| `output_notes` | Text | Fluid output notes |
| `nursing_observation` | Text | Observations |
| `action_taken` | Text | Actions taken |
| `state` | Selection | draft → recorded → reviewed \| cancelled |
| `reviewed_by` | Many2one | res.users |
| `reviewed_date` | Datetime | Auto-set on review |

### `hospital.nursing.note`
Free-text nursing note for a patient/admission.

| Field | Type | Description |
|-------|------|-------------|
| `name` | Char | Auto-sequence, NURN00001 |
| `patient_id` | Many2one | hospital.patient (required) |
| `admission_id` | Many2one | hospital.admission (required) |
| `nurse_id` | Many2one | res.users |
| `note_datetime` | Datetime | Default: now |
| `note_type` | Selection | general / observation / intervention / incident / patient_complaint / family_communication / discharge_preparation |
| `note` | Text | Note body (required) |
| `action_required` | Boolean | Flag for required follow-up |
| `action_required_note` | Text | Details of required action |
| `state` | Selection | draft → submitted → reviewed \| cancelled |

### `hospital.nursing.care.plan`
Nursing care plan for an admitted patient.

| Field | Type | Description |
|-------|------|-------------|
| `name` | Char | Auto-sequence, CARE00001 |
| `patient_id` | Many2one | hospital.patient (required) |
| `admission_id` | Many2one | hospital.admission (required) |
| `nurse_id` | Many2one | res.users |
| `physician_id` | Many2one | hospital.doctor |
| `plan_date` | Date | Default: today |
| `nursing_diagnosis` | Text | Nursing diagnosis |
| `goals` | Text | Patient care goals |
| `interventions` | Text | Nursing interventions |
| `evaluation` | Text | Evaluation findings |
| `priority` | Selection | low / normal / high / urgent |
| `state` | Selection | draft → active → completed \| cancelled |
| `completed_date` | Datetime | Auto-set on completion |

### `hospital.medication.administration`
Medication administration record (MAR) for inpatient nursing.

| Field | Type | Description |
|-------|------|-------------|
| `name` | Char | Auto-sequence, MAR00001 |
| `patient_id` | Many2one | hospital.patient (required) |
| `admission_id` | Many2one | hospital.admission (required) |
| `prescription_id` | Many2one | hospital.prescription (optional link) |
| `medication_name` | Char | Medication name (required) |
| `dose` | Char | Dose/strength |
| `route` | Selection | oral / iv / im / sc / topical / inhalation / other |
| `scheduled_datetime` | Datetime | Scheduled time |
| `administered_datetime` | Datetime | Actual administration time |
| `nurse_id` | Many2one | res.users |
| `administration_status` | Selection | scheduled → administered \| missed \| refused \| held \| cancelled |
| `reason_not_administered` | Text | Required when missed/refused/held |
| `notes` | Text | Additional notes |

---

## Sequences

| Model | Prefix | Padding | Example |
|-------|--------|---------|---------|
| hospital.nursing.round | NURR | 5 | NURR00001 |
| hospital.nursing.note | NURN | 5 | NURN00001 |
| hospital.nursing.care.plan | CARE | 5 | CARE00001 |
| hospital.medication.administration | MAR | 5 | MAR00001 |

---

## Workflows

### Nursing Round
```
draft → [Record Round] → recorded → [Review] → reviewed
draft/recorded → [Cancel] → cancelled → [Reset to Draft] → draft
```

### Nursing Note
```
draft → [Submit] → submitted → [Review] → reviewed
draft/submitted → [Cancel] → cancelled → [Reset to Draft] → draft
```

### Care Plan
```
draft → [Activate] → active → [Complete] → completed
draft/active → [Cancel] → cancelled → [Reset to Draft] → draft
```

### Medication Administration (MAR)
```
scheduled → [Mark Administered] → administered
scheduled → [Mark Missed] → missed (requires reason)
scheduled → [Mark Refused] → refused (requires reason)
scheduled → [Hold] → held (requires reason)
scheduled/held → [Cancel] → cancelled
missed/refused/held/cancelled → [Reset to Scheduled] → scheduled
```

---

## Menu Structure

```
Nursing (App Launcher)
├── Nursing Rounds
├── Nursing Notes
├── Care Plans
├── Medication Administration
└── Configuration (Manager/Admin only)
```

---

## Security Role Summary

| Role | Nursing Round | Nursing Note | Care Plan | MAR |
|------|--------------|--------------|-----------|-----|
| Nurse | R/W/C | R/W/C | R/W/C | R/W/C |
| Doctor | R/W | R/W | R/W | R/W |
| Receptionist | R | R | R | R |
| Pharmacist | R | R | R | R/W |
| Lab Technician | R | R | R | R |
| Accountant | R | R | R | R |
| Manager | R/W/C | R/W/C | R/W/C | R/W/C |
| DPO | R | R | R | R |
| System Admin | Full | Full | Full | Full |

**Delete Protection:** Only System Administrators can delete nursing records. All blocked delete attempts are audit-logged.

---

## Report

**Nursing Summary PDF**
- Bound to: `hospital.admission`
- Template: `hospital_nursing.report_nursing_summary_template`
- Printed from: Admission form → Print menu
- Contains:
  - Patient and admission details (ward, room, bed, physician, dates)
  - Recent nursing rounds (last 10)
  - Recent nursing notes (last 10)
  - Active care plans
  - Medication administration summary (last 20)
- Uses shared YOYA Hospital report header (`hospital_management.hospital_report_header`)

---

## Install Notes

1. Ensure `hospital_management`, `hospital_admission`, and `hospital_pharmacy` are installed first.
2. Install `hospital_nursing` via Apps menu or `./odoo-bin -u hospital_nursing`.
3. The **Nursing** app launcher will appear on the home screen.
4. Assign users to `group_hospital_nurse` to grant nursing access.

---

## Manual Test Checklist

- [ ] Module installs without errors
- [ ] Nursing app launcher appears on home screen
- [ ] Nursing Rounds list opens with no errors
- [ ] Create Nursing Round: set patient + admission, enter vitals, click Record Round → state = recorded
- [ ] Click Review on recorded round → state = reviewed, reviewed_by and reviewed_date populated
- [ ] Cancel a draft round → state = cancelled
- [ ] Reset cancelled round to draft → state = draft
- [ ] Oxygen saturation validation: entering 150 raises UserError
- [ ] Negative BP validation raises UserError
- [ ] Create Nursing Note: type = observation, enter note, click Submit → state = submitted
- [ ] Click Review on submitted note → state = reviewed
- [ ] Action Required flag shows action detail field when checked
- [ ] Create Care Plan: enter diagnosis/goals/interventions, click Activate → state = active
- [ ] Click Complete on active plan → state = completed, completed_date populated
- [ ] Create MAR record: enter medication, route, scheduled time
- [ ] Click Mark Administered → status = administered, administered_datetime set
- [ ] Click Mark Missed without reason → UserError raised
- [ ] Enter reason, click Mark Missed → status = missed
- [ ] Reset to Scheduled from missed → status = scheduled
- [ ] Open patient record — Nursing Rounds, Notes, Care Plans, MAR smart buttons show correct counts
- [ ] Click smart button → opens filtered list for that patient
- [ ] Nursing tab on patient shows embedded lists of recent records
- [ ] Open admission — nursing smart buttons show counts filtered by admission
- [ ] Print Nursing Summary PDF from admission → report renders without RPC error
- [ ] Non-admin user tries to delete a nursing round → UserError raised, attempt logged in audit log
- [ ] Audit log shows create/state change entries for nursing records

---

## Known Limitations

- No pharmacy stock deduction: MAR records medication administration but does not deduct inventory
- No barcode medication scanning (Task 27C+)
- No doctor electronic signature on nursing records
- No shift scheduling or shift handover module
- No advanced nursing dashboard / KPI charts
- No inpatient billing charges for nursing actions
- No Walnut Accounting integration
- App launcher icon uses hospital logo placeholder (replace `static/src/img/nursing_icon.png` with a dedicated nursing icon)

---

## Task 27A-2 — Finalized Nursing Record Locking

**Status:** Completed 2026-06-27

### Summary

Extends the clinical audit-integrity locking introduced in Task 27A-1 (Nursing
Rounds) to the remaining nursing records. Once a record reaches a clinically
final state, its clinical fields are locked at both the UI and server-write
levels. Workflow transitions and chatter/system updates are unaffected.

### Lock Behavior by Model

| Model | Locked when | Held / intermediate |
|-------|-------------|---------------------|
| Nursing Note | `state in ['reviewed', 'cancelled']` | submitted stays editable |
| Care Plan | `state in ['completed', 'cancelled']` | active stays editable |
| Medication Administration (MAR) | `administration_status in ['administered', 'missed', 'refused', 'cancelled']` | **held remains editable** (may be resumed, cancelled, or corrected) |

### Nursing Note

- Locked fields: `patient_id`, `admission_id`, `nurse_id`, `note_datetime`,
  `note_type`, `note`, `action_required`, `action_required_note`, `active`.
- `patient_id`/`admission_id` keep their existing stricter `readonly="state != 'draft'"`.
- Draft/submitted editable; reviewed/cancelled locked.
- Workflow-safe fields: `state`, `reviewed_by`, `reviewed_date`.

### Care Plan

- Locked fields: `patient_id`, `admission_id`, `nurse_id`, `physician_id`,
  `plan_date`, `nursing_diagnosis`, `goals`, `interventions`, `evaluation`,
  `priority`, `active`.
- `patient_id`/`admission_id` keep their existing stricter `readonly="state not in ('draft',)"`.
- Draft/active editable; completed/cancelled locked.
- Workflow-safe fields: `state`, `completed_date`.

### Medication Administration (MAR)

- Locked fields: `patient_id`, `admission_id`, `prescription_id`,
  `medication_name`, `dose`, `route`, `scheduled_datetime`,
  `administered_datetime`, `nurse_id`, `reason_not_administered`, `notes`, `active`.
- Scheduled and **held** remain editable; administered/missed/refused/cancelled locked.
- `patient_id`/`admission_id` keep their existing `readonly="administration_status != 'scheduled'"`.
- Workflow-safe field: `administration_status`.
- `Mark Administered` is not blocked: the record is still `scheduled` at write
  time, so writing `administration_status` + `administered_datetime` is allowed.

### UI Readonly Rules Added

- Nursing Note: `readonly="state in ['reviewed', 'cancelled']"` on clinical fields.
- Care Plan: `readonly="state in ['completed', 'cancelled']"` on clinical fields.
- MAR: `readonly="administration_status in ['administered', 'missed', 'refused', 'cancelled']"` on clinical fields (held excluded → stays editable).
- All fields remain visible; nothing hidden or removed from create mode; header
  buttons and statusbars unchanged; existing layout untouched.

### Server-Side Write Protection Added

Each model gained a protected-field set and a `write()` guard that raises a
`UserError` when a protected clinical field is changed in a final state:

| Model | Set | Final states | Error |
|-------|-----|--------------|-------|
| Nursing Note | `PROTECTED_NOTE_FIELDS` | reviewed, cancelled | "Reviewed or cancelled nursing notes are locked. Reset to Draft before making clinical corrections." |
| Care Plan | `PROTECTED_CARE_PLAN_FIELDS` | completed, cancelled | "Completed or cancelled care plans are locked. Reset to Draft before making clinical corrections." |
| MAR | `PROTECTED_MAR_FIELDS` | administered, missed, refused, cancelled | "Finalized medication administration records are locked. Reset to Scheduled before making corrections." |

Workflow/system fields, chatter/activity, and `write_uid`/`write_date` are
excluded from each set, so workflow methods and mail tracking keep working.

### Files Modified

| File | Change |
|------|--------|
| `models/nursing_note.py` | `PROTECTED_NOTE_FIELDS` + write guard |
| `models/care_plan.py` | `PROTECTED_CARE_PLAN_FIELDS` + write guard |
| `models/medication_administration.py` | `PROTECTED_MAR_FIELDS` + write guard |
| `views/nursing_note_views.xml` | Clinical-field readonly rules |
| `views/care_plan_views.xml` | Clinical-field readonly rules |
| `views/medication_administration_views.xml` | Clinical-field readonly rules |
| `README.md` | This section |

### Manual Test Checklist

**Nursing Note**
1. Create a draft note → confirm fields editable.
2. Submit → Review.
3. Confirm fields become readonly.
4. Try editing note text / action fields → blocked by UI and/or `UserError`.

**Care Plan**
1. Create a draft care plan → Activate → Complete.
2. Confirm fields become readonly.
3. Try editing diagnosis/goals/interventions/evaluation → blocked by UI and/or `UserError`.

**MAR**
1. Create a scheduled MAR → confirm fields editable.
2. Mark Administered → confirm fields become readonly.
3. Try editing medication/dose/route/notes → blocked by UI and/or `UserError`.
4. Create another scheduled MAR → Mark Held → confirm held record remains editable.
5. Cancel or Reset to Scheduled per existing workflow → no RPC error.

---

## Task 27A-1 — Reviewed Nursing Round Clinical Lock

**Status:** Completed 2026-06-27

### Summary

Once a Nursing Round is **reviewed** or **cancelled**, its clinical fields are now
locked for clinical audit integrity. They can no longer be edited from the UI or
via direct RPC writes. Workflow transitions and system/chatter updates are
unaffected.

### Edit Behavior by State

| State | Clinical fields |
|-------|-----------------|
| Draft | Editable |
| Recorded | Editable (patient/admission already locked to draft-only by existing workflow) |
| Reviewed | Read-only / locked |
| Cancelled | Read-only / locked |
| Reset to Draft (from cancelled) | Editable again |

### UI Readonly Rules

- Added `readonly="state in ['reviewed', 'cancelled']"` to all editable clinical
  fields on the Nursing Round form: `nurse_id`, `round_datetime`,
  `consciousness_level`, `temperature`, `pulse_rate`, `respiratory_rate`,
  `systolic_bp`, `diastolic_bp`, `oxygen_saturation`, `blood_sugar`, `pain_level`,
  `intake_notes`, `output_notes`, `nursing_observation`, `action_taken`.
- `patient_id` and `admission_id` retain their existing `readonly="state != 'draft'"`
  rule (already stricter — locked in reviewed/cancelled).
- Fields remain visible; required fields are not hidden or removed from create mode.
- Header buttons (Record Round, Review, Cancel, Reset to Draft) and the statusbar
  are unchanged. UI Pattern V1 layout is preserved.

### Server-Side Write Protection

- Added a `PROTECTED_CLINICAL_FIELDS` set on `hospital.nursing.round`.
- `write()` now raises a `UserError` if any protected clinical field is changed
  while a record is in `reviewed` or `cancelled` state:
  > "Reviewed or cancelled nursing rounds are locked. Reset to Draft before making clinical corrections."
- Workflow-safe: `state`, `reviewed_by`, `reviewed_date`, chatter/activity, and
  `write_uid`/`write_date` are **not** protected, so Review, Cancel, and Reset to
  Draft all continue to work. Reset to Draft is only allowed from `cancelled`
  (existing workflow — unchanged).

### Files Modified

| File | Change |
|------|--------|
| `views/nursing_round_views.xml` | Added `readonly="state in ['reviewed', 'cancelled']"` to clinical fields |
| `models/nursing_round.py` | Added `PROTECTED_CLINICAL_FIELDS` and write-protection guard in `write()` |
| `README.md` | This section |

### Manual Test Checklist

1. Upgrade `hospital_nursing`.
2. Hard refresh browser.
3. Open the Nursing app.
4. Open a Draft Nursing Round.
5. Confirm clinical fields are editable.
6. Record Round → confirm workflow still works.
7. Review the record.
8. Confirm clinical fields become readonly.
9. Try editing temperature, pulse, BP, SpO₂, observation, action taken → blocked by UI and/or `UserError`.
10. Confirm chatter/system updates do not cause an RPC error.
11. Reset to Draft (from cancelled) → confirm fields become editable again.
12. Cancel a draft/recorded record → confirm clinical fields become readonly, no RPC error.
13. Confirm the Nursing Round UI layout is not broken.

---

## Task 27B-2A — Nursing Round UI Blueprint Alignment Fix

**Status:** Completed 2026-06-23

### Issues Fixed

| # | Issue | Fix |
|---|-------|-----|
| 1 | Reviewed ribbon was a small square badge | Replaced with tall banner using `clip-path: polygon(...)` notched ribbon shape, 90px wide × full hero height |
| 2 | Respiratory Rate KPI icon blank (`fa-lungs` missing) | Changed to `fa fa-stethoscope` (available in all FA versions) |
| 3 | Blood Pressure `120/80.00` overlapped unit text | Added `.nursing-kpi-bp-value` class with 16px font (vs 18px) and `max-width: 40px` per field |
| 4 | KPI cards uneven spacing (flex wrap) | Switched to CSS grid: `repeat(auto-fit, minmax(170px, 1fr))` — 5 cards now equal-width |
| 5 | Hero chips were a flex row | Redesigned as 2×2 CSS grid — State, Nurse, Round Date/Time, Admission Ref |
| 6 | "Patient & Round" label | Renamed to "Patient & Admission" to match blueprint |
| 7 | Clinical Status card had no data rows | Rebuilt with label/value row layout — Respiratory Rate, Blood Sugar, Intake Notes, Output Notes, Reviewed By |
| 8 | Status insight panel was 2nd column of 2 | Moved to 3rd narrow column (190px) of `nursing-main-grid` |
| 9 | 7 KPI cards (incl. RR and Blood Sugar) | Reduced to 5 KPI cards; RR and Blood Sugar moved to Clinical Status card |
| 10 | Intake/Output as separate section | Moved into Clinical Status card as compact rows |

### Files Modified

| File | Change |
|------|--------|
| `static/src/scss/nursing_theme.scss` | Complete rewrite — new ribbon, chip-icon/chip-body, 3-col grid, CS rows, BP fix |
| `views/nursing_round_views.xml` | Form view rewritten — 5 KPI cards, 2×2 chips, new ribbon classes, CS rows |

### Manual Test Checklist

1. Upgrade `hospital_nursing` and hard-refresh browser
2. Open Nursing → Nursing Rounds → open NURR00001
3. Confirmed reviewed state shows tall green REVIEWED ribbon (90px wide, notched bottom)
4. Confirmed Respiratory Rate icon is visible (stethoscope icon)
5. Confirmed Blood Pressure displays as `120/80 mmHg` without overlap
6. Confirmed KPI cards are 5 (Temperature, Pulse Rate, BP, SpO₂, Pain Level)
7. Confirmed hero meta chips show in 2×2 grid layout
8. Confirmed Clinical Status card shows RR, Blood Sugar, Intake, Output, Reviewed By
9. Confirmed status insight panel is 3rd narrow column
10. Confirmed Draft/Recorded/Cancelled states render ribbons without error
11. Confirmed edit mode works (patient/admission editable in draft)
12. Confirmed Record Round and Review buttons function
13. Confirmed no RPC error on open
14. Confirmed no global CSS break in other modules (Billing, Admission, Patient, Pharmacy)

---

## Task 27B-2 — Nursing Round UI Pattern V1 Polish

**Status:** Completed 2026-06-23

### What Changed

| File | Change |
|------|--------|
| `static/src/scss/nursing_theme.scss` | New — full UI Pattern V1 SCSS scoped under `.hospital_nursing_round_profile` |
| `views/nursing_round_views.xml` | Form view replaced with polished layout (list/search/action unchanged) |
| `__manifest__.py` | `assets.web.assets_backend` entry added for the SCSS file |

### Layout Sections (Nursing Round Form)

1. **Hero header** — emerald icon block, record name (NURR00001), subtitle, 4 read-only meta chips (Patient, Admission, Nurse, Round Date/Time), state ribbon (Draft / Recorded / Reviewed / Cancelled)
2. **KPI vital-sign cards** — 7 cards: Temperature, Pulse Rate, Respiratory Rate, Blood Pressure (Sys/Dia combined), SpO₂, Blood Sugar, Pain Level — each with icon, label, editable field, and unit
3. **Two-column info grid** — "Patient & Round" editable card (patient, admission, nurse, datetime, consciousness level) + "Clinical Status" insight panel (state-aware status cards)
4. **Intake / Output** — two side-by-side cards with textarea fields
5. **Notebook tabs** — Nursing Observation, Action Taken, Review (shows reviewer + date when reviewed; placeholder message otherwise)

### SCSS Conventions

- Scope class: `.hospital_nursing_round_profile` (on `<sheet>`)
- CSS custom properties: `--nursing-primary: #006A4F`, `--nursing-border`, `--nursing-card`, `--nursing-page`, `--nursing-text`, `--nursing-muted`
- No global selectors — all rules scoped under `.hospital_nursing_round_profile`
- Responsive breakpoints: 991.98 px (single-column info grid, stacked hero) and 575.98 px (full-width KPI cards)

---

## Next Recommended Task

**Task 27C — Nursing Medication Administration Enhancement**
- Barcode scan for patient wristband and medication verification
- Scheduled MAR generation from active prescriptions
- Shift-level MAR view

**Task 27D — Shift Handover**
- Shift handover model with patient list, flagged items, and outgoing/incoming nurse signature
