# Hospital Radiology

## Overview

`hospital_radiology` adds radiology request and result workflows for the Ethiopian Hospital ERP.

This addon depends on `hospital_management` and extends the existing patient, doctor, appointment, diagnosis, treatment plan, audit, and security foundation without modifying the core addon.

## Module Information

* Module Name: Hospital Radiology
* Technical Name: `hospital_radiology`
* Depends On: `hospital_management`
* Odoo Version: Odoo 18 Community
* Purpose: Radiology exam catalog, request workflow, result workflow, patient integration, and radiology PDF reporting.

## Features

* Radiology Exam catalog
* Radiology Request workflow
* Radiology Request Lines
* Radiology Result workflow
* Radiology Result Lines
* Patient smart buttons for radiology requests and results
* Patient form tabs for radiology records
* RADREQ sequence for radiology requests
* RADRES sequence for radiology results
* PDF reports for radiology requests and results
* Audit logging for create, update, archive, state changes, and blocked delete attempts
* ACLs using existing hospital security groups from `hospital_management`
* Separate Radiology app launcher icon

## Folder Structure

```text
hospital_radiology/
├── __init__.py
├── __manifest__.py
├── README.md
├── models/
│   ├── __init__.py
│   ├── radiology_request.py
│   ├── radiology_result.py
│   └── patient_radiology.py
├── views/
│   ├── radiology_request_views.xml
│   ├── radiology_result_views.xml
│   ├── radiology_patient_views.xml
│   └── radiology_menus.xml
├── security/
│   └── ir.model.access.csv
├── data/
│   └── radiology_sequence.xml
└── reports/
    ├── radiology_request_report.xml
    ├── radiology_request_template.xml
    ├── radiology_result_report.xml
    └── radiology_result_template.xml
```

## Models

### `hospital.radiology.exam`

Stores the radiology exam catalog.

Examples:

* Chest X-Ray
* Abdominal Ultrasound
* Brain CT Scan
* MRI Spine
* Mammography

Main fields:

* Name
* Code
* Category
* Body Part
* Description
* Active

### `hospital.radiology.request`

Stores doctor-created radiology imaging requests for patients.

Main fields:

* Request Reference
* Patient
* Physician
* Appointment
* Evaluation
* Diagnosis
* Treatment Plan
* Request Date
* Priority
* State
* Clinical Indication
* Instructions
* Active

### `hospital.radiology.request.line`

Stores requested exams under a radiology request.

Main fields:

* Request
* Exam
* Body Part
* Special Instruction
* Sequence

### `hospital.radiology.result`

Stores radiology result records after imaging is completed.

Main fields:

* Result Reference
* Radiology Request
* Patient
* Physician
* Reporter / Technician
* Result Date
* State
* Findings
* Impression
* Recommendation
* Notes
* Active

### `hospital.radiology.result.line`

Stores individual exam findings where applicable.

Main fields:

* Result
* Exam
* Body Part
* Findings
* Impression
* Notes
* Sequence

### `hospital.patient` Extension

This module extends `hospital.patient` using inheritance.

Added patient features:

* Radiology Requests smart button
* Radiology Results smart button
* Radiology Requests tab
* Radiology Results tab
* Direct opening behavior for latest related radiology record

## Workflow

### Radiology Request Workflow

```text
Draft -> Requested -> Scheduled -> Imaging Done
Draft / Requested / Scheduled -> Cancelled
Cancelled -> Draft
```

If the code uses `in_progress` instead of `imaging_done`, update this section to match the actual implementation.

### Radiology Result Workflow

```text
Draft -> Entered -> Validated -> Released
Draft / Entered -> Cancelled
Cancelled -> Draft
Released -> Draft only for Hospital Manager or Hospital System Administrator
```

## Sequences

Radiology Request:

```text
RADREQ0001
RADREQ0002
RADREQ0003
```

Radiology Result:

```text
RADRES0001
RADRES0002
RADRES0003
```

## Menus

The module creates a separate app launcher icon:

```text
Radiology
```

Expected menu structure:

```text
Radiology
├── Radiology Requests
├── Radiology Results
├── Radiology Exams
└── Configuration
    └── Radiology Exams
```

The module may also add patient shortcuts for radiology requests and results.

## Reports

### Radiology Request PDF

Includes:

* Request reference
* Patient
* Physician
* Appointment
* Diagnosis
* Treatment Plan
* Priority
* State
* Requested exams
* Clinical indication
* Instructions

Does not include:

* Findings
* Images
* DICOM
* PACS
* Billing
* Pricing
* Inventory

### Radiology Result PDF

