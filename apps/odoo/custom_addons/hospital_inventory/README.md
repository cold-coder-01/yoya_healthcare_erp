# Hospital Inventory

Hospital-specific inventory and medical consumables control foundation for the
**Ethiopian Hospital ERP** (Odoo 18 Community).

## Overview

`hospital_inventory` provides an operational inventory foundation for a hospital:
a master catalog of stock items, batch/lot-level stock with expiry tracking,
department-level stock visibility, an internal stock request workflow, and an
immutable stock movement audit trail. It is intentionally **self-contained** and
does **not** depend on the Odoo `stock`, `purchase`, or `account` modules. Those
can be added later through a dedicated bridge module.

Item families covered:

- Medicines (stock foundation)
- Medical consumables
- Laboratory reagents
- Radiology consumables
- Procedure materials
- Ward / nursing supplies
- Small equipment and other items

This is a **foundation module only**. See *Known Limitations* below.

## Dependencies

- `hospital_management`
- `hospital_pharmacy`
- `hospital_procedure`
- `hospital_nursing`
- `hospital_radiology`
- `mail`

It deliberately does **not** depend on `stock`, `purchase`, `account`, or
`om_account_accountant`.

## Features

- Master item catalog with item type, unit of measure, costing and stock rules.
- Batch / lot tracking with expiry and near-expiry detection.
- Computed stock totals (on hand / available / near-expiry / expired) per item.
- Department-level stock visibility computed from batches.
- Stock request workflow (submit → approve → issue / return).
- Direct batch deduction on issue with automatic stock movement creation.
- Immutable stock movement audit trail (no delete for normal users).
- Patient / admission / procedure inventory usage tracing via smart buttons.
- Printable **Stock Request Summary** PDF using the shared YOYA Hospital header.
- Audit logging integrated with `hospital.audit.log`.

## Models

| Model | Purpose |
|-------|---------|
| `hospital.inventory.location` | Hospital-specific stock locations (Task 31A). |
| `hospital.inventory.item` | Master catalog of hospital stock items. |
| `hospital.inventory.batch` | Batch/lot-level stock, location and expiry tracking. |
| `hospital.opening.stock` | Initial-receipt / opening stock loader (Task 31A). |
| `hospital.opening.stock.line` | Lines of an opening stock document. |
| `hospital.department.stock` | Department-level stock visibility (computed). |
| `hospital.stock.request` | Stock request / internal transfer between locations. |
| `hospital.stock.request.line` | Lines of a stock request. |
| `hospital.stock.movement` | Immutable audit trail of inventory movement. |
| `hospital.stock.consumption` | Reviewable consumption document (Task 31). |
| `hospital.stock.consumption.line` | Lines of a consumption document. |
| `hospital.procedure.material.line` | Procedure-type material template (Task 31). |

Patient, admission, and procedure models are extended with inventory usage
counts and smart-button actions.

## Task 31 — Operational Consumption Integration

Task 31 connects the inventory foundation to real consumption points. Every
consumption flows through a single, reviewable **`hospital.stock.consumption`**
document so stock deduction logic is never duplicated and nothing is consumed
silently — consuming always creates `hospital.stock.movement` records of type
`consumption` and links them back via `movement.consumption_id`.

### Consumption workflow

| Action | Transition |
|--------|------------|
| Approve | draft → approved |
| Consume | approved → consumed (creates one movement per line, deducts batch) |
| Cancel | draft / approved → cancelled |
| Reset to Draft | cancelled → draft |

Rules enforced on consume: cannot consume from draft, cannot consume twice,
cannot cancel after consumed, batch must match item, quantity must be > 0, and
expired/blocked/depleted batches or insufficient stock are blocked with a
`UserError`. Batch selection is **manual** (no FIFO/FEFO). The shared
`hospital.inventory.batch.deduct_quantity()` helper is the single source of
truth for deduction, reused by both the stock-request issue flow and
consumption.

### Integrations

