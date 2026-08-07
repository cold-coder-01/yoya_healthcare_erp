"""Convert legacy single-charge receipts into payment HEADERS with one allocation.

Before: hospital.charge.receipt.charge_id + a plain, independently-written
        receipt.amount column, and a plain charge.amount_received column -- two
        sources of truth that could drift.
After:  one allocation per legacy receipt. receipt.amount and charge.amount_received
        are now stored COMPUTES derived from the allocations.

The receipt keeps its reference, amount, date, method, user, audit link, intake token
and downstream flags. No second receipt is created and no reference changes.

Key detail: the legacy `amount` and `amount_received` COLUMNS already hold the correct
values (300 for RCPT000001). Once the allocation row exists they are consistent with
the new computes, so we do NOT need a fragile ORM recompute during migration -- any
later recompute derives the same figure from the allocation.
"""

import logging

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        return

    # (The stale CHECK(amount>0) constraint was dropped in pre-migrate, before the
    #  model reloaded, so the init-time recompute of amount could set 0 safely.)

    # 1. One allocation per legacy receipt: full ORIGINAL amount (captured in
    #    pre-migrate), to the charge it was for. Skip any receipt that already has an
    #    allocation, so re-running the upgrade is idempotent.
    cr.execute("""
        SELECT m.receipt_id, r.name, m.charge_id, m.amount
        FROM _hb_receipt_migration_1_1_0 m
        JOIN hospital_charge_receipt r ON r.id = m.receipt_id
        WHERE NOT EXISTS (
            SELECT 1 FROM hospital_charge_receipt_allocation a
            WHERE a.receipt_id = m.receipt_id
        )
        ORDER BY m.receipt_id
    """)
    legacy = cr.fetchall()

    migrated = 0
    for rid, name, charge_id, amount in legacy:
        if not amount or amount <= 0:
            _logger.warning(
                "  receipt %s has captured amount %s -- skipped, needs manual review",
                name, amount)
            continue
        cr.execute("""
            INSERT INTO hospital_charge_receipt_allocation
                (receipt_id, charge_line_id, amount, create_uid, write_uid,
                 create_date, write_date)
            VALUES (%s, %s, %s, 1, 1, now(), now())
        """, (rid, charge_id, amount))
        migrated += 1
        _logger.info("  %s -> 1 allocation of %.2f to charge id=%s",
                     name, amount, charge_id)

    cr.execute("DROP TABLE IF EXISTS _hb_receipt_migration_1_1_0")

    # 2. The init-time recompute set amount / amount_received to 0 (no allocations
    #    existed yet). Now that the allocations are in, re-derive the stored computes
    #    -- and, crucially, their DEPENDENTS (payment_state, amount_prepayment_held,
    #    amount_due_for_clearance, the account rollups). A plain SQL write of the
    #    columns would not refresh that chain, so CHRG000001 could look 'unpaid'.
    env = api.Environment(cr, SUPERUSER_ID, {})

    Alloc = env["hospital.charge.receipt.allocation"]
    Receipt = env["hospital.charge.receipt"]
    Charge = env["hospital.charge.line"]

    allocs = Alloc.search([])
    receipts = Receipt.search([])
    charges = Charge.with_context(active_test=False).search([])
    allocs.invalidate_recordset()
    receipts.invalidate_recordset()
    charges.invalidate_recordset()

    # The allocations were inserted by raw SQL. Declaring the base fields I wrote as
    # `modified` lets the ORM dependency graph cascade the recompute of EVERYTHING
    # downstream -- the stored related mirrors (receipt_state etc.), receipt.amount,
    # charge.amount_received, and in turn payment_state / amount_prepayment_held /
    # amount_due_for_clearance and the account rollups. No fragile per-field list.
    allocs.modified(["amount", "receipt_id", "charge_line_id"])
    env.flush_all()

    _logger.info(
        "hospital_billing: migrated %d legacy receipt(s); recomputed %d receipt(s) "
        "and %d charge(s)", migrated, len(receipts), len(charges))
