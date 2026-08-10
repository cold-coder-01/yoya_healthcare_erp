# Hospital Billing Accounting Bridge

## Task 32B-1 — patient-advance accounting foundation

Version 18.0.1.1.0 adds explicit company configuration for:

* `305410 Patient Advances` — reconciliable current liability;
* `305420 Patient Credits and Refundable Advances` — separate reconciliable
  current liability, so ordinary unapplied advances are not called patient credit;
* `PADV Patient Advances` — general journal for future receipt and application;
* `PREF Patient Advance Refunds` — general journal for future approved refunds.

The migration stops on account/journal code collisions instead of choosing another
account silently. It reuses the single existing company cash (`101201`), bank/card
(`101202`) and mobile-money (`101203`) mappings and fills the previously incomplete
admission mapping. It creates no `account.move`, invoice, payment, reconciliation,
refund or fiscal record.

Configuration writes are checked server-side for Accountant, Manager or System
Administrator membership, tracked in the chatter, constrained to allowed companies,
and validated for current-liability type and reconciliation. Delete remains
System-Administrator-only.

Technical name: `hospital_billing_accounting`

## Purpose

A **bridge** module that connects the existing Hospital Billing system
(`hospital_billing`) to Odoo 18 Accounting Community (`om_account_accountant`).

It does **not** create a second billing system, does **not** re-create hospital
bills, and does **not** create Odoo customer invoices. It only:

1. Maps hospital bill **source types** and **payment methods** to accounting
   accounts and journals.
2. Posts **manual** journal entries for bills and payments when an authorized
   user explicitly clicks a button.
3. Keeps an audit trail of every accounting action.

There is **no automatic posting**. Nothing reaches accounting without an
explicit button click.

## Architecture

```
hospital_billing            -> operational patient bills, cashier receipts, payment history
om_account_accountant       -> accounting engine: chart of accounts, journals, journal entries
hospital_billing_accounting -> bridge: posts hospital bills & payments into accounting
```

The bridge:

* Adds accounting state/log fields to `hospital.patient.bill` and
  `hospital.patient.bill.payment` (via `_inherit`, no core/source changes).
* Provides a configuration model to map source types to accounts/journals.
* Provides an audit log model.

## Dependency on om_account_accountant

`om_account_accountant` (and its dependency on the core `account` module) provides
the standard accounting models the bridge relies on. The manifest depends on
`hospital_billing`, `om_account_accountant`, and `mail`.

## Accounting models used (standard Odoo)

* `account.move` — journal entry (created with `move_type = "entry"`)
* `account.move.line` — journal entry lines (debit/credit)
* `account.account` — chart of accounts
* `account.journal` — journals

Journal entries are created in **draft** and then posted with the standard
`account.move.action_post()` method.

## Configuration model — `hospital.billing.accounting.config`

One configuration per **company + source type**. Fields:

| Field | Purpose |
|-------|---------|
| `name` | Label |
| `company_id` | Company (default: current) |
| `active` | Active flag |
| `source_type` | consultation / laboratory / radiology / pharmacy / admission / procedure / surgery / other |
| `receivable_account_id` | Patient receivable account (required) |
| `revenue_account_id` | Revenue account for this source type (required) |
| `cash_account_id` | Cash payment account |
| `bank_account_id` | Bank/card payment account |
| `mobile_money_account_id` | Mobile money payment account |
| `journal_id` | Bill journal (required) |
| `payment_journal_id` | Payment journal (falls back to `journal_id`) |
| `notes` | Free text |

Display name: `Company / Source Type`. A SQL + Python constraint enforces a
single active config per company/source type.

## Bill posting flow

1. Open a **confirmed**, **partially paid**, or **paid** bill.
2. Click **Mark Ready for Accounting** → `accounting_state = ready`, a
   `bill_ready` log is written.
