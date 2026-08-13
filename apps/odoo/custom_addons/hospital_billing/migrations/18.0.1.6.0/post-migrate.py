"""Phase 1A/1B: install the ACTIVE-agreement overlap exclusion constraint.

Upgrade counterpart of __init__.post_init_hook, which covers fresh installs.
Runs POST, so hospital_payer_agreement already exists by the time it fires.

Nothing else is migrated here on purpose:

  * No legacy hospital.insurance.provider data is imported. That migration is a
    separate, manually-invoked, dry-run-by-default script; doing it implicitly
    during an upgrade would create payer master data nobody reviewed.
  * res.company.payer_responsibility_mode needs no backfill. It is a required
    Selection with default 'off', so Odoo stamps every existing company at
    column-creation time. The explicit UPDATE below is a belt-and-braces guard
    for any row the default somehow missed, and is a no-op on a healthy upgrade.
"""

import logging

from odoo.addons.hospital_billing.db_constraints import (
    ensure_agreement_overlap_exclusion,
)

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        # Fresh install: post_init_hook owns this path.
        return

    outcome = ensure_agreement_overlap_exclusion(cr)
    _logger.info(
        "hospital_billing 18.0.1.6.0: agreement overlap exclusion constraint -> %s",
        outcome,
    )

    cr.execute(
        """
        UPDATE res_company
           SET payer_responsibility_mode = 'off'
         WHERE payer_responsibility_mode IS NULL
        """
    )
    if cr.rowcount:
        _logger.info(
            "hospital_billing 18.0.1.6.0: defaulted payer_responsibility_mode to "
            "'off' on %d company row(s).",
            cr.rowcount,
        )
