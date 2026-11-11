# Hospital Operation Theatre

Surgery and operating-room workflow for the **Ethiopian Hospital ERP** (Odoo 18 Community).
Part of the YOYA Hospital module suite. This is the **foundation** task (Task 32): it
delivers the full surgical case lifecycle, clinical documentation, theatre scheduling and a
**safe, button-driven** integration with `hospital_inventory`. It does **not** post accounting
entries or create bills automatically.

## Overview

The module manages the end-to-end surgical case:

* Surgery request (the main case document)
* Pre-op safety checklist
* Operation theatre setup and scheduling
* Surgical team assignment
* Anesthesia notes
* Operation report
* Post-op notes
* Surgery-related inventory consumption with valued stock movements
* Patient and admission linkage
* Billing / accounting *readiness* (no posting yet)

## Dependencies

```
hospital_management, hospital_admission, hospital_nursing,
hospital_inventory, hospital_billing, mail
```

The module deliberately does **not** depend on `stock`, `purchase`, `account`,
`om_account_accountant`, or `hospital_procedure`. Inventory is handled entirely through
`hospital_inventory`'s `hospital.stock.consumption` / `hospital.stock.movement` models.

## Models

| Model | Purpose |
|-------|---------|
| `hospital.operation.theatre` | Operating room master + theatre store location |
| `hospital.surgery.type` | Surgery catalog (price, requirements, default materials) |
| `hospital.surgery.material.line` | Default material template lines on a surgery type |
| `hospital.surgery.request` | Main surgery case document + workflow |
| `hospital.surgery.team.member` | Surgical team assignment lines |
| `hospital.preop.checklist` | Pre-operative safety/readiness checklist |
| `hospital.anesthesia.note` | Anesthesia documentation |
| `hospital.operation.report` | Formal operation report (locked when finalized) |
| `hospital.postop.note` | Post-operative clinical notes |

**Extended models** (via `_inherit`):

* `hospital.stock.consumption` — adds a `surgery` consumption type and `surgery_id`.
* `hospital.stock.movement` — adds `surgery_id`; populated when consumption is consumed.
* `hospital.patient.bill.line` — adds `surgery` to `source_type` (Task 32A).
* `hospital.surgery.request` — inventory One2manys, counts, cost computes, inventory actions.
* `hospital.patient` — `surgery_ids`, `surgery_count`, `action_view_surgeries` + smart button + tab.
* `hospital.admission` — `surgery_ids`, `surgery_count`, `action_view_surgeries` + smart button.

## Surgery workflow

```
draft → requested → scheduled → preop_ready → in_theatre
      → operation_done → postop_done → completed
(any of draft/requested/scheduled/preop_ready) → cancelled → draft
```

Workflow rules enforced:

* **Start Operation** requires a theatre and a planned start time.
* **Mark Pre-op Ready** requires a pre-op checklist that is *completed*.
* **Complete Operation** requires an anesthesia note (or `anesthesia_status = documented`)
  when the surgery type requires anesthesia.
* **Complete Case** requires an operation report to exist.
* Completed/cancelled cases lock critical clinical fields (reset to draft to correct).

## Pre-op checklist

A dedicated checklist with boolean safety items (identity, consent, fasting, allergies,
site marking, blood prep, equipment/instrument readiness, infection prevention, etc.).
States: `draft → completed`, with `cancelled` and reset. Completing the checklist updates
the surgery's `preop_status`.

## Operation theatre scheduling

`Theatre Schedule` menu opens a calendar on `hospital.surgery.request` using
`planned_start` / `planned_end`, colored by theatre, plus a list grouped by theatre.
The Surgery Requests action also exposes a calendar view.

## Surgical team

Inline team lines on the surgery form with role (lead surgeon, assistant, anesthetist,
scrub/circulating nurse, technician, observer, other), physician, user and free label.
A lead surgeon is *recommended* but not hard-blocked.

## Anesthesia notes

Type, anesthetist, times, pre-anesthesia/airway assessment, medications, monitoring,
complications and recovery. States: `draft → documented`, plus cancel/reset. Documenting
sets the surgery's `anesthesia_status`.

## Operation report

Full operative report (diagnoses, procedure, incision, findings, steps, specimens,
implants, drains, closure, blood loss, complications, recommendations). States:
`draft → finalized`, plus cancel/reset. Finalized/cancelled reports lock clinical fields.

## Post-op notes

Recovery status, pain score (0-10), vitals, wound/bleeding, medication and nursing/surgeon
instructions, follow-up. States: `draft → submitted → reviewed`, plus cancel/reset.

## Inventory integration

Inventory is **button-driven and never automatic**. The flow is:

```
Theatre Store location (on the theatre)
  → Prepare Inventory Consumption  (creates a draft hospital.stock.consumption)
  → manual batch selection in Inventory
  → Approve + Consume               (deducts batch, creates valued hospital.stock.movement)
  → movement stamped with surgery_id → visible as Patient Inventory Usage
```

