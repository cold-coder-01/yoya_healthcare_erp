# Hospital Fiscal POS Bridge (`hospital_fiscal_bridge`)

## 1. Module Name

**Hospital Fiscal POS Bridge** — technical name `hospital_fiscal_bridge`, part of the
YOYA / Ethiopian Hospital ERP suite (Odoo 18 Community).

Documentation set:

- This README — module overview for implementers.
- [Operator Guide](docs/FISCAL_POS_BRIDGE_OPERATOR_GUIDE.md) — for cashiers, supervisors and finance users.
- [API Integration Guide](docs/FISCAL_POS_API_INTEGRATION_GUIDE.md) — for Zoorya/ETTA and Synergy technical teams.
- [Test Checklist](docs/FISCAL_POS_TEST_CHECKLIST.md) — acceptance/regression checklist.

## 2. Purpose

Ethiopian fiscal regulations require fiscal receipts to be produced by a certified
fiscal device. This module connects the hospital billing system to an external
fiscal POS / payment terminal (e.g. **SUNMI P3 MIX running Zoorya ET**) without
giving the terminal any control over hospital data. The bridge is the **only
controlled door** between hospital billing and the fiscal terminal.

> Fiscal certification (receipt numbering, TIN, fiscal memory, ERCA behavior) is
> handled entirely by the external Zoorya/SUNMI layer and must be confirmed with
> ETTA Solutions. This Odoo module is **not** itself a certified fiscal device.

## 3. Architecture Summary

```
Hospital ERP (Odoo)                          Fiscal Terminal (SUNMI/Zoorya)
┌──────────────────────────┐                 ┌───────────────────────────┐
│ hospital.patient.bill    │  1. Prepare     │                           │
│   └► hospital.fiscal.    │─────────────►   │ 2. Scan/enter FISC00001   │
│       transaction (FISC) │                 │ 3. POST /lookup           │
│                          │ ◄───────────────│    → amount + lines       │
│ 4. state: ready → locked │                 │ 5. Collect money, print   │
│                          │ ◄───────────────│    fiscal receipt         │
│ 6. POST /payment/success │                 │                           │
│    → creates official    │                 │                           │
│    hospital.patient.bill.│                 │                           │
│    payment (PAYxxxxx)    │                 │                           │
└──────────────────────────┘                 └───────────────────────────┘
```

The terminal is a **stateless fiscal payment and receipt gateway**. It looks up a
prepared fiscal transaction, accepts the payment, issues the fiscal receipt, and
sends a callback. Odoo validates the callback and creates the hospital payment
record **through the existing billing payment engine** — the terminal never
writes bill states or accounting itself.

## 4. What Odoo Owns

Patients, appointments, consultations, lab/radiology/pharmacy charges,
admission/procedure/surgery charges, insurance/corporate payer split, patient
responsibility, bill lines, bill states, payment history
(`hospital.patient.bill.payment`), the accounting bridge, and inventory.

## 5. What SUNMI/Zoorya Owns

Physical payment collection, fiscal receipt printing, fiscal numbering/
certification, and the fiscal reporting obligations of the device. Nothing else.
The terminal cannot create bill lines, edit patients, calculate insurance rules,
post accounting entries, or change hospital bill states directly.

## 6. Core Workflow

| Step | Actor | Action | Result |
|---|---|---|---|
| 1 | Cashier | **Prepare Fiscal Payment** on a confirmed bill with amount due > 0 | `hospital.fiscal.transaction` `FISCxxxxx` created in **Ready**, amounts and bill lines frozen (snapshot), barcode + QR payload generated |
| 2 | Terminal | `POST /lookup` with the FISC reference | Transaction becomes **Locked** for that terminal; amount + minimal lines returned |
| 3 | Terminal | Collects money, prints fiscal receipt | External (Zoorya/SUNMI) |
| 4 | Terminal | `POST /payment/success` with exact amount + receipt references | Odoo validates, creates payment `PAYxxxxx`, transaction becomes **Paid**, bill state updates through the existing payment logic |
| 4b | Terminal | `POST /payment/failure` | Transaction becomes **Failed**; can be reset by authorized users |

Transaction states: `draft → ready → locked → paid`, with `failed`, `expired`,
`cancelled`, `exception` (manual review) and `reversed` (foundation placeholder).

**Prepare Fiscal Payment vs Register Payment:** Prepare Fiscal Payment starts the
normal SUNMI/Zoorya fiscal terminal flow. **Register Payment remains the valid
internal/back-office payment engine** for manual, partial, payer/corporate,
insurance, reconciliation and controlled admin cases. Both channels create
`hospital.patient.bill.payment` records through the same engine; neither is
obsolete.

## 7. Supported API Endpoints

All endpoints: `POST`, raw JSON body, authenticated per device.

| Endpoint | Purpose |
|---|---|
| `POST /api/hospital/fiscal/lookup` | Terminal fetches amount + lines by exact FISC reference; locks the transaction |
| `POST /api/hospital/fiscal/payment/success` | Terminal confirms fiscal payment; Odoo creates the hospital payment |
| `POST /api/hospital/fiscal/payment/failure` | Terminal reports failed/cancelled payment |
| `POST /api/hospital/fiscal/status` | Terminal checks current transaction state |