3. Click **Post Bill to Accounting**. The bridge:
   * Validates state, lines, config completeness, and duplicate protection.
   * Groups bill lines by `source_type`, summing `subtotal` per source.
   * **Skips zero/negative lines** — a bill line whose `subtotal <= 0` is ignored,
     and any grouped source whose rounded total is `<= 0` is dropped, so the
     journal entry never contains a `0.00` revenue line.
   * If **no positive-value lines remain**, posting is blocked with
     `UserError`: *"Cannot post accounting entry because the bill has no
     positive-value lines."*
   * Creates one `account.move`:
     * **Debit** receivable account = sum of the positive revenue lines (not
       blindly `amount_total`)
     * **Credit** revenue account per positive source type = summed subtotal
   * Uses **currency rounding** for all amounts. The credit total is validated
     against `amount_total`: rounding noise is tolerated, but a material
     mismatch raises a `UserError` explaining the discrepancy.
   * Calls `action_post()`.
   * On success: `accounting_state = posted`, `accounting_move_id` set,
     `accounting_posted = True`, success log written.
   * If `action_post()` fails: `accounting_state = error`, error stored, failed
     log written — **no fake posted state**.
   * If `account.move` creation itself fails: error logged, state set to
     `error`, and a `UserError` is raised.

### Journal entry example (bill)

Bill `BILL00001` with Radiology 2,500, Laboratory 250, Consultation 300,
Pharmacy 0:

| Account | Debit | Credit |
|---------|------:|-------:|
| Patient Receivable | 3,050 | |
| Radiology Revenue | | 2,500 |
| Lab Revenue | | 250 |
| Consultation Revenue | | 300 |

The Pharmacy 0 line is **not** posted — no `Pharmacy Revenue 0.00` line is
created.

### Duplicate posting protection

* A bill is blocked from posting if `accounting_state == posted` **or**
  `accounting_move_id` is already set (even if that move is still in draft) —
  `UserError`: *"This bill already has an accounting journal entry."*
* `Mark Ready for Accounting` refuses to revert a posted bill back to `ready`.
* `Post Bill to Accounting` can run only from `ready` or `error`.
* A payment is blocked if `accounting_state == posted` **or**
  `accounting_move_id` is set — `UserError`: *"This payment has already been
  posted to accounting."*

### Accounting state behavior

`hospital.patient.bill.accounting_state`:

| State | Meaning |
|-------|---------|
| `not_posted` | No accounting move and not marked ready |
| `ready` | Marked ready, no move posted yet |
| `posted` | `accounting_move_id` set and move posted |
| `error` | Last posting attempt failed (see `accounting_error`) |
| `reversed` | Reserved for Task 29B |

* On successful posting, `accounting_error` is cleared.
* On failure, `accounting_state = error` and `accounting_error` stores a useful
  message; the bill can be retried from the `error` state.

`hospital.patient.bill.payment.accounting_state`: `not_posted` / `posted` /
`error`, with the same clear-on-success / set-on-failure behavior.

## Payment posting flow

From the **Payment History / Payments** tab on the bill, an authorized user
clicks **Post Payment to Accounting**. The tab shows each payment's
`accounting_state` (as a colored badge), the linked journal entry
(`accounting_move_id`), a **Post Payment to Accounting** button (when not yet
posted) and a **View Accounting Entry** button (when a move exists). The bridge:

* Validates amount > 0, bill link, config presence, receivable + payment account.
* Resolves the payment account from the payment method:
  * `cash` → `cash_account_id`
  * `bank_transfer` / `card` → `bank_account_id`
  * `mobile_money` (and telebirr / cbe_birr / mobile variants) → `mobile_money_account_id`
  * unknown → `UserError`: "No accounting payment account configured for this payment method."
* Creates one `account.move`:
  * **Debit** cash/bank/mobile account = `amount`
  * **Credit** receivable account = `amount`
* Posts it; on success sets `accounting_state = posted` and writes a log; on
  failure sets `error`, logs, and raises `UserError`.

### Journal entry example (payment)

Payment of 1,000 by cash for `BILL00001`:

| Account | Debit | Credit |
|---------|------:|-------:|
| Cash | 1,000 | |
| Patient Receivable | | 1,000 |

## Audit log — `hospital.accounting.posting.log`

Sequence-numbered (`HACC00001`, prefix `HACC`, padding 5). Records event type
(`bill_ready` / `bill_post` / `payment_post` / `reversal` / `error`), status
(`draft` / `success` / `failed`), the related bill/payment, the journal entry,
debit/credit totals, and a message.

The `raw_payload` field stores a readable JSON snapshot of each successful
posting (billing/accounting detail only — **no clinical data**):

* **Bill posting:** bill name, currency, debit account + total, list of credit
  accounts with source type and amount, credit total.
* **Payment posting:** payment name, bill name, payment method, currency, debit
  account, credit receivable account, amount.

### View Accounting Entry

* On the bill **Accounting** tab, `accounting_move_id` is shown and a header
  **View Accounting Entry** button (visible only when a move exists) opens the
  journal entry.
