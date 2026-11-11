"""Task 31C — surgery record remediation + surgery-aware recompute.

Runs after hospital_operation_theatre is loaded, so ``surgery_id`` and the
surgery-aware source hooks are in the registry.

1. Remediates STCON00002 (a genuine Surgery consumption whose pre-fix creation
   leaked procedure_id + medication_administration_id). Preserves then clears
   the two leaked base-field links; retains surgery_id (SURG00001) and the
   contextual admission (ADM00001).
2. Force-recomputes the stored ``is_source_generated`` for ALL consumptions with
   the full, surgery-aware source-field set. This is required because the base
   hospital_inventory upgrade computed the flag before ``surgery_id`` existed in
   the registry, so every Surgery consumption would otherwise be left False.

Same safety properties as the base migration: preserve-then-clear via the
immutable archive, exact precondition checks (raise -> rollback), idempotent.
"""

import logging

from odoo import api, SUPERUSER_ID

_logger = logging.getLogger(__name__)

MIGRATION_VERSION = "18.0.1.1.0"

TARGET = {
    "name": "STCON00002",
    "type": "surgery",
    "state": "draft",
    "clear": {"procedure_id": "PROC00001", "medication_administration_id": "MAR00001"},
    "retain": {"surgery_id": "SURG00001", "admission_id": "ADM00001"},
    "reason": (
        "Task 31C remediation: pre-fix default_* leak on a surgery consumption. "
        "True primary is surgery_id (SURG00001); procedure/medication-"
        "administration links were never intended."
    ),
}


def _remediate_surgery(env):
    Consumption = env["hospital.stock.consumption"].with_context(active_test=False)
    Archive = env["hospital.inventory.provenance.archive"]
    spec = TARGET

    cons = Consumption.search([("name", "=", spec["name"])])
    if not cons:
        _logger.info("31C: %s not present — skipping.", spec["name"])
        return
    if len(cons) != 1:
        raise ValueError("31C: expected one %s, found %s" % (spec["name"], len(cons)))

    if cons.consumption_type != spec["type"] or cons.state != spec["state"]:
        raise ValueError(
            "31C precondition failed for %s: expected type=%s state=%s, got %s/%s."
            % (spec["name"], spec["type"], spec["state"],
               cons.consumption_type, cons.state)
        )
    for field_name, expected in spec["retain"].items():
        rec = cons[field_name]
        if not rec or rec.name != expected:
            raise ValueError(
                "31C precondition failed for %s: retained %s expected %s, got %s."
                % (spec["name"], field_name, expected, rec.name if rec else None)
            )

    if all(not cons[f] for f in spec["clear"]):
        _logger.info("31C: %s already remediated — no-op.", spec["name"])
        return

    for field_name, expected in spec["clear"].items():
        rec = cons[field_name]
        if rec and rec.name != expected:
            raise ValueError(
                "31C precondition failed for %s: %s holds %s but expected %s."
                % (spec["name"], field_name, rec.name, expected)
            )

    for field_name in spec["clear"]:
        if cons[field_name]:
            Archive.snapshot(cons, field_name, MIGRATION_VERSION, spec["reason"])

    set_clause = ", ".join('"%s" = NULL' % f for f in spec["clear"])
    env.cr.execute(
        "UPDATE hospital_stock_consumption SET %s WHERE id = %%s" % set_clause,
        (cons.id,),
    )
    _logger.info("31C: remediated %s (cleared %s).", spec["name"], list(spec["clear"]))


def _recompute_source_generated(env):
    """Surgery-aware recompute of is_source_generated for every consumption."""
    Consumption = env["hospital.stock.consumption"].with_context(active_test=False)
    recs = Consumption.search([])
    recs.invalidate_recordset()
    recs._compute_is_source_generated()
    recs.flush_recordset(["is_source_generated"])
    _logger.info("31C: recomputed is_source_generated for %s consumptions.", len(recs))


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    _remediate_surgery(env)
    _recompute_source_generated(env)
