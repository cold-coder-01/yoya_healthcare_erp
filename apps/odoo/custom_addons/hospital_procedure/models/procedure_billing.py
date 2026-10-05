"""Procedures on the unified charge engine (Admissions Slice 4).

BEFORE: a completed procedure was billed only through the LEGACY
hospital.patient.bill (action_generate_procedure_bill): outside the visit's
billing account, outside payer responsibility, invisible to the inpatient
settlement.

NOW -- A COMPATIBILITY BRIDGE, NOT A MIGRATION. When a procedure belongs to a
visit (through its admission or its appointment), its lifecycle drives ONE
charge on that visit's hospital.billing.account, through
hospital.billing.engine exactly as laboratory, radiology and pharmacy do:

    submitted  -> charge REQUESTED (qty 1, the procedure type's price)
    done       -> charge DELIVERED
    cancelled  -> charge CANCELLED (never deleted; never once delivered)

So an ordered procedure is an estimate and never actual care, a completed one
counts once, a cancelled one not at all, and a payer's benefit rule can cover
it like any other charge (the engine resolves coverage on creation).

SOURCE IDENTITY. hospital.procedure.request : <id> : 0 : procedure -- the
engine's own key format. Re-submitting, retrying or completing never makes a
second charge.

NO DOUBLE BILLING. A procedure with a LIVE engine charge cannot be billed on a
legacy bill as well (action_generate_procedure_bill refuses), and the inpatient
summary counts a legacy bill only for a procedure with no live engine charge.
Procedures with no visit, and legacy bills already issued, stay exactly as they
were and remain readable.
"""
from odoo import models
from odoo.exceptions import UserError

SOURCE_MODEL = "hospital.procedure.request"
SOURCE_EVENT = "procedure"
FROZEN_CHARGE_STATES = ("cancelled", "reversed")
LOCKED_ENCOUNTER_STATES = ("closed", "cancelled")


class HospitalProcedureRequestBilling(models.Model):
    _inherit = "hospital.procedure.request"

    # ------------------------------------------------------------------
    # Resolution
    # ------------------------------------------------------------------
    def _billing_encounter(self):
        """The visit this procedure is care for: its admission's, else its
        appointment's. sudo(): the visit is a property of the record, and the
        performing nurse may not read the encounter."""
        self.ensure_one()
        record = self.sudo()
        encounter = record.admission_id.encounter_id if record.admission_id else False
        if not encounter and record.appointment_id and "encounter_id" in record.appointment_id._fields:
            encounter = record.appointment_id.encounter_id
        if not encounter or encounter.state in LOCKED_ENCOUNTER_STATES:
            return self.env["hospital.encounter"].browse()
        return encounter

    def _procedure_charge(self):
        """This procedure's engine charge, live or not."""
        self.ensure_one()
        engine = self.env["hospital.billing.engine"]
        key = engine._build_source_key(SOURCE_MODEL, self.id, SOURCE_EVENT, 0)
        return self.env["hospital.charge.line"].sudo().with_context(active_test=False).search(
            [("source_key", "=", key)], limit=1
        )

    def _ensure_procedure_charge(self):
        """Raise (or re-find) the REQUESTED charge. Returns it, or an empty
        recordset when this procedure is not billed on a visit."""
        self.ensure_one()
        Charge = self.env["hospital.charge.line"].sudo()
        existing = self._procedure_charge()
        if existing:
            return existing
        encounter = self._billing_encounter()
        price = self.procedure_type_id.default_price or 0.0
        if not encounter or price <= 0:
            return Charge.browse()
        engine = self.env["hospital.billing.engine"].sudo()
        service = self.env.ref(
            "hospital_procedure.billing_service_clinical_procedure", raise_if_not_found=False
        )
        charge = engine.create_or_update_charge(
            encounter,
            SOURCE_MODEL,
            self.id,
            SOURCE_EVENT,
            "%s - %s" % (self.procedure_type_id.name, self.name),
            service=service or None,
            qty_requested=1.0,
            unit_price=price,
        )
        engine.activate_charge(charge)
        return charge

    # ------------------------------------------------------------------
    # Lifecycle hooks
    # ------------------------------------------------------------------
    def action_submit_request(self):
        submitting = self.filtered(lambda r: r.state == "draft")
        result = super().action_submit_request()
        for rec in submitting.filtered(lambda r: r.state == "requested"):
            rec._ensure_procedure_charge()
        return result

    def action_mark_done(self):
        finishing = self.filtered(lambda r: r.state in ("requested", "scheduled", "in_progress"))
        result = super().action_mark_done()
        engine = self.env["hospital.billing.engine"].sudo()
        for rec in finishing.filtered(lambda r: r.state == "done"):
            charge = rec._ensure_procedure_charge()
            if charge and charge.charge_state not in FROZEN_CHARGE_STATES:
                engine.mark_charge_delivered(charge, qty_delivered=1.0)
        return result

    def action_cancel(self):
        cancelling = self.filtered(
            lambda r: r.state in ("draft", "requested", "scheduled", "in_progress")
        )
        result = super().action_cancel()
        engine = self.env["hospital.billing.engine"].sudo()
        for rec in cancelling.filtered(lambda r: r.state == "cancelled"):
            charge = rec._procedure_charge()
            if charge and charge.charge_state not in FROZEN_CHARGE_STATES:
                engine.cancel_charge(charge, reason="Procedure %s cancelled" % rec.name)
        return result

    def action_generate_procedure_bill(self):
        """LEGACY bill -- refused when the procedure is already on the visit's
        billing account, which is where it is settled."""
        self.ensure_one()
        charge = self._procedure_charge()
        if charge and charge.charge_state not in FROZEN_CHARGE_STATES:
            raise UserError(
                "This procedure is billed on the visit's billing account. A separate "
                "procedure bill would bill it twice."
            )
        return super().action_generate_procedure_bill()
