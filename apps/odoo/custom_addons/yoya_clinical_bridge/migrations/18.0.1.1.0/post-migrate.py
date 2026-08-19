"""Backfill a consultation for visits that were already in consultation.

WHY THIS EXISTS
---------------
hospital.appointment.action_start_consultation() now opens the consultation as
part of the transition, which establishes the invariant the read path depends
on:

    appointment.state == 'in_consultation'
        =>  a hospital.consultation exists for that appointment's encounter

Visits that reached in_consultation BEFORE this module version did not go
through that code, so on an existing database the invariant is false for them
the moment it is introduced. The Doctor Desk reports a violation as an
integrity error and deliberately does NOT repair it at runtime -- silently
creating a note for a consultation that may already have been conducted would
show the clinician a blank screen with no indication anything was missing.

A ONE-OFF BACKFILL IS THE RIGHT PLACE FOR THAT REPAIR, and it is a different
act from a runtime self-heal: it runs once, at upgrade, under an administrator,
for a knowable set of rows, and it is auditable afterwards. Without it, every
visit already in consultation would answer 500 on its clinical note for the
rest of its life.

WHAT IT DOES NOT DO
-------------------
It does not touch appointments in any other state, does not create encounters,
does not move any workflow state and does not write narrative beyond the
copy-once presenting complaint that a live start would have seeded. A visit
whose encounter is missing is skipped and logged rather than guessed at.
"""
import logging

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        # Fresh install: no historical rows to reconcile.
        return

    env = api.Environment(cr, SUPERUSER_ID, {})
    Consultation = env["hospital.consultation"]

    # active_test=False: an ARCHIVED visit still in consultation is exactly the
    # row a default search silently omits, and unarchiving it later would then
    # produce the integrity error this backfill exists to prevent. Backfilling
    # it costs nothing and closes that hole permanently.
    appointments = env["hospital.appointment"].with_context(active_test=False).search(
        [("state", "=", "in_consultation")]
    )
    if not appointments:
        _logger.info("Consultation backfill: no in-consultation visits found.")
        return

    created = 0
    skipped = 0
    for appointment in appointments:
        encounter = appointment.encounter_id
        if not encounter:
            # Pre-encounter legacy visit. Nothing to anchor a consultation to,
            # and inventing an encounter here would fabricate an episode of
            # care. The read path will report it if anyone opens it.
            skipped += 1
            _logger.warning(
                "Consultation backfill: appointment %s (%s) is in consultation "
                "but has no encounter; skipped.",
                appointment.id,
                appointment.appointment_code or "no code",
            )
            continue

        if Consultation.search_count([("encounter_id", "=", encounter.id)]):
            continue

        # Reuse the authoritative opener rather than creating rows by hand, so
        # the sequence, the doctor attribution and the copy-once presenting
        # complaint behave exactly as they would for a live start. It is
        # idempotent, so re-running this migration is safe.
        Consultation.get_or_create_for_appointment(appointment)
        created += 1

    _logger.info(
        "Consultation backfill complete: %s created, %s skipped (no encounter), "
        "%s in-consultation visits examined.",
        created,
        skipped,
        len(appointments),
    )
