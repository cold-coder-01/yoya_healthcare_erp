# Fiscal POS Bridge — API Integration Guide

**Audience:** Zoorya / ETTA Solutions technical team and Synergy Tech Solutions
developers integrating a SUNMI/Zoorya fiscal terminal with the YOYA Hospital ERP
(Odoo 18 Community, module `hospital_fiscal_bridge`).

> All example values in this guide (`SUNMI-001`, `test-secret`, `FISC00001`,
> `ZRY-TEST-0001`, `ETTA-TEST-0001`) are **development/test values**.
> **`test-secret` must never be used in production.**

---

## 1. Integration Boundary

Odoo is the source of truth for all billing data. The terminal:

1. Looks up a fiscal transaction by **exact** reference (no browsing/list APIs).
2. Receives the payable amount and minimal line info.
3. Collects payment and issues the fiscal receipt (external, Zoorya-side).
4. Reports success or failure back to Odoo.

The terminal never creates bills, modifies patients, calculates insurance, posts
accounting, or writes bill states. Odoo validates every callback and creates the
official payment record through its existing billing payment engine.

**Base URL (development):** `http://<odoo-host>:8069`
**Transport:** `POST`, `Content-Type: application/json`, raw JSON body (not
JSON-RPC). Production must be HTTPS only.

## 2. Authentication

Every request body must include the device credentials:

```json
{ "terminal_code": "SUNMI-001", "api_key": "test-secret", ... }
```

Checks performed on every call: device exists and is **active**; `api_key`
matches (constant-time comparison); caller IP is in the device's optional
allowlist. Failures return `401` (`auth_required` / `auth_failed`) or `403`
(`ip_not_allowed`). The `api_key` value is stripped before request payloads are
stored in the audit log.

## 3. Device Registration

Done in Odoo by a manager/administrator: **Billing ▸ Fiscal POS Bridge ▸ Fiscal
Devices ▸ New** — set name, unique `device_code` (e.g. `SUNMI-001`), terminal
type, API key, and optionally the allowed IP list. Archived (inactive) devices
are rejected immediately, which is also the kill switch for lost/stolen
terminals.

## 4. Database Routing / dbfilter Requirement (critical)

Odoo 17/18 resolves the target database from the **session cookie** or, for
sessionless clients like the terminal, only when **exactly one** database passes
the server's db filter. The `?db=` query parameter is **ignored**. If the Odoo
server exposes more than one database, all fiscal API calls return **404**
(logged as `INFO ? werkzeug` in odoo.log) even though the routes exist.

Required server configuration in `odoo.conf` (key must be `dbfilter` — a
`db_filter` line is silently ignored), followed by a full service restart:

```ini
dbfilter = ^healthcare_erp$
```

## 5. Endpoint: Lookup

`POST /api/hospital/fiscal/lookup`

Fetches the payable amount by exact reference and **locks** the transaction for
this terminal. Accepts the FISC name, the barcode, or the QR payload
(`YOYA-FISC:FISC00001`) in `bill_reference` (alias: `transaction_reference`).

Request:

```json
{
  "terminal_code": "SUNMI-001",
  "api_key": "test-secret",
  "bill_reference": "FISC00001",
  "idempotency_key": "lookup-001",
  "timestamp": "2026-07-06T11:20:00+03:00"
}
```

Success response (`200`):

```json
{
  "status": "ok",
  "transaction_reference": "FISC00001",
  "bill_reference": "BILL00004",
  "patient_display": "HMS0001 - K.Z.",
  "amount_payable": 3050.00,
  "currency": "ETB",
  "state": "locked",
  "expires_at": "2026-07-06 10:20:00",
  "lines": [
    { "name": "Consultation Fee", "quantity": 1.0, "unit_price": 300.0, "total": 300.0 }
  ]
}
```

Notes:

- `patient_display` is deliberately minimal (identification code + initials).
  No clinical or contact data is ever returned.
- Re-lookup by the **same** terminal of its own locked transaction is allowed
  (idempotent). Another terminal gets `409 locked_by_other_terminal`.
- A transaction past its expiry window is auto-expired and returns `409 expired`.
- Datetimes in responses are UTC (`YYYY-MM-DD HH:MM:SS`).

