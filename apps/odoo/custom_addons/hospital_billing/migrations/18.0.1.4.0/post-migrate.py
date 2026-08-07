"""Recompute stored settlement states after advance-application semantics fix."""

import logging

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        return
    env = api.Environment(cr, SUPERUSER_ID, {})
    Charge = env["hospital.charge.line"].with_context(active_test=False)
    charges = Charge.search([])
    if not charges:
        return
    charges.invalidate_recordset()
    charges.modified(["amount_applied_to_invoice"])
    env.flush_all()
    _logger.info(
        "hospital_billing: recomputed settlement state chain for %d charge(s) after advance settlement fix",
        len(charges),
    )