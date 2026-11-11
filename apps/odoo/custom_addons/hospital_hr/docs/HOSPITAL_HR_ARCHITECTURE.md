# Task 38A — `hospital_hr` Architecture Blueprint

**Module:** `custom_addons/hospital_hr`
**Target:** Odoo 18 Community — YOYA General Hospital ERP
**Database inspected:** `healthcare_erp`
**Status:** Architecture approved. **Tasks 38B (core extension), 38C
(credential/license tracking), 38D (shift templates & duty roster) and 38E
(duty logs / check-in / check-out) implemented and installed on
`healthcare_erp`** — roles, employee profile extension, doctor bridge,
credentials with expiry cron, shift templates, duty schedules/lines with
overlap protection and license gating, and duty execution logs with late/early
detection. Next: 38F (integration bridges & reporting).

Core decision: `hr.employee` is the master staff record. `hospital_hr` is a thin,
hospital-specific extension layer on top of Odoo HR. No parallel employee model
is created.

---

## 1. Existing Odoo HR foundation (inspected, not assumed)

### 1.1 Installed HR-related modules in `healthcare_erp`

| Module | State | Relevance |
|---|---|---|
| `hr` | installed | Employees, Departments, Job Positions, Work Locations, Employee Tags |
| `resource` | installed | `resource.calendar` (Working Schedules), `resource.resource` |
| `hr_skills` + `hr_skills_survey` | installed | Resume / Skills tabs on employee form |
| `hr_org_chart` | installed | Org chart widget on employee form |
| `hr_calendar`, `hr_gamification`, `calendar` | installed | Peripheral |
| `hr_attendance` | **not installed** | Duty log must not assume it exists |
| `hr_holidays` (Time Off) | **not installed** | Leave-awareness must be a soft hook |
| `hr_contract` | **not installed** | No contract fields available today |
| `hr_payroll_community`, `ethiopian_payroll` | present in addons dir, **not installed** | Future payroll path exists; both extend `hr.employee`/`hr.contract` |
| `act_hr_certification` | present, **not installed** | Employment "Certificate of Service" *letter/report* module — **no overlap** with clinical credential tracking |

### 1.2 `hr.employee` fields verified in Odoo 18 source

Verified in `odoo/addons/hr/models/hr_employee.py` + `hr_employee_base.py`.
Fields we will **reuse, never duplicate**:

- Identity/org: `name`, `active`, `company_id`, `department_id` (→ `hr.department`),
  `job_id` (→ `hr.job`), `job_title`, `parent_id` (manager), `coach_id`,
  `category_ids` (employee tags), `employee_type`
- Linkage: `user_id` (→ `res.users`), `work_contact_id`, `resource_id`
- Schedule/location: `resource_calendar_id` (→ `resource.calendar`),
  `work_location_id` (→ `hr.work.location`), `tz`
- Contact: `work_email`, `work_phone`, `mobile_phone`,
  `emergency_contact`, `emergency_phone` (already exist — do not re-add)
- HR data: `identification_id`, `barcode`, `pin`, `certificate` (education level),
  `study_field`, `study_school`, `notes`, `birthday`, `gender`, `marital`

`hr.department` (has `manager_id`, `parent_id`, `member_ids`), `hr.job`,
`hr.work.location`, and `resource.calendar` are all available and installed.

`hr_skills` provides `hr.resume.line` / `hr.employee.skill`. Resume lines are
free-text CV entries — not structured enough for license expiry tracking, status
lifecycle, or compliance reporting, so a dedicated credential model is justified
(see §4.3). Skills remain useful for competencies and stay untouched.

### 1.3 Existing Employees app menus

Root: `hr.menu_hr_root` (Employees app). Config: `hr.menu_human_resources_configuration`.
Sub-menus verified: `menu_hr_employee`, `menu_hr_department_kanban`,
`menu_view_hr_job`, `menu_hr_work_location_tree`, `menu_resource_calendar_view`.
`hospital_hr` menus will attach under `hr.menu_hr_root` (see §6).

---

## 2. Existing hospital staff landscape (inspected)

### 2.1 Staff-related models found

