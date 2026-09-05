# Fiscal POS Bridge — Operator Guide

**Audience:** Cashiers, front desk staff, supervisors, and finance users of the
YOYA Hospital ERP.

**What this covers:** taking patient payments through the SUNMI/Zoorya fiscal
terminal, checking the results in Odoo, and what to do when something goes wrong.

---

## 1. Overview for Cashiers

The hospital ERP (Odoo) is where all bills live. The SUNMI/Zoorya terminal is a
**fiscal cash register**: it collects the money and prints the official fiscal
receipt. It does not change bills, patients or prices — it only pays exactly
what Odoo prepared.

Your job in one sentence: **prepare the fiscal payment in Odoo, let the patient
pay on the terminal, and confirm the bill became Paid in Odoo.**

## 2. Normal Fiscal Payment Workflow

```
Confirmed bill → Prepare Fiscal Payment → FISC reference (Ready)
→ terminal scans/enters FISC reference → patient pays on terminal
→ terminal prints fiscal receipt → Odoo records the payment automatically
→ bill becomes Paid (or Partially Paid if other charges remain)
```

## 3. Step-by-Step: Taking a Patient Payment Using SUNMI/Zoorya

1. Open **Billing ▸ Patient Bills** and open the patient's bill.
2. Check the bill is **Confirmed** (or Partially Paid) and **Amount Due** is
   greater than 0. Draft bills must be confirmed first.
3. Click **Prepare Fiscal Payment**. Odoo creates a fiscal transaction with a
   reference like **FISC00001** in **Ready** state and shows it on screen.
4. Give the reference to the terminal: scan the barcode/QR or type the FISC
   number on the SUNMI/Zoorya terminal.
5. The terminal shows the exact payable amount (e.g. **3,050.00 ETB**). In Odoo
   the transaction switches to **Locked (In Progress)** — this is normal.
6. Collect the money on the terminal. The terminal prints the **fiscal receipt**
   — give it to the patient.
7. Within a few seconds the terminal confirms to Odoo automatically. Refresh the
   bill: a payment (e.g. **PAY00004**) appears in Payment History and the bill
   becomes **Paid**.

> The fiscal payment request expires after a set time (default 2 hours). If it
> expires before payment, a supervisor/accountant can reset it to Ready.

## 4. What Happens in Odoo After Terminal Payment

Automatically, with no extra clicks:

- The fiscal transaction becomes **Paid** and stores the terminal's receipt
  number and transaction id.
- An official payment record (**PAYxxxxx**) is created in the bill's Payment
  History — the same kind of record as any other payment.
- The bill state updates through the normal billing rules (Paid / Partially Paid).
- Every step is written to the fiscal logs for audit.

## 5. How to Check Fiscal Transactions

**Billing ▸ Fiscal POS Bridge ▸ Fiscal Transactions** — or open the bill and use
**View Fiscal Transactions** / the **Fiscal POS** tab.

State colors:

| State | Meaning |
|---|---|
| Ready (blue) | Waiting for the terminal to scan it |
| Locked / In Progress (amber) | Terminal picked it up; payment underway |
| Paid (green) | Money received; hospital payment created |
| Failed (red) | Terminal reported failure/cancellation |
| Exception / Manual Review (red) | Something unsafe happened — do not retry blindly (see section 8) |
| Expired / Cancelled (grey) | Window passed or request was cancelled |

## 6. How to Check Payment History

Open the bill → **Payments** tab. Fiscal terminal payments look like any other
payment; the reference is the fiscal receipt number (e.g. `ZRY-...`) and the
notes say "Fiscal payment via … terminal". Amount Paid and Amount Due on the
bill update automatically.

## 7. What to Do If the Terminal Fails

- **Patient cancelled / card declined:** the terminal reports the failure and
  the transaction becomes **Failed**. A supervisor/accountant can **Reset to
  Ready** for another attempt, or **Cancel Request**.
- **Terminal offline / no response:** the transaction stays Ready or Locked.
  Wait, or ask your supervisor. Do not create a second fiscal request for the
  same bill — Odoo blocks duplicates while one is active.
- **Patient paid but bill not updated:** wait a moment and refresh. If it still
  doesn't update, **keep the fiscal receipt** and inform your supervisor /
  finance — do not take the money again.

## 8. What Exception / Manual Review Means

Odoo refused something unsafe and **did not record any payment**. The two causes:

1. **Amount mismatch** — the terminal reported a different amount than prepared
   (live-tested: 1,000.00 sent for a 2,500.00 request was rejected).
2. **Reused fiscal receipt** — the same receipt number was already used for
   another transaction (live-tested with `ZRY-TEST-0001`).

What to do: **stop and escalate to finance/supervisor.** They will check the
transaction's Logs tab, verify with the terminal report, and then either Cancel
the request or Reset it to Ready. Never retry an Exception without review.

## 9. Difference Between Prepare Fiscal Payment and Register Payment

Both are valid. They serve different purposes:

| | **Prepare Fiscal Payment** | **Register Payment** |
|---|---|---|
| Who uses it | Cashier, for normal patient payments | Finance/back-office, controlled cases |
| What it does | Starts the SUNMI/Zoorya fiscal terminal flow | Records a payment manually in Odoo |
| Fiscal receipt | Printed by the terminal | Not produced by Odoo |
| Typical cases | Standard cash/card patient payment at the desk | Partial payments, insurance/corporate payer settlement, reconciliation, corrections, admin-approved manual cases |

**Register Payment is not obsolete.** It remains the internal payment engine.
As a cashier, use **Prepare Fiscal Payment** for normal fiscal receipt payments;
use Register Payment only when instructed by finance/admin.

## 10. Partial Payment Note

The fiscal terminal currently accepts **only the exact prepared amount**. If a
patient wants to pay part of the bill, do **not** try it through the terminal —
it will be rejected as an amount mismatch. Partial payments are handled by
finance through Register Payment until a future enhancement lets Odoo prepare a
fiscal transaction for a chosen partial amount.

## 11. Do's and Don'ts

**Do**
- Confirm the bill before preparing the fiscal payment.
- Give the patient the fiscal receipt printed by the terminal.
- Verify the bill shows Paid before the patient leaves.
- Escalate Exceptions to finance immediately.

**Don't**
- Don't type amounts on the terminal manually — always scan/enter the FISC reference.
- Don't prepare a second fiscal request while one is Ready/Locked.
- Don't use Register Payment for a normal terminal payment unless finance tells you to.
- Don't retry an Exception transaction without supervisor review.
- Don't share terminal API keys or settings — those are admin-only.

## 12. Quick Troubleshooting Table

| Problem | What it usually means | What to do |
|---|---|---|
| "Prepare Fiscal Payment" button not visible | Bill is Draft/Paid/Cancelled, or due is 0, or an active fiscal request already exists | Confirm the bill / open the existing FISC transaction |
| Terminal says "not found" | Wrong reference typed | Re-scan / re-type the exact FISC number |
| Terminal says "not payable" | Transaction already paid, cancelled, expired or in Exception | Check the transaction state in Odoo |
| Transaction Expired | Payment window passed (default 2 h) | Ask supervisor/accountant to Reset to Ready |
| Transaction Locked but patient left | Payment never completed | Supervisor cancels or lets it expire |
| Paid on terminal, bill not updated | Callback delayed or failed | Keep the receipt, refresh, escalate to finance if it persists |
| Exception / Manual Review | Amount mismatch or reused receipt | Escalate — never retry blindly |