Preconditions: transaction in `ready` (or `locked` by the same device), not
expired, `amount_payable > 0`. Otherwise `409 not_payable`.

## 6. Endpoint: Payment Success

`POST /api/hospital/fiscal/payment/success`

Request:

```json
{
  "terminal_code": "SUNMI-001",
  "api_key": "test-secret",
  "transaction_reference": "FISC00001",
  "external_receipt_no": "ZRY-TEST-0001",
  "external_transaction_id": "ETTA-TEST-0001",
  "amount_paid": 3050.00,
  "payment_method": "cash",
  "paid_at": "2026-07-06T11:26:00+03:00",
  "idempotency_key": "success-001"
}
```

Accepted response (`200`) — Odoo has created the official hospital payment:

```json
{
  "status": "accepted",
  "duplicate": false,
  "transaction_reference": "FISC00001",
  "bill_reference": "BILL00004",
  "hospital_payment_reference": "PAY00004",
  "bill_state": "paid",
  "amount_due": 0.00
}
```

Field notes:

- `payment_method`: mapped to the hospital's payment methods. Recognized values:
  `cash`, `card`/`pos_card`, `bank`/`bank_transfer`/`transfer`,
  `mobile`/`mobile_money`/`telebirr`/`cbe_birr`; anything else is stored as "other".
- `paid_at`: ISO-8601; converted to UTC. Omitted/unparseable → server time is used.
- The transaction must be in `ready` or `locked` state.

## 7. Endpoint: Payment Failure

`POST /api/hospital/fiscal/payment/failure`

```json
{
  "terminal_code": "SUNMI-001",
  "api_key": "test-secret",
  "transaction_reference": "FISC00001",
  "external_transaction_id": "ETTA-TEST-0001",
  "failure_reason": "Customer cancelled",
  "idempotency_key": "failure-001"
}
```

Response (`200`): `{ "status": "ok", "transaction_reference": "FISC00001", "state": "failed" }`

A failure callback for an **already paid** transaction is ignored and returns
`409 already_paid`. Failure callbacks for transactions already in a final state
are logged and returned idempotently.

## 8. Endpoint: Status

`POST /api/hospital/fiscal/status`

```json
{
  "terminal_code": "SUNMI-001",
  "api_key": "test-secret",
  "transaction_reference": "FISC00001"
}
```

Response (`200`):

```json
{
  "status": "ok",
  "transaction_reference": "FISC00001",
  "state": "paid",
  "bill_reference": "BILL00004",
  "amount_payable": 3050.00,
  "amount_due": 0.00
}
```

`state` is one of: `draft`, `ready`, `locked`, `paid`, `failed`, `expired`,
`cancelled`, `exception`, `reversed`. `amount_due` is the **live** remaining due
on the linked bill (0.00 once settled).

## 9. Idempotency Rules

- **Retry-safe success callback:** if a success callback arrives for a
  transaction that is already Paid **with the same** `external_receipt_no`,
  `external_transaction_id` or `idempotency_key`, Odoo does **not** create a
  second payment and answers (live-tested):

```json
{
  "status": "accepted",
  "duplicate": true,
  "transaction_reference": "FISC00001",
  "bill_reference": "BILL00004",
  "hospital_payment_reference": "PAY00004",
  "bill_state": "paid",
  "amount_due": 0.00
}
```

  → Terminals should treat `status: accepted` with `duplicate: true` exactly
  like a normal acceptance (safe to retry on network timeouts).

- A success callback for a paid transaction with **different** external
  references is rejected (`409`, `error_code: rejected`) and logged for review.

## 10. Duplicate Receipt Rules

An `external_receipt_no` or `external_transaction_id` that was already used to
pay **another** fiscal transaction is treated as a fraud/error signal. The
target transaction moves to **Exception / Manual Review**, no payment is
created, and the response is (`409`, live-tested with `ZRY-TEST-0001`):

```json
{
  "status": "error",
  "error_code": "exception",
  "message": "External receipt/transaction id already used by another fiscal transaction. Moved to manual review."
}
```

## 11. Amount Validation Rules

`amount_paid` must equal the prepared `amount_payable` exactly (tolerance
0.005). Any other value moves the transaction to Exception with no payment
(`409`, live-tested: 1,000.00 sent against 2,500.00):