| Model | Module | Shape |
|---|---|---|
| `hospital.doctor` | hospital_management | Lightweight: `name`, `user_id` (res.users), `department_id` (→ `hospital.department`), `specialization`, `license_number`, `phone`, `email`, `active`, `notes`. **The only custom staff model in the ERP.** |
| `hospital.department` | hospital_management | `name`, `code`, `description`, `manager_id` (res.users), `active`. Clinical unit — distinct from `hr.department`. |
| `hospital.surgery.team.member` | hospital_operation_theatre | `surgery_id`, `physician_id` (→ hospital.doctor), `user_id` (res.users), `role` selection (lead_surgeon / assistant_surgeon / anesthetist / scrub_nurse / circulating_nurse / technician / observer / other), `name`, `notes`. |

There is **no** `hospital.nurse`, `hospital.staff`, `hospital.technician`,
`hospital.employee`, or `hospital.physician` model. Good — less to migrate.

### 2.2 Where operational records reference staff today

**Pattern A — doctors via `hospital.doctor`:**

| Field | Records |
|---|---|
| `physician_id` / `doctor_id` | appointment, admission, patient bill, lab request/result, diagnosis, evaluation, prescription, treatment plan, nursing care plan, pharmacy dispense, procedure request, radiology request/result, surgery team member |
| `surgeon_id` | operation report |
| `anesthetist_id` | anesthesia note |
| `primary_care_doctor_id` | patient |

**Pattern B — everyone else via `res.users`:**

| Field | Records |
|---|---|
| `nurse_id` | nursing care plan, medication administration, nursing note, nursing round |
| `pharmacist_id` | pharmacy dispense |
| `radiologist_id` | radiology result |
| `lab_technician_id` | laboratory result |
| `cashier_id` | patient bill, bill payment, fiscal transaction |
| `performed_by` | procedure request, stock movement, insurance accounting log |

**Consequence:** nurses/technicians/pharmacists/cashiers have no staff *profile*
anywhere — only a login. `hr.employee` (with `user_id` already linking to
`res.users`) is the natural profile record for all of them. Doctors have a thin
profile (`hospital.doctor`) that must eventually bridge to `hr.employee`.

### 2.3 Security groups (hospital_management)

`group_hospital_receptionist`, `group_hospital_doctor`, `group_hospital_nurse`,
`group_hospital_pharmacist`, `group_hospital_lab_technician`,
`group_hospital_accountant`, `group_hospital_manager`,
`group_hospital_data_protection_officer`, `group_hospital_system_administrator`.

### 2.4 Menu conventions

Each hospital domain is its own root app (Patient seq 10, Physician seq 20,
Admissions seq 45, Billing, Nursing, …), group-restricted, using
`ir.ui.menu` records or `<menuitem>`. Manifest conventions: version
`18.0.1.0.0`, author "Synergy Tech solns", LGPL-3, category "Healthcare",
optional SCSS theme in `web.assets_backend`.

---

## 3. Recommended architecture

```text
hr.employee (Odoo, master record — org, job, schedule, user link)
    │
    ├── hospital profile fields (extension, §5)
    ├── hospital.hr.role          (staff category catalog)
    ├── hospital.hr.credential    (licenses/certifications, 1:n)
    ├── hospital.hr.duty.line     (scheduled duties, 1:n)
    └── hospital.hr.attendance.log (duty log foundation, 1:n)

hospital.hr.shift.template  (Morning/Night/On-Call/OT... catalog)
hospital.hr.duty.schedule   (roster header) ──< hospital.hr.duty.line
```

**Module dependencies:** `depends = ["hr", "hospital_management"]`
(+ `mail` implied via hr). Depending on `hospital_management` gives us
`hospital.department` for clinical-unit assignment, the hospital security
groups, and the ability to ship the `hospital.doctor → hr.employee` bridge
field inside `hospital_hr` (via `_inherit`) without touching
`hospital_management` itself. No other hospital module is a dependency —
integration with nursing/OT/procedure/etc. is deferred (§8).

### Proposed module structure (confirmed viable after inspection)

```text
hospital_hr/
    __init__.py
    __manifest__.py
    docs/
        HOSPITAL_HR_ARCHITECTURE.md      (this file)
    models/
        __init__.py
        hr_employee.py                   (_inherit hr.employee)
        hospital_doctor.py               (_inherit hospital.doctor — bridge field only)
        hospital_hr_role.py
        hospital_hr_credential.py
        hospital_hr_shift_template.py
        hospital_hr_duty_schedule.py     (header + line in one file is fine)
        hospital_hr_attendance_log.py
    views/
        hr_employee_views.xml
        hospital_hr_role_views.xml
        hospital_hr_credential_views.xml
        hospital_hr_shift_views.xml
        hospital_hr_duty_schedule_views.xml
        hospital_hr_attendance_log_views.xml
        hospital_hr_menus.xml
    data/
        hospital_hr_role_data.xml        (seed roles, noupdate="1")
        hospital_hr_shift_data.xml       (seed shift templates, noupdate="1")
        hospital_hr_sequence.xml         (duty schedule sequence)
        hospital_hr_cron.xml             (credential expiry cron)
    security/
        hospital_hr_security.xml
        ir.model.access.csv
    static/src/scss/hospital_hr.scss     (optional, later)
    README.md
```

