"""Phase 3C/3D: install the live-responsibility unique index.

Upgrade counterpart of __init__.post_init_hook, which covers fresh installs.
Runs POST, so hospital_charge_responsibility exists by the time it fires.

NO DATA BACKFILL, deliberately, and this is the whole point of the phase:

  * hospital.charge.responsibility is brand new. There are no historical rows,
    and none can be inferred -- the system has no coverage percentage, copay
    rate or benefit schedule from which a past visit's split could be derived.
    Manufacturing one would be a guess written into the ledger.

  * Every existing charge therefore has NO sponsor share, so its patient
    responsibility computes to the full amount_estimated, which is exactly what
    it was before this upgrade. The new stored computes backfill themselves to
    the legacy figures.

  * res_company.payer_responsibility_mode stays 'off'. Nothing changes for any
    company until an operator flips it, per company, deliberately.

The one thing that DOES need stating is the partial unique index, because Odoo
cannot express "unique on charge_id WHERE state is live" as an _sql_constraint.
"""

import logging

from odoo.addons.hospital_billing.db_constraints import (
    ensure_charge_responsibility_live_unique,
)

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        # Fresh install: post_init_hook owns this path.
        return

    outcome = ensure_charge_responsibility_live_unique(cr)
    _logger.info(
        "hospital_billing 18.0.1.10.0: live-responsibility unique index -> %s",
        outcome,
    )
