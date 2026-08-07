"""Re-home group_hospital_cashier from yoya_reception_bridge to hospital_billing.

The res.groups RECORD must survive: it already has members, ACL rows and record
rules pointing at it. Only its ir.model.data ownership moves. Renaming the
external id (rather than declaring a second group here) is what keeps existing
cashiers cashiers.

Ordering is what makes this safe. Odoo upgrades dependencies before dependents,
so hospital_billing's pre-migration runs BEFORE:
  - hospital_billing loads security/hospital_billing_groups.xml, which then
    UPDATES the adopted row instead of creating a duplicate group, and
  - yoya_reception_bridge reloads its own data, by which time it no longer owns
    an xmlid for the group and so has no orphan to clean up.

Both modules must be upgraded in the same run. Upgrading hospital_billing alone
leaves the bridge's ACL/record-rule XML referencing an external id that has
moved, which fails loudly at load rather than silently degrading access.
"""

import logging

_logger = logging.getLogger(__name__)

OLD_MODULE = "yoya_reception_bridge"
NEW_MODULE = "hospital_billing"
XMLID_NAME = "group_hospital_cashier"


def migrate(cr, version):
    # Fresh install: the group is created by the XML, nothing to adopt.
    if not version:
        return

    cr.execute(
        """
        SELECT id, res_id FROM ir_model_data
         WHERE module = %s AND name = %s AND model = 'res.groups'
        """,
        (OLD_MODULE, XMLID_NAME),
    )
    old = cr.fetchone()
    if not old:
        _logger.info(
            "hospital_billing: no %s.%s to adopt; assuming it is already re-homed "
            "or the bridge was never installed.",
            OLD_MODULE,
            XMLID_NAME,
        )
        return

    cr.execute(
        """
        SELECT id, res_id FROM ir_model_data
         WHERE module = %s AND name = %s AND model = 'res.groups'
        """,
        (NEW_MODULE, XMLID_NAME),
    )
    existing = cr.fetchone()
    if existing:
        # Both ids exist: a previous partial run, or a duplicate group was
        # created by hand. Do not guess which one holds the members -- refuse.
        raise ValueError(
            "Both %s.%s (res.groups %s) and %s.%s (res.groups %s) exist. "
            "Two cashier groups cannot be merged automatically because their "
            "memberships may differ. Reconcile them manually, then re-run the "
            "upgrade." % (
                OLD_MODULE, XMLID_NAME, old[1],
                NEW_MODULE, XMLID_NAME, existing[1],
            )
        )

    cr.execute(
        "UPDATE ir_model_data SET module = %s WHERE id = %s",
        (NEW_MODULE, old[0]),
    )
    cr.execute(
        "SELECT COUNT(*) FROM res_groups_users_rel WHERE gid = %s", (old[1],)
    )
    member_count = cr.fetchone()[0]
    _logger.info(
        "hospital_billing: adopted cashier group (res.groups %s, %d member(s)) "
        "from %s; external id is now %s.%s",
        old[1], member_count, OLD_MODULE, NEW_MODULE, XMLID_NAME,
    )