---

## 4. Models to create

All new models: `_description` set, `mail.thread` only where state/lifecycle
matters (credential, duty schedule, duty line) to keep tables light.

### 4.1 `hospital.hr.role` — staff category catalog

Configuration model (Doctor, Nurse, Radiology Technician, Lab Technician,
Pharmacist, Cashier, Receptionist, Surgeon, Anesthetist, OT Nurse, Inventory
Officer, Cleaner, Security, Administrator — shipped as `noupdate` seed data).

| Field | Type | Notes |
|---|---|---|
| `name` | Char, required, translate | |
| `code` | Char, required, unique | e.g. `DOC`, `RN`, `RAD_TECH` |
| `role_type` | Selection: `medical` / `nursing` / `allied_health` / `pharmacy` / `administrative` / `support` | reporting axis |
| `clinical_role` | Boolean | drives `is_clinical_staff` on employee |
| `requires_license` | Boolean | drives credential warnings |
| `can_be_scheduled` | Boolean, default True | duty roster eligibility |
| `can_be_on_call` | Boolean | |
| `allowed_department_ids` | M2m → `hospital.department` | optional duty-area constraint; empty = all |
| `active`, `notes`, `sequence` | | |

Deliberately a model (not a Selection) so the hospital can add roles without code.

### 4.2 `hr.employee` extension — see §5.

### 4.3 `hospital.hr.credential` — license/certification tracking

| Field | Type | Notes |
|---|---|---|
| `employee_id` | M2o → hr.employee, required, index, ondelete=cascade | |
| `credential_type` | Selection: `medical_license` / `nursing_license` / `pharmacy_license` / `radiology_license` / `lab_certification` / `specialty_certification` / `bls_acls` / `professional_registration` / `other` | |
| `credential_name` | Char, required | |
| `license_number` | Char | |
| `issuing_authority` | Char | e.g. Ethiopian health authority / MOH |
| `issue_date`, `expiry_date` | Date | expiry optional (some never expire) |
| `status` | Selection: `pending_verification` / `valid` / `expiring_soon` / `expired` / `suspended` | computed+stored from dates, manually overridable to `suspended`/`pending_verification` |
| `attachment_ids` | M2m → ir.attachment | scanned documents |
| `notes`, `active` | | |

- Daily **cron** flips `valid → expiring_soon` (configurable horizon, default
  60 days) and `→ expired`, posting a chatter message / activity to HR manager.
- Employee license summary fields (§5) are **computed from** the primary
  (`credential_type` matching role) credential — the credential is the single
  source of truth; nothing is stored twice.
- Not merged into `hr.resume.line`: resume lines have no expiry lifecycle,
  status, or compliance reporting; keeping credentials separate is deliberate.

### 4.4 `hospital.hr.shift.template`

| Field | Type | Notes |
|---|---|---|
| `name`, `code` | Char, required | Morning / Afternoon / Night / On-Call / Emergency / OT / Ward |
| `start_time`, `end_time` | Float (Odoo time-of-day widget) | |
| `duration_hours` | Float, computed (handles overnight wrap) | |
| `shift_type` | Selection: `regular` / `on_call` / `emergency` / `ot` / `ward` | |
| `is_night_shift` | Boolean | |
| `active`, `notes`, `sequence`, `color` | | color for calendar/gantt views |

Shipped seed data. Deliberately **not** `resource.calendar`: a calendar is an
employee's contractual working pattern; a shift template is a rosterable slot.
Both coexist — `resource_calendar_id` stays untouched for future payroll.

### 4.5 `hospital.hr.duty.schedule` (header) + `hospital.hr.duty.line`

**Header** — a roster for a unit and period:

| Field | Type |
|---|---|
| `name` | Char, sequence-generated (e.g. `DUTY/2026/00042`) + label |
| `date_from`, `date_to` | Date, required, constrained `from <= to` |
| `department_id` | M2o → `hospital.department` (clinical unit — rosters follow clinical units, not HR org chart) |
| `responsible_id` | M2o → hr.employee (head nurse / unit lead) |
| `state` | `draft` → `confirmed` → `done`, + `cancelled` |
| `line_ids` | O2m → duty lines |
| `notes` | Text |

**Line** — one employee, one date, one shift:

| Field | Type |
|---|---|
| `schedule_id` | M2o, required, ondelete=cascade |
| `date` | Date, required |
| `employee_id` | M2o → hr.employee, required, domain: duty-eligible |
| `hospital_role_id` | M2o → hospital.hr.role (defaults from employee, overridable — an RN can cover an OT-nurse slot) |
| `department_id` | M2o → hospital.department (related default from header, stored) |
| `shift_template_id` | M2o, required |
| `duty_area` | Char (free: "Ward B / ICU / Triage") |
| `state` | `draft` / `scheduled` / `confirmed` / `on_duty` / `completed` / `missed` / `cancelled` |
| `check_in`, `check_out` | Datetime (mirrors of the attendance log entry) |
| `remarks` | Char |

Guards: SQL/api constraint against the same employee having two overlapping
shift lines on the same date; `_check_employee_availability()` hook (§8 leave
awareness). Views: list (editable), **calendar**, and pivot by
department/role/employee for workload visibility.

### 4.6 `hospital.hr.attendance.log` — duty log foundation

Explicitly **not** a replacement for `hr_attendance` (not installed). If
`hr_attendance` is installed later, a small bridge module
(`hospital_hr_attendance`) can sync — the `source` field anticipates that.

| Field | Type |
|---|---|
| `employee_id` | M2o → hr.employee, required, index |
| `duty_line_id` | M2o → hospital.hr.duty.line, optional (ad-hoc duty allowed) |
| `check_in` | Datetime, required |
| `check_out` | Datetime, constrained `> check_in` |
| `worked_hours` | Float, computed stored |
| `source` | Selection: `manual` / `duty_schedule` / `hr_attendance` / `biometric` / `mobile` (last three reserved for the future) |
| `status` | Selection: `open` / `closed` / `corrected` |
| `notes` | Char |

Duty line buttons "Check In" / "Check Out" create/close the log entry and set
the line to `on_duty` / `completed`.

---

## 5. `hr.employee` fields to extend

New file `models/hr_employee.py`, `_inherit = "hr.employee"`. All fields
prefixed to avoid collision with current/future Odoo fields (verified against
§1.2 — no name clashes).

```text
# Categorization
hospital_role_id          M2o  → hospital.hr.role                (tracking)
hospital_staff_type       Selection: clinical / clinical_support /
                          administrative / support               (tracking)
is_clinical_staff         Boolean, computed stored from role.clinical_role
hospital_department_id    M2o  → hospital.department
                          (clinical unit; hr.department stays the payroll/
                           org-chart department — both kept, roles differ)

# Duty eligibility
can_be_assigned_to_duty     Boolean, default from role.can_be_scheduled
on_call_eligible            Boolean, default from role.can_be_on_call
can_perform_clinical_services  Boolean
can_approve_clinical_work      Boolean

# Specialty
primary_specialty         Char
secondary_specialty       Char
(free Char now, mirroring hospital.doctor.specialization; a specialty
 catalog model can replace them later without data loss)

# License summary — COMPUTED from hospital.hr.credential, never stored twice
license_number            Char, computed (primary credential)
license_expiry_date       Date, computed
license_status            Selection, computed
credential_ids            O2m → hospital.hr.credential
credential_count          Integer, computed        (smart button)

# Duty
duty_line_ids             O2m → hospital.hr.duty.line
duty_line_count           Integer, computed        (smart button)
attendance_log_count      Integer, computed        (smart button)

# Misc
hospital_notes            Text (groups="hr.group_hr_user")
```

**Explicitly NOT added** (already on `hr.employee`): department, job, work
location, working schedule, tags, emergency contact/phone, identification,
user link, education. `emergency_contact_notes` from the task brief is dropped —
`emergency_contact` + `emergency_phone` already exist in `hr`.

### UI (form extension, Task 38B)

- New notebook page **"Hospital Profile"** via XPath after the HR Settings page:
  role, staff type, clinical department, eligibility flags, specialty,
  license summary (readonly computed), hospital notes.
