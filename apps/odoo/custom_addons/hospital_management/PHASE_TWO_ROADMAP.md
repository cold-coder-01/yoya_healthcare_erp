# Ethiopian Hospital ERP - Phase Two Roadmap

## 1. Phase Two Overview
- Project name: Ethiopian Hospital ERP
- Odoo version: Odoo 18 Community
- Current addon: `hospital_management`
- Phase Two name: Clinical Services Foundation
- Goal: Extend Phase One into prescriptions, treatment plans, laboratory requests and results, radiology requests and results, pharmacy preparation, billing preparation, and a clinical dashboard foundation.

Phase Two should build practical clinical service workflows on top of the Phase One patient foundation while preserving the compliance-first approach already established in the module.

## 2. Phase One Foundation Recap
Phase One already includes the core hospital foundation:
- Patient profile
- Doctor/physician setup
- Departments
- Appointments
- Patient consents
- Audit logs
- Patient evaluations
- Clinical assessment summary
- Diseases and diagnoses
- Patient tags
- Family members
- Patient documents
- Patient Profile PDF report
- Emerald hospital UI styling

## 3. Phase Two Scope Lock
Allowed Phase Two features:
- Prescription Foundation
- Treatment Plan Foundation
- Laboratory Request and Result Foundation
- Radiology Request and Result Foundation
- Pharmacy Preparation/Foundation
- Service Catalog and Billing Preparation
- Clinical Dashboard Foundation

Excluded Phase Two features:
- Full accounting implementation
- Full inventory valuation
- Insurance claim processing
- Inpatient hospitalization
- Surgery/theater management
- Emergency department workflows
- Blood bank
- National EHR/HIE integration
- SMS/Email patient notifications
- Mobile app
- Public patient portal
- Advanced analytics/AI
- Payment gateway integration

Phase Two must remain focused on clinical service foundations only. Any excluded feature requires explicit approval before analysis or implementation.

## 4. Recommended Phase Two Build Order

### 5. Prescription Foundation
Purpose:
Create the first medication-ordering workflow connected to patients, physicians, appointments, and diagnoses.

Main models likely needed:
- `hospital.prescription`
- `hospital.prescription.line`

Main menus/views likely needed:
- Prescriptions list, form, and search views
- Prescription lines inside the prescription form
- Patient or Physician menu entry
- Optional patient smart button after the workflow is stable

Dependencies on Phase One:
- `hospital.patient`
- `hospital.doctor`
- `hospital.appointment`
- `hospital.patient.diagnosis`
- Existing sequence and audit-log patterns

Important security/compliance notes:
- Prescriptions are sensitive clinical records.
- Normal operational users should not delete confirmed prescriptions.
- State transitions should be audited.
- Cancellation should be preferred over hard deletion.
- Role permissions must separate clinical creation from pharmacy dispensing readiness.

Expected deliverables:
- Prescription parent and line model foundation
- Manual medicine text entry
- Draft, confirmed, dispensed, and cancelled states
- Access rights
- Views and menus
- README update and validation notes

### 6. Treatment Plan Foundation
Purpose:
Provide a structured way for clinicians to document planned patient care beyond one-time prescriptions.

Main models likely needed:
- `hospital.treatment.plan`
- `hospital.treatment.plan.line` or treatment activity lines

Main menus/views likely needed:
- Treatment Plans list, form, and search views
- Treatment activities tab on the treatment plan
- Patient or Physician menu entry
- Optional patient smart button

Dependencies on Phase One:
- `hospital.patient`
- `hospital.doctor`
- `hospital.appointment`
- `hospital.patient.diagnosis`
- Patient evaluations and clinical assessment summary

Important security/compliance notes:
- Treatment plans should preserve clinical decision history.
- Confirmed or completed plans should not be casually deleted.
- Changes to active plans should be auditable.
- Role validation is needed before production use.

Expected deliverables:
- Treatment plan model foundation
- Workflow states such as draft, active, completed, cancelled
- Treatment activity structure
- Access rights, views, menus, and README update

### 7. Laboratory Request and Result Foundation
Purpose:
Start laboratory workflow with controlled test requests and structured result capture.

Main models likely needed:
- `hospital.lab.request`
- `hospital.lab.request.line`
- `hospital.lab.result`
- `hospital.lab.result.line`

Main menus/views likely needed:
- Lab Requests list, form, and search views
- Lab Results list, form, and search views
- Request and result line tables
- Future Laboratory app icon only if the workflow becomes large

Dependencies on Phase One:
- `hospital.patient`
- `hospital.doctor`
- `hospital.appointment`
- `hospital.patient.diagnosis`
- Patient documents for possible attachments later

Important security/compliance notes:
- Lab results are sensitive health data.
- Result confirmation should be protected.
- Normal users should not delete lab requests or results.
- Audit result confirmation, cancellation, and major edits.

Expected deliverables:
- Lab request foundation
- Lab result foundation
- Workflow states for request and result handling
- Access rights, views, menus, and README updates

