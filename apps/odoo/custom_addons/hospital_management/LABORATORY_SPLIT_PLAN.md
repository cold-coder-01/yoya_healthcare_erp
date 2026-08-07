# Laboratory Module Split Plan

## 1. Purpose
The Laboratory workflow currently lives inside the `hospital_management` addon. After the Laboratory Request and Laboratory Result workflow has been manually tested in Odoo and proven stable, it should be extracted into a separate addon named `hospital_laboratory`.

This plan documents a safe future refactor path. It is a planning artifact only: no code is moved by this task, and no new module is created yet.

## 2. Current Laboratory Components Inside hospital_management
Current laboratory-related components inside `hospital_management` include:

- `models/laboratory_request.py`
- `models/laboratory_result.py`
- `views/laboratory_request_views.xml`
- `views/laboratory_result_views.xml`
- `reports/laboratory_request_report.xml`
- `reports/laboratory_request_template.xml`
- `reports/laboratory_result_report.xml`
- `reports/laboratory_result_template.xml`
- Laboratory-related ACL rows in `security/ir.model.access.csv`
- Laboratory sequences in `data/hospital_sequence.xml`
- Laboratory menu entries in `views/hospital_menus.xml`
- Patient integration fields and methods in `models/patient.py`
- Patient form smart buttons and tabs in `views/patient_views.xml`
- Manifest references in `__manifest__.py`
- README references in `README.md`

## 3. Target Module
Target addon:

```text
custom_addons/hospital_laboratory
```

Target dependencies:

- `hospital_management`
- `base`
- `mail`, if laboratory models continue to require chatter/tracking behavior

`hospital_management` should become the core hospital foundation module. `hospital_laboratory` should depend on it and extend core patient, doctor, security group, audit, and menu infrastructure instead of duplicating it.

## 4. Target hospital_laboratory Structure
Proposed structure:

```text
hospital_laboratory/
  __init__.py
  __manifest__.py
  models/
    __init__.py
    laboratory_request.py
    laboratory_result.py
  views/
    laboratory_request_views.xml
    laboratory_result_views.xml
    laboratory_patient_views.xml
    laboratory_menus.xml
  security/
    ir.model.access.csv
  data/
    laboratory_sequence.xml
  reports/
    laboratory_request_report.xml
    laboratory_request_template.xml
    laboratory_result_report.xml
    laboratory_result_template.xml
  README.md
```

## 5. What Stays in hospital_management
The following should remain in `hospital_management`:

- Patient
- Doctor
- Department
- Appointment
- Consent
- Audit Log
- Patient Evaluation
- Disease and Diagnosis
- Patient Tags
- Family Members
- Patient Documents
- Prescription and Treatment Plan for now
- Base security groups
- Core styling
- Core app/menu structure

## 6. What Moves to hospital_laboratory
The following should move to `hospital_laboratory`:

- `hospital.laboratory.test`
- `hospital.laboratory.request`
- `hospital.laboratory.request.line`
- `hospital.laboratory.result`
- `hospital.laboratory.result.line`
- Laboratory app menu
- Laboratory Requests menu
- Laboratory Results menu
- Laboratory Tests menu
- Laboratory Request PDF report
- Laboratory Result PDF report
- Laboratory sequences
- Laboratory ACLs
- Laboratory audit hooks currently implemented in the laboratory models

## 7. Patient Integration Strategy
Laboratory-related Patient fields and smart-button methods should move out of `hospital_management` and into `hospital_laboratory` as an inherited extension of `hospital.patient`.

Move from `hospital_management/models/patient.py` into a `hospital_laboratory` patient extension:

- `laboratory_request_ids`
- `laboratory_request_count`
- `action_view_laboratory_requests`
- `laboratory_result_ids`
- `laboratory_result_count`
- `action_view_laboratory_results`

Move Patient form lab UI from `hospital_management/views/patient_views.xml` into:

```text
hospital_laboratory/views/laboratory_patient_views.xml
```

Use XML inheritance to add:

- Laboratory Requests smart button
- Laboratory Results smart button
- Laboratory Requests tab
- Laboratory Results tab

## 8. Menu Strategy
Move the Laboratory app root menu to `hospital_laboratory`.

Do not leave duplicate Laboratory menus in `hospital_management`.

Patient shortcut menus for Laboratory Requests and Laboratory Results should also be created by `hospital_laboratory`, because those shortcuts belong to the laboratory feature area even though they appear under Patient navigation.

## 9. Security Strategy
Move only laboratory ACL rows into:

```text
hospital_laboratory/security/ir.model.access.csv
```

Keep hospital groups in:

```text
hospital_management/security/hospital_security.xml
```

`hospital_laboratory` should reference existing group XML IDs from `hospital_management`:

- `hospital_management.group_hospital_receptionist`
- `hospital_management.group_hospital_doctor`
- `hospital_management.group_hospital_nurse`
- `hospital_management.group_hospital_manager`
- `hospital_management.group_hospital_system_administrator`
- `hospital_management.group_hospital_data_protection_officer`
- `hospital_management.group_hospital_lab_technician`
- `hospital_management.group_hospital_pharmacist`
- `hospital_management.group_hospital_accountant`

## 10. Sequence Strategy
Move laboratory sequences to:

```text
hospital_laboratory/data/laboratory_sequence.xml
```

Sequence codes should remain unchanged:

- `hospital.laboratory.request.sequence`
- `hospital.laboratory.result.sequence`

Keeping sequence codes unchanged reduces migration risk and avoids changing existing sequence lookup logic in `create()` methods.

## 11. XML ID Strategy
Preserve XML IDs where possible if the split happens before production data exists.

If production data already exists, changing module ownership of XML IDs needs careful migration planning. Odoo stores XML IDs with a module namespace, so moving a record from `hospital_management.some_xml_id` to `hospital_laboratory.some_xml_id` can affect upgrades, references, menus, actions, and reports.

## 12. Migration Risk Notes
Important risks:

- Existing database records remain tied to model names, not file locations.
- Model names should not change.
- Moving model definitions is safe only when dependencies and import order are correct.
- XML IDs changing module namespace can affect menu/action/report references.
- Existing menus/actions from the old module may need deletion or migration if they were installed before the split.
- If the system has already been used in production, write an upgrade/migration script instead of manually deleting old XML records.

## 13. Recommended Split Steps Later
Recommended future process:

1. Backup database and code.
2. Create `hospital_laboratory` addon skeleton.
3. Copy lab model files.
4. Copy lab views, reports, sequences, and ACLs.
5. Create inherited patient extension file.
6. Remove lab imports and references from `hospital_management`.
7. Move manifest references from `hospital_management` to `hospital_laboratory`.
8. Upgrade `hospital_management`.
9. Install `hospital_laboratory`.
10. Verify Patient smart buttons and tabs.
11. Verify Laboratory app.
12. Verify reports.
13. Verify ACLs.
14. Verify audit logs.
15. Remove duplicate menus/actions if needed.

## 14. When to Perform the Split
Perform the split after the Laboratory workflow is manually tested in the Odoo UI and before building Pharmacy or Billing.

This timing avoids splitting an unstable workflow while still preventing Pharmacy and Billing from becoming tightly coupled to laboratory code inside the core module.

## 15. Recommendation
Do not split immediately unless manual Laboratory testing passes.

The next coding task can be Task 22 Radiology Request Foundation, but the Laboratory module split should happen before Pharmacy or Billing work begins.
