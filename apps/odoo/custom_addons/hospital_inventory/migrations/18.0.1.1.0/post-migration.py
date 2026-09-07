"""Task 31C — controlled legacy remediation (base / non-surgery records).

Clears stale, leaked source references from consumptions that were created
before the default-context leak was fixed. Surgery record STCON00002 and the
surgery-aware ``is_source_generated`` recompute are handled by the
hospital_operation_theatre migration (surgery_id is only in the registry once
that module is loaded).

Safety properties:
* Before clearing anything, an immutable snapshot row is written to
  ``hospital.inventory.provenance.archive`` (preserve-then-clear).
* Exact preconditions are verified per record (type, state, retained sources,
  and the exact identity of each field being cleared). ANY difference raises,
  which rolls the whole upgrade transaction back. No range-based updates.
* Idempotent: re-running is a no-op (already-cleared fields are skipped and
  snapshots are de-duplicated).
* Clearing is done with SQL because consumed records are frozen by the
  provenance write-guard; SQL is the correct migration channel and is not an
  RPC-exploitable bypass.
"""

import logging

from odoo import api, SUPERUSER_ID

_logger = logging.getLogger(__name__)

MIGRATION_VERSION = "18.0.1.1.0"

# name -> spec. `clear`/`retain` map field -> expected source record .name.
TARGETS = {
    "STCON00001": {
        "type": "nursing",
        "state": "consumed",
        "clear": {
            "procedure_id": "PROC00001",
            "pharmacy_dispense_id": "DISP00001",
            "lab_request_id": "LABREQ0001",
            "radiology_request_id": "RADREQ0002",
        },
        "retain": {"nursing_round_id": "NURR00001", "admission_id": "ADM00001"},
        "reason": (
            "Task 31C remediation: stale default_* context leak on a nursing "
            "consumption. True primary is nursing_round_id (NURR00001); "
            "procedure/pharmacy-dispense/lab/radiology links were never intended."
        ),
    },
    "STCON00021": {
        "type": "pharmacy",
        "state": "consumed",
        "clear": {"lab_request_id": "LABREQ0002"},
        "retain": {"pharmacy_dispense_id": "DISP00036"},
        "reason": (
            "Task 31C remediation: stale lab_request leak on a pharmacy "
            "consumption. True primary is pharmacy_dispense_id (DISP00036)."
        ),
    },
    "STCON00017": {
        "type": "pharmacy",
        "state": "approved",
        "clear": {"lab_request_id": "LABREQ0002"},
        "retain": {},
        "reason": (
            "Task 31C remediation: erroneous lab_request on a pharmacy "
            "consumption (medicine lines). No dispense invented; type stays "
            "pharmacy and state stays approved."
        ),
    },
}


def _remediate(env, name, spec):
    Consumption = env["hospital.stock.consumption"].with_context(active_test=False)
    Archive = env["hospital.inventory.provenance.archive"]

    cons = Consumption.search([("name", "=", name)])
    if not cons:
        _logger.info("31C: %s not present in this database — skipping.", name)
        return
    if len(cons) != 1:
        raise ValueError("31C: expected exactly one %s, found %s" % (name, len(cons)))

    # Type/state must always match (they are never changed by remediation).
    if cons.consumption_type != spec["type"] or cons.state != spec["state"]:
        raise ValueError(
            "31C precondition failed for %s: expected type=%s state=%s, "
            "got type=%s state=%s — aborting."
            % (name, spec["type"], spec["state"], cons.consumption_type, cons.state)
        )
    # Retained sources must be exactly as expected.
    for field_name, expected in spec["retain"].items():
        rec = cons[field_name]
        if not rec or rec.name != expected:
            raise ValueError(
                "31C precondition failed for %s: retained %s expected %s, got %s "
                "— aborting." % (name, field_name, expected, rec.name if rec else None)
            )

    # Idempotency: already remediated when every clear-field is empty.
    if all(not cons[f] for f in spec["clear"]):
        _logger.info("31C: %s already remediated — no-op.", name)
        return

    # Verify the exact identity of each field being cleared (skip empties).
    for field_name, expected in spec["clear"].items():
        rec = cons[field_name]
        if rec and rec.name != expected:
            raise ValueError(
                "31C precondition failed for %s: %s holds %s but expected %s "
                "— aborting (no range updates)."
                % (name, field_name, rec.name, expected)
            )

    # Preserve-then-clear.
    for field_name in spec["clear"]:
        if cons[field_name]:
            Archive.snapshot(cons, field_name, MIGRATION_VERSION, spec["reason"])

    set_clause = ", ".join('"%s" = NULL' % f for f in spec["clear"])
    env.cr.execute(
        "UPDATE hospital_stock_consumption SET %s WHERE id = %%s" % set_clause,
        (cons.id,),
    )

    # Recompute the stored is_source_generated for this record (base fields only
    # here; surgery records are handled by the OT migration).
    cons.invalidate_recordset()
    cons._compute_is_source_generated()
    cons.flush_recordset(["is_source_generated"])
    _logger.info("31C: remediated %s (cleared %s).", name, list(spec["clear"]))


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    for name, spec in TARGETS.items():
        _remediate(env, name, spec)
