# Hospital Insurance / Corporate Credit Patient Foundation

Foundation module for the Ethiopian Hospital ERP (YOYA Hospital) that manages
patients whose bills are paid — fully or partially — by insurance companies,
corporate employers, NGOs / donors, government programs, credit agreements, or
institutional sponsors.

This is a **foundation** task: it establishes payer master data, patient
coverage, guarantee letters, and the claim lifecycle, and wires them into the
existing patient bill without changing the cash billing math and **without
posting any accounting entries**.

## Overview

A hospital bill may be:

1. **Cash / self pay** — settled by the patient (unchanged behaviour).
2. **Fully covered** by an insurance / corporate payer.
3. **Partially covered** — payer pays a share, patient pays the rest.
4. **Pending authorization / guarantee** — awaiting a payer guarantee letter.
5. **Rejected by payer** — converted back to patient responsibility.

## Dependencies

- `hospital_management`
- `hospital_billing`
- `hospital_admission`
- `mail`

`hospital_billing_accounting` is **not** required. `hospital_operation_theatre`
is not a dependency.

## Models

| Model | Purpose |
| --- | --- |
| `hospital.insurance.provider` | Payer master data (insurance / corporate / government / NGO / donor / credit). |
| `hospital.patient.coverage` | Patient-specific coverage record (COV sequence). |
| `hospital.guarantee.letter` | Payer guarantee / authorization letter (GUAR sequence). |
| `hospital.insurance.claim` | Claim header created from a patient bill (CLAIM sequence). |
| `hospital.insurance.claim.line` | Claim lines copied from bill lines. |
| `hospital.patient.bill` *(extended)* | Payer fields, responsibility amounts, claim actions. |
| `hospital.patient` *(extended)* | Coverage / guarantee / claim smart buttons and tab. |
| `hospital.admission` *(extended)* | Coverage / guarantee links and claim smart button. |

## Insurance / Corporate Payer Workflow

Create a payer under **Insurance → Providers / Payers** (or Configuration).
Set the payer type, default coverage percent, guarantee / pre-authorization
requirements, payment terms, and credit limit. Display name is `[CODE] Name`
when a code is set.

## Patient Coverage Workflow

`draft → active → suspended → expired`, with `cancelled` reachable from
draft/active/suspended and `reset to draft` from suspended/cancelled.
`is_active_coverage` reflects state plus the start/end date window.

## Guarantee Letter Workflow

`draft → submitted → approved` (or `rejected`), with `expired` from
submitted/approved, `cancelled` from draft/submitted/approved, and reset to
draft from rejected/cancelled. Approval records the approver and timestamp and
requires a payer and a positive authorized amount. `used_amount` /
`remaining_amount` are computed from linked non-cancelled claims. Only an
approved, non-expired guarantee can be used to create a claim.

## Claim Workflow

`draft → submitted → under_review → approved | partially_approved → paid`, plus
`rejected` and `cancelled`. Rules:

- A claim requires bill, payer, and patient before submission.
- Approving with no approved amount defaults it (and the lines) to the full
  claimed amount; the approved amount can never exceed the claim amount.
- `partially_approved` when approved amount is below the claim amount.
- `rejected_amount = claim_amount - approved_amount`.
- `amount_due_from_payer = approved_amount - amount_paid_by_payer`.
- The claim is `paid` when `amount_paid_by_payer >= approved_amount` and the
  approved amount is positive.
- A duplicate active (non-cancelled) claim for the same bill and payer is
  blocked.

## Bill Integration

The patient bill gains an **Insurance / Payer** tab and header buttons:

- **Create Insurance Claim** — copies bill lines into a new claim (blocked for
  cash bills, when a payer/guarantee requirement is unmet, or when an active
  claim already exists).
- **View Insurance Claim** — opens the linked claim.
- **Mark Patient Responsibility** — sets the bill insurance state.

Responsibility amounts are computed and **informational**:

- `payer_responsibility_amount = amount_total * coverage_percent / 100`
- `patient_responsibility_amount = amount_total - payer_responsibility_amount`

