"""Re-gate charges whose prepayment snapshot was taken from an unset service.

THE PROBLEM THIS EXISTS FOR
--------------------------
billing_basis is SNAPSHOTTED onto the charge at creation
(billing_engine.create_or_update_charge) and is deliberately immutable
thereafter. Correcting prepayment_required on the SERVICE therefore fixes every
future charge and NOTHING already raised: an unpaid test ordered before this
upgrade would stay performable without payment for the rest of its life.

Closing the gate only for future orders is not closing the gate.

WHY THIS DOES NOT CONTRADICT SNAPSHOT IMMUTABILITY
--------------------------------------------------
The engine's rule exists because of CHRG000063, where a later service edit
silently rewrote billing_basis prepaid -> delivery on a charge the patient had
ALREADY PAID. That corrupted settled history and REMOVED a gate that had been
satisfied.

This is the opposite act in every respect that made that one harmful:

    CHRG000063          this migration
    prepaid -> delivery delivery -> prepaid
    removed a gate      restores a gate that was never correctly applied
    on a PAID charge    only on charges where nothing is settled
    at runtime, silently  once, at upgrade, itemised in the log

The runtime rule is untouched: create_or_update_charge still refuses to rewrite
a snapshot, and nothing in normal operation gains the ability to do so.

THE PREDICATE IS DELIBERATELY NARROW
------------------------------------
Only charges where the gate moment has NOT yet passed are touched:

    billing_basis = 'delivery'      the wrong snapshot
    charge_state in (draft, active) not cancelled or reversed
    payment_state = 'unpaid'        nothing collected
    delivery_state = 'pending'      nothing delivered
    invoice_state = 'not_invoiced'  no fiscal document exists
    amount_received = 0             belt and braces against partial cash
    qty_delivered = 0

`delivery_state = 'in_progress'` is EXCLUDED on purpose. That means the sample
is already drawn and the work has commenced; demanding prepayment for it now
would be the same category of error as CHRG000063 -- rewriting a charge whose
gate moment has passed -- and would strand work the laboratory is mid-way
through. Those charges keep their basis and are collected the ordinary way.

THE WRITE GOES THROUGH THE ORM, NOT SQL
---------------------------------------
amount_due_for_clearance is a STORED compute that @api.depends on
billing_basis. Writing the column in SQL would leave it stale and the gate still
open -- the exact failure this migration exists to prevent. An ORM write fires
the recompute; the flush below makes that observable in the same transaction.
"""
import logging

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)

REGATE_DOMAIN = [
    ("billing_basis", "=", "delivery"),
    ("charge_state", "in", ("draft", "active")),
    ("payment_state", "=", "unpaid"),
    ("delivery_state", "=", "pending"),
    ("invoice_state", "=", "not_invoiced"),
    ("amount_received", "=", 0.0),
    ("qty_delivered", "=", 0.0),
]


def migrate(cr, version):
    if not version:
        return

    env = api.Environment(cr, SUPERUSER_ID, {})
    Charge = env["hospital.charge.line"].with_context(active_test=False)

    # SCOPE: ONLY the services whose NULL the pre-migration just resolved.
    #
    # Read from the hand-off table rather than re-derived, because by now every
    # service reads True and a service that was always True is indistinguishable
    # from one that was NULL. Deriving it here instead pulled in 198 charges
    # belonging to Consultation Fee and Complete Blood Count: services that were
    # ALWAYS prepaid, whose old delivery-basis charges predate that setting and
    # are none of this migration's business. Those charges were created under a
    # configuration that said delivery, and rewriting them would be the
    # retroactive snapshot edit this file exists to avoid.
    #
    # A service someone deliberately set to False is likewise untouched: that is
    # a decision, and this migration does not overrule decisions -- only
    # non-answers.
    cr.execute(
        """
        SELECT table_name FROM information_schema.tables
        WHERE table_name = 'hospital_billing_prepayment_backfill_18_1_13'
        """
    )
    if not cr.fetchone():
        _logger.info(
            "Charge re-gate: no backfill hand-off table, so no service was "
            "resolved from NULL. Nothing to re-gate."
        )
        return

    cr.execute("SELECT service_id FROM hospital_billing_prepayment_backfill_18_1_13")
    service_ids = [row[0] for row in cr.fetchall()]
    cr.execute("DROP TABLE hospital_billing_prepayment_backfill_18_1_13")
    if not service_ids:
        _logger.info("Charge re-gate: no candidate services.")
        return

    candidates = Charge.search(
        REGATE_DOMAIN + [("service_id", "in", service_ids)], order="id"
    )
    if not candidates:
        _logger.info("Charge re-gate: no charges met the not-yet-settled predicate.")
        return

    before = {
        charge.id: (
            charge.billing_basis,
            charge.amount_due_for_clearance,
            charge.service_id.name,
            charge.amount_patient_responsibility,
        )
        for charge in candidates
    }

    candidates.write({"billing_basis": "prepaid"})
    # Force the dependent stored compute to materialise now, so the log below
    # reports what the database actually holds rather than what is queued.
    candidates.flush_recordset()
    candidates.invalidate_recordset(["amount_due_for_clearance"])

    regated = 0
    for charge in candidates:
        was_basis, was_due, service_name, responsibility = before[charge.id]
        if charge.amount_due_for_clearance > 0:
            regated += 1
        _logger.info(
            "Charge re-gate: charge %s (%s, responsibility %s) "
            "basis %s -> %s, due_for_clearance %s -> %s",
            charge.id, service_name, responsibility,
            was_basis, charge.billing_basis,
            was_due, charge.amount_due_for_clearance,
        )

    # Excluded rows are reported too. A gate that was NOT restored is the more
    # important line in this log: it is money that stays collectable only by the
    # ordinary route, and somebody may need to chase it.
    skipped = Charge.search_count(
        [
            ("billing_basis", "=", "delivery"),
            ("service_id", "in", service_ids),
            ("payment_state", "=", "unpaid"),
            ("charge_state", "in", ("draft", "active")),
            "|",
            ("delivery_state", "!=", "pending"),
            ("qty_delivered", ">", 0.0),
        ]
    )

    _logger.info(
        "Charge re-gate complete: %s charge(s) examined, %s now carry a "
        "prepayment requirement. %s unpaid charge(s) were left as "
        "delivery-based because delivery had already commenced.",
        len(candidates), regated, skipped,
    )
