"""Consultation billing wired onto the existing appointment lifecycle.

This lives in hospital_billing, NOT hospital_management: hospital_billing already
depends on hospital_management, so the reverse reference would be a cycle.

No new clinical event is invented. The existing appointment workflow already has
exactly the events we need:

    action_confirm            -> check-in    : open encounter + account + pending charge
    action_start_consultation -> commencement: REQUIRE clearance, then mark in progress
    action_done               -> completion  : record delivery
    action_cancel             -> cancellation: cancel the charge (never delete)

Financial clearance is enforced at COMMENCEMENT, never at completion: once care has
been delivered, no financial state may block the clinician from recording it.
"""

from odoo import api, fields, models
from odoo.exceptions import UserError

CONSULTATION_EVENT = "consultation"
SOURCE_MODEL = "hospital.appointment"


class HospitalAppointmentBilling(models.Model):
    _inherit = "hospital.appointment"

    encounter_id = fields.Many2one(
        "hospital.encounter",
        string="Encounter",
        compute="_compute_encounter_id",
        search="_search_encounter_id",
        compute_sudo=True,
    )
    consultation_charge_id = fields.Many2one(
        "hospital.charge.line",
        string="Consultation Charge",
        compute="_compute_encounter_id",
        compute_sudo=True,
    )
    billing_blocked = fields.Boolean(
        compute="_compute_billing_blocked",
        compute_sudo=True,
        help="True when financial clearance is required before the consultation may start.",
    )
    billing_clearance_message = fields.Char(
        compute="_compute_billing_blocked", compute_sudo=True,
    )

    # ------------------------------------------------------------------
    # Links (computed; legacy appointments simply have none -- still readable)
    # ------------------------------------------------------------------
    def _compute_encounter_id(self):
        Encounter = self.env["hospital.encounter"].sudo()
        for appointment in self:
            enc = Encounter.search([("appointment_id", "=", appointment.id)], limit=1)
            appointment.encounter_id = enc
            charge = self.env["hospital.charge.line"].sudo().search(
                [("source_key", "=", appointment._consultation_source_key())], limit=1
            ) if appointment.id else self.env["hospital.charge.line"]
            appointment.consultation_charge_id = charge

    @api.model
    def _search_encounter_id(self, operator, value):
        Encounter = self.env["hospital.encounter"].sudo()
        linked_ids = Encounter.search([("appointment_id", "!=", False)]).mapped("appointment_id").ids
        if isinstance(value, bool):
            # ('encounter_id','=',False) -> appointments with NO encounter, and vice versa.
            has_encounter = (operator == "=") != value  # '=' False -> not linked
            return [("id", "not in" if has_encounter else "in", linked_ids)]
        encounters = Encounter.search([("id", operator, value)])
        return [("id", "in", encounters.mapped("appointment_id").ids)]

    def _compute_billing_blocked(self):
        engine = self.env["hospital.billing.engine"]
        for appointment in self:
            enc = appointment.encounter_id
            if not enc or appointment.state not in ("confirmed",):
                appointment.billing_blocked = False
                appointment.billing_clearance_message = False
                continue
            result = engine.check_financial_clearance(
                enc, charges=appointment._consultation_charge()
            )
            appointment.billing_blocked = not result["cleared"]
            appointment.billing_clearance_message = result["reason"]

    # ------------------------------------------------------------------
    # Deterministic idempotency key (existing convention:
    # <source_model>:<res_id>:<line_id>:<event>)
    # ------------------------------------------------------------------
    def _consultation_source_key(self):
        self.ensure_one()
        return "%s:%s:0:%s" % (SOURCE_MODEL, self.id, CONSULTATION_EVENT)

    # ------------------------------------------------------------------
    # Check-in: encounter + billing account + pending consultation charge
    # ------------------------------------------------------------------
    def _ensure_consultation_billing(self):
        """Idempotent. Safe to call on every confirm.

        Runs ELEVATED: establishing the encounter/account/charge is a system action
        that a clinical event triggers. A doctor has no write right on charge lines
        and must not need one to have a consultation billed. This grants no cash
        powers -- the allocation buckets are guarded separately, by group, inside
        hospital.charge.line.write().
        """
        engine = self.env["hospital.billing.engine"].sudo()
        Service = self.env["hospital.billing.service"].sudo()
        for appointment in self.sudo():
            if appointment.state == "cancelled":
                continue
            encounter = engine.get_or_create_encounter(appointment)
            engine.get_or_create_billing_account(encounter)
            service = Service.sudo().get_default_consultation_service(encounter.company_id)
            charge = engine.create_or_update_charge(
                encounter,
                SOURCE_MODEL,
                appointment.id,
                CONSULTATION_EVENT,
                service.name,
                service=service,
                qty_requested=1.0,
                source_key=appointment._consultation_source_key(),
            )
            # The appointment is CONFIRMED, so the charge is a real commitment, not a
            # draft. Nobody should have to press Activate by hand.
            engine.activate_charge(charge)
        return True

    def _consultation_charge(self):
        self.ensure_one()
        return self.env["hospital.charge.line"].sudo().search(
            [("source_key", "=", self._consultation_source_key())], limit=1
        )

    # ------------------------------------------------------------------
    # Workflow overrides
    # ------------------------------------------------------------------
    def action_confirm(self):
        result = super().action_confirm()
        self.filtered(lambda a: a.state == "confirmed")._ensure_consultation_billing()
        return result

    def action_start_consultation(self):
        """Financial clearance is enforced HERE -- before care commences."""
        engine = self.env["hospital.billing.engine"].sudo()
        for appointment in self.filtered(lambda a: a.state == "confirmed"):
            # Self-heal: an appointment confirmed before this module existed has no
            # encounter yet. Establish it now rather than skipping the check.
            appointment._ensure_consultation_billing()
            encounter = appointment.encounter_id
            # persist=True: this is a workflow decision point, so the resulting
            # clearance state is recorded on the account.
            # Scoped to the CONSULTATION charge: an unpaid lab test elsewhere on the
            # same encounter must not block a consultation that is itself cleared.
            clearance = engine.check_financial_clearance(
                encounter, persist=True, charges=appointment._consultation_charge()
            )
            if not clearance["cleared"]:
                raise UserError(
                    "Consultation for %s cannot start: %s\n\n"
                    "Take payment, record payer authorization, or authorize an emergency "
                    "bypass on encounter %s."
                    % (appointment.patient_id.display_name, clearance["reason"], encounter.name)
                )

        result = super().action_start_consultation()

        for appointment in self.filtered(lambda a: a.state == "in_consultation"):
            encounter = appointment.encounter_id
            if encounter and encounter.state in ("planned", "checked_in"):
                encounter.sudo().action_start()
            charge = appointment._consultation_charge()
            if charge:
                engine.mark_charge_in_progress(charge)
        return result

    def action_done(self):
        """Completion records DELIVERY. It never re-checks clearance: care that has
        been given must always be recordable, whatever the financial position."""
        engine = self.env["hospital.billing.engine"].sudo()
        result = super().action_done()
        for appointment in self.filtered(lambda a: a.state == "done"):
            charge = appointment._consultation_charge()
            if charge and charge.charge_state not in ("cancelled", "reversed"):
                engine.mark_charge_delivered(charge, qty_delivered=1.0)
            encounter = appointment.encounter_id
            if encounter and encounter.state == "active":
                encounter.sudo().action_complete()
        return result

    def action_cancel(self):
        """Cancel the operational charge -- never delete it. Delivered care is
        left alone (the engine refuses to cancel it)."""
        engine = self.env["hospital.billing.engine"].sudo()
        for appointment in self.filtered(
            lambda a: a.state in ("draft", "confirmed", "in_consultation")
        ):
            charge = appointment._consultation_charge()
            if charge and charge.charge_state not in ("cancelled", "reversed"):
                if charge.qty_delivered <= 0 and charge.delivery_state not in (
                    "delivered", "partially_delivered"
                ):
                    engine.cancel_charge(
                        charge, reason="Appointment %s cancelled" % appointment.appointment_code
                    )
            encounter = appointment.encounter_id
            if encounter and encounter.state in ("planned", "checked_in", "active"):
                encounter.sudo().action_cancel()
        return super().action_cancel()

    # ------------------------------------------------------------------
    # Front-desk actions
    # ------------------------------------------------------------------
    def action_view_encounter(self):
        self.ensure_one()
        if not self.encounter_id:
            raise UserError("This appointment has no encounter yet. Confirm it first.")
        return {
            "type": "ir.actions.act_window", "name": "Encounter",
            "res_model": "hospital.encounter", "view_mode": "form",
            "res_id": self.encounter_id.id,
        }