* `consumption_type = surgery` (added safely via `selection_add`).
* Consumption header carries patient, admission, department, `surgery_id`, the theatre
  store as source location, and the default virtual consumption location.
* Lines are pre-filled from the surgery type's material template; batches are left empty
  for **manual selection** (no FIFO/FEFO).
* `action_consume` is overridden to stamp `surgery_id` on the resulting movements.
* **Estimated material cost** = Σ `default_quantity × item.standard_cost` (from the type).
* **Actual material cost** = Σ `movement_value` of consumption movements linked to the surgery.

> **Note:** Actual consumption cost is calculated from the **selected batch**. Unit Cost,
> Available Qty and Line Value populate once a Batch/Lot is chosen on each consumption line, so
> select a Batch/Lot before approving/consuming. A surgery consumption links only to the surgery,
> patient, admission, department and theatre store — never to procedure/pharmacy/nursing/lab/
> radiology source documents.

### Single-preparation rule (no duplicate consumption)

Surgery inventory consumption is **single-preparation by default**. *Prepare Inventory
Consumption* only creates a record when the surgery has **no** existing consumption
(draft/approved/consumed). Once one exists:

* The header shows **View Inventory Consumption** instead of *Prepare* (and the *Prepare* button
  is hidden when `inventory_consumption_count > 0`).
* Re-running the prepare action **opens the existing consumption** (or, if earlier testing left
  several, the filtered list) and posts a chatter note — it never creates a duplicate. This guard
  is enforced server-side in `action_prepare_inventory_consumption`, so duplicate creation is
  blocked even if the UI is bypassed.

Additional surgery material consumption is **not** allowed automatically. A future
*"Additional Material Consumption"* flow (with manager approval) can add more consumptions
deliberately.

> **If duplicate consumptions were created during testing before this fix:** do **not** delete the
> stock movements directly and do **not** auto-reverse consumed stock. Either start a fresh surgery
> case for clean testing, or perform a controlled stock adjustment / opening correction in a test
> database. Automated reversal is a future task.

Smart buttons on the surgery: Pre-op Checklist, Anesthesia Note, Operation Report,
Post-op Notes, Inventory Consumptions, Inventory Movements, Patient Bill (when linked).

## Patient / admission integration

* Patient form: **Surgeries** smart button + a **Surgeries** tab.
* Admission form: **Surgeries** smart button.

## Surgery billing automation (Task 32A)

Surgery billing creates a normal `hospital.patient.bill`. **No accounting entry / `account.move`
is posted from surgery** — posting stays with the existing `hospital_billing_accounting` bridge.

**Billing fields on `hospital.surgery.request`:**

* `default_price` — surgery service price (from the surgery type).
* `estimated_material_cost`, `actual_material_cost`, `total_estimated_cost`.
* `material_cost_difference` = `actual_material_cost − estimated_material_cost`.
* `billable_amount` = service price (+ actual material cost when it is billed).
* `include_material_cost_in_bill` (default **False**), `material_cost_reviewed` (default **False**),
  `material_cost_review_note`, `billing_note`.
* `bill_id`, `billing_state` (computed: `not_billed / billed / partial / paid / cancelled`).

**Billing timing.** *Generate Surgery Bill* is only available when the surgery is
`operation_done`, `postop_done` or `completed`. It is hidden/blocked in
`draft / requested / scheduled / preop_ready / in_theatre`.

**Generate Surgery Bill** (`action_generate_surgery_bill`):

1. Blocks if a bill already exists — *"This surgery already has a patient bill."*
2. Requires patient, surgery type, and a surgery price > 0
   (*"Please set a surgery price before generating the bill."*).
3. Always adds a **service** line: `Surgery - <type> - <ref>`, `source_type = surgery`,
   qty 1, unit price = surgery price.
4. Adds a **materials** line (`Surgery Materials - <ref>`, `source_type = surgery`,
   unit price = `actual_material_cost`) **only when** `include_material_cost_in_bill` is ticked.
   If ticked but `actual_material_cost` is 0 →
   *"Cannot add material cost to the bill because no consumed material cost has been recorded."*
5. Creates the bill (patient, lead surgeon as physician if assigned, bill date today,
   cashier = current user, currency, billing note) and links `bill_id`.
6. Opens the created bill.

**Duplicate protection.** A second generation attempt raises
*"This surgery already has a patient bill."* (also enforced by hiding the button once `bill_id` is set).

**View Patient Bill** (`action_view_patient_bill`) opens the linked bill.

**Inventory cost review.** The Billing and Inventory/Materials tabs show Estimated vs Actual
material cost, the Difference, the *Material Cost Reviewed* / *Include Material Cost in Bill*
flags and a review note — so staff can decide whether to bill consumables separately. Material
cost is **never** billed as revenue automatically; it is opt-in per surgery.

## Accounting readiness

No journal entries are posted. The generated bill lines carry `source_type = surgery` so the
existing `hospital_billing_accounting` bridge can map them to a **Surgery Revenue** account when
the bill is posted later. Stock movements carry a valuation (`movement_value`) and an
`inventory_accounting_state` flag for a future inventory bridge.