All integrations are **controlled buttons**, never forced — clinical workflows
keep working with no inventory setup.

- **Pharmacy** — `hospital.pharmacy.medicine` gains an `inventory_item_id`
  link. The dispense form has *Create Inventory Consumption* (prefills lines
  from dispensed medicines mapped to inventory items) plus an *Inventory*
  smart button.
- **Procedure** — `hospital.procedure.type` gains a material template
  (`material_line_ids`). The procedure request form has *Prepare Inventory
  Consumption* (prefills lines from the template) plus a *Consumption* smart
  button.
- **Nursing** — `hospital.medication.administration` gains inventory item/batch
  /quantity fields and a *Consume Medicine* button (dose is textual, so the
  quantity is manual). `hospital.nursing.round` has *Create Nursing Supply
  Consumption* plus a smart button.
- **Laboratory** — `hospital.laboratory.request` has *Create Lab Reagent
  Consumption* plus a smart button.
- **Radiology** — `hospital.radiology.request` has *Create Radiology Consumable
  Consumption* plus a smart button.

The patient **Inventory Usage** tab now shows both stock movements and stock
consumptions; the Task 30 movement smart button is unchanged.

## Inventory Flow

1. Create master **Items** (medicines, consumables, reagents, supplies …).
2. Receive stock as **Batches / Lots** (quantity, expiry, department, cost).
3. Departments raise **Stock Requests** for items they need.
4. A pharmacist/manager **approves** the request and **issues** the stock.
5. Issuing **deducts** the quantity from the selected batch and writes a
   **Stock Movement** record.
6. Returns add stock back and write a return movement.

## Batch / Expiry Logic

- `available_quantity = max(quantity_on_hand - reserved_quantity, 0)`.
- A batch is flagged **expired** when `expiry_date < today`, and **near expiry**
  when it expires within `NEAR_EXPIRY_DAYS` (30) days.
- State is auto-synced (no cron): a batch becomes `depleted` when quantity
  reaches zero, `expired` when past its expiry date, and recovers to
  `available` otherwise. Manually **blocked** batches are never auto-changed.
- Constraints: quantities cannot be negative, reserved cannot exceed on hand,
  and expiry cannot precede the received date.

## Department Stock Visibility

`hospital.department.stock` lines aggregate quantities from all active batches
matching the same `department_id` and `item_id`. A `(department, item)` pair is
unique. Available quantity excludes expired/blocked/depleted batches and flags
lines below their reorder level.

## Stock Request Workflow

| Action | Transition |
|--------|------------|
| Submit | draft → submitted |
| Approve | submitted → approved (defaults approved qty from requested) |
| Issue Stock | approved / partially_issued → issued or partially_issued |
| Return | approved / issued / partially_issued → returned (return type only) |
| Reject | submitted → rejected |
| Cancel | draft / submitted / approved → cancelled |
| Reset to Draft | rejected / cancelled → draft |

Issue logic (foundation): manual batch selection, direct deduction from the
selected batch. Expired, blocked, or depleted batches cannot be issued, and an
issue that exceeds available quantity raises a `UserError`. No FIFO/FEFO
auto-picking yet.

## Stock Movement Audit Trail

Every issue/return creates a `hospital.stock.movement` record (sequence
`STMOV#####`). Movements are read-only in the UI and cannot be deleted by
normal users — only the Hospital System Administrator may delete them.
Movements can be linked to a patient, admission, procedure, or nursing round
for future patient-level consumable tracing.

## Patient Inventory Usage Tracing

`hospital.patient`, `hospital.admission`, and `hospital.procedure.request` each
gain an **Inventory Usage** smart button (and the patient gains a notebook tab)
showing the stock movements linked to that record. This is the foundation for
future patient-level consumable costing.

## Security Role Summary

