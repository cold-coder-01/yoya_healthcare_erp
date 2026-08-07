"""Drop the stale receipt amount>0 CHECK before the model reloads.

`hospital.charge.receipt.amount` changes from a plain stored column to a stored
COMPUTE in this version. During model init Odoo force-recomputes it, and a receipt
whose allocations do not exist yet momentarily computes to 0 -- which the old
CHECK(amount > 0) constraint would reject, aborting the whole upgrade.

The constraint is gone from the model (a brand-new draft header legitimately has
amount = 0 until its allocations are added), so drop it here, before init runs.
"""

import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        return

    cr.execute("""
        ALTER TABLE hospital_charge_receipt
        DROP CONSTRAINT IF EXISTS hospital_charge_receipt_receipt_amount_positive
    """)

    # Capture each legacy receipt's ORIGINAL charge + amount NOW, before the model
    # reloads. During init `amount` becomes a compute and is recomputed to 0 (no
    # allocations exist yet), so by post-migrate the live column no longer tells us
    # how much the receipt was for. This scratch table carries the truth across.
    cr.execute("DROP TABLE IF EXISTS _hb_receipt_migration_1_1_0")
    cr.execute("""
        CREATE TABLE _hb_receipt_migration_1_1_0 AS
        SELECT id AS receipt_id, charge_id, amount
        FROM hospital_charge_receipt
        WHERE charge_id IS NOT NULL
    """)
    cr.execute("SELECT count(*) FROM _hb_receipt_migration_1_1_0")
    n = cr.fetchone()[0]
    _logger.info(
        "hospital_billing: dropped stale amount>0 constraint; captured %d legacy "
        "receipt amount(s) for migration", n)
