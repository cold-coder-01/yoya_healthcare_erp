# Hospital Dashboard (`hospital_dashboard`)

Executive command center for the YOYA General Hospital ERP (Odoo 18 Community).
A single, read-only page that summarizes the whole hospital operation and links
into the existing operational modules.

## Purpose

- Give management and operational staff one live overview page: patient
  activity, clinical operations, billing/collections, insurance, fiscal POS,
  inventory health and recent activity.
- Strictly **read-only + navigation**: the dashboard never creates accounting
  entries, never marks bills paid, never touches fiscal transactions or
  clinical records. All "View …" / quick-action buttons only open the standard
  actions of the source modules.

## Architecture

- **OWL client action** (`hospital_dashboard.dashboard`) rendered from
  `static/src/js/hospital_dashboard.js` + `static/src/xml/hospital_dashboard.xml`.
- **Data provider**: abstract model `hospital.dashboard`
  (`models/hospital_dashboard.py`) with a single RPC entry point
  `get_dashboard_data(filters)`. One RPC per load/refresh.
- Charts (patient-flow line chart, billing/bed donuts, sparklines) are plain
  inline **SVG** computed client-side — no external chart library, no new asset
  dependencies.
- Styling is scoped under `.o_hospital_dashboard`
  (`static/src/scss/hospital_dashboard.scss`) using the emerald hospital
  design language (`#006A4F`). No global selectors, no changes to other
  modules or themes.

## Dashboard sections and data sources

| Section | Source models |
|---|---|
| KPI row (patients, appointments, admissions, lab, pharmacy, revenue) | `hospital.appointment`, `hospital.admission`, `hospital.laboratory.request`, `hospital.pharmacy.dispense`, `hospital.patient.bill.payment` |
| Patient Flow Trend | `hospital.appointment` (outpatients), `hospital.admission` (inpatients), bucketed per 2 hours (today) or per day (ranges) |
| Billing & Collection Summary | `hospital.patient.bill` (`amount_total`, `amount_paid`, `amount_due`, cancelled bills as write-off) |
| Department Load | `hospital.appointment` grouped by `department_id`, plus lab/radiology/pharmacy request counts |
| Bed Occupancy | `hospital.bed` grouped by `state` |
| Clinical Operations | `hospital.laboratory.request`, `hospital.radiology.request`, `hospital.procedure.request`, `hospital.surgery.request`, `hospital.nursing.round` + `hospital.medication.administration`, `hospital.pharmacy.dispense` |
| Alerts & Tasks | ICU bed pressure, fiscal failed/exception transactions, out-of-stock items, overdue discharges, expiring batches, pending claims/draft bills/surgeries/stock requests, low-stock items |
| Financial Overview | `hospital.insurance.claim`, `hospital.patient.bill`, `hospital.fiscal.transaction`, `hospital.inventory.item` / `hospital.inventory.batch` |
| Recent Activity | merged latest records from payments, admissions, lab results, appointments, dispenses, fiscal paid transactions |
| Quick Actions | standard window actions of the existing modules |

## Dependencies

Hard dependencies: `web`, `hospital_management` only.
All other modules (`hospital_billing`, `hospital_admission`, `hospital_nursing`,
`hospital_procedure`, `hospital_inventory`, `hospital_operation_theatre`,
`hospital_radiology`, `hospital_pharmacy`, `hospital_fiscal_bridge`,
`hospital_insurance`) are detected at runtime (`model in env`). If a module is
not installed its card shows a clean empty state — the dashboard never crashes.

## Assumptions & known limitations

- **Department Load is an activity share**, not staffing capacity: each bar is
  the unit's record count relative to the busiest unit (=100%) in the selected
  period. There is no real capacity model yet.
- "Today's Patients" = unique patients with a non-cancelled appointment in the
  period (no unified encounter model exists).
- Aggregations run as superuser so all hospital roles see the same totals;
  drill-down actions still enforce each user's own access rights.
- The Branch selector is visual (single-company deployment); Department filter
  applies to appointment-based figures (KPIs, patient flow, department load).
- Currency label comes from the company currency (ETB expected); amounts are
  compacted (`2.45M ETB`).
- Chart tooltips use native SVG `<title>` hovers (lightweight by design).
- Trend badges compare against the previous period of equal length.

## Future enhancements

- Real capacity model for department load; per-branch filtering for
  multi-company; configurable alert thresholds (ICU %, expiry window, overdue
  days); auto-refresh timer; export/print snapshot.

## Testing

1. Update the app list and install **Hospital Dashboard**.
2. Open menu **Dashboards → Hospital Dashboard**: the page must load without
   RPC errors and every section renders (zeros/empty states are fine).
3. Switch Date Range (Today / 7 Days / 30 Days / This Month) and Department —
   figures reload.
4. Click KPI cards, clinical cards, "View …" buttons, alerts and quick
   actions — each opens the corresponding existing list/form action.
5. Confirm existing modules (billing, fiscal bridge, insurance, …) still open
   normally and the fiscal status endpoint still returns 200 JSON.