| Role | Access |
|------|--------|
| Pharmacist | Create/read/write items, batches, requests, movements (no delete). |
| Nurse | Create/read stock requests; read items, batches, department stock, movements. |
| Doctor | Read-only stock context. |
| Lab Technician | Create/read stock requests; read items, batches, department stock. |
| Receptionist | No inventory access. |
| Accountant | Read stock and movements. |
| Manager | Create/read/write (no delete). |
| Data Protection Officer | Read-only. |
| System Administrator | Full access, including delete. |

All groups are reused from `hospital_management`; no new groups are defined.

## Reports

**Inventory Consumption Summary** (`qweb-pdf`) bound to
`hospital.stock.consumption` — consumption type, patient, department, all source
document references, requested/approved/consumed by, status, line items with
batch numbers, quantities, unit cost, total estimated cost, and notes. Uses the
shared YOYA Hospital header.

**Stock Request Summary** (`qweb-pdf`) bound to `hospital.stock.request`. Uses
the shared `hospital_management.hospital_report_header`. Includes request
reference, department, requested/approved/issued by, request type, priority,
status, line items with requested/approved/issued quantities, and notes.

## Install Notes

Install/upgrade in this order:

1. `hospital_management`
2. `hospital_pharmacy`
3. `hospital_procedure`
4. `hospital_nursing`
5. `hospital_inventory`

## Manual Test Checklist

1. Install/upgrade the modules above.
2. Open the **Inventory** app.
3. Create an **Item** (e.g. Sterile Wound Dressing Kit, code `PROC-DRESS`,
   type Procedure Material, unit Kit, min stock 10, reorder 15, cost 80).
4. Create a **Batch** (e.g. `DRESS-B001`, qty 50, future expiry, cost 80).
5. Confirm available quantity = 50.
6. Create a **Stock Request** (Issue to Department, priority Normal) with a line
   for the item, requested 5, approved 5, batch `DRESS-B001`.
7. Submit → Approve → Issue Stock.
8. Confirm: batch quantity reduces 50 → 45, a movement `STMOV00001` is created,
   request state = issued.
9. Try issuing from an expired batch → blocked with `UserError`.
10. Open a patient and check the **Inventory Usage** smart button.
11. Print the **Stock Request Summary** PDF.

## Task 31 Manual Test Checklist

- **Manual:** create item *Sterile Gloves* (Nursing Supply, Pair) + batch
  `GLOVE-B001` qty 100; create Stock Consumption (type nursing, patient, dept),
  line gloves/batch/qty 2; Approve → Consume → batch 100→98, movement created,
  patient Inventory Usage shows it.
- **Procedure:** link a procedure type material template; on a procedure request
  click *Prepare Inventory Consumption*, pick batch, consume qty 1 → batch
  reduces, movement linked to procedure + patient.
- **Pharmacy:** set medicine `inventory_item_id`, batch stock; on a dispense
  click *Create Inventory Consumption*, pick batch, consume → batch reduces,
  movement linked to patient + dispense; existing dispense workflow unaffected.
- **Radiology:** item *Ultrasound Gel*; on a radiology request *Create
  Radiology Consumable Consumption*, consume qty 1 → batch reduces.
- **Lab:** item *CBC Reagent*; on a lab request *Create Lab Reagent
  Consumption*, consume qty 1 → batch reduces.
- Verify insufficient stock, expired/blocked/depleted batch, and a second
  Consume on the same document are all blocked.

## Task 31A Manual Test Checklist

**Scenario 1 — Opening stock**

1. Confirm baseline locations exist (Central Medical Store `MAIN-STORE`, Transit
   `TRANSIT`, Ward/Nursing Store `WARD-STORE`, Patient Consumption `CONSUMED`).
2. Create item *Sterile Gloves* (Nursing Supply, Pair, standard cost 25).
3. Create **Opening Stock** into *Central Medical Store*; line: Sterile Gloves,
   batch `GLOVE-B001`, qty 100, unit cost 25, future expiry. **Confirm.**
4. Expect: batch `GLOVE-B001` at Central Store qty 100; a `receipt` movement;
   batch inventory value = 2,500.

**Scenario 2 — Issue to department**

