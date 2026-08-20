"""Populate encounter_id on historical diagnoses, where it is provable.

WHAT THIS DOES, AND WHAT IT REFUSES TO DO
-----------------------------------------
hospital.patient.diagnosis gains encounter_id and consultation_id in this
version. Historical rows have neither.

encounter_id IS backfilled, but only where it can be DERIVED rather than
guessed: the diagnosis names an appointment, that appointment has exactly one
encounter, and that encounter belongs to the same patient as the diagnosis.
Anything short of all three is left empty.

consultation_id IS NEVER BACKFILLED. A consultation is a physician's
documented act with a start time and an author. Inventing one for a historical
diagnosis -- or attaching the diagnosis to a consultation that happens to share
its encounter -- would assert authorship nobody recorded. Empty is the honest
value, and every read path treats it as the normal historical shape.

The patient check is not paranoia: diagnosis.patient_id and
appointment.patient_id are independent columns on rows written years apart, and
a mismatched pair would otherwise be silently welded to the wrong episode of
care by this script.
"""
import logging

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        # Fresh install: no historical rows to reconcile.
        return

    env = api.Environment(cr, SUPERUSER_ID, {})

    # active_test=False: an archived diagnosis is still part of the patient's
    # history and can be un-archived. Skipping it here would leave exactly the
    # gap this backfill exists to close.
    diagnoses = env["hospital.patient.diagnosis"].with_context(
        active_test=False
    ).search(
        [("encounter_id", "=", False), ("appointment_id", "!=", False)]
    )
    if not diagnoses:
        _logger.info("Diagnosis backfill: nothing to reconcile.")
        return

    encounters = env["hospital.encounter"].with_context(active_test=False)

    linked = 0
    skipped_no_encounter = 0
    skipped_mismatch = 0

    for diagnosis in diagnoses:
        appointment = diagnosis.appointment_id
        candidates = encounters.search([("appointment_id", "=", appointment.id)])
        if len(candidates) != 1:
            # No encounter, or an ambiguous set. Either way there is nothing to
            # derive, and picking one would be a guess.
            skipped_no_encounter += 1
            continue

        encounter = candidates
        if encounter.patient_id != diagnosis.patient_id:
            skipped_mismatch += 1
            _logger.warning(
                "Diagnosis backfill: diagnosis %s is for patient %s but "
                "appointment %s resolves to an encounter for patient %s; "
                "left unlinked.",
                diagnosis.id,
                diagnosis.patient_id.id,
                appointment.id,
                encounter.patient_id.id,
            )
            continue

        # Written in SQL, not through write(). The ORM path would fire this
        # model's audit log for every historical row and stamp a spurious
        # "Patient diagnosis updated." entry authored by the migration, which
        # would corrupt exactly the trail the audit log exists to preserve.
        cr.execute(
            "UPDATE hospital_patient_diagnosis SET encounter_id = %s WHERE id = %s",
            (encounter.id, diagnosis.id),
        )
        linked += 1

    diagnoses.invalidate_recordset(["encounter_id"])

    _logger.info(
        "Diagnosis backfill complete: %s linked, %s skipped (no single "
        "encounter), %s skipped (patient mismatch), %s examined. "
        "consultation_id was intentionally left empty on all rows.",
        linked,
        skipped_no_encounter,
        skipped_mismatch,
        len(diagnoses),
    )