**Required accounting bridge config (create later in Accounting — not created automatically):**

* Account — Code `401107`, Name `Surgery Revenue`, Type *Income / Revenue*.
* Accounting Bridge config:
  * Source Type: `surgery`
  * Revenue Account: Surgery Revenue
  * Receivable Account: Patient Receivable
  * Bill Journal: Hospital Sales Journal
  * Payment Journal: Cash / Bank / Mobile as applicable

> Note: `source_type = surgery` is added to `hospital.patient.bill.line` by this module via
> `_inherit`. The bridge **config** dropdown now exposes `surgery` as well — Task 32B added
> `surgery` to the `hospital.billing.accounting.config` source-type selection inside
> `hospital_billing_accounting` (no reverse dependency, no `account.move` created from surgery).
> Once a config row maps `surgery` → Surgery Revenue, posting the bill yields:
> **Debit** Patient Receivable, **Credit** Surgery Revenue.

Future direction: consumed-material COGS/expense and inventory-asset credit through a future
inventory/accounting bridge.

## Security roles

Uses existing `hospital_management` groups only (no new groups). Summary:

* **Doctor**: create/read/write surgery requests, operation reports, post-op notes, team.
* **Nurse**: read/write surgery requests; create/write pre-op checklists, anesthesia and post-op notes, team.
* **Pharmacist**: read surgery requests (manages consumption in `hospital_inventory`).
* **Lab / Accountant / DPO**: read-only on surgery and cost values.
* **Manager**: create/read/write everything, **no delete**.
* **System Administrator**: full access including delete.
* **Receptionist**: read surgery requests (scheduling visibility), no write.

Surgery records, finalized operation reports and consumed stock cannot be deleted by normal
clinical users; only a System Administrator may delete. Critical fields lock on
completion/cancellation.

## Report

**Surgery Summary** (PDF) on `hospital.surgery.request`, using the shared
`hospital_management.hospital_report_header` (YOYA Hospital header). Includes patient,
admission, surgery type, theatre, urgency/risk, planned/actual times, team, pre-op,
anesthesia, operation report, post-op notes, inventory consumption and cost summary.

## Manual test checklist

1. Install/upgrade the module; the **Operation Theatre** app appears.
2. Create a Surgery Type with material lines.
3. Create an Operation Theatre with a theatre store location.
4. Create a Surgery Request; run the workflow buttons end to end.
5. Create + complete a Pre-op Checklist; Mark Pre-op Ready.
6. Document an Anesthesia Note (if required); Start/Complete Operation.
7. Finalize an Operation Report; submit/review a Post-op Note.
8. **Prepare Inventory Consumption** → lines populate from the surgery type.
9. Select batches manually → Approve → Consume → stock reduces, movement created.
10. Confirm Patient Inventory Usage shows surgery movements and actual material cost updates.
11. Patient & Admission **Surgeries** smart buttons work.
12. Print the **Surgery Summary** PDF.

## Manual test checklist — billing (Task 32A)

1. Move a surgery to `operation_done` (or `postop_done` / `completed`).
2. *Generate Surgery Bill* is **hidden** before `operation_done`; **visible** after.
3. With surgery price 0 → generation is blocked with a clear error.
4. Set a price (e.g. 3000), leave *Include Material Cost in Bill* unchecked → generate →
   bill total = 3000, one line with `source_type = surgery`.
5. Generate again → blocked as duplicate.
6. On another surgery: consume materials first (actual material cost e.g. 250), tick
   *Include Material Cost in Bill* → generate → two lines (service 3000 + materials 250),
   total 3250.
7. *View Patient Bill* opens the bill; it appears in the Billing app.
8. Register a payment → surgery `billing_state` updates (billed → partial → paid).
9. Confirm **no** `account.move` is created by surgery.
10. (After adding the bridge config) posting the bill debits Patient Receivable and
    credits Surgery Revenue.

## Known limitations

* No automatic COGS posting yet.
* No direct `account.move` posting from surgery (posting stays in the billing/accounting bridge).
* No insurance package / prior authorization yet.
* No surgical package pricing yet.
* No multi-line theatre/anesthesia fee breakdown yet.
* Material cost billing is controlled by a checkbox + review note (opt-in per surgery).
* Inventory accounting bridge (consumed-material COGS) is a future task.
* No advanced anesthesia charting, blood bank, ICU, sterilization or implant-serial tracking yet.
* Inventory batch selection is manual (no FIFO/FEFO). No dashboard yet.

## Next recommended tasks

* Inventory/accounting bridge for consumed-material COGS/expense posting.
* ~~Expose `source_type = surgery` in the `hospital_billing_accounting` config selection.~~
  Done in **Task 32B**.
* Surgical package pricing and multi-line theatre/anesthesia fee breakdown.
* Theatre utilization dashboard and analytics.
* Blood bank and ICU integration.
