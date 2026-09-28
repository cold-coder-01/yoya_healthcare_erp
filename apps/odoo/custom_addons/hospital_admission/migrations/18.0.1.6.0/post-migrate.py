"""Admission reference integrity: stop the rewind, then move the counter past
every reference already issued.

THE DEFECT
----------
data/admission_sequence.xml was loaded WITHOUT noupdate, and it states
number_next = 1. Every `-u hospital_admission` therefore rewrote the sequence,
and writing number_next on a 'standard' ir.sequence RESTARTs its PostgreSQL
sequence -- so the first admission after each upgrade was ADM00001 again. In UAT
three different stays carry ADM00001.

WHAT THIS DOES
--------------
1. Marks the sequence's xmlid noupdate, matching the file (now noupdate="1"),
   so no later upgrade -- from any data file -- can rewind it.
2. Reads every existing reference that has the sequence's shape (prefix +
   digits) and sets the counter to max + 1, THROUGH ir.sequence.write, i.e. the
   sequence's own mechanism (ALTER SEQUENCE ... RESTART). It only ever moves
   the counter FORWARD: a counter already past every reference is left alone,
   so re-running this is harmless.

The max is taken over ALL rows, duplicates included: a duplicated ADM00001 is
still an issued 1, and it is the highest issued number -- whatever it is
attached to -- that the next reference must clear.

WHAT THIS DOES NOT DO
---------------------
It renames nothing. The historical duplicates keep their references; they are
reported in the log for a controlled, separate maintenance decision. No unique
constraint is added here: with those rows present it could not be created.
"""
import logging
import re

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)

XMLID = "hospital_admission.sequence_hospital_admission"


def migrate(cr, version):
    if not version:
        # Fresh install: the XML seeds the sequence at 1 and nothing is issued.
        return

    cr.execute(
        "UPDATE ir_model_data SET noupdate = TRUE "
        "WHERE module = 'hospital_admission' AND name = 'sequence_hospital_admission'"
    )

    env = api.Environment(cr, SUPERUSER_ID, {})
    sequence = env.ref(XMLID, raise_if_not_found=False)
    if not sequence:
        _logger.error("Admission reference resync: %s not found; nothing done.", XMLID)
        return

    prefix, suffix = sequence.prefix or "", sequence.suffix or ""
    if "%(" in prefix or "%(" in suffix:
        # A date-interpolated reference cannot be parsed back to a counter
        # safely. Not the shipped configuration; refuse to guess.
        _logger.error(
            "Admission reference resync: sequence prefix/suffix is interpolated "
            "(%r/%r); counter NOT changed.", prefix, suffix,
        )
        return

    shape = re.compile(r"^%s(\d+)%s$" % (re.escape(prefix), re.escape(suffix)))
    cr.execute("SELECT id, name FROM hospital_admission WHERE name IS NOT NULL")
    rows = cr.fetchall()
    highest = 0
    for _id, name in rows:
        match = shape.match(name or "")
        if match:
            highest = max(highest, int(match.group(1)))

    cr.execute(
        "SELECT name, array_agg(id ORDER BY id) FROM hospital_admission "
        "WHERE name IS NOT NULL GROUP BY name HAVING count(*) > 1 ORDER BY name"
    )
    for name, ids in cr.fetchall():
        _logger.warning(
            "Admission reference resync: historical duplicate %s on admission ids %s "
            "(left unchanged).", name, ids,
        )

    # Read the counter from PostgreSQL, not from any cached value.
    sequence.invalidate_recordset(["number_next_actual"])
    current = sequence.number_next_actual
    target = highest + 1
    if current >= target:
        _logger.info(
            "Admission reference resync: next %s already clears the highest issued %s.",
            current, highest,
        )
        return
    sequence.write({"number_next": target})
    _logger.info(
        "Admission reference resync: counter moved from %s to %s (highest issued %s).",
        current, target, highest,
    )