- Smart buttons: **Credentials**, **Duty Schedules**, **Duty Logs**.
- List/kanban: optional `hospital_role_id` column + filters
  ("Clinical Staff", "License Expiring", "Duty Eligible", group-by role).
- Keep the main form light — everything hospital-specific lives in the tab.

---

## 6. Menu strategy

**Primary home: the Employees app** (`hr.menu_hr_root`). No new app icon —
the ERP already has many root apps, and hospital HR is an extension of
Employees, so it should feel like one.

```text
Employees (hr.menu_hr_root)
└── Hospital HR                      (menu_hospital_hr_root, seq ~15)
    ├── Hospital Staff               (hr.employee action, filtered/grouped by role)
    ├── Duty Schedules               (hospital.hr.duty.schedule)
    ├── Duty Lines                   (calendar/list of hospital.hr.duty.line)
    ├── Duty Logs                    (hospital.hr.attendance.log)
    ├── Credentials / Licenses       (hospital.hr.credential, "Expiring" filter default)
    └── Reporting                    (duty line pivot — workload visibility)

Employees ▸ Configuration (hr.menu_human_resources_configuration)
    ├── Hospital Roles
    └── Shift Templates
```

Optional cross-link (nice-to-have, Task 38B decision): one `Staff Duty` menu
entry under an existing hospital root (e.g. the Physician app) opening the same
duty schedule action — a link, not a duplicate app. No new `web_icon` anywhere.

---

## 7. Security strategy

Two new groups in `hospital_hr_security.xml`, in the existing "Hospital"
category alongside the `hospital_management` groups:

| Group | Implied | Purpose |
|---|---|---|
| `group_hospital_hr_user` | — | HR/duty operations staff (unit supervisors, head nurses) |
| `group_hospital_hr_manager` | implies `group_hospital_hr_user` | HR manager; also granted automatically to `hr.group_hr_manager`? **No** — kept separate; assign manually to avoid surprising existing HR users |

Access matrix (`ir.model.access.csv`):

| Model | hospital staff groups (doctor/nurse/pharm/lab, read-only awareness) | hr_user (`hospital_hr` user) | hr_manager |
|---|---|---|---|
| hospital.hr.role | read | read | CRUD |
| hospital.hr.shift.template | read | read | CRUD |
| hospital.hr.credential | — (own only via record rule, read) | read/create/write | CRUD |
| hospital.hr.duty.schedule | read | read/create/write | CRUD + unlink |
| hospital.hr.duty.line | read | read/create/write | CRUD |
| hospital.hr.attendance.log | create own (record rule `employee_id.user_id = user`) | read/create/write | CRUD |

Record rules: employees see their **own** credentials and duty lines
(`employee_id.user_id = user.id`); `group_hospital_hr_user` sees all.
Sensitive fields (`hospital_notes`, license documents) restricted with
`groups="hr.group_hr_user"` at field level, matching Odoo HR's own pattern.
Existing Odoo `hr.group_hr_user` / `hr.group_hr_manager` keep governing the
base employee record — we don't touch that.

---

## 8. Integration points with existing hospital modules

**Task 38A ships one additive bridge and documents the rest. Nothing existing
is modified or broken.**

### Shipped in hospital_hr (additive, zero-risk)

- `models/hospital_doctor.py`: `_inherit = "hospital.doctor"`, add
  `employee_id = Many2one("hr.employee")` (+ optional smart button both ways).
  Existing `physician_id` fields across 15+ models keep working unchanged;
  the bridge lets us join doctor ↔ employee for credentials and duty.

### Documented migration path (future tasks, per module)

