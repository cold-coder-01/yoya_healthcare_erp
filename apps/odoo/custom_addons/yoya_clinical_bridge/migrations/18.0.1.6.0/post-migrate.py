"""Report on historical prescriptions. Changes nothing.

hospital.prescription gains consultation_id and request_token in this version.
Every existing row has neither, and that is the correct final state for all of
them.

WHY THERE IS NOTHING TO BACKFILL, STATED EXPLICITLY RATHER THAN LEFT IMPLICIT.
This is the same reasoning 18.0.1.3.0 recorded for the laboratory request and
18.0.1.4.0 for the radiology request, and it holds at least as strongly here,
because prescriptions are routinely entered at the pharmacy counter rather than
by the prescribing clinician.

  consultation_id  A consultation is a physician's documented act with an
                   author and a start time. A historical prescription shares an
                   APPOINTMENT with any consultation on that visit, but sharing
                   a visit is not evidence that the consultation wrote the
                   prescription -- it may predate consultations entirely, have
                   been entered at the pharmacy, or have been written by another
                   clinician on the same episode. Deriving authorship from
                   co-location would attribute prescriptions to consultations
                   that never wrote them, and those attributions would then be
                   indistinguishable from real ones.

  request_token    Identifies one client submission. A row created before the
                   token existed had no submission to identify.

  encounter_id     DOES NOT EXIST ON THIS MODEL AND IS NOT BEING ADDED. The
                   encounter is resolved onto the DISPENSE by hospital_billing's
                   _prepare_pharmacy_dispense_vals(). A second copy on the
                   prescription header would be a second place for one fact to
                   drift, so unlike the radiology script above there is no
                   encounter figure to report here.

Empty is therefore the honest value, and every read path treats a prescription
with no consultation as the normal historical shape: for_consultation() simply
does not return it, and the Doctor Desk never offers to cancel it.

THE UNIQUE INDEX NEEDS NO MIGRATION STEP. It is created by
HospitalPrescription.init(), which Odoo runs on every upgrade of this module,
and it is PARTIAL on both columns -- so the existing rows, all of which have
NULL in both, cannot collide with each other or block its creation.

LEGACY ROWS WITH NULL medicine_id ARE LEFT ALONE, and are counted below only so
the upgrade leaves a record of how many there are. hospital_pharmacy declares
hospital.prescription.line.medicine_id required=True, which Odoo cannot apply to
a table that already contains NULLs; it logs that it could not set NOT NULL and
continues. That is pre-existing, is not made worse by the two nullable columns
this version adds, and is not repaired here: rewriting a clinical line to name a
medicine nobody recorded would be inventing what a patient was prescribed.

This script exists to LOG that decision against the data it applies to.
"""
import logging

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        return

    env = api.Environment(cr, SUPERUSER_ID, {})

    # active_test=False: an archived prescription is still part of the patient's
    # medication history and could be un-archived, so it belongs in the count.
    prescriptions = env["hospital.prescription"].with_context(
        active_test=False
    ).search([])

    cr.execute(
        "SELECT count(*) FROM hospital_prescription_line WHERE medicine_id IS NULL"
    )
    legacy_lines = cr.fetchone()[0]

    _logger.info(
        "Prescription consultation linkage: %s existing prescription(s) "
        "examined, all left with consultation_id and request_token empty by "
        "design. No row was modified. Pre-existing legacy prescription lines "
        "with no medicine_id: %s (left untouched; see this script's docstring).",
        len(prescriptions),
        legacy_lines,
    )