1. Create **Stock Request**, type *Issue to Department*, source *Central Medical
   Store*, destination *Ward/Nursing Store*; line Sterile Gloves, batch
   `GLOVE-B001`, requested/approved 20.
2. Submit → Approve → Issue Stock.
3. Expect: Central Store batch 100 → 80; Ward Store batch created/at 20; a
   movement Central Store → Ward Store; stock did **not** disappear.

**Scenario 3 — Patient consumption**

1. Create **Stock Consumption**, type nursing, a patient, source *Ward/Nursing
   Store*, consumption location *Patient Consumption*; line Sterile Gloves, the
   ward batch, qty 2.
2. Approve → Consume.
3. Expect: Ward batch 20 → 18; a `consumption` movement Ward Store → Patient
   Consumption; patient **Inventory Usage** shows it; consumption value 50; no
   `account.move` created.

**Scenario 4 — Accounting readiness**

1. Open a consumption stock movement; confirm `unit_cost`, `movement_value` and
   `inventory_accounting_state = pending` exist.
2. Confirm no `account.move` was created anywhere.

## Task 31A — Warehouse, Opening Stock & Inventory Accounting Readiness

Task 31A answers the operational question *"where is the material coming from?"*.
Earlier tasks could consume stock from a batch without modelling **where** that
batch physically lived. Task 31A adds a proper hospital warehouse/location layer
so the inventory flow is business-accurate before live testing.

> **Business rule:** Inventory is **not** created at consumption. It must first
> exist in a warehouse/location through opening stock or receipt. Consumption only
> removes stock from a real batch and links that usage to a patient/service.

### Why locations are required

A hospital does not hold all stock in one place. Stock arrives in a **Central
Store**, is **issued** to department/ward stores, and is finally **consumed** for
a patient or service. Without locations, issuing stock made it "disappear" and
consumption had no real source store. Locations make every quantity traceable to
a place and make per-department stock accurate.

### Central Store / Transit / Department Store concept

| Location type | Role |
|---------------|------|
| `central_store` | Main hospital store that receives opening stock / receipts. |
| `transit` | Temporary location for internal transfers between stores. |
| `department_store` / `pharmacy_store` / `laboratory_store` / `radiology_store` / `procedure_store` / `ward_store` | Department-level stores that receive issued stock and from which consumption happens. |
| `adjustment` | Counterpart location for adjustments. |
| `virtual_consumption` | Virtual location that receives stock when it is consumed (used stock). |
| `other` | Anything else. |

`hospital.inventory.location` fields: `name`, `code` (unique), `location_type`,
`department_id`, `responsible_user_id`, `is_default`, `is_consumption_location`,
`active`, `notes`. One default per *central store / transit / virtual
consumption* is enforced so automatic flows resolve deterministically. A
department link is **recommended** (non-blocking hint) for department-level
stores.

Baseline locations are shipped in `data/inventory_location_data.xml` (locations
only — **no quantities**):

| Code | Name | Type |
|------|------|------|
| `MAIN-STORE` | Central Medical Store | central_store (default) |
| `TRANSIT` | Internal Transfer Transit | transit (default) |
| `PHARM-STORE` | Pharmacy Store | pharmacy_store |
| `LAB-STORE` | Laboratory Store | laboratory_store |
| `RAD-STORE` | Radiology Store | radiology_store |
| `WARD-STORE` | Ward / Nursing Store | ward_store |
| `PROC-STORE` | Procedure Room Store | procedure_store |
| `CONSUMED` | Patient Consumption / Used Stock | virtual_consumption (default, consumption) |

### Opening stock flow

`hospital.opening.stock` (sequence `OPSTK#####`) loads initial quantities into a
location. Each line carries item, batch number, received/expiry dates, quantity
and unit cost. **Confirm** (draft → confirmed):

1. validates every line (quantity > 0, expiry ≥ received),
2. creates a `hospital.inventory.batch` at the opening location (state `expired`
   if already past expiry, else `available`),