### 8. Radiology Request and Result Foundation
Purpose:
Create a basic imaging request and report workflow without PACS integration.

Main models likely needed:
- `hospital.radiology.request`
- `hospital.radiology.request.line`
- `hospital.radiology.result`

Main menus/views likely needed:
- Radiology Requests list, form, and search views
- Radiology Results list, form, and search views
- Imaging study lines or requested study section
- Future Radiology app icon only if the workflow becomes large

Dependencies on Phase One:
- `hospital.patient`
- `hospital.doctor`
- `hospital.appointment`
- `hospital.patient.diagnosis`
- Patient documents for possible report attachments later

Important security/compliance notes:
- Radiology reports are protected clinical records.
- Images should not be integrated in Phase Two unless explicitly approved.
- Report confirmation and cancellation should be audited.
- Deletion should be restricted.

Expected deliverables:
- Radiology request foundation
- Radiology result/report foundation
- Workflow states
- Access rights, views, menus, and README updates

### 9. Pharmacy Preparation/Foundation
Purpose:
Prepare the module for future pharmacy operations without implementing full stock, valuation, or dispensing inventory logic.

Main models likely needed:
- `hospital.pharmacy.queue` or prescription dispensing preparation model
- Optional medicine catalog placeholder model only if needed and approved

Main menus/views likely needed:
- Pharmacy preparation queue
- Prescription review view or filtered prescription menu
- Future Pharmacy app icon only when the workflow becomes large

Dependencies on Phase One:
- Patients and physicians
- Prescription foundation from Task 18
- Existing security groups, including Pharmacist

Important security/compliance notes:
- Pharmacists should see required prescription details but not unrelated patient data.
- Dispensing preparation must not imply full inventory valuation.
- Confirmed prescription handling should be audited.

Expected deliverables:
- Pharmacy preparation workflow outline
- Controlled prescription review path
- No full inventory implementation
- Access rights, views, menus, and README update

### 10. Service Catalog and Billing Preparation
Purpose:
Create a foundation for hospital billable services without implementing full accounting or payment workflows.

Main models likely needed:
- `hospital.service.catalog`
- `hospital.service.charge` or billing preparation record

Main menus/views likely needed:
- Service Catalog list, form, and search views
- Billing preparation list and form views
- Future Billing app icon only when accounting workflows are approved

Dependencies on Phase One:
- Patients
- Appointments
- Prescriptions, lab, radiology, and treatment records as future charge sources
- Future Odoo Product and Accounting integration readiness

Important security/compliance notes:
- Billing preparation should not expose unnecessary clinical detail to accounting users.
- Full invoicing and payment processing are excluded.
- Price and charge changes should be controlled by appropriate roles.

Expected deliverables:
- Service catalog foundation
- Billing preparation model or workflow
- No full accounting implementation
- Access rights, views, menus, and README update

### 11. Clinical Dashboard Foundation
Purpose:
Provide a simple operational overview of clinical activity without advanced analytics or AI.

Main models likely needed:
- No new persistent model unless dashboard data cannot be computed safely through existing records.
- Optional transient model or action context only if needed.

Main menus/views likely needed:
- Clinical dashboard menu or kanban/pivot-style overview
- Basic counts for appointments, prescriptions, lab requests, radiology requests, and treatment plans

Dependencies on Phase One:
- Patients
- Appointments
- Evaluations
- Diagnoses
- Phase Two clinical workflow records

Important security/compliance notes:
- Dashboard visibility must respect user roles.
- Avoid exposing sensitive details in aggregate dashboards to unauthorized users.
- Do not build advanced analytics or AI in Phase Two.

Expected deliverables:
- Basic clinical dashboard foundation
- Role-aware overview
- README update and validation notes

### 12. Final Phase Two Cleanup and Testing
Purpose:
Review all Phase Two work as a complete clinical foundation before handover.

Main models likely needed:
- No new models expected.

Main menus/views likely needed:
- No new feature views expected unless fixing Phase Two issues.

Dependencies on Phase One:
- Full Phase One foundation
- All completed Phase Two tasks

Important security/compliance notes:
- Recheck access rights, deletion restrictions, workflow states, and audit coverage.
- Confirm sensitive clinical records are protected.

Expected deliverables:
- Static validation of Python, XML, CSV, and manifest
- Menu and action reference checks
- Security review
- README update
- Manual Odoo UI checklist

### 13. Phase Two Handover Summary
Purpose:
Document completed Phase Two scope, known limitations, validation status, and next recommended phase.

Main models likely needed:
- No new models expected.

Main menus/views likely needed:
- No new menus or views expected.

Dependencies on Phase One:
- Phase One handover document
- Final Phase Two cleanup and testing results

Important security/compliance notes:
- Clearly identify remaining production validation needs.
- Document excluded features that remain out of scope.

Expected deliverables:
- Phase Two handover document
- README final Phase Two status update
- Recommended Phase Three direction

