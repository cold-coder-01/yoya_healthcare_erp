"""Report on historical laboratory requests. Changes nothing.

hospital.laboratory.request gains consultation_id and request_token in this
version. Every existing row has neither, and that is the correct final state
for all of them.

WHY THERE IS NOTHING TO BACKFILL, STATED EXPLICITLY RATHER THAN LEFT IMPLICIT.

  consultation_id  A consultation is a physician's documented act with an
                   author and a start time. A historical lab request shares an
                   ENCOUNTER with any consultation on that visit, but sharing an
                   encounter is not evidence that the consultation ordered the
                   test -- the request may predate consultations entirely, or
                   have been raised at the bench, or by another clinician on the
                   same episode. Deriving authorship from co-location would
                   attribute orders to consultations that never made them.

  request_token    Identifies one client submission. A row created before the
                   token existed had no submission to identify.

  encounter_id     Already populated by hospital_billing's own confirmation
                   path, which resolves it from the appointment and refuses to
                   guess. There is nothing for this module to add.

Empty is therefore the honest value, and every read path treats a request with
no consultation as the normal historical shape: `for_consultation()` simply does
not return it, and the Doctor Desk never offers to cancel it.

This script exists to LOG that decision against the data it applies to, so an
upgrade leaves a record of how many rows were deliberately left alone.
"""
import logging

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        return

    env = api.Environment(cr, SUPERUSER_ID, {})

    # active_test=False: an archived request is still part of the patient's
    # history and could be un-archived, so it belongs in the count.
    requests = env["hospital.laboratory.request"].with_context(
        active_test=False
    ).search([])

    without_encounter = len(requests.filtered(lambda r: not r.encounter_id))

    _logger.info(
        "Laboratory consultation linkage: %s existing request(s) examined, all "
        "left with consultation_id and request_token empty by design "
        "(%s of them also carry no encounter, which hospital_billing resolves "
        "at confirmation). No row was modified.",
        len(requests),
        without_encounter,
    )
