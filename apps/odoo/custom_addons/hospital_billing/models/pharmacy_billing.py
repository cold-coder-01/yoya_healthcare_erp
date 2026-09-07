"""Pharmacy integration with unified encounter billing."""

from odoo import api, fields, models
from odoo.exceptions import UserError, ValidationError

# Imported from the module that owns the transition table rather than restated.
# hospital_billing hard-depends on hospital_pharmacy, and a private copy of the
# cancellable-state tuple here is exactly how the charge cleanup below would one
# day run for a transition the base model had stopped allowing.
from odoo.addons.hospital_pharmacy.models.pharmacy_dispense import (
    DISPENSE_CANCELLABLE_STATES,
)

from .charge_line import (
    AMOUNT_TOLERANCE,
    FROZEN_CHARGE_STATES,
    OPERATIONAL_INTAKE_GROUPS,
)


PHARMACY_EVENT = "pharmacy_dispense"
SOURCE_MODEL = "hospital.pharmacy.dispense"
LIVE_CHARGE_STATES = ("draft", "active")
# Imported, not restated: this module used to carry a third copy of the tuple.
RECEIPT_GROUPS = OPERATIONAL_INTAKE_GROUPS
QTY_TOLERANCE = 0.0005


class HospitalPharmacyMedicineBilling(models.Model):
    _inherit = "hospital.pharmacy.medicine"

    billing_service_id = fields.Many2one(
        "hospital.billing.service",
        string="Billing Service",
        domain="[('service_type', '=', 'pharmacy')]",
        help="Unified billing service used for outpatient pharmacy charges.",
    )

    @api.constrains("billing_service_id")
    def _check_billing_service(self):
        for medicine in self:
            service = medicine.billing_service_id
            if service and service.service_type != "pharmacy":
                raise ValidationError("%s: billing service must be Pharmacy." % medicine.display_name)

    @api.model
    def _doctor_orderable_billing_domain(self, company=None):
        """The billing half of medicine orderability. AS A DOMAIN.

        A ONE-TO-ONE RESTATEMENT OF _assert_billable() BELOW, and it lives here
        rather than in the Doctor Desk bridge for exactly that reason: the two
        must agree, and the only way to keep them agreeing is to write them in
        the same file, next to each other, owned by the module that decides what
        billable means.

        The four conditions map to the four problems _assert_billable reports:

            no billing service mapped   billing_service_id is set
            service is archived         the service is active
            belongs to another company  the service is company-less or ours
            not effective on <date>     today falls inside its window

        Nothing is added and nothing is left out. Notably NOT service_type,
        which _assert_billable does not check either: a model constraint already
        guarantees it at write time, and duplicating it here would make the
        picker stricter than the gate it exists to predict.

        RETURNED AS A DOMAIN so the catalogue filters in SQL. Post-filtering a
        fetched page would silently shrink it below the requested limit and make
        the `truncated` flag a lie.

        _assert_billable REMAINS THE AUTHORITY. This decides only what is
        OFFERED; a medicine that reaches Mark Ready by any other route is still
        refused there, and the transaction still rolls back.
        """
        company = company or self.env.company
        today = fields.Date.context_today(self)
        return [
            ("billing_service_id", "!=", False),
            ("billing_service_id.active", "=", True),
            "|",
            ("billing_service_id.company_id", "=", False),
            ("billing_service_id.company_id", "=", company.id),
            "|",
            ("billing_service_id.effective_date_start", "=", False),
            ("billing_service_id.effective_date_start", "<=", today),
            "|",
            ("billing_service_id.effective_date_end", "=", False),
            ("billing_service_id.effective_date_end", ">=", today),
        ]

    def _assert_billable(self, company=None):
        company = company or self.env.company
        today = fields.Date.context_today(self)
        problems = []
        for medicine in self:
            service = medicine.billing_service_id
            if not service:
                problems.append("%s: no billing service mapped" % medicine.display_name)
            elif not service.active:
                problems.append("%s: billing service is archived" % medicine.display_name)
            elif service.company_id and service.company_id != company:
                problems.append("%s: billing service belongs to %s, not %s" % (medicine.display_name, service.company_id.name, company.name))
            elif not service.is_effective_on(today):
                problems.append("%s: billing service is not effective on %s" % (medicine.display_name, today))
        if problems:
            raise UserError(
                "Pharmacy billing configuration error. No payment, charge delivery, or stock movement was created.\n\n"
                + "\n".join("  - %s" % p for p in sorted(problems))
                + "\n\nMap each medicine to an active Pharmacy billing service and retry."
            )
        return True