3. creates a `receipt` `hospital.stock.movement` (`to_location_id` = opening
   location, accounting state `pending`, note `Opening stock OPSTK#####`),
4. links `created_batch_id` and `movement_id` back on the line.

Confirmed opening stock is **locked** (only notes editable) and cannot be
cancelled or deleted (a reversal flow is deferred to a future task; only a System
Administrator can force-delete).

### Transfer / issue flow

`hospital.stock.request` gains `source_location_id`, `destination_location_id`,
`transit_location_id` and `use_transit`. Issuing now **moves** stock instead of
destroying it:

- the **source batch decreases**, and
- a matching destination batch (same item / batch number / expiry / unit cost) is
  **created or topped up** at the destination location,
- a movement is written with `from_location_id` / `to_location_id` and a type
  derived from the request type (`issue` / `transfer` / `return` / `adjustment`).

| Request type | Source | Destination |
|--------------|--------|-------------|
| Issue to Department | Central Store | department store |
| Transfer Between Departments | department store | another department store |
| Return to Store | department store | Central Store |
| Adjustment Request | adjustment / central, per flow | per flow |

Backward compatible: requests with **no** destination location keep the legacy
behaviour (issue simply deducts; return adds back).

### Patient consumption flow

`hospital.stock.consumption` gains `source_location_id` and
`consumption_location_id` (defaults to the virtual consumption location). On
**Consume**:

- the consumed batch must sit at `source_location_id` when one is set,
- the batch is deducted (consumption is the **only** flow that permanently removes
  stock from inventory),
- a `consumption` movement is written `from_location_id` → `consumption_location_id`
  with the patient/admission/procedure/nursing links and accounting state
  `pending`.

The patient **Inventory Usage** tab now shows from/to location and movement value
per movement, so patient-level inventory is auditable.

### Accounting readiness (no posting yet)

This module still does **not** depend on `account`. Instead it records the data a
future accounting bridge needs:

- item `accounting_category` and `cost_method` (`batch_actual` default),
- item `standard_cost` and batch `unit_cost`,
- batch `inventory_value` (= qty × unit cost) and `available_value`,
- movement `movement_value` (= qty × unit cost),
- movement `inventory_accounting_state`
  (`not_applicable` / `pending` / `ready` / `posted` / `error` / `reversed`) and
  `accounting_note`.

Opening stock and consumption movements are flagged `pending`; internal
issues/transfers/returns are `not_applicable`. **No journal entry is posted.** A
future **Task 31B (or later)** can post, from consumption movements:

```
Dr  Medical Consumables Expense / COGS
    Cr  Inventory Asset
```

### Setup checklist (Task 31A)

1. Open **Inventory ▸ Locations** — baseline locations are pre-loaded; set their
   `department_id` to match your hospital, or create your own.
2. Create **Items** (set accounting category / cost method if desired).
3. **Inventory ▸ Opening Stock** — load initial quantities into the Central Store
   and **Confirm**.
4. Raise **Stock Requests** to issue stock from the Central Store to department
   stores.
5. Record **Stock Consumption** from a department store for a patient/service.

## Task 31B — Demo Setup Scenario

Task 31B adds a clean **setup/demo layer** so the full inventory flow can be
tested slowly and accurately, without forcing any production data.

> ⚠️ **Warning:** Inventory consumption should **never** be tested before stock
> exists in a real location. Stock must first enter the system through **Opening
> Stock** (or a future receipt). Consuming an item that was never received is not
> a valid test — the system is designed to refuse it.

### Recommended setup order

1. **Configure departments** (`hospital.department`).
2. **Map department store locations** — open *Inventory ▸ Locations* and set the
   `department_id` on `PHARM-STORE`, `LAB-STORE`, `RAD-STORE`, `WARD-STORE`,
   `PROC-STORE` (baseline locations ship without a department on purpose).
3. **Create inventory items** (or enable demo data — see below).
4. **Create Opening Stock** into the *Central Medical Store* and confirm.
5. **Issue stock** from the Central Store to the relevant Department Store via a
   Stock Request (Issue to Department).