Full request/response contracts: [API Integration Guide](docs/FISCAL_POS_API_INTEGRATION_GUIDE.md).

## 8. Current Limitations

- **No partial fiscal payment.** The callback amount must exactly match the
  prepared `amount_payable`; anything else is rejected as an amount mismatch and
  moved to Exception. Partial fiscal payment should be a future controlled
  enhancement where **Odoo prepares the fiscal transaction for the intended
  partial amount** — the terminal will still pay exactly what was prepared.
- The real Zoorya API contract must be confirmed with ETTA Solutions; the JSON
  schema here is the hospital-side proposal.
- Plain API key authentication (foundation). HMAC signing recommended for production.
- `reversed` state exists but no refund/reversal workflow is implemented.
- No Android/SUNMI SDK integration, no direct fiscal printer control, no
  offline fiscal payment support.

## 9. Security Model

Foundation security, enforced on every API call:

- Device registry: only **active** registered devices (`hospital.fiscal.device`)
  can call the API (`device_code` + `api_key`, constant-time comparison).
- Optional per-device **IP allowlist**.
- API key visible only to Manager/System Administrator, masked in the form,
  never shown in list views, stripped from logged payloads.
- Every lookup, callback, error and admin action is recorded in
  `hospital.fiscal.payment.log` (immutable for non-admins).
- **Patient privacy:** the API returns only an identification code + initials
  (e.g. `HMS0001 - K.Z.`), amounts and service line names. No diagnosis, notes,
  phone, address, documents or clinical details. No list endpoints — exact
  reference lookup only.

## 10. Multi-Database Deployment Requirement

Odoo 17/18 resolves the database for a request from the **session cookie** or,
for sessionless clients like the terminal, only when **exactly one** database
passes the db filter ("monodb"). The `?db=` query parameter is **not** used for
this. On a server exposing several databases, sessionless calls are dispatched
with no database context and custom module routes return **404** (visible as
`INFO ? werkzeug` lines in odoo.log) even though the routes are registered.

Fix applied on this deployment — in `odoo.conf` (note the correct key is
`dbfilter`; a `db_filter` line is silently ignored):

```ini
dbfilter = ^healthcare_erp$
```

Then fully restart the Odoo service. Other databases on the same PostgreSQL
cluster stay intact but are not reachable through this instance until the
filter is relaxed.

## 11. Production Hardening Checklist

- [ ] Replace test API keys with long random secrets (never use `test-secret` in production).
- [ ] Serve Odoo behind **HTTPS only** (TLS at the reverse proxy).
- [ ] Restrict allowed IPs per device.
- [ ] Add HMAC request signing.
- [ ] Add timestamp/replay protection.
- [ ] Rotate keys per terminal; separate test and production devices.
- [ ] Disable (archive) lost/stolen terminals immediately.
- [ ] Monitor failed authentication attempts and `exception` transactions.
- [ ] Keep `dbfilter` correctly configured after any server change.
- [ ] Rate-limit `/api/hospital/fiscal/*` at the proxy.

## 12. Testing Summary

All of the following passed live testing on the production database (2026-07-06):

| Scenario | Reference | Result |
|---|---|---|
| Prepare Fiscal Payment + snapshot creation | FISC00001 (BILL00004, 3,050.00 ETB) | PASSED |
| Lookup locks transaction | FISC00001 | PASSED |
| Success callback creates exactly one payment | PAY00004, bill became Paid | PASSED |
| Duplicate success callback | `duplicate: true`, no second payment | PASSED |
| Duplicate external receipt (`ZRY-TEST-0001` reused) | FISC00002 → Exception, no payment | PASSED |
| Amount mismatch (1,000.00 sent for 2,500.00) | FISC00003 → Exception, no payment | PASSED |
| Exception-state payment blocking | PASSED |
| Status endpoint, dbfilter fix, diagnostic cleanup, UI polish (transaction + device forms) | PASSED |

Repeatable steps: [Test Checklist](docs/FISCAL_POS_TEST_CHECKLIST.md).

## 13. Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| All API calls return 404 | Multiple databases exposed; request served without db context (`INFO ? werkzeug` in log) | Set `dbfilter = ^healthcare_erp$`, restart Odoo (see section 10) |
| 401 `auth_failed` | Wrong `terminal_code`/`api_key`, or device archived | Check device record; device must be Active |
| 403 `ip_not_allowed` | Caller IP not in the device allowlist | Update `Allowed IP` or clear it for testing |
| 404 `not_found` | Wrong FISC reference | Terminal must send the exact transaction name/barcode |
| 409 `not_payable` | Transaction not in Ready/Locked (already paid, cancelled, exception…) | Check transaction state in Odoo |
| 409 `expired` | Payment window passed | Authorized user resets to Ready (re-snapshots amounts) |
| 409 `locked_by_other_terminal` | Another device locked the transaction | Complete/cancel on the locking terminal, or reset |
| Transaction stuck in Exception | Amount mismatch or reused fiscal receipt | Review Logs tab, then Cancel or Reset to Ready |
| Expiry window too short/long | Default 120 minutes | System parameter `hospital_fiscal_bridge.expiry_minutes` |