| Module / record | Today | Bridge path |
|---|---|---|
| `hospital.doctor` (all `physician_id`/`surgeon_id`/`anesthetist_id` users) | thin custom model | keep as clinical directory; `employee_id` bridge now; long-term, doctor license fields (`license_number`) deprecate in favor of `hospital.hr.credential` via the bridge |
| Nursing (`nurse_id` → res.users ×4 models) | login-only | future: add optional `nurse_employee_id` computed from `user_id` via `hr.employee.user_id`, or switch domain to duty-eligible employees; **no change in 38A** |
| OT `hospital.surgery.team.member` | `physician_id` + `user_id` + role selection | future: optional `employee_id`; its `role` selection maps 1:1 onto seeded `hospital.hr.role` codes; duty lines with `shift_type = ot` can pre-populate teams |
| Procedure `performed_by`, radiology `radiologist_id`, lab `lab_technician_id`, pharmacy `pharmacist_id` (res.users) | login-only | same `user_id → employee` join; report-level first, field-level later |
| Billing/fiscal `cashier_id` (res.users) | login-only | join-only; probably never needs an employee FK |
| `hospital.department` vs `hr.department` | two department trees | keep both on purpose: `hr.department` = org/payroll axis, `hospital.department` = clinical unit axis. Optional future mapping field `hr_department_id` on `hospital.department` if reporting demands it |
| Leave awareness | `hr_holidays` not installed | duty line validation calls `_check_employee_availability()`; base implementation checks overlapping duty lines only; when `hr_holidays` is installed, a 20-line bridge module overrides it to also check `hr.leave` — no hard dependency now |
| Payroll/attendance | `hr_payroll_community` / `ethiopian_payroll` on disk, not installed | `attendance.log.worked_hours` + duty line dates are exactly the inputs work entries need; `resource_calendar_id` left untouched as the contractual schedule |
| `hospital_dashboard` | installed | future: duty-coverage / license-expiry tiles reading the new models |

### The res.users → hr.employee join

`hr.employee.user_id` already exists, so every `res.users`-based staff field in
the ERP becomes employee-resolvable **the moment an employee record links its
user** — with zero schema changes to the operational modules. This is the
single biggest payoff of building on `hr.employee`.

---

## 9. Risks and avoided duplication

| Risk / temptation | Resolution |
|---|---|
| Parallel staff model (`hospital.staff`) | Rejected. `hr.employee` is the master; only `hospital.doctor` predates it and is bridged, not replaced. |
| Duplicating `department_id`, `job_id`, `work_location_id`, `resource_calendar_id`, `emergency_contact` | Not re-declared. Verified against Odoo 18 source field list. |
| Two "license number" sources (employee field + credential rows + `hospital.doctor.license_number`) | Employee license fields are computed from credentials. `hospital.doctor.license_number` untouched in 38A but flagged deprecated-in-favor-of-credentials once bridged. |
| Shift template vs `resource.calendar` confusion | Separated by definition: calendar = contractual pattern (payroll), template = rosterable slot (operations). Documented in both models' help text. |
| Duty log vs future `hr_attendance` clash | Log is additive with a `source` discriminator; sync bridge deferred until `hr_attendance` is actually installed. |
| Breaking doctor/nurse/OT/pharmacy/radiology workflows | Zero fields changed on existing models; only one additive M2o on `hospital.doctor`. All migration is opt-in and deferred. |
| Menu clutter / duplicate app icons | No new root app. Everything under Employees; config under HR Configuration. |
| Over-permissive access to HR data | New models get their own groups + record rules; base employee visibility remains governed by Odoo HR groups. |
| `hospital.department` vs `hr.department` drift | Kept as two deliberate axes (clinical vs organizational); mapping field deferred until a report needs it. |

---

## 10. Implementation task breakdown (38B onward)

| Task | Scope | Contents |
|---|---|---|
| **38B — Core extension** | skeleton + roles + employee | `__manifest__` (depends `hr`, `hospital_management`), `hospital.hr.role` + seed data, `hr.employee` extension fields (minus credential-computed ones), `hospital.doctor.employee_id` bridge, Hospital Profile tab, security groups + ACLs, menus (Hospital HR root, Roles config). Installable and useful on its own. |
| **38C — Credentials** | compliance | `hospital.hr.credential`, status compute + expiry cron + activities, employee license summary fields + Credentials smart button, "Expiring Licenses" filter/menu. |
| **38D — Shifts & duty roster** | scheduling | `hospital.hr.shift.template` + seed data, duty schedule + lines, states, overlap constraint, `_check_employee_availability()` hook, calendar + pivot views, sequences, Duty menus. |
| **38E — Duty logs** | attendance foundation | `hospital.hr.attendance.log`, check-in/out actions from duty lines, worked-hours compute, record rules for own-log entry, Duty Logs menu + smart button. |
| **38F — Integration & reporting** | bridges | OT team / nursing optional employee fields (each in its own micro-bridge or behind the join), workload report, `hospital_dashboard` tiles, README + user docs. |

Each task ends with: module upgrade on `healthcare_erp` (staging first), smoke
test of Employees form + one workflow from admission/nursing/OT to confirm
nothing regressed.

---

*Prepared as Task 38A deliverable — architecture only; no module code has been
written yet beyond this documentation.*
