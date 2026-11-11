# hospital_hr — Hospital HR Extension (YOYA General Hospital ERP)

Hospital-specific HR layer built **on top of Odoo 18 Employees**.

## Purpose & architecture decision

`hr.employee` is the **master staff record** for the entire hospital ERP.
`hospital_hr` extends it with hospital-specific categorization; it does **not**
create a parallel employee/staff model.

- Doctors keep their clinical directory (`hospital.doctor`), now bridged to
  `hr.employee` via `employee_id`.
- Nurses, pharmacists, radiologists, lab technicians, and cashiers are
  referenced as `res.users` across the ERP — `hr.employee.user_id` already
  resolves them to an employee, with zero changes to those modules.

## What Task 38B adds

| Piece | Details |
|---|---|  
| `hospital.hr.role` | Staff category catalog (Doctor, Nurse, Pharmacist, ...) with `role_type`, `clinical_role`, `requires_license`, scheduling/on-call flags, allowed departments. 14 roles seeded (`noupdate`). |
| `hr.employee` extension | `hospital_role_id`, `hospital_staff_type`, `is_clinical_staff` (computed from role, overridable), `hospital_department_id` (clinical unit — the Odoo `department_id` stays the org/payroll department), duty/on-call eligibility, clinical permissions, specialties, `hospital_notes` (HR-only). |
| Employee form | New **Hospital Profile** notebook tab; search filters (Clinical Staff, Duty Eligible, On-Call Eligible) and group-bys (Hospital Role, Clinical Department). |
| `hospital.doctor` bridge | Additive `employee_id` → `hr.employee` field + form placement. Existing doctor fields and workflows untouched. |
| Menus | Employees ▸ **Hospital HR** ▸ Hospital Staff / Hospital Roles. No new app icon. |
| Security | Groups `group_hospital_hr_user` (read config) and `group_hospital_hr_manager` (manage roles), in the existing Hospital Management category. Internal users get read on roles; Odoo HR users get read on `hospital.department`. Base Odoo HR security is not weakened. |

## What Task 38C adds — Credential / License Tracking

| Piece | Details |
|---|---|
| `hospital.hr.credential` | Per-employee licenses/certifications: type (medical/nursing/pharmacy/radiology/lab/specialty/BLS-ACLS/registration/clearance/other), license number, issuing authority, issue/expiry dates, document attachments, notes, chatter. |
| Status lifecycle | Computed stored `status`: `suspended` / `pending_verification` (from the HR-controlled `manual_status` override) → `no_expiry` → `expired` → `expiring_soon` (within a horizon, default **60 days**, configurable via system parameter `hospital_hr.credential_expiry_horizon_days`) → `valid`. |
| Daily cron | *Hospital HR: Check Credential Expiry* re-evaluates date-based statuses and, **only on a status change** to expiring/expired (guarded by `last_notified_status`, so no daily spam), posts a chatter warning and schedules an activity for the first Hospital HR Manager. |
| Employee summary | `credential_ids`, `credential_count` + smart button, computed `license_number` / `license_expiry_date` / `license_status` (incl. `missing` when the role requires a license) / `credential_warning`, shown in a Credential/License Summary section on the Hospital Profile tab. Primary credential = role-preferred type first (Doctor→medical license, Nurse→nursing license, …), then fixed type priority, then earliest expiry. |
| Menus | Employees ▸ Hospital HR ▸ **Credentials / Licenses** and **Expiring Credentials** (expiring/expired/suspended/pending). Restricted to Hospital HR users. |
| Security | ACLs: hospital HR user read/create/write (no delete), hospital HR manager full CRUD, internal users read-only. Record rules: internal users see **only their own** credentials (`employee_id.user_id = user`); hospital HR users see all. |

Limitations (by design, for now): no renewal workflow/wizard, no per-role
mandatory-credential checklist, no document expiry OCR, notification goes to a
single HR manager. These are candidates for 38F.

## What Task 38D adds — Shift Templates & Duty Roster