* On the **Payments** tab, each posted payment exposes a **View Accounting
  Entry** row button.

## Security

Uses the existing `hospital_management` groups:

| Group | Config | Logs |
|-------|--------|------|
| Accountant | read/create/write (no delete) | read/create/write (no delete) |
| Manager | read/create/write (no delete) | read/create/write (no delete) |
| System Administrator | full | full |
| Receptionist | — | read-only |
| Data Protection Officer | read-only | read-only |
| Doctor / Nurse / Pharmacist / Lab | no access | no access |

Posting / Mark Ready / Post Payment buttons are restricted (via `groups`) to
Accountant, Manager, and System Administrator. Inherited bill/payment fields
rely on the existing `hospital_billing` ACLs.

## Manual test checklist

1. Ensure `om_account_accountant` is installed.
2. Install/upgrade `hospital_billing_accounting`.
3. Create accounts: Patient Receivable, Consultation/Lab/Radiology/Pharmacy/
   Admission/Procedure/**Surgery (401107 Surgery Revenue)** Revenue, Cash, Bank,
   Mobile Money.
4. Create journals: Hospital Sales/General journal (+ Cash/Bank if needed).
5. Create an Accounting Config for each source type.
6. Open a confirmed/paid bill → **Mark Ready for Accounting** → state = `ready`,
   `HACC00001` log created.
7. **Post Bill to Accounting** → `account.move` created, state = `posted`,
   `accounting_move_id` set, debit == credit.
8. Open the journal entry: Debit receivable, Credit revenue by source type.
9. Register a payment, then **Post Payment to Accounting** → balanced entry.
10. Confirm no RPC errors and existing billing UI still works.

## Known limitations

* No automatic posting on bill confirmation yet.
* No automatic posting on payment registration yet.
* No tax/VAT/WHT handling yet.
* **Bill cancellation after accounting posting is not auto-reversed.** Once a
  bill (or payment) has a posted journal entry, cancelling/refunding it on the
  hospital side does **not** create a reversing entry. This must currently be
  done **manually** in Accounting (reverse the `account.move`). Automated
  reversal is deferred to **Task 29B**.
* No bank reconciliation import yet.
* No mobile-money settlement matching yet.
* No multi-company advanced mapping yet.
* No detailed cost-of-service accounting yet.
* No stock/material costing yet.

## Task 29A — Cleanup & Hardening (completed)

* **Zero-value accounting lines skipped** — bill lines with `subtotal <= 0` and
  grouped source totals `<= 0` are never posted; no `0.00` revenue lines.
* Posting a bill with no positive-value lines is blocked with a clear
  `UserError`.
* Debit receivable equals the sum of positive revenue lines, with currency
  rounding and a material-mismatch guard against `amount_total`.
* **Duplicate posting protection strengthened** for both bills and payments
  (blocks on `posted` state or an existing move, even draft).
* **Accounting state behavior clarified** — `Mark Ready` cannot revert a posted
  bill; posting runs only from `ready`/`error`; error clears on success.
* **Payment accounting visibility improved** — state badge, journal entry
  column, post + view buttons on the Payments tab.
* **View Accounting Entry** actions added for bills and payments.
* **Posting log `raw_payload`** now carries readable billing/accounting detail.

### Files modified (Task 29A)

* `models/patient_bill_accounting.py`
* `views/patient_bill_accounting_views.xml`
* `README.md`

### Manual test checklist (Task 29A)

1. Upgrade `hospital_billing_accounting`.
2. Open a bill with Radiology 2500, Laboratory 250, Consultation 300,
   Pharmacy 0 → **Mark Ready** → **Post Bill to Accounting**.
3. Journal entry posted; **no** `Pharmacy Revenue 0.00` line; Debit Receivable
   = 3050; Credit Radiology 2500 / Lab 250 / Consultation 300; debit == credit.
4. Click **Post Bill** again → blocked (duplicate `UserError`).
5. Try posting a fully zero-value bill → blocked (no positive-value lines).
6. Register a cash payment → **Post Payment to Accounting** → Debit Cash,
   Credit Receivable.
7. Post the same payment again → blocked (duplicate payment `UserError`).
8. Check **Posting Logs** → `raw_payload` and messages are clear; no RPC error.

## Next recommended task

**Task 29B — Accounting Reversal / Cancellation Handling**: automatically
reverse posted journal entries when a bill or payment is cancelled/refunded,
plus optional automatic posting with a per-company switch.