For cash bills the insurance state is `not_applicable`. The existing
`amount_paid` / `amount_due` billing math is **not** modified, so cash billing
continues to work exactly as before.

### Settlement mode and the fully-paid rule

Each non-cash bill carries a `claim_settlement_mode`:

- **Direct to Hospital** (default): the payer owes the hospital. The patient
  pays only the uncovered share and the hospital creates a claim for the payer
  share.
- **Patient Reimbursement**: the patient has paid the hospital directly and
  claims reimbursement from the payer personally — the hospital does **not**
  create a receivable claim. Clicking *Create Insurance Claim* in this mode is
  blocked; print the bill/receipt as the patient's supporting documentation.

**Fully paid bill rule:** a patient bill that is already fully paid
(`amount_due` settled and `amount_paid >= amount_total`, compared with the bill
currency's rounding) cannot be converted into a direct payer claim, because
there is no payer receivable left. This is a patient-reimbursement case, not a
hospital-to-payer claim.

Insurance claims support **surgery** bill lines: surgery claim lines are copied
from `hospital.patient.bill.line` records whose `source_type` is `surgery`
(alongside consultation, laboratory, radiology, pharmacy, procedure, admission,
and other). Claim creation is defensive — a bill line source type the claim line
does not recognise is stored as `other` with the original value preserved in the
claim line notes, so a claim is never blocked by a raw `ValueError`.

## Patient / Admission Integration

The patient form shows **Coverage**, **Guarantees**, and **Claims** smart
buttons and an **Insurance / Coverage** tab. Admissions can reference a coverage
and guarantee letter and show a **Claims** smart button.

## Accounting Readiness

This module does **not** post `account.move` entries and does not create any
accounts or journals. Claims track operational receivables only.

Future accounting (out of scope here):

- **Cash patient:** Dr Cash/Bank · Cr Patient Receivable.
- **Insurance claim approved:** payer receivable may need reclassification from
  patient receivable to an Insurance / Corporate Receivable account.
- A future task can add: insurance/corporate receivable accounts, claim payment
  posting, and claim write-off / rejection adjustment entries.

## Security Roles

Existing `hospital_management` groups only:

- **Receptionist:** create/read/write coverages, guarantees, claims, and bill
  insurance fields; read payers.
- **Accountant:** read/write claims (and payment amounts); read payers/coverages.
- **Doctor / Nurse / Pharmacist / Lab Technician:** read-only insurance status.
- **Manager:** create/read/write everything, no delete.
- **Data Protection Officer:** read-only.
- **System Administrator:** full access including delete.

`unlink` is overridden on claims (approved/partially approved/paid) and on
approved guarantee letters so non-administrators cannot delete them.

## Report

**Insurance Claim Summary** PDF (`hospital.insurance.claim`) using the shared
`hospital_management.hospital_report_header`: patient, admission, payer,
coverage, guarantee, bill reference, state, amounts, claim lines, and
notes / rejection reason.

## Manual Test Checklist

1. Module installs / upgrades; the **Insurance** app appears.
2. Create a payer.
3. Create and activate a patient coverage.
4. Create and approve a guarantee letter.
5. Mark a patient bill as insurance / corporate and set the payer.
6. Verify payer and patient responsibility amounts compute.
7. Create an insurance claim from the bill; confirm lines copy over.
8. Run submit → approve → register payer payment; confirm state `paid`.
9. Confirm a duplicate claim is blocked.
10. Confirm patient smart buttons open the right records.
11. Print the Insurance Claim Summary PDF.
12. Confirm existing cash billing still works and **no `account.move`** is
    created.

## Known Limitations

- No external insurer API / EDI.
- No electronic claim submission.
- No automatic claim adjudication.
- No package pricing.
- No deductible / copay rules beyond a simple coverage percent.
- No payer receivable journal posting yet.
- No write-off accounting yet.
- No insurance dashboard yet.

## Next Recommended Tasks

- Payer receivable / corporate receivable accounting integration.
- Claim payment reconciliation and write-off adjustments.
- Insurance analytics / dashboard.
- Ethiopian insurer-specific integrations and electronic claim submission.
