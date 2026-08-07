# Hospital ERP UI Pattern v1

## 1. Purpose
This document defines the reusable UI pattern for Ethiopian Hospital ERP pages and future modules. It captures the approved direction from the Patient profile page, Appointment form page, emerald green hospital ERP theme, and realistic Odoo XML + SCSS implementation style.

Future pages should use this as the first design reference before adding new form layouts, workflows, reports, or module-specific pages.

## 2. Core Design Identity
- Primary color: Emerald Green `#006A4F`
- Hover/darker green: `#005A43`
- Light emerald background: `rgba(0, 106, 79, 0.08)`
- Main background: light gray / Odoo default
- Content panels: white
- Text: dark gray / near black
- Borders: light gray or subtle emerald border
- Design tone: professional, clean, medical, and Odoo-compatible

The visual language should feel like a hospital ERP: clear, calm, readable, and focused on operational work rather than marketing-style decoration.

## 3. Global Layout Pattern
- Use an emerald top navbar.
- Use the MUK-style emerald sidebar already approved for the project.
- Keep the main content area white or Odoo-neutral light gray.
- Preserve Odoo breadcrumbs and standard record headers.
- Preserve standard Odoo action buttons where possible.
- Do not create custom web client pages unless absolutely necessary.
- Prefer native Odoo XML views with scoped SCSS enhancements.

## 4. Form Page Pattern
Every major form page should generally use:
- Top reference/title area
- Optional summary chips/cards
- Main two-column information card
- Notebook tabs for secondary details
- Scoped SCSS wrapper class
- Statusbar for workflows
- Odoo standard buttons

This keeps each module visually consistent while still allowing each clinical workflow to keep its own fields and business logic.

## 5. Top Reference Area
The top reference area should make the current record immediately recognizable.

Use:
- Large primary record reference/name
- Secondary code/reference below or near it
- Odoo breadcrumb above the form
- Minimal visual weight on field labels above the main title
- Emerald color for important codes, links, or references

Examples:
- Patient: `Ketema Zeleke` / `HMS0001`
- Appointment: `APP0001`
- Prescription: `RX0001`
- Laboratory Request: `LABREQ0001`
- Laboratory Result: `LABRES0001`

## 6. Summary Cards / Chips Pattern
Summary chips should show compact, current-record information near the top of the form.

Use:
- White background
- Subtle border
- Emerald icons or accent where scoped CSS allows
- Small label + highlighted value
- Only meaningful information from the current record

Avoid complicated dashboard-style computations unless the workflow truly needs them.

Good examples:
- Patient
- Doctor
- Department
- State
- Blood Group
- Priority
- Request Date

## 7. Main Information Card Pattern
Use a white card with subtle border and optional shadow for the main record fields.

Recommended structure:
- Two-column layout where possible
- Left column for identity/context
- Right column for schedule/status/summary
- Aligned, readable fields
- Reduced excessive whitespace

Appointment example:

Left:
- Appointment Code
- Patient
- Doctor
- Department

Right:
- Appointment Date
- Duration
- State
- Active

## 8. Notebook Tab Pattern
Use notebook tabs for secondary details and related information.

Guidelines:
- Active tab should use emerald accent where scoped SCSS allows.
- Keep labels short.
- Put the most frequently used tab first.
- Avoid too many tabs when smart buttons would be clearer.

Approved Patient tab examples:
- General Information
- Hospital Info
- Clinical Assessment
- Prescriptions
- Treatment Plans
- Laboratory Requests
- Laboratory Results
- Diseases
- Family
- Documents
- Notes

Approved Appointment tab examples:
- Appointment Details
- Patient Information

## 9. Workflow Statusbar Pattern
Use the native Odoo statusbar for workflow states.

Guidelines:
- Active/current state may use emerald styling where safe.
- Workflow buttons should stay standard Odoo buttons.
- The primary workflow button can use emerald styling.
- Cancel buttons should remain neutral gray.
- Avoid aggressive colors unless the state is dangerous, critical, or destructive.

## 10. Smart Button Pattern
Use standard Odoo smart buttons for related records.

Guidelines:
- Use emerald icons/accent where scoped CSS allows.
- Move crowded smart buttons under the More dropdown when needed.
- The More dropdown should match the emerald hospital styling.
- Smart buttons should open the latest related record directly where that is the approved behavior.
- When no related record exists, smart buttons may open a new form with the parent record prefilled if that matches the workflow.

## 11. SCSS Naming Convention
Use page-level wrapper classes to keep form styling scoped and predictable.

Recommended page wrapper classes:
- `hospital_patient_profile`
- `hospital_appointment_profile`
- `hospital_prescription_profile`
- `hospital_treatment_profile`
- `hospital_laboratory_request_profile`
- `hospital_laboratory_result_profile`
- `hospital_radiology_request_profile`
- `hospital_radiology_result_profile`

Generic helper classes:
- `hospital_record_card`
- `hospital_summary_row`
- `hospital_summary_chip`
- `hospital_summary_value`
- `hospital_info_section`
- `hospital_statusbar`
- `hospital_notebook`
- `hospital_form_compact`

## 12. SCSS Scope Rules
- Always scope styles to a page wrapper class.
- Do not globally override all Odoo forms.
- Do not edit Odoo core files.
- Do not edit MUK theme files.
- Avoid custom JavaScript.
- Prefer Odoo XML + SCSS.
- Keep layouts realistic for Odoo form views.

Scoped styling protects other apps and makes future module polish easier to maintain.

## 13. Recommended SCSS Tokens
Use these values or concepts consistently, even when they are not literal SCSS variables:
- Primary emerald: `#006A4F`
- Dark emerald: `#005A43`
- Light emerald background: `rgba(0, 106, 79, 0.08)`
- Border emerald: `rgba(0, 106, 79, 0.14)`
- Subtle shadow: `0 6px 18px rgba(0, 0, 0, 0.06)`
- Card radius: `8px` to `10px`

## 14. Report/Page Consistency
- Reports should use the same emerald section titles.
- Keep PDF reports simple and professional.
- Avoid heavy styling in QWeb PDFs.
- Do not add custom fonts unless necessary.
- Prioritize clean tables, readable spacing, and reliable wkhtmltopdf output.

## 15. Future Module Guidance
Future modules such as `hospital_radiology`, `hospital_pharmacy`, `hospital_billing`, and `hospital_dashboard` should follow this UI pattern.

Radiology Request should use:
- Reference area: `RADREQ0001`
- Summary chips: Patient, Doctor, Priority, State
- Two-column card: Request Information / Clinical Context
- Tabs: Requested Exams, Clinical Indication

## 16. Approved Current Examples
- Patient profile page is approved as the patient blueprint.
- Appointment page is approved as the appointment/workflow form blueprint.

These examples should guide future decisions for record identity, summary chips, information cards, notebook tabs, workflow buttons, and scoped SCSS.

## 17. Implementation Rule
Every new major form should first follow this UI Pattern v1 unless there is a strong reason to deviate.

Any deviation should be intentional, documented, and justified by the workflow or Odoo technical constraints.