class HospitalPrescriptionPharmacyBilling(models.Model):
    _inherit = "hospital.prescription"

    def _prepare_pharmacy_dispense_vals(self):
        vals = super()._prepare_pharmacy_dispense_vals()
        if not self.appointment_id:
            return vals
        encounter = self.env["hospital.encounter"].search(
            [
                ("appointment_id", "=", self.appointment_id.id),
                ("patient_id", "=", self.patient_id.id),
            ],
            order="id desc",
            limit=1,
        )
        if encounter:
            vals["encounter_id"] = encounter.id
        return vals


class HospitalPharmacyDispenseBilling(models.Model):
    _inherit = "hospital.pharmacy.dispense"

    encounter_id = fields.Many2one(
        "hospital.encounter",
        string="Encounter",
        ondelete="restrict",
        copy=False,
        domain="[('patient_id', '=', patient_id), ('state', 'not in', ('closed', 'cancelled'))]",
    )
    billing_account_id = fields.Many2one(
        "hospital.billing.account",
        string="Billing Account",
        compute="_compute_billing_account_id",
        compute_sudo=True,
    )
    charge_line_ids = fields.One2many(
        "hospital.charge.line",
        compute="_compute_charge_line_ids",
        compute_sudo=True,
        search="_search_charge_line_ids",
        string="Pharmacy Charges",
    )
    charge_count = fields.Integer(compute="_compute_charge_line_ids", compute_sudo=True)
    receipt_ids = fields.Many2many(
        "hospital.charge.receipt",
        string="Payment Receipts",
        compute="_compute_receipts",
        compute_sudo=True,
    )
    receipt_count = fields.Integer(compute="_compute_receipts", compute_sudo=True)
    unified_billing_enabled = fields.Boolean(
        string="Unified Billing",
        compute="_compute_unified_billing_enabled",
        store=True,
        compute_sudo=True,
    )
    unified_amount_due_for_clearance = fields.Float(
        string="Remaining Clearance Due",
        compute="_compute_pharmacy_payment_totals",
        compute_sudo=True,
        digits=(16, 2),
    )
    unified_amount_received = fields.Float(
        string="Collected / Allocated",
        compute="_compute_pharmacy_payment_totals",
        compute_sudo=True,
        digits=(16, 2),
    )

    @api.depends("encounter_id")
    def _compute_unified_billing_enabled(self):
        for dispense in self:
            dispense.unified_billing_enabled = bool(dispense.encounter_id)

    @api.depends("encounter_id.billing_account_id")
    def _compute_billing_account_id(self):
        for dispense in self:
            dispense.billing_account_id = dispense.encounter_id.billing_account_id

    def _charge_domain(self):
        self.ensure_one()
        return [("source_model", "=", SOURCE_MODEL), ("source_res_id", "=", self.id or 0), ("source_event", "=", PHARMACY_EVENT)]

    def _compute_charge_line_ids(self):
        Charge = self.env["hospital.charge.line"].sudo().with_context(active_test=False)
        for dispense in self:
            charges = Charge.search(dispense._charge_domain()) if dispense.id else Charge
            dispense.charge_line_ids = charges
            dispense.charge_count = len(charges)

    def _search_charge_line_ids(self, operator, value):
        Charge = self.env["hospital.charge.line"].sudo().with_context(active_test=False)
        charges = Charge.search([("id", operator, value)])
        dispense_ids = charges.filtered(
            lambda charge: charge.source_model == SOURCE_MODEL
            and charge.source_event == PHARMACY_EVENT
            and charge.source_res_id
        ).mapped("source_res_id")
        return [("id", "in", dispense_ids)]

    def _compute_receipts(self):
        Alloc = self.env["hospital.charge.receipt.allocation"].sudo()
        Receipt = self.env["hospital.charge.receipt"]
        for dispense in self:
            receipts = Alloc.search([("charge_line_id", "in", dispense.charge_line_ids.ids)]).mapped("receipt_id") if dispense.charge_line_ids else Receipt
            dispense.receipt_ids = receipts
            dispense.receipt_count = len(receipts)

    @api.depends("charge_line_ids.amount_due_for_clearance", "charge_line_ids.amount_received")
    def _compute_pharmacy_payment_totals(self):
        for dispense in self:
            charges = dispense.charge_line_ids.filtered(lambda c: c.charge_state in LIVE_CHARGE_STATES)
            dispense.unified_amount_due_for_clearance = sum(charges.mapped("amount_due_for_clearance"))
            dispense.unified_amount_received = sum(charges.mapped("amount_received"))

    @api.onchange("prescription_id")
    def _onchange_prescription_id_billing(self):
        if self.prescription_id and self.prescription_id.appointment_id and not self.encounter_id:
            encounter = self.env["hospital.encounter"].search([("appointment_id", "=", self.prescription_id.appointment_id.id)], limit=1)
            if encounter:
                self.encounter_id = encounter

    @api.constrains("patient_id", "encounter_id")
    def _check_encounter_patient_company(self):
        for dispense in self:
            if dispense.encounter_id:
                if dispense.encounter_id.patient_id != dispense.patient_id:
                    raise ValidationError("The selected encounter belongs to another patient.")
                if dispense.encounter_id.company_id and dispense.encounter_id.company_id not in self.env.companies:
                    raise ValidationError("The selected encounter belongs to a company you cannot use.")

    def _line_source_key(self, line):
        return "%s:%s:%s:%s" % (SOURCE_MODEL, self.id, line.id, PHARMACY_EVENT)

    def _line_charge(self, line):
        return self.env["hospital.charge.line"].sudo().search([("source_key", "=", self._line_source_key(line))], limit=1)

    def _pharmacy_charges(self):
        self.ensure_one()
        return self.env["hospital.charge.line"].sudo().with_context(active_test=False).search(self._charge_domain())

    def _resolve_encounter(self):
        self.ensure_one()
        if self.encounter_id:
            return self.encounter_id
        if self.appointment_id:
            encounter = self.env["hospital.billing.engine"].sudo().get_or_create_encounter(self.appointment_id)
            if encounter.patient_id != self.patient_id:
                raise ValidationError("The appointment/encounter belongs to another patient.")
            self.sudo().write({"encounter_id": encounter.id})
            return encounter
        raise UserError("Select the patient's active encounter before preparing pharmacy billing.")

    def _charge_quantity_for_line(self, line):
        qty = line.dispensed_quantity or 0.0
        if qty <= QTY_TOLERANCE:
            raise UserError("Enter the intended dispense quantity for %s before preparing payment." % line.medicine_id.display_name)
        return qty

    def _ensure_pharmacy_billing(self):
        engine = self.env["hospital.billing.engine"].sudo()
        for dispense in self:
            if not dispense.line_ids:
                raise UserError("This dispense has no medicine lines.")
            encounter = dispense._resolve_encounter()
            if encounter.patient_id != dispense.patient_id:
                raise ValidationError("The encounter belongs to another patient.")
            engine.get_or_create_billing_account(encounter)
            active_lines = dispense.line_ids.filtered(lambda l: l.medicine_id and (l.dispensed_quantity or 0.0) > QTY_TOLERANCE)
            if not active_lines:
                raise UserError("Enter at least one positive intended dispense quantity before preparing pharmacy payment.")
            active_lines.mapped("medicine_id").sudo()._assert_billable(encounter.company_id or self.env.company)
            for line in active_lines:
                service = line.medicine_id.billing_service_id
                qty = dispense._charge_quantity_for_line(line)
                charge = engine.create_or_update_charge(
                    encounter,
                    SOURCE_MODEL,
                    dispense.id,
                    PHARMACY_EVENT,
                    line.medicine_id.display_name,
                    source_line_id=line.id,
                    source_key=dispense._line_source_key(line),
                    service=service,
                    qty_requested=qty,
                    unit_price=line.unit_price if "unit_price" in line._fields and line.unit_price else service.default_price,
                )
                engine.activate_charge(charge)
                line.sudo().write({"charge_line_id": charge.id})
            dispense.invalidate_recordset(["charge_line_ids", "charge_count", "unified_amount_due_for_clearance", "unified_amount_received"])
        return True

    def action_mark_ready(self):
        # RE-ASSERTED HERE, AND IN THE TWO OVERRIDES BELOW, BECAUSE THIS LAYER
        # ACTS BEFORE super(). hospital_pharmacy owns the authorization rule and
        # states it once in _assert_pharmacy_operator(); but these overrides
        # raise charges and cancel charges on the way DOWN to it, so leaving the
        # only check inside super() would mean an unauthorized RPC call created
        # and activated medication charges before being refused. The transaction
        # would roll them back -- correctness is not at stake -- yet the refusal
        # would arrive as an opaque access error on a state write, and a future
        # nested savepoint could make the rollback less complete than it looks.
        self._assert_pharmacy_operator("mark a pharmacy dispense ready")
        self._ensure_pharmacy_billing()
        return super().action_mark_ready()

    def _clearance_error(self, clearance):
        self.ensure_one()
        details = []
        for charge in self.charge_line_ids:
            if charge.charge_state not in LIVE_CHARGE_STATES:
                continue
            due = charge.amount_due_for_clearance
            if due > AMOUNT_TOLERANCE:
                details.append("%s -- remaining %.2f %s" % (charge.description, due, charge.currency_id.name or ""))
        body = "\n".join("  - %s" % d for d in details) or "  - %s" % clearance["reason"]
        return UserError(
            "Pharmacy dispense %s cannot be validated before financial clearance.\n\n%s\n\nCollect payment or obtain authorization before Validate Dispense. No dispense state, charge delivery, receipt, accounting entry, or stock movement was changed."
            % (self.name, body)
        )

    def _assert_financially_cleared_for_dispense(self, persist=False):
        self.ensure_one()
        self._ensure_pharmacy_billing()
        charges = self._pharmacy_charges().filtered(lambda c: c.charge_state in LIVE_CHARGE_STATES)
        clearance = self.env["hospital.billing.engine"].sudo().check_financial_clearance(self.encounter_id, persist=persist, charges=charges)
        if not clearance["cleared"]:
            raise self._clearance_error(clearance)
        return clearance

    def action_mark_dispensed(self):
        self._assert_pharmacy_operator("validate a pharmacy dispense")
        for dispense in self.filtered(lambda d: d.state in ("ready", "partial")):
            dispense._assert_financially_cleared_for_dispense(persist=True)
        result = super().action_mark_dispensed()
        engine = self.env["hospital.billing.engine"].sudo()
        for dispense in self.filtered(lambda d: d.unified_billing_enabled and d.state in ("dispensed", "partial")):
            for line in dispense.line_ids.filtered(lambda l: l.charge_line_id or dispense._line_charge(l)):
                if not line.charge_line_id:
                    charge_for_line = dispense._line_charge(line)
                    if charge_for_line:
                        line.sudo().write({"charge_line_id": charge_for_line.id})
                target = line.dispensed_quantity or 0.0
                already = line.billing_delivered_quantity or 0.0
                if target <= already + QTY_TOLERANCE:
                    continue
                charge = line.charge_line_id.sudo()
                if target > charge.qty_requested + QTY_TOLERANCE and charge.invoice_state == "not_invoiced":
                    charge.with_context(pharmacy_quantity_sync=True).write({"qty_requested": target})
                engine.mark_charge_delivered(charge, qty_delivered=target)
                line.sudo().write({"billing_delivered_quantity": target})
        return result

    def _delivered_medication_details(self):
        """What on this dispense says medication has already been handed over.

        TWO WITNESSES, BOTH READ, because they are written at different moments
        and either can be the only one present. `billing_delivered_quantity` is
        this module's own per-line high-water mark, written after delivery
        succeeds; the charge's `qty_delivered` / `delivery_state` is the engine's
        record of the same event. A line whose charge link was never written back
        would show the first and not the second; a charge delivered through some
        other path would show the second and not the first.
        """
        self.ensure_one()
        details = []
        for line in self.line_ids:
            if (line.billing_delivered_quantity or 0.0) > QTY_TOLERANCE:
                details.append(
                    "%s -- %.3f already delivered"
                    % (line.medicine_id.display_name, line.billing_delivered_quantity)
                )
        for charge in self._pharmacy_charges():
            if charge.charge_state in FROZEN_CHARGE_STATES:
                continue
            if charge.qty_delivered > QTY_TOLERANCE or charge.delivery_state in (
                "delivered", "partially_delivered"
            ):
                details.append(
                    "%s -- charge %s records %.3f delivered"
                    % (charge.description, charge.name, charge.qty_delivered)
                )
        return details

    def action_cancel(self):
        """Cancel the medication charges. Never deletes.

        THE LEAK THIS CLOSES. Charges are raised and ACTIVATED at Mark Ready, so
        from `ready` onward the patient owes for medication that has not been
        handed over. The base model's action_cancel() moved the dispense to
        `cancelled` and stopped there, leaving every charge live and payable: the
        medication was never dispensed, the patient still owed for it, and
        because hospital.appointment._is_active_service_clearance_pending() is
        driven by encounter-wide live clearance rather than by any clinical
        model, the visit sat in the cashier's SERVICE PAYMENTS lane indefinitely
        with nothing left to deliver. Laboratory has had this override since its
        own slice and radiology received it in Slice 5; pharmacy is the third
        module in the same position and never received it at all.

        THE SAME SHAPE AS radiology_billing.action_cancel(), deliberately, so
        one cancellation semantic covers the three ancillary services rather
        than three that drift.

        THE DELIVERED GUARD MATTERS MORE HERE THAN ANYWHERE ELSE. Radiology
        delivers all-or-nothing at result release, so its guard protects an edge
        case. Pharmacy's `partial` state is ROUTINE -- the ordinary outcome of
        prescribing thirty tablets to a counter holding ten -- and a partially
        dispensed record has real medication in the patient's hand, a real
        receivable behind it and real stock consumed for it. Cancelling that
        charge would erase a receivable for medication that was genuinely
        supplied. It refuses loudly instead; a credit/reversal workflow is
        outside this phase.

        THE GUARD RUNS BEFORE ANY CHARGE IS TOUCHED, per dispense, so a refusal
        leaves the charges exactly as it found them rather than half-cancelled.
        engine.cancel_charge() would refuse a delivered charge on its own -- but
        only when it reached one, having already cancelled the lines before it.

        THE CHARGE CLEANUP IS IDEMPOTENT, which is the half that matters on a
        retry: engine.cancel_charge() returns early on an already-frozen charge,
        the explicit skip above it never even calls in, and the state filter
        passes over a dispense that is already cancelled. A repeated call can
        neither double-cancel a charge nor resurrect one. The TRANSITION is not
        made idempotent and is not the place to try: the base model owns the
        transition table and simply does nothing for a dispense outside
        DISPENSE_CANCELLABLE_STATES.
        """
        self._assert_pharmacy_operator("cancel a pharmacy dispense")
        engine = self.env["hospital.billing.engine"].sudo()
        for dispense in self.filtered(
            lambda record: record.state in DISPENSE_CANCELLABLE_STATES
        ):
            delivered = dispense._delivered_medication_details()
            if delivered:
                raise UserError(
                    "Pharmacy dispense %s has already delivered medication and "
                    "cannot be cancelled.\n\n%s\n\nReversing a delivered charge "
                    "requires a credit/reversal workflow, which is outside this "
                    "phase. No dispense state, charge, receipt, accounting entry "
                    "or stock movement was changed."
                    % (dispense.name, "\n".join("  - %s" % d for d in delivered))
                )
            for charge in dispense._pharmacy_charges():
                if charge.charge_state in FROZEN_CHARGE_STATES:
                    continue
                engine.cancel_charge(
                    charge, reason="Pharmacy dispense %s cancelled" % dispense.name
                )
        return super().action_cancel()

    def action_reset_to_draft(self):
        """Reopening is refused once the medication charges have been cancelled.

        THE PATH THIS CLOSES, AND THIS SLICE IS WHAT OPENED IT. Before the
        cancellation cleanup above, a cancelled dispense still carried LIVE
        charges, so reopening it and marking it ready again reused them and the
        patient still owed the money. Now those charges are cancelled -- and
        hospital.billing.engine.create_or_update_charge() searches by source_key
        and returns a frozen charge UNTOUCHED, while activate_charge() only ever
        promotes a draft. So a reopened dispense would sail through Mark Ready
        and come to rest in `ready` bound to a cancelled charge: nothing payable,
        nothing for the cashier to collect, financial clearance trivially
        satisfied because _assert_financially_cleared_for_dispense() sums only
        charges in LIVE_CHARGE_STATES -- and medication handed over free. It
        would then fail at the very end, when mark_charge_delivered() refuses a
        frozen charge, rolling back with an error naming neither the reopen nor
        the cancelled charge.

        Refusing the reopen is the smallest fix that holds the invariant. The
        alternative -- minting a replacement charge on a new source_key -- is a
        re-billing mechanism, and re-billing a cancelled episode is precisely
        the credit/reversal design this phase defers. A withdrawn dispense is
        re-prescribed, not reopened.
        """
        self._assert_pharmacy_operator("reopen a cancelled pharmacy dispense")
        for dispense in self.filtered(lambda record: record.state == "cancelled"):
            frozen = dispense._pharmacy_charges().filtered(
                lambda charge: charge.charge_state in FROZEN_CHARGE_STATES
            )
            if frozen:
                raise UserError(
                    "Pharmacy dispense %s cannot be reopened: its medication "
                    "charges (%s) were cancelled with it and cannot be revived. "
                    "Reopening would leave the dispense billable to nobody. Ask "
                    "the prescriber for a new prescription."
                    % (dispense.name, ", ".join(frozen.mapped("name")))
                )
        return super().action_reset_to_draft()

    def action_record_manual_payment(self):
        self.ensure_one()
        if not any(self.env.user.has_group(g) for g in RECEIPT_GROUPS):
            raise UserError("You are not authorized to record pharmacy payment.")
        self._ensure_pharmacy_billing()
        return {
            "type": "ir.actions.act_window",
            "name": "Record Manual Pharmacy Payment",
            "res_model": "hospital.charge.payment.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {"default_source_pharmacy_dispense_id": self.id, "default_payment_method": "cash"},
        }

    def action_view_charges(self):
        self.ensure_one()
        action = {"type": "ir.actions.act_window", "name": "Pharmacy Charges", "res_model": "hospital.charge.line", "context": {"active_test": False}, "domain": self._charge_domain()}
        charges = self.env["hospital.charge.line"].sudo().with_context(active_test=False).search(self._charge_domain())
        if len(charges) == 1:
            action.update({"view_mode": "form", "res_id": charges.id})
        else:
            action["view_mode"] = "list,form"
        return action

    def action_view_receipts(self):
        self.ensure_one()
        action = {"type": "ir.actions.act_window", "name": "Payment Receipts", "res_model": "hospital.charge.receipt", "domain": [("id", "in", self.receipt_ids.ids)]}
        if len(self.receipt_ids) == 1:
            action.update({"view_mode": "form", "res_id": self.receipt_ids.id})
        else:
            action["view_mode"] = "list,form"
        return action