6. **Consume stock** from the Department Store for a patient/service.
7. **Review Patient Inventory Usage** (patient form ▸ Inventory Usage tab).
8. **Review stock movement value / accounting readiness** (Stock Movements list).

### Demo item catalog (optional, demo data only)

`demo/inventory_demo_data.xml` is registered under the manifest **`demo`** key, so
it loads **only when demo data is enabled** (never in production). It creates an
item catalog **only** — no batches, no opening quantities:

| Code | Name | Type | Unit | Std Cost | Acct Category |
|------|------|------|------|---------:|---------------|
| `MED-PARA500` | Paracetamol 500mg Tablet | medicine | tablet | 2.50 | medicine |
| `MED-AMOX500` | Amoxicillin 500mg Capsule | medicine | capsule | 6.00 | medicine |
| `NURS-GLOVE` | Sterile Gloves | nursing_supply | pair | 25.00 | nursing_supply |
| `NURS-GAUZE` | Sterile Gauze Pack | nursing_supply | pack | 15.00 | nursing_supply |
| `PROC-DRESS` | Sterile Wound Dressing Kit | procedure_material | kit | 80.00 | procedure_material |
| `PROC-SYR5ML` | 5ml Syringe | procedure_material | unit | 5.00 | procedure_material |
| `LAB-CBC-REAG` | CBC Reagent | lab_reagent | bottle | 450.00 | lab_reagent |
| `LAB-GLU-STRIP` | Glucose Test Strip | lab_reagent | pack | 300.00 | lab_reagent |
| `RAD-US-GEL` | Ultrasound Gel | radiology_consumable | bottle | 120.00 | radiology_consumable |
| `RAD-CONTRAST` | Radiology Contrast Medium | radiology_consumable | vial | 900.00 | radiology_consumable |
| `CONS-MASK` | Surgical Face Mask | consumable | box | 150.00 | medical_consumable |

If you are not using demo data, create these items manually under
*Inventory ▸ Items*.

### Recommended Demo Opening Stock

Load these quantities **manually** through *Inventory ▸ Opening Stock* (one
document into the Central Medical Store, or one per item). They are **not**
created automatically — opening quantities are always a deliberate user action.

| Item | Batch | Location | Qty | Unit Cost |
|------|-------|----------|----:|----------:|
| Sterile Gloves | `GLOVE-B001` | Central Medical Store | 100 | 25 |
| Sterile Wound Dressing Kit | `DRESS-B001` | Central Medical Store | 50 | 80 |
| Paracetamol 500mg Tablet | `PARA-B001` | Central Medical Store | 1000 | 2.50 |
| CBC Reagent | `CBC-B001` | Central Medical Store | 30 | 450 |
| Ultrasound Gel | `GEL-B001` | Central Medical Store | 20 | 120 |

### Source-specific demo scenarios

Each scenario assumes the item exists, its opening stock was loaded into the
Central Medical Store, and the target department store has been mapped to a
department. Issue first (Central → Department Store), then consume.

**Scenario 1 — Nursing gloves consumption**
- Item: *Sterile Gloves* (`NURS-GLOVE`) · Opening: `GLOVE-B001` ×100 @25 in Central Store.
- Department store: *Ward / Nursing Store* (`WARD-STORE`).
- Issue 20 Central → Ward. Consume (type *nursing*, patient HMS0001, source *Ward Store*, consumption *Patient Consumption*) qty 2.
- Expected: Ward batch 20 → 18 · movement value = 2 × 25 = **50** · patient Inventory Usage shows the Ward → Patient Consumption movement.

**Scenario 2 — Procedure dressing kit**
- Item: *Sterile Wound Dressing Kit* (`PROC-DRESS`) · Opening: `DRESS-B001` ×50 @80 in Central Store.
- Department store: *Procedure Room Store* (`PROC-STORE`).
- Issue 10 Central → Procedure Store. On a **procedure request** click *Prepare Inventory Consumption*, source *Procedure Store*, consume qty 1.
- Expected: Procedure batch 10 → 9 · movement value = **80** · movement linked to procedure + patient.

