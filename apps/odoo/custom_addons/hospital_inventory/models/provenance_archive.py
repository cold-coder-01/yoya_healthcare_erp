from odoo import api, fields, models
from odoo.exceptions import UserError


class HospitalInventoryProvenanceArchive(models.Model):
    """Immutable, append-only snapshot of source references removed by a
    remediation migration.

    Before any legacy provenance is cleared, one row is written here per
    (consumption, field, old value). The record cannot be edited or deleted, so
    historical provenance is never silently erased — it is preserved as an audit
    trail that a later reversal/analysis task can rely on.
    """

    _name = "hospital.inventory.provenance.archive"
    _description = "Inventory Provenance Migration Snapshot (immutable)"
    _order = "id desc"

    consumption_id = fields.Many2one(
        "hospital.stock.consumption",
        string="Consumption",
        ondelete="set null",
        index=True,
    )
    consumption_ref = fields.Char(
        string="Consumption Reference",
        required=True,
        help="Snapshot of the consumption name at migration time (survives even "
        "if the consumption is later archived/renamed).",
    )
    field_name = fields.Char(string="Cleared Field", required=True)
    comodel = fields.Char(string="Source Model")
    old_res_id = fields.Integer(string="Old Record ID")
    old_display = fields.Char(string="Old Record (display)")
    migration_version = fields.Char(string="Migration Version", required=True)
    snapshot_date = fields.Datetime(
        string="Snapshot Date", default=fields.Datetime.now, required=True
    )
    reason = fields.Text(string="Reason", required=True)

    # ------------------------------------------------------------------
    # Immutability: create-only. No edits, no deletes.
    # ------------------------------------------------------------------
    def write(self, vals):
        raise UserError(
            "Provenance migration snapshots are immutable and cannot be modified."
        )

    def unlink(self):
        raise UserError(
            "Provenance migration snapshots are immutable and cannot be deleted."
        )

    @api.model
    def snapshot(
        self,
        consumption,
        field_name,
        migration_version,
        reason,
    ):
        """Append one immutable snapshot row for a field about to be cleared.

        Idempotent: if a snapshot for this (consumption, field, version) already
        exists, it is returned instead of creating a duplicate.
        """
        old_record = consumption[field_name]
        existing = self.sudo().search(
            [
                ("consumption_id", "=", consumption.id),
                ("field_name", "=", field_name),
                ("migration_version", "=", migration_version),
            ],
            limit=1,
        )
        if existing:
            return existing
        return self.sudo().create(
            {
                "consumption_id": consumption.id,
                "consumption_ref": consumption.name,
                "field_name": field_name,
                "comodel": consumption._fields[field_name].comodel_name,
                "old_res_id": old_record.id if old_record else False,
                "old_display": old_record.display_name if old_record else False,
                "migration_version": migration_version,
                "reason": reason,
            }
        )
