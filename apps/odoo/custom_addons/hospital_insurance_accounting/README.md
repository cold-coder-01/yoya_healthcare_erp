# Hospital Insurance Accounting Bridge

Task 33B — Insurance Accounting Bridge Foundation.
Task 33C — Split Patient / Payer payment control and payer payment posting.

## Purpose

This module reclassifies the **payer share** of an insurance / corporate /
government / NGO / donor / credit claim from the generic **Patient Receivable**
account to a dedicated **payer receivable** account, and posts **payer
payments** against that payer receivable — keeping patient and payer
collections strictly separated.

It is a *thin accounting bridge*. It does **not** re-post revenue, and does
**not** implement write-off, rejection transfer, or reversal accounting. Those
are future tasks.

## Split payment model

An insurance / corporate direct-payer bill carries **two** responsibilities:

- **Patient responsibility** — cleared only by patient payments against
  **Patient Receivable**.
- **Payer responsibility** — cleared only by payer payments against the
  dedicated **Payer Receivable** (e.g. Corporate Receivable).

### Patient payment vs. payer payment

| | Patient payment | Payer payment |
|---|---|---|
| Entry point | Bill → Register Payment wizard | Claim → **Post Payer Payment** wizard |
| Creates | `hospital.patient.bill.payment` (`payment_source = patient`) | `account.move` only (no bill payment record) |
| Clears | Patient Receivable | Payer (Corporate/Insurance/…) Receivable |
| Capped at | remaining **patient** responsibility | remaining **amount due from payer** |

Payer payments never create a `bill.payment` row, so they never reduce the
patient's `amount_due` or mislabel patient collection. The bill exposes split
balances (`patient_amount_paid/due`, `payer_amount_paid/due`,
`total_responsibility_paid/due`) on the **Insurance / Payer** tab.

### Worked example

Bill 3,050 · coverage 80% · patient 610 · corporate 2,440.

```
1. Bill posting (billing bridge):
   Dr Patient Receivable   3,050
       Cr Revenue              3,050

2. Reclassify payer share (this module):
   Dr Corporate Receivable 2,440
       Cr Patient Receivable   2,440

3. Patient pays 300:
   Dr Cash/Bank              300
       Cr Patient Receivable     300

4. Patient pays 310:
   Dr Cash/Bank              310
       Cr Patient Receivable     310

5. Corporate pays 2,440 (Post Payer Payment):
   Dr Bank                 2,440
       Cr Corporate Receivable 2,440

Final: Patient Receivable = 0, Corporate Receivable = 0,
       revenue recognized only once.
```

### Why payer payment must happen after reclassification

Until reclassification runs, the payer share still sits in **Patient
Receivable**. Posting a payer payment before reclassification would credit the
wrong account and leave both balances wrong. The **Post Payer Payment** button
is therefore only available once `accounting_state = receivable_reclassified`
(or `partially_paid`) and the reclassification entry exists.

## Dependencies

- `hospital_insurance`
- `hospital_billing_accounting`
- `om_account_accountant`
- `mail`

## Why this module exists

The `hospital_billing_accounting` bridge already posts normal revenue when a
patient bill is confirmed:

```
Dr Patient Receivable   5,000
    Cr Surgery Revenue      5,000
```

For insurance / corporate direct-payer bills, the bill is first posted normally.
The payer share must then be reclassified out of the shared Patient Receivable
account into a payer-specific receivable so that patient vs. payer balances are
tracked separately and can be aged / collected independently.

## Accounting strategy

The bill posts revenue exactly once (in `hospital_billing_accounting`). This
module then posts a **single balanced reclassification entry** per claim that
moves only the payer share between two balance-sheet receivable accounts. No
revenue, tax, or cash account is touched.

### Example entry

Bill total 5,000 · coverage 80% · patient share 1,000 · payer share 4,000.

Existing bill posting (billing bridge):

```
Dr Patient Receivable   5,000
    Cr Surgery Revenue      5,000
```

Reclassification (this module):

```
Dr Corporate Receivable 4,000
    Cr Patient Receivable   4,000
```

## Chart of accounts required

Configure these (or equivalents) and map them in the accounting configuration:

| Code   | Account                        | Role                          |
|--------|--------------------------------|-------------------------------|
| 101101 | Patient Receivable             | Credit side (all provider types) |
| 101110 | Insurance Receivable           | Debit — insurance             |
| 101120 | Corporate Receivable           | Debit — corporate             |
| 101130 | Government Program Receivable   | Debit — government_program    |
| 101140 | NGO Receivable                 | Debit — ngo                   |
| 101150 | Donor Receivable (if used)     | Debit — donor                 |
| 101160 | Credit Agreement Receivable (if used) | Debit — credit_agreement |
| 501900 | Claim Rejection / Write-off Expense   | Debit — rejection write-off   |

