"""Phase 3A pre-flight: resynchronise the billing account payer classification.

hospital.billing.account.payer_type / payer_id were plain columns, seeded once
by hospital.billing.engine.get_or_create_billing_account() and never refreshed.
They are now stored related fields deriving from hospital.encounter, which is
the documented authoritative source (see billing_account.py).

WHY AN EXPLICIT UPDATE AND NOT A RECOMPUTE
------------------------------------------
Odoo only schedules a recomputation for a stored computed column it has just
CREATED. These two columns already existed as plain data columns, so converting
them to related leaves whatever bytes were there -- including any drift the
conversion is meant to eliminate. The resync has to be stated.

Plain SQL rather than the ORM, deliberately: this is a projection of one column
onto another within a single table join. Going through the ORM would fire
_check_payer and the stored-related recompute machinery on every historical
account for no gain, and this must not be able to fail on legacy data.

EXPECTED SCALE: ZERO ROWS.
Measured before the change on every database in this deployment
(healthcare_erp_phase1_test: 356 accounts, healthcare_erp_manual_uat: 1) the
drift was 0/0. The defect was latent, not realised -- nothing writes
encounter.payer_type after account creation today, because encounter_payer.py
restricts it to PAYER_IDENTITY_AUTHORITY and no code path exercises it. This
migration exists so the invariant is guaranteed rather than assumed, and so a
database that HAS drifted (a hand-edited account form, a direct RPC write) is
corrected before anything starts trusting the field.
"""

import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        # Fresh install: the related fields compute themselves from the start.
        return

    cr.execute(
        """
        UPDATE hospital_billing_account a
           SET payer_type = e.payer_type,
               payer_id   = e.payer_id
          FROM hospital_encounter e
         WHERE e.id = a.encounter_id
           AND (a.payer_type IS DISTINCT FROM e.payer_type
                OR a.payer_id IS DISTINCT FROM e.payer_id)
        """
    )
    drifted = cr.rowcount
    if drifted:
        _logger.warning(
            "hospital_billing 18.0.1.9.0: resynchronised payer classification on "
            "%d billing account(s) that had drifted from their encounter. The "
            "encounter value won in every case.",
            drifted,
        )
    else:
        _logger.info(
            "hospital_billing 18.0.1.9.0: billing account payer classification "
            "already matched every encounter; no rows changed."
        )

    # Accounts whose encounter row is missing would be a referential-integrity
    # fault, not a drift. encounter_id is required with ondelete=restrict, so
    # this is reported rather than repaired -- there is no correct value to pick.
    cr.execute(
        """
        SELECT count(*)
          FROM hospital_billing_account a
     LEFT JOIN hospital_encounter e ON e.id = a.encounter_id
         WHERE e.id IS NULL
        """
    )
    orphans = cr.fetchone()[0]
    if orphans:
        _logger.error(
            "hospital_billing 18.0.1.9.0: %d billing account(s) have no encounter "
            "row. Their payer classification cannot be derived and needs manual "
            "review.",
            orphans,
        )