**Scenario 3 — Pharmacy paracetamol**
- Item: *Paracetamol 500mg Tablet* (`MED-PARA500`) · Opening: `PARA-B001` ×1000 @2.50 in Central Store.
- Department store: *Pharmacy Store* (`PHARM-STORE`).
- Issue 200 Central → Pharmacy Store. On a **pharmacy dispense** (medicine mapped via `inventory_item_id`) click *Create Inventory Consumption*, source *Pharmacy Store*, consume qty 20.
- Expected: Pharmacy batch 200 → 180 · movement value = 20 × 2.50 = **50** · movement linked to patient + dispense.

**Scenario 4 — Lab CBC reagent**
- Item: *CBC Reagent* (`LAB-CBC-REAG`) · Opening: `CBC-B001` ×30 @450 in Central Store.
- Department store: *Laboratory Store* (`LAB-STORE`).
- Issue 10 Central → Lab Store. On a **lab request** click *Create Lab Reagent Consumption*, source *Lab Store*, consume qty 1.
- Expected: Lab batch 10 → 9 · movement value = **450** · movement linked to lab request + patient.

**Scenario 5 — Radiology ultrasound gel**
- Item: *Ultrasound Gel* (`RAD-US-GEL`) · Opening: `GEL-B001` ×20 @120 in Central Store.
- Department store: *Radiology Store* (`RAD-STORE`).
- Issue 5 Central → Radiology Store. On a **radiology request** click *Create Radiology Consumable Consumption*, source *Radiology Store*, consume qty 1.
- Expected: Radiology batch 5 → 4 · movement value = **120** · movement linked to radiology request + patient.

> **Procedure material template:** no stable demo `hospital.procedure.type` record
> exists, so the procedure material template is **not** seeded. To use Scenario 2's
> *Prepare Inventory Consumption*, open a procedure type, add a material line
> (*Sterile Wound Dressing Kit*, default quantity 1) manually first.

## Inventory Accounting Readiness

This module stores the data a future accounting bridge needs but posts **no**
journal entries:

- **Opening stock** creates inventory **quantity** and inventory **value**
  (batch `inventory_value` = qty × unit cost).
- **Internal transfer / issue** changes the stock **location** only and should
  **not** create an expense (movement `inventory_accounting_state` =
  `not_applicable`).
- **Patient consumption** creates a `consumption` movement carrying a
  `movement_value` (qty × unit cost) and `inventory_accounting_state` = `pending`.
- The **consumption movement is the future basis for accounting** — it is the
  record a bridge would post from.
- **No `account.move` is created** anywhere in this module.
- A future bridge (Task 31C or later) may post:

  ```
  Dr  Medical Consumables Expense
      Cr  Inventory Asset
  ```

- For now, `inventory_accounting_state` on stock movements is only
  `pending` (opening stock + consumption) or `not_applicable` (internal moves).

## Known Limitations

- No purchase / vendor receipt workflow yet.
- No Odoo `stock` integration yet.
- No automatic FIFO/FEFO issue or consumption yet (batch selection is manual).
- No barcode scanning yet.
- No accounting journal posting / COGS posting yet (readiness data only).
- No dashboard yet.
- Confirmed opening stock reversal is deferred to a future task.
- Stock valuation (`inventory_value`, `movement_value`, …) is informational only
  until an accounting bridge is created.
- Inventory consumption is a controlled button, not forced globally; clinical
  workflows continue without inventory setup until integration is hardened.
- No automatic pharmacy / procedure / lab consumable auto-deduction yet.

## Next Recommended Task

**Task 31C** — build the inventory **accounting bridge**: post COGS/expense vs
inventory-asset journal entries from `pending` consumption movements, add a
confirmed-opening-stock reversal flow, and (separately) Odoo `stock`/`purchase`
integration plus an inventory dashboard.