## Setup steps

1. Go to **Insurance → Accounting Bridge → Insurance Accounting
   Configurations**.
2. Create one active configuration per provider type per company:
   - Provider Type (e.g. `corporate`)
   - Patient Receivable Account (credit side)
   - The matching payer receivable account (e.g. Corporate Receivable)
   - Reclassification Journal (e.g. Hospital Sales / Insurance Accounting Journal)
   - Company
3. Open a claim, click **Mark Ready for Accounting**, then **Reclassify Payer
   Receivable**.
4. Once reclassified, click **Post Payer Payment** to record payer settlements
   against the payer receivable. Configure a default account on the payment
   journal (or pick a bank/cash account in the wizard).

## Payer payment workflow

1. **Mark Ready for Accounting** → `ready`.
2. **Reclassify Payer Receivable** → `receivable_reclassified`
   (`Dr Payer Receivable / Cr Patient Receivable`).
3. **Post Payer Payment** (one or more times) →
   `Dr Bank/Cash / Cr Payer Receivable`. Each posting increases
   `amount_paid_by_payer`; the claim becomes `partially_paid` while a balance
   remains and `paid` once the payer balance reaches zero.

Payer payment is blocked unless: the claim is approved / partially approved /
reclassified; `accounting_state` is `receivable_reclassified` or
`partially_paid`; the reclassification entry exists; the amount is > 0 and does
not exceed the amount due from the payer; and a journal + payer receivable
account resolve from config.

## Validation rules

Reclassification is blocked unless:

- The claim has a linked bill (`bill_id`) and a payer (`provider_id`).
- The claim is not fully rejected.
- The claim state is `submitted`, `under_review`, `approved`,
  `partially_approved`, or `paid`.
- The payer / claim amount is greater than zero.
- The claim has no existing reclassification entry and is not already
  `receivable_reclassified` (duplicate protection).
- `accounting_state` is `ready` or `error`.
- The originating bill is already posted to accounting (when the billing bridge
  fields are present).
- An active configuration exists for the provider type and company, with the
  matching payer receivable account, patient receivable account, and journal.

Only Accountant, Manager, and System Administrator users can execute the
accounting buttons. Receptionist / clinical / DPO users get read-only access.

## Scope note — legacy payer payment button

The legacy claim **Register Payer Payment** button (from `hospital_insurance`)
silently marked a claim paid with no accounting. When this bridge is installed
that button is **hidden** and replaced by **Post Payer Payment**, which posts a
proper `Dr Bank / Cr Payer Receivable` entry. `hospital_insurance` itself is not
modified — the button is only hidden via view inheritance.

## Rejection resolution (partial approval / write-off)

A payer may approve less than the claimed amount. The rejected balance must be
resolved by an accountant with one of two controlled actions from the claim
(**Resolve Rejected Amount**), only after the payer receivable has been
reclassified. Both are manual — nothing is transferred or written off
automatically.

Worked example — bill 3,050, patient 610, claim 2,440, approved 2,000,
**rejected 440**:

**Option A — transfer rejected amount to patient**
```
Dr Patient Receivable    440   "Rejected Claim Transferred to Patient - CLAIMxxxxx"
    Cr Corporate Receivable  440   "Reduce Payer Receivable for Rejection - CLAIMxxxxx"
```
Effect: adjusted patient responsibility 1,050 (patient due becomes 440), payer
collectible 2,000 (payer due 0). Bill stays *Partially Paid* until the patient
pays the extra 440, then *Paid*.

**Option B — write off rejected amount**
```
Dr Claim Rejection / Write-off Expense  440   "Claim Rejection Write-off - CLAIMxxxxx"
    Cr Corporate Receivable                 440   "Reduce Payer Receivable for Write-off - CLAIMxxxxx"
```
Effect: patient due 0, payer due 0, write-off resolved 440. Bill becomes
*Paid / Settled*. **Total Collected (cash) = 2,610; Total Resolved = 3,050** —
the write-off is counted as resolved, never as cash collected.

Partial approval also feeds the payer side: `amount_due_from_payer` is based on
the **approved** amount, so payer payment never asks for the rejected portion.
The claim payer portion can read *Paid* while the rejection is still pending;
the **bill** is only fully settled once the rejection is resolved.

Config requires a **Write-off Expense Account** (e.g. `501900`). The resolution
wizard supports partial amounts and blocks over-resolution, resolution before
reclassification, zero amounts, missing journal/config/account, and missing
notes. Logs are written with `action_type = rejection_transfer` / `writeoff`.

## Known limitations

- No reversal automation yet.
- No payer aging dashboard yet (Task 33E).
- No external insurer API.
- No payment reconciliation against bank statements.

## Future tasks

- **Task 33E** — Insurance Aging Reports / payer aging dashboard.
