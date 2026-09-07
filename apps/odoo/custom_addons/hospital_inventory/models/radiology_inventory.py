from odoo import fields, models
from odoo.exceptions import UserError


class HospitalRadiologyRequest(models.Model):
    _inherit = "hospital.radiology.request"

    inventory_consumption_ids = fields.One2many(
        "hospital.stock.consumption", "radiology_request_id", string="Inventory Consumptions"
    )
    inventory_consumption_count = fields.Integer(
        compute="_compute_inventory_consumption_count", string="Inventory Consumption"
    )

    def _compute_inventory_consumption_count(self):
        Consumption = self.env["hospital.stock.consumption"]
        for rec in self:
            rec.inventory_consumption_count = (
                Consumption.search_count([("radiology_request_id", "=", rec.id)]) if rec.id else 0
            )

    def action_create_radiology_consumable_consumption(self):
        self.ensure_one()
        self._assert_radiology_consumption_allowed()
        # See lab_inventory: the Radiology Store is stamped for attribution but is
        # not a hard prerequisite, so an unconfigured store never blocks imaging.
        source_vals = {
            "patient_id": self.patient_id.id,
            "radiology_request_id": self.id,
        }
        rad_store = self.env["hospital.inventory.location"].get_default_radiology_store()
        if rad_store:
            source_vals["source_location_id"] = rad_store.id

        consumption = self.env["hospital.stock.consumption"].create_for_source(
            consumption_type="radiology",
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

    def _assert_radiology_consumption_allowed(self):
        for request in self:
            if request.state not in ("in_progress", "completed"):
                raise UserError(
                    "Radiology consumables cannot be validated or consumed before request %s is Marked In Progress and financially cleared."
                    % request.name
                )
            if hasattr(request, "_assert_financially_cleared_for_service"):
                request._assert_financially_cleared_for_service(persist=False)
        return True

    def action_view_inventory_consumptions(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": "Inventory Consumption",
            "res_model": "hospital.stock.consumption",
            "view_mode": "list,form",
            "domain": [("radiology_request_id", "=", self.id)],
            "context": {
                "default_radiology_request_id": self.id,
                "default_consumption_type": "radiology",
                "default_patient_id": self.patient_id.id,
            },
        }


class HospitalStockConsumptionRadiologyClearance(models.Model):
    _inherit = "hospital.stock.consumption"

    def _assert_radiology_clearance(self):
        requests = self.mapped("radiology_request_id")
        if requests:
            requests._assert_radiology_consumption_allowed()
        return True

    def action_approve(self):
        self._assert_radiology_clearance()
        return super().action_approve()

    def action_consume(self):
        self._assert_radiology_clearance()
        return super().action_consume()