| Piece | Details |
|---|---|
| `hospital.hr.shift.template` | Rosterable shift slots with float times, computed overnight-aware `duration_hours`, `shift_type` (regular/on_call/emergency/ot/ward), auto-suggested `is_night_shift`, color. 7 templates seeded (`noupdate`): Morning, Afternoon, Night, On Call, Emergency, OT Duty, Ward Duty. Deliberately **not** `resource.calendar` — that stays the contractual working pattern. |
| `hospital.hr.duty.schedule` | Roster header per clinical department + period, sequence-named (`DUTY/2026/00001`), states draft → confirmed → done (+ cancelled), chatter. Confirm moves draft lines to confirmed; Cancel cancels non-completed lines; Reset to Draft is HR-manager-only. |
| `hospital.hr.duty.line` | One employee/date/shift. Snapshot `start_time`/`end_time` (kept even if the template changes later), computed `start_datetime`/`end_datetime` (tz-aware, overnight-aware) for the calendar, `duty_area`, 7 states, chatter. Role defaults from the employee, department/date from the schedule. |
| Overlap protection | Real interval overlap: the same employee cannot hold two time-overlapping duty lines (overnight shifts handled; cancelled lines excluded). |
| License gating | On schedule confirmation: duty-ineligible employees are blocked; if the duty role requires a license, employees with missing/expired/suspended/pending licenses are blocked with a clear message; expiring-soon licenses confirm but post a warning in the schedule chatter. |
| Views | Schedules: list/form (editable line grid)/calendar/search. Lines: list/form/**week calendar** (start/end datetimes)/**pivot** (department × shift, hours measure)/search with Today / This Week / This Month + state filters. |
| Employee | **Duty Lines** smart button (non-cancelled count). |
| Menus | Employees ▸ Hospital HR ▸ Duty Schedules, Duty Lines (Hospital HR users); Shift Templates listed after Hospital Roles. |
| Security | Shift templates: read for internal users, CRUD for HR manager. Schedules/lines: HR user read/create/write (no delete), HR manager full CRUD. Record rules: internal users see **only their own** duty lines; hospital HR sees all. |

Limitations (by design): no check-in/check-out (38E), no leave-conflict check
(hr_holidays not installed — the confirmation hook is where it will plug in),
no auto-generation of lines across a date range, no OT/nursing auto-assignment.

## What Task 38E adds — Duty Logs / Check-in / Check-out

| Piece | Details |
|---|---|
| `hospital.hr.attendance.log` | Actual duty execution record: employee, optional duty line link (ad-hoc manual logs allowed; roster link survives line deletion via `set null`), check-in/out, computed `worked_hours`, `source` (manual / duty_schedule + reserved hr_attendance/biometric/mobile), status open → closed (+ corrected/cancelled), chatter. **Not** a replacement for Odoo Attendance — a bridge module can sync if `hr_attendance` is installed later. |
| Late/early detection | Computed against the duty line's UTC `start_datetime`/`end_datetime` with a grace period (default **15 min**, configurable via system parameter `hospital_hr.late_grace_minutes`): `is_late`/`late_minutes`, `early_checkout`/`early_checkout_minutes`. |
| Duty line execution | Buttons: **Check In** (scheduled/confirmed → on_duty, creates an open log, source=duty_schedule), **Check Out** (on_duty → completed, closes the log), **Mark Missed** (scheduled/confirmed → missed, no log), **Cancel** (manager-only if completed), **Reset to Scheduled** (manager-only; logs are never deleted), **View Logs** smart button. Chatter messages on each transition. |
| Duty line actuals | `actual_check_in` (earliest), `actual_check_out` (latest closed), `actual_worked_hours` (sum of closed logs), `lateness_summary` ("On time" / "Late by 12 min / Early checkout by 20 min") — shown in the schedule's line grid and standalone views. |
| Integrity | check_out > check_in; only one open log per duty line; check-in blocked on cancelled/completed/missed lines; check-out requires an open log. |
| Views/menus | Duty Logs list (open/late/early decorations), form, search (Open/Closed/Late/Early/source/date filters), pivot (worked hours per employee per week), week calendar. Employees ▸ Hospital HR ▸ **Duty Logs**; employee **Duty Logs** smart button. |
| Security | HR user read/create/write (no delete), HR manager full CRUD; internal users read **their own** logs only. Self check-in by employees (portal/mobile) is a documented future task — for now HR/supervisors operate from the roster. |

Timezone note: all comparisons happen in UTC (`Datetime` fields vs the duty
line's computed UTC start/end); Odoo renders in the user's timezone.

## Task 38E.1 — Doctor license source of truth

Professional license data is managed in **Employee Credentials**
(`hospital.hr.credential`), never on the physician form. The physician form
and list now display a readonly `credential_license_number` (+ status) related
to `employee_id.license_number`; it is empty until the doctor is linked to an
Employee Profile. The legacy `hospital.doctor.license_number` field and its
stored values remain in the database for old reports/code, but are no longer
shown or editable in the UI.

## What it deliberately does NOT duplicate

`department_id`, `job_id`, `work_location_id`, `resource_calendar_id`,
`user_id`, `emergency_contact` / `emergency_phone`, `employee_type`, employee
tags — all reused from Odoo HR.

## Not yet implemented (later tasks)

- 38F — Integration bridges (nursing/OT/etc.) & reporting

Deferred: Odoo Attendance bridge, biometric integration, mobile/portal self
check-in, leave conflict integration, payroll export.

## Dependencies

`hr`, `hospital_management` (for `hospital.department`, the security category,
and the `hospital.doctor` bridge).

## Installation / testing

```bash
odoo-bin -d healthcare_erp -i hospital_hr --stop-after-init
# upgrade later with -u hospital_hr
```

Smoke test: open Employees → employee form shows the **Hospital Profile** tab;
pick a Hospital Role → duty/clinical flags and staff type are suggested;
Employees ▸ Hospital HR ▸ Hospital Roles lists the 14 seed roles;
a doctor form (Physician app) shows **Employee Profile**.

## Documentation

Full architecture (Task 38A inspection findings, model specs, integration
paths): [docs/HOSPITAL_HR_ARCHITECTURE.md](docs/HOSPITAL_HR_ARCHITECTURE.md)
