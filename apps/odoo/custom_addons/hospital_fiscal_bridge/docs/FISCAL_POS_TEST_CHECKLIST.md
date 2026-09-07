# Fiscal POS Bridge — Test Checklist

Acceptance/regression checklist for `hospital_fiscal_bridge`. Run after every
module upgrade, server migration, or terminal change. All items below passed
live testing on `healthcare_erp` on 2026-07-06 (references FISC00001–FISC00003).

Prerequisites: module installed; `dbfilter = ^healthcare_erp$` in odoo.conf;
a confirmed patient bill with amount due > 0; PowerShell examples in the
[API Integration Guide](FISCAL_POS_API_INTEGRATION_GUIDE.md).

| # | Check | Steps | Expected result | Pass/Fail | Notes |
|---|---|---|---|---|---|
| 1 | [ ] Create Fiscal Device | Billing ▸ Fiscal POS Bridge ▸ Fiscal Devices ▸ New (`SUNMI-001`, API key set) | Device saved, Active, API key masked in UI | | |
| 2 | [ ] Confirm bill | Open a draft bill ▸ Confirm Bill | State Confirmed, Amount Due > 0 | | |
| 3 | [ ] Prepare Fiscal Payment | Button on the bill | FISC reference created in **Ready**; snapshot lines, barcode and `YOYA-FISC:` QR payload present; amounts frozen | | |
| 4 | [ ] Status endpoint returns ready | POST `/api/hospital/fiscal/status` | 200 JSON, `"state": "ready"` | | |
| 5 | [ ] Lookup locks transaction | POST `/api/hospital/fiscal/lookup` | 200 JSON with amount, minimal `patient_display`, lines; transaction becomes **Locked** | | |
| 6 | [ ] Success callback creates exactly one payment | POST `/payment/success` with the exact amount | 200 `"status": "accepted"`; PAYxxxxx in bill Payment History; transaction **Paid**; bill Paid/Partially Paid | | Live: PAY00004, bill Paid |
| 7 | [ ] Duplicate success callback creates no second payment | Re-send the identical success callback | 200 `"accepted"`, `"duplicate": true`; still exactly one payment line | | |
| 8 | [ ] Duplicate receipt on another transaction is rejected | New FISC on another bill; success callback reusing the previous `external_receipt_no` | 409 `error_code: exception`; transaction **Exception**; no payment | | Live: `ZRY-TEST-0001` on FISC00002 |
| 9 | [ ] Amount mismatch is rejected | New FISC; success callback with a wrong amount | 409 `error_code: exception`, "Amount mismatch"; transaction **Exception**; no payment | | Live: 1,000.00 vs 2,500.00 on FISC00003 |
| 10 | [ ] Exception transaction cannot be paid | Re-send success callback to the Exception transaction | 409 `error_code: rejected`, "not payable (state: exception)"; no payment | | |
| 11 | [ ] Failure callback | POST `/payment/failure` on a Ready/Locked transaction | 200; transaction **Failed**; reason in notes/logs | | |
| 12 | [ ] Logs recorded | Transaction ▸ Logs tab / Fiscal Logs menu | Entries for lookup, success/failure, errors; no `api_key` values stored | | |
| 13 | [ ] Privacy check | Inspect lookup/status JSON | Only code + initials for the patient; no clinical/contact data | | |
| 14 | [ ] API still works after UI polish | Repeat check 4 after any view/asset change | 200 JSON unchanged | | |
| 15 | [ ] Diagnostic ping route removed | GET `/hospital_fiscal_bridge/ping` | **404** (route must not exist) | | |
| 16 | [ ] dbfilter configured correctly | Check odoo.conf key `dbfilter` (not `db_filter`); restart; watch odoo.log | API calls logged as `INFO healthcare_erp werkzeug`, never `INFO ?` | | |
| 17 | [ ] Billing UI regression | Open Patient Bills form; Register Payment wizard | No RPC/view errors; Register Payment still works for manual cases | | |

**Failure triage:** any 404 on all endpoints → check #16 first. Payment count
wrong → inspect Fiscal Logs and the transaction chatter before retrying;
Exception states are intentional blocks, not bugs.