```json
{
  "status": "error",
  "error_code": "exception",
  "message": "Amount mismatch: expected 2500.00. Transaction moved to manual review. No payment was created."
}
```

Once in Exception, further success callbacks are rejected until staff resolve it:

```json
{
  "status": "error",
  "error_code": "rejected",
  "message": "Transaction is not payable (state: exception)."
}
```

**Partial payment is not supported** — see section 15.

## 12. Error Response Format

All errors share one shape:

```json
{ "status": "error", "error_code": "<code>", "message": "<human readable>" }
```

| HTTP | error_code | Meaning |
|---|---|---|
| 400 | `invalid_request` | Malformed/non-JSON body |
| 401 | `auth_required` / `auth_failed` | Missing or wrong device credentials |
| 403 | `ip_not_allowed` | Caller IP not in the device allowlist |
| 404 | `not_found` | Unknown transaction reference (exact match only) |
| 409 | `expired` | Payment window passed |
| 409 | `locked_by_other_terminal` | Another device holds the lock |
| 409 | `not_payable` | State does not allow payment/lookup |
| 409 | `already_paid` | Failure callback on a paid transaction |
| 409 | `exception` | Moved to manual review (amount mismatch / duplicate receipt) |
| 409 | `rejected` | Callback refused (e.g. exception state, mismatched references on a paid transaction) |
| 500 | `internal_error` | Unexpected server error (logged; safe to retry with the same idempotency key) |

## 13. Example PowerShell Test Commands

```powershell
# Status
Invoke-WebRequest -Uri "http://localhost:8069/api/hospital/fiscal/status" `
  -Method Post -ContentType "application/json" `
  -Body '{"terminal_code":"SUNMI-001","api_key":"test-secret","transaction_reference":"FISC00001"}'

# Lookup (locks the transaction)
Invoke-WebRequest -Uri "http://localhost:8069/api/hospital/fiscal/lookup" `
  -Method Post -ContentType "application/json" `
  -Body '{"terminal_code":"SUNMI-001","api_key":"test-secret","bill_reference":"FISC00001","idempotency_key":"lookup-001"}'

# Payment success
Invoke-WebRequest -Uri "http://localhost:8069/api/hospital/fiscal/payment/success" `
  -Method Post -ContentType "application/json" `
  -Body '{"terminal_code":"SUNMI-001","api_key":"test-secret","transaction_reference":"FISC00001","external_receipt_no":"ZRY-TEST-0001","external_transaction_id":"ETTA-TEST-0001","amount_paid":3050.00,"payment_method":"cash","paid_at":"2026-07-06T11:26:00+03:00","idempotency_key":"success-001"}'

# Payment failure
Invoke-WebRequest -Uri "http://localhost:8069/api/hospital/fiscal/payment/failure" `
  -Method Post -ContentType "application/json" `
  -Body '{"terminal_code":"SUNMI-001","api_key":"test-secret","transaction_reference":"FISC00001","external_transaction_id":"ETTA-TEST-0001","failure_reason":"Customer cancelled","idempotency_key":"failure-001"}'
```

## 14. Production Security Hardening

Required before go-live:

- Replace every test API key with a long random secret; rotate per terminal.
- HTTPS only (TLS at the reverse proxy); never send keys over plain HTTP.
- Fill per-device IP allowlists.
- Add HMAC request signing and timestamp/replay protection (agree the signing
  contract with ETTA/Zoorya).
- Archive lost/stolen terminals immediately (instant API kill switch).
- Monitor `401/403` responses and Exception transactions daily.
- Keep `dbfilter` correct after any server/database change.
- Keep separate device records (and keys) for test vs production.

The fiscal certification behavior (receipt numbering, TIN, fiscal memory,
reporting) is owned by the Zoorya/SUNMI layer and must be confirmed with ETTA
Solutions — it is outside this module.

## 15. Future Enhancement: Partial Fiscal Payment

Not currently supported. The design direction: **Odoo prepares a fiscal
transaction for the intended partial amount** (respecting patient/payer
responsibility), and the terminal still pays exactly the prepared amount. The
terminal will never decide amounts on its own. Until then, partial payments are
handled internally through the hospital's Register Payment flow.