## 5. Task 18 Preview: Prescription Foundation
Task 18 should begin Phase Two coding with the Prescription Foundation.

Expected prescription model ideas:
- `hospital.prescription`
- `hospital.prescription.line`

Possible `hospital.prescription` fields:
- Prescription reference
- Patient
- Physician
- Appointment
- Diagnosis
- Prescription date
- State: draft, confirmed, dispensed, cancelled
- Notes

Possible `hospital.prescription.line` fields:
- Medicine name or product reference placeholder
- Dosage
- Frequency
- Duration
- Route
- Quantity
- Instructions

Implementation guidance:
- Use medicine text/manual entry first because full Pharmacy is not built yet.
- Keep the model ready for future product integration by designing the medicine field so an Odoo Product relation can be added later without disrupting the early workflow.
- Use a sequence for prescription references.
- Link prescriptions to `hospital.patient` and `hospital.doctor`.
- Appointment and diagnosis links should support clinical context but should not be mandatory unless workflow testing proves they must be.
- Add workflow states before adding reports.
- Restrict deletion for normal users and prefer cancellation.
- Audit important prescription events such as confirmation, dispensing, and cancellation.
- Keep Prescription and Treatment under Patient or Physician menus initially, not as separate app launcher icons.

## 6. Data Model Strategy
- Continue using one addon, `hospital_management`, for now unless a workflow becomes large enough to separate later.
- Keep Phase Two models linked to `hospital.patient` where relevant.
- Use sequence numbers for major clinical records.
- Avoid hard-coding values that should become configuration later.
- Keep future Odoo Product and Odoo Accounting integration in mind.
- Keep clinical models small enough to validate in real hospital workflows before adding advanced configuration.
- Avoid premature coupling to Inventory, Accounting, or external systems during Phase Two.

## 7. Menu/App Strategy
Recommended app launcher icons for Phase Two:

Keep existing Phase One icons:
- Patient
- Physician
- Appointment
- Consent Form

For Phase Two:
- Do not immediately create many app icons.
- Add early clinical menus under Patient or Physician first where practical.
- Prescription and Treatment can initially be menus under Patient or Physician, not separate app icons.
- Create separate app icons only when a workflow becomes large, such as:
  - Laboratory
  - Radiology
  - Pharmacy
  - Billing

This keeps the Odoo interface realistic and prevents the app launcher from becoming crowded before the workflows mature.

## 8. Security and Compliance Strategy
- Health data remains sensitive.
- Normal users should not delete clinical records.
- Use archive/cancel states instead of unlink.
- DPO/audit role should retain compliance visibility.
- Audit important workflow events.
- Prescriptions, lab results, and radiology results should be protected.
- Real hospital role validation is required before production.
- Access rights should be added with each workflow and reviewed again during final Phase Two cleanup.
- Confirmed clinical records should be harder to modify than draft records where practical.

## 9. Reporting Strategy
Phase Two reports may include:
- Prescription printout
- Lab request slip
- Lab result report
- Radiology request slip
- Radiology result report
- Treatment plan printout
- Patient clinical summary

Reports should be added after each workflow is stable, not before. Early tasks should prioritize correct models, states, security, menus, and form behavior before PDF output.

## 10. Integration Readiness
Phase Two should remain ready for future integration with:
- Odoo Product for medicines/services
- Odoo Inventory for pharmacy stock
- Odoo Accounting/Invoicing for billing
- Laboratory machine integrations later
- Radiology/PACS integration later
- National interoperability later

No external integration will be implemented in Phase Two unless explicitly approved.

## 11. Phase Two Risks
- Scope creep
- Too many app icons
- Over-custom CSS
- Weak security model
- Missing workflow states
- Complex pharmacy/billing too early
- PDF report errors
- User-role confusion

These risks should be checked after every Phase Two task and again during the final cleanup task.

## 12. Development Rules for Phase Two
- One task at a time
- `README.md` must be updated after every task
- Validate Python/XML/CSV/manifest after every task
- Avoid cleanup after every small feature unless needed
- Do final cleanup at end of Phase Two
- Do not build large unrelated features in one task
- Keep UI realistic for Odoo
- Keep compliance-first logic
- Do not edit Odoo core or MUK theme files

## 13. Proposed Task List
- Task 17: Phase Two Technical Roadmap and Scope Lock
- Task 18: Prescription Foundation
- Task 19: Treatment Plan Foundation
- Task 20: Laboratory Request Foundation
- Task 21: Laboratory Result Foundation
- Task 22: Radiology Request Foundation
- Task 23: Radiology Result Foundation
- Task 24: Pharmacy Preparation/Foundation
- Task 25: Service Catalog and Billing Preparation
- Task 26: Clinical Dashboard Foundation
- Task 27: Phase Two Final Cleanup and Testing
- Task 28: Phase Two Handover Summary

## 14. Recommendation
Start Phase Two coding with Task 18: Prescription Foundation.
