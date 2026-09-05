# External Odoo addons

Addons this repository **depends on but does not contain**. They must be present on
the `addons_path` before the modules in `custom_addons/` can be installed.

Everything under `apps/odoo/custom_addons/` is first-party and is tracked here. This
file exists for the one dependency group that is not ours, so a clean clone has an
honest answer to "what else does this need".

Odoo core addons (`base`, `web`, `mail`, `uom`, `account`, …) are not listed — they
ship with the Odoo server.

---

## Odoo Mates community accounting suite

**Required by:** `hospital_billing_accounting` (which declares `om_account_accountant`).

**Not required by the Doctor Consultation Core.** `yoya_emr_api`,
`yoya_clinical_bridge`, `yoya_reception_bridge`, `hospital_billing`,
`hospital_radiology`, `hospital_pharmacy` and `hospital_inventory` all install
without any of these. `hospital_billing_accounting` is a leaf: nothing in this
repository depends on it, so it can be left uninstalled if the accounting
integration is not being deployed.

**Why it is not vendored.** These are third-party modules maintained by Odoo Mates /
Walnut Software Solutions, on their own release cadence. Copying them into this
repository would fork them silently and make upstream fixes invisible. The licence
(LGPL-3) would permit redistribution; the reason not to is maintenance, not
licensing.

| Module | Version | Licence | Author (as declared in manifest) |
|---|---|---|---|
| `om_account_accountant` | 1.0.3 | LGPL-3 | Odoo Mates, Walnut Software Solutions, Odoo SA |
| `accounting_pdf_reports` | 1.0.3 | LGPL-3 | Odoo Mates, Odoo SA |
| `om_account_asset` | 1.0.0 | LGPL-3 | Odoo Mates, Odoo SA |
| `om_account_budget` | 1.0.1 | LGPL-3 | Odoo Mates, Odoo SA |
| `om_account_daily_reports` | 1.0.1 | LGPL-3 | Odoo Mates |
| `om_account_followup` | 1.0.2 | LGPL-3 | Odoo Mates, Odoo S.A |
| `om_fiscal_year` | 1.0.1 | LGPL-3 | Odoo Mates, Odoo SA |
| `om_recurring_payments` | 1.0.0 | LGPL-3 | Odoo Mates |

`om_account_accountant` is the only one named as a dependency by our code; the other
seven are its own transitive dependencies and are listed so the install set is
complete.

**Upstream.** The manifests declare `https://www.odoomates.tech`
(and `https://www.walnutit.com` for `om_account_accountant`). No download URL is
recorded here because none has been verified against a specific release artifact —
obtain the modules from the publisher and pin the versions in the table above.

**Currently deployed.** All eight are installed in the `healthcare_erp_phase1_test`
UAT database at the versions above (Odoo reports them as `18.0.1.0.x`, which is the
18.0 series prefix applied to the manifest version).

**Installation.** Place them on the `addons_path` alongside `custom_addons/`, for
example:

```
addons_path = <odoo>/addons, <repo>/apps/odoo/custom_addons, <path-to-external-addons>
```

---

## Nothing else is external

Every other dependency of every module in `custom_addons/` resolves either to another
module in `custom_addons/` or to an Odoo core addon. If a module is added here that
needs something new from outside, it belongs in this file.
