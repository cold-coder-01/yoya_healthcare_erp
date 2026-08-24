"""Report on historical radiology requests. Changes nothing.

hospital.radiology.request gains consultation_id and request_token in this
version. Every existing row has neither, and that is the correct final state
for all of them.

WHY THERE IS NOTHING TO BACKFILL, STATED EXPLICITLY RATHER THAN LEFT IMPLICIT.
This is the same reasoning 18.0.1.3.0 recorded for the laboratory request, and
it holds at least as strongly here, because radiology orders are routinely
raised at the imaging department rather than by the referring clinician.

  consultation_id  A consultation is a physician's documented act with an
                   author and a start time. A historical radiology request
                   shares an ENCOUNTER with any consultation on that visit, but
                   sharing an encounter is not evidence that the consultation
                   ordered the study -- the request may predate consultations
                   entirely, have been raised at the imaging desk, or have been
                   ordered by another clinician on the same episode. Deriving
                   authorship from co-location would attribute orders to
                   consultations that never made them, and those attributions
                   would then be indistinguishable from real ones.

  request_token    Identifies one client submission. A row created before the
                   token existed had no submission to identify.

  encounter_id     Already populated by hospital_billing's own confirmation
                   path, which resolves it from the appointment and refuses to
                   guess. There is nothing for this module to add.

Empty is therefore the honest value, and every read path treats a request with
no consultation as the normal historical shape: `for_consultation()` simply does
not return it, and the Doctor Desk never offers to cancel it.

THE UNIQUE INDEX NEEDS NO MIGRATION STEP. It is created by
HospitalRadiologyRequest.init(), which Odoo runs on every upgrade of this
module, and it is PARTIAL on both columns -- so the existing rows, all of which
have NULL in both, cannot collide with each other or block its creation.

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
    requests = env["hospital.radiology.request"].with_context(
        active_test=False
    ).search([])

    without_encounter = len(requests.filtered(lambda r: not r.encounter_id))

    _logger.info(
        "Radiology consultation linkage: %s existing request(s) examined, all "
        "left with consultation_id and request_token empty by design "
        "(%s of them also carry no encounter, which hospital_billing resolves "
        "at confirmation). No row was modified.",
        len(requests),
        without_encounter,
    )