class HospitalPharmacyDispenseLineBilling(models.Model):
    _inherit = "hospital.pharmacy.dispense.line"

    charge_line_id = fields.Many2one("hospital.charge.line", string="Unified Charge", readonly=True, copy=False, ondelete="restrict")
    billing_delivered_quantity = fields.Float(string="Billing Delivered Qty", readonly=True, copy=False, digits=(16, 3), default=0.0)


class HospitalChargePaymentWizardPharmacy(models.TransientModel):
    _inherit = "hospital.charge.payment.wizard"

    source_pharmacy_dispense_id = fields.Many2one("hospital.pharmacy.dispense", readonly=True)

    @api.model
    def _resolve_source_charges(self, charge_id=None, account_id=None, request_id=None, radiology_request_id=None, pharmacy_dispense_id=None):
        if pharmacy_dispense_id:
            dispense = self.env["hospital.pharmacy.dispense"].sudo().browse(pharmacy_dispense_id)
            dispense._ensure_pharmacy_billing()
            return dispense._pharmacy_charges()
        return super()._resolve_source_charges(charge_id=charge_id, account_id=account_id, request_id=request_id, radiology_request_id=radiology_request_id)

    @api.model
    def _charges_from_context(self):
        if self.env.context.get("default_source_pharmacy_dispense_id"):
            return self._resolve_source_charges(pharmacy_dispense_id=self.env.context.get("default_source_pharmacy_dispense_id"))
        return super()._charges_from_context()

    def _source_charges(self):
        self.ensure_one()
        if self.source_pharmacy_dispense_id:
            return self._resolve_source_charges(pharmacy_dispense_id=self.source_pharmacy_dispense_id.id)
        return super()._source_charges()
