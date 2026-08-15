from . import models

from .db_constraints import (
    ensure_agreement_overlap_exclusion,
    ensure_charge_responsibility_live_unique,
    ensure_patient_payer_overlap_exclusion,
)


def post_init_hook(env):
    """Fresh-install counterpart of the controlled overlap post-migrations.

    Runs once, at module install, with an operator behind it. The DDL is never
    executed at import time -- see db_constraints for why.
    """
    ensure_agreement_overlap_exclusion(env.cr)
    ensure_patient_payer_overlap_exclusion(env.cr)
    ensure_charge_responsibility_live_unique(env.cr)
