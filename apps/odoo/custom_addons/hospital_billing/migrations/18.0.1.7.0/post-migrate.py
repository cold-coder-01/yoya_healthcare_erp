"""Phase 2A: install the patient-eligibility overlap exclusion backstop."""

import logging

from odoo.addons.hospital_billing.db_constraints import (
    ensure_patient_payer_overlap_exclusion,
)

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        return
    outcome = ensure_patient_payer_overlap_exclusion(cr)
    _logger.info(
        "hospital_billing 18.0.1.7.0: patient eligibility overlap "
        "exclusion constraint -> %s",
        outcome,
    )