Includes:

* Result reference
* Request reference
* Patient
* Physician
* Reporter / Technician
* Findings
* Impression
* Recommendation
* Notes

Does not include:

* DICOM/PACS integration
* Image viewer
* Billing
* Pricing
* Inventory

## Security

This module uses existing hospital security groups from `hospital_management`.

Expected access behavior:

* Doctor: create/read/write radiology requests, no delete
* Nurse: read-only
* Receptionist: read-only
* Manager: create/read/write, no delete
* System Administrator: full access
* Data Protection Officer: read-only
* Lab Technician: no radiology access unless intentionally changed
* Pharmacist: no radiology access
* Accountant: no radiology access

Normal users should not delete radiology records. Use workflow states such as Cancelled or archive behavior instead.

## Audit Logging

If reusable audit support from `hospital_management` is available, this module logs:

* Radiology request creation
* Radiology request updates
* Radiology request archive
* Radiology request state changes
* Radiology result creation
* Radiology result updates
* Radiology result archive
* Radiology result state changes
* Blocked deletion attempts

## Install Notes

Both addons must be available in Odoo's `addons_path`:

```text
hospital_management
hospital_radiology
```

Install order:

```text
1. Install or upgrade hospital_management.
2. Copy hospital_radiology into custom_addons.
3. Restart Odoo.
4. Update Apps List.
5. Install Hospital Radiology.
```

## Manual Test Checklist

1. Restart Odoo.
2. Update Apps List.
3. Install `hospital_radiology`.
4. Confirm the Radiology app appears once in the app launcher.
5. Open Radiology app.
6. Create Radiology Exams:

   * Chest X-Ray
   * Abdominal Ultrasound
   * Brain CT Scan
7. Open an existing patient.
8. Confirm Radiology Requests smart button appears.
9. Create a Radiology Request from the patient.
10. Confirm patient is prefilled.
11. Select physician, appointment, diagnosis, and treatment plan if available.
12. Add requested exam lines.
13. Save the request.
14. Confirm sequence is assigned, for example `RADREQ0001`.
15. Test request workflow:

* Confirm Request
* Schedule Imaging
* Mark Imaging Done or In Progress, depending on actual code
* Cancel
* Reset to Draft

16. Print Radiology Request PDF.
17. Create a Radiology Result from the request.
18. Confirm patient and request details are linked correctly.
19. Add findings/impression/recommendation.
20. Save the result.
21. Confirm sequence is assigned, for example `RADRES0001`.
22. Test result workflow:

* Mark Entered
* Validate
* Release
* Cancel
* Reset to Draft where allowed

23. Print Radiology Result PDF.
24. Return to the patient.
25. Confirm Radiology Requests count updates.
26. Confirm Radiology Results count updates.
27. Confirm Radiology Requests tab works.
28. Confirm Radiology Results tab works.
29. Confirm no PDF rendering error occurs.
30. Confirm normal users cannot delete radiology records.

## Validation

Static validation completed:

* Python files parse successfully.
* XML files parse successfully.
* CSV access file parses successfully.
* Manifest file references are present.
* Core `hospital_management` files are not modified by this addon.

Live Odoo validation still required:

* Module installation
* Patient inherited view rendering
* Workflow button behavior
* PDF rendering through wkhtmltopdf
* Role-based access testing

## Known Limitations

This module does not currently include:

* DICOM integration
* PACS integration
* Image viewer
* Radiology machine integration
* Billing or pricing
* Insurance claims
* Advanced reporting dashboard
* External national interoperability

## Next Recommended Task

After manual testing, the next task should be:

```text
Radiology Workflow Stabilization
```

Then continue with:

```text
Pharmacy Foundation
```



## Shared YOYA Hospital PDF Header

Radiology Request and Radiology Result PDF templates now call `hospital_management.hospital_report_header`; the radiology addon does not duplicate the shared header or logo. The dependency on `hospital_management` guarantees the shared template is available.

Both reports use the shared YOYA logo/header, `#F3F8F6` table headings, green section headings, and the standard YOYA generated-report footer. Appointment and diagnosis links render through `display_name` to avoid assumptions about a concrete `name` field. XML declarations and report XML parsing were statically validated.

### Manual PDF test checklist

1. Upgrade `hospital_management`, then upgrade `hospital_radiology`.
2. Print Radiology Request and Radiology Result PDFs.
3. Confirm the shared green header and proportional YOYA logo render correctly.
4. Confirm existing report content remains present and there are no QWeb/RPC errors.
5. Confirm linked appointment and diagnosis values do not raise `KeyError: name`.