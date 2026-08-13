"""Controlled DDL that Odoo's ORM cannot express.

Imported by __init__.post_init_hook (fresh install) and by the controlled
18.0.1.6.0 / 18.0.1.7.0 post-migrations (upgrade). It is NEVER executed at
model import time -- installing an extension as a side effect of loading a
Python module would run on every worker boot, against whatever database
happened to be open, with no operator intent behind it.

Deliberately free of any `odoo.` import so both call sites can use it with a raw
cursor and no registry.
"""

import logging

_logger = logging.getLogger(__name__)

AGREEMENT_TABLE = "hospital_payer_agreement"
AGREEMENT_OVERLAP_CONSTRAINT = "hospital_payer_agreement_no_active_overlap"
PATIENT_PAYER_TABLE = "hospital_patient_payer"
PATIENT_PAYER_OVERLAP_CONSTRAINT = "hospital_patient_payer_no_active_overlap"

# Two ACTIVE agreements for one payer in one company may not cover the same day.
#
# daterange with '[]' bounds makes both ends inclusive, matching the business
# reading of effective_from/effective_to. COALESCE to 'infinity' is what makes a
# NULL effective_to mean open-ended rather than "no range".
_OVERLAP_DDL = """
ALTER TABLE {table}
  ADD CONSTRAINT {constraint}
  EXCLUDE USING gist (
      payer_id   WITH =,
      company_id WITH =,
      daterange(effective_from,
                COALESCE(effective_to, 'infinity'::date),
                '[]') WITH &&
  ) WHERE (state = 'active')
"""

_PATIENT_PAYER_OVERLAP_DDL = """
ALTER TABLE {table}
  ADD CONSTRAINT {constraint}
  EXCLUDE USING gist (
      patient_id   WITH =,
      agreement_id WITH =,
      daterange(effective_from,
                COALESCE(effective_to, 'infinity'::date),
                '[]') WITH &&
  ) WHERE (state = 'active')
"""


def _constraint_exists(cr, table, constraint):
    cr.execute(
        """
        SELECT 1
          FROM pg_constraint c
          JOIN pg_class t ON t.oid = c.conrelid
         WHERE t.relname = %s AND c.conname = %s
        """,
        (table, constraint),
    )
    return bool(cr.fetchone())


def _table_exists(cr, table):
    cr.execute("SELECT 1 FROM information_schema.tables WHERE table_name = %s", (table,))
    return bool(cr.fetchone())


def _try(cr, label, statement, params=None):
    """Run one statement inside an explicit SQL savepoint.

    Explicit SAVEPOINT/ROLLBACK TO rather than cursor.savepoint(): this runs
    from a migration with a bare cursor and no environment to flush, and the
    plain SQL form behaves identically on every Odoo version.
    """
    savepoint = "hb_payer_ddl"
    cr.execute("SAVEPOINT %s" % savepoint)
    try:
        cr.execute(statement, params or ())
    except Exception as error:  # noqa: BLE001 -- degradation is the point
        cr.execute("ROLLBACK TO SAVEPOINT %s" % savepoint)
        _logger.warning("hospital_billing: %s failed: %s", label, error)
        return False
    cr.execute("RELEASE SAVEPOINT %s" % savepoint)
    return True


def ensure_agreement_overlap_exclusion(cr):
    """Best-effort DB-level guard against overlapping ACTIVE agreements.

    Returns one of: 'present', 'created', 'no_table', 'no_extension',
    'conflicting_data'.

    This is a BACKSTOP, not the primary control. The primary control is the
    advisory lock plus re-check inside
    hospital.payer.agreement._assert_no_active_overlap(), which is race-safe on
    its own and works with or without this constraint. Callers must not treat a
    non-'created' result as a failure.
    """
    if not _table_exists(cr, AGREEMENT_TABLE):
        _logger.info(
            "hospital_billing: %s does not exist yet; skipping the overlap "
            "exclusion constraint.",
            AGREEMENT_TABLE,
        )
        return "no_table"

    if _constraint_exists(cr, AGREEMENT_TABLE, AGREEMENT_OVERLAP_CONSTRAINT):
        return "present"

    if not _try(cr, "CREATE EXTENSION btree_gist", "CREATE EXTENSION IF NOT EXISTS btree_gist"):
        _logger.warning(
            "hospital_billing: btree_gist is unavailable (it usually needs a "
            "superuser or a pre-created extension), so the ACTIVE-agreement "
            "overlap EXCLUDE constraint was NOT installed. Overlap is still "
            "enforced in Python by _assert_no_active_overlap() under an advisory "
            "lock, which is race-safe for the activation path. Direct SQL writes "
            "bypassing the ORM would not be caught."
        )
        return "no_extension"

    created = _try(
        cr,
        "ADD CONSTRAINT %s" % AGREEMENT_OVERLAP_CONSTRAINT,
        _OVERLAP_DDL.format(
            table=AGREEMENT_TABLE, constraint=AGREEMENT_OVERLAP_CONSTRAINT
        ),
    )
    if not created:
        _logger.warning(
            "hospital_billing: could not add %s. The most likely cause is "
            "pre-existing data with two overlapping ACTIVE agreements for one "
            "payer. Resolve the overlap, then re-run the upgrade.",
            AGREEMENT_OVERLAP_CONSTRAINT,
        )
        return "conflicting_data"

    _logger.info(
        "hospital_billing: installed %s on %s.",
        AGREEMENT_OVERLAP_CONSTRAINT,
        AGREEMENT_TABLE,
    )
    return "created"


def ensure_patient_payer_overlap_exclusion(cr):
    """Best-effort DB backstop for overlapping ACTIVE patient eligibilities.

    The authoritative normal-write guard is
    hospital.patient.payer._assert_no_active_overlap() under its advisory lock.
    This controlled DDL catches direct SQL writes when btree_gist is available,
    and reports a loud outcome without making module upgrade depend solely on
    extension privileges.
    """
    if not _table_exists(cr, PATIENT_PAYER_TABLE):
        _logger.info(
            "hospital_billing: %s does not exist yet; skipping the overlap "
            "exclusion constraint.",
            PATIENT_PAYER_TABLE,
        )
        return "no_table"

    if _constraint_exists(
        cr, PATIENT_PAYER_TABLE, PATIENT_PAYER_OVERLAP_CONSTRAINT
    ):
        return "present"

    if not _try(cr, "CREATE EXTENSION btree_gist", "CREATE EXTENSION IF NOT EXISTS btree_gist"):
        _logger.warning(
            "hospital_billing: btree_gist is unavailable, so the ACTIVE patient-"
            "eligibility overlap EXCLUDE constraint was NOT installed. The ORM "
            "advisory-lock guard remains authoritative, but direct SQL writes "
            "would not have this backstop."
        )
        return "no_extension"

    created = _try(
        cr,
        "ADD CONSTRAINT %s" % PATIENT_PAYER_OVERLAP_CONSTRAINT,
        _PATIENT_PAYER_OVERLAP_DDL.format(
            table=PATIENT_PAYER_TABLE,
            constraint=PATIENT_PAYER_OVERLAP_CONSTRAINT,
        ),
    )
    if not created:
        _logger.warning(
            "hospital_billing: could not add %s. Resolve any pre-existing "
            "overlapping ACTIVE eligibilities, then re-run the upgrade.",
            PATIENT_PAYER_OVERLAP_CONSTRAINT,
        )
        return "conflicting_data"

    _logger.info(
        "hospital_billing: installed %s on %s.",
        PATIENT_PAYER_OVERLAP_CONSTRAINT,
        PATIENT_PAYER_TABLE,
    )
    return "created"
