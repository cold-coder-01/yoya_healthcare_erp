"""Backfill NULL prepayment_required before the NOT NULL constraint lands.

WHY PRE- AND NOT POST-.
prepayment_required becomes required=True in this version, so Odoo adds a NOT
NULL constraint during the schema update. Any row still holding NULL at that
moment aborts the upgrade. The backfill therefore has to happen before the ORM
touches the column, which is what a pre-migration is for.

WHY True FOR EVERY REMAINING NULL.
NULL never meant "delivery-based" -- it meant nobody answered the question. The
column was nullable with no default, and NULL is falsy, so an unanswered service
silently became deliverable without payment. Resolving those to True restores
the answer the hospital's own billing policy would have given: payment before
service. A service that genuinely bills after delivery is a deliberate decision
and can be set back to False by someone who makes it.

Written in SQL rather than through the ORM on purpose: at pre-migration time the
new field definition is not yet in the registry, so an ORM write would be
operating against the old schema.
"""
import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        return

    cr.execute(
        """
        SELECT id, name, service_type, default_price, active
        FROM hospital_billing_service
        WHERE prepayment_required IS NULL
        ORDER BY id
        """
    )
    rows = cr.fetchall()
    if not rows:
        _logger.info("prepayment_required backfill: no NULL rows.")
        return

    for service_id, name, service_type, price, active in rows:
        _logger.info(
            "prepayment_required backfill: service %s '%s' (%s, price %s, "
            "active=%s) NULL -> True",
            service_id, name, service_type, price, active,
        )

    # HAND THE EXACT ID SET TO THE POST-MIGRATION.
    #
    # The post step re-gates charges, and its scope must be "services whose NULL
    # this upgrade just resolved" -- nothing wider. It cannot re-derive that
    # afterwards, because by then every service reads True and the ones that
    # were always True are indistinguishable from the ones that were NULL.
    #
    # Getting this wrong is not academic: scoping by "prepayment_required is now
    # True" instead re-gated 198 charges belonging to Consultation Fee and
    # Complete Blood Count -- services that were ALWAYS True, whose old
    # delivery-basis charges predate that setting and are none of this
    # migration's business.
    cr.execute("DROP TABLE IF EXISTS hospital_billing_prepayment_backfill_18_1_13")
    cr.execute(
        "CREATE TABLE hospital_billing_prepayment_backfill_18_1_13 "
        "(service_id INTEGER PRIMARY KEY)"
    )
    cr.execute(
        """
        INSERT INTO hospital_billing_prepayment_backfill_18_1_13 (service_id)
        SELECT id FROM hospital_billing_service WHERE prepayment_required IS NULL
        """
    )

    cr.execute(
        "UPDATE hospital_billing_service SET prepayment_required = TRUE "
        "WHERE prepayment_required IS NULL"
    )
    _logger.info(
        "prepayment_required backfill: %s service(s) resolved to True.", len(rows)
    )
