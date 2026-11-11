# Third-party addons (kept outside Git)

These modules are **not YOYA code**. They live in `apps/odoo/external_addons/`,
which is excluded from Git via `.git/info/exclude`. That folder must stay on the
Odoo `addons_path`: YOYA modules depend on some of them
(`hospital_insurance_accounting` -> `om_account_accountant`,
`yoya_muk_appsbar_hover` -> `muk_web_appsbar`).

YOYA-authored modules that used to sit here (`hospital_dashboard`, `hospital_hr`,
`hospital_insurance`, `hospital_insurance_accounting`,
`hospital_operation_theatre`, `yoya_muk_appsbar_hover`) were moved into
`apps/odoo/custom_addons/` and are now tracked in Git.

A dated ZIP backup of the remaining `external_addons/` folder is kept outside
Git in `C:\Users\user\yoya-external-backup\` (`external_addons-<YYYYMMDD>.zip`).

All modules below declare `license: LGPL-3`.

## Odoo Mates accounting

Source: copied from the old PC. **Upstream commit unknown.**

| Module | Version |
|---|---|
| accounting_pdf_reports | 1.0.3 |
| om_account_accountant | 1.0.3 |
| om_account_asset | 1.0.0 |
| om_account_budget | 1.0.1 |
| om_account_daily_reports | 1.0.1 |
| om_account_followup | 1.0.2 |
| om_fiscal_year | 1.0.1 |
| om_recurring_payments | 1.0.0 |

## MuK IT web modules

Source: <https://github.com/muk-it/odoo-modules>, branch `18.0`, commit
`29aa95f86527ac762b87836019fee4bc093ad63a` (2026-11-10), from the local
checkout `C:\Users\user\muk-upstream-18.0`. The copies match that commit
except for line endings (CRLF).

| Module | Version |
|---|---|
| muk_web_appsbar | 18.0.1.1.6 |
| muk_web_chatter | 18.0.1.2.7 |
| muk_web_colors | 18.0.1.0.6 |
| muk_web_dialog | 18.0.1.0.5 |
| muk_web_theme | 18.0.1.2.5 |

Known oddity: `muk_web_theme/om_account_budget/` is a stray copy of
`om_account_budget`. It is not in upstream and is not imported by
`muk_web_theme`, so it has no effect.

## Payroll

| Module | Version | Source |
|---|---|---|
| payroll | 18.0.1.3.2 | OCA (<https://github.com/OCA/payroll>), upstream commit unknown |

## Walnut bank reconciliation

| Module | Version | Source |
|---|---|---|
| walnut_bank_reconciliation | 18.0.1.1.0 | Walnut Accounting, upstream commit unknown |
