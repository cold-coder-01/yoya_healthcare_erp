from odoo import api, fields, models
from odoo.exceptions import UserError

# Reagent is physically consumed only once a sample actually exists and the test is
# being run. Before a sample is collected there is nothing to consume; after the
# request is cancelled there never will be.
CONSUMPTION_ALLOWED_STATES = ("sample_collected", "in_progress")


class HospitalLaboratoryRequest(models.Model):
    _inherit = "hospital.laboratory.request"

    inventory_consumption_ids = fields.One2many(
        "hospital.stock.consumption", "lab_request_id", string="Inventory Consumptions"
    )
    inventory_consumption_count = fields.Integer(
        compute="_compute_inventory_consumption_count", string="Inventory Consumption"
    )
    can_create_reagent_consumption = fields.Boolean(
        compute="_compute_can_create_reagent_consumption",
        help="Reagent consumption is only valid once a sample has been collected, "
        "and never for a cancelled request.",
    )

    def _compute_inventory_consumption_count(self):
        Consumption = self.env["hospital.stock.consumption"]
        for rec in self:
            rec.inventory_consumption_count = (
                Consumption.search_count([("lab_request_id", "=", rec.id)]) if rec.id else 0
            )

    @api.depends("state")
    def _compute_can_create_reagent_consumption(self):
        # @api.depends("state") is load-bearing: without it the flag is never
        # recomputed when the request changes state, so the button would stay
        # hidden after the sample is collected.
        for rec in self:
            rec.can_create_reagent_consumption = rec.state in CONSUMPTION_ALLOWED_STATES

    def _assert_reagent_consumption_allowed(self):
        """Server-side gate.

        The view's `invisible` is a convenience for the operator, NOT a control:
        RPC, data import, direct ORM calls and a forged context never evaluate it and
        all land here instead.
        """
        self.ensure_one()
        if self.state in CONSUMPTION_ALLOWED_STATES:
            return True
        if self.state == "cancelled":
            raise UserError(
                "Laboratory request %s is cancelled. Reagent consumption can never be "
                "created for a cancelled request." % self.name
            )
        raise UserError(
            "Laboratory request %s is '%s'. Reagent consumption can only be recorded "
            "once the sample has been collected (Sample Collected or In Progress) -- "
            "there is nothing to consume before a sample exists." % (self.name, self.state)
        )

    def action_create_lab_reagent_consumption(self):
        self.ensure_one()
        self._assert_reagent_consumption_allowed()

        Consumption = self.env["hospital.stock.consumption"]

        # IDEMPOTENT: one consumption per request. A second click -- or a repeated RPC
        # call -- opens the record that already exists instead of quietly creating a
        # duplicate that would double-deduct stock. A live record wins over a cancelled
        # one; we still return a cancelled record rather than silently creating a second
        # consumption behind the operator's back.
        existing = Consumption.search(
            [("lab_request_id", "=", self.id), ("state", "!=", "cancelled")],
            order="id desc", limit=1,
        ) or Consumption.search(
            [("lab_request_id", "=", self.id)], order="id desc", limit=1
        )

        # Stamp the Laboratory Store so reagent draws are attributed to the lab
        # instead of landing location-less. Deliberately NOT fatal when the store
        # is unconfigured: an admin who renamed or archived it must not be able to
        # block reagent consumption outright. Batch selection stays open either
        # way; the store is binding only when it can actually serve the item
        # (see hospital.stock.consumption.line._check_batch_location_policy).
        source_vals = {
            "patient_id": self.patient_id.id,
            "lab_request_id": self.id,
        }
        lab_store = self.env["hospital.inventory.location"].get_default_laboratory_store()
        if lab_store:
            source_vals["source_location_id"] = lab_store.id

        consumption = existing or Consumption.create_for_source(
            consumption_type="laboratory",
            source_vals=source_vals,
        )
        return {
            "type": "ir.actions.act_window",
            "name": "Inventory Consumption",
            "res_model": "hospital.stock.consumption",
            "res_id": consumption.id,
            "view_mode": "form",
            "target": "current",
        }

    def action_view_inventory_consumptions(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": "Inventory Consumption",
            "res_model": "hospital.stock.consumption",
            "view_mode": "list,form",
            "domain": [("lab_request_id", "=", self.id)],
            "context": {
                "default_lab_request_id": self.id,
                "default_consumption_type": "laboratory",
                "default_patient_id": self.patient_id.id,
            },
        }
