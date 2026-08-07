"""Radiology encounter billing and clearance gate.

Radiology follows the same ownership split as laboratory:

* request confirmation creates one operational charge per examination line;
* Mark In Progress is the pre-service clearance gate;
* released reports are the authoritative completion event for request completion
  and delivered/invoiceable charge quantities.
"""

from odoo import api, fields, models
from odoo.exceptions import UserError, ValidationError


RAD_EVENT = "radiology_exam"
SOURCE_MODEL = "hospital.radiology.request"
LINE_EDITABLE_STATES = ("draft",)
LIVE_CHARGE_STATES = ("draft", "active")


class HospitalRadiologyExamBilling(models.Model):
    _inherit = "hospital.radiology.exam"

    billing_service_id = fields.Many2one(
        "hospital.billing.service",
        string="Billing Service",
        domain="[('service_type', '=', 'radiology')]",
        help="Billable service used to create the radiology charge. Required before confirmation.",
    )

    @api.constrains("billing_service_id")
    def _check_billing_service(self):
        for exam in self:
            service = exam.billing_service_id
            if not service:
                continue
            if service.service_type != "radiology":
                raise ValidationError("%s: billing service must be Radiology." % exam.display_name)
            if not service.active:
                raise ValidationError("%s: billing service is archived." % exam.display_name)

    def _assert_billable(self, company=None):
        company = company or self.env.company
        today = fields.Date.context_today(self)
        unmapped = []
        bad = []
        for exam in self:
            service = exam.billing_service_id
            if not service:
                unmapped.append(exam.display_name)
                continue
            if not service.active:
                bad.append("%s: service archived" % exam.display_name)
            elif service.company_id and service.company_id != company:
                bad.append("%s: service belongs to %s, not %s" % (exam.display_name, service.company_id.name, company.name))
            elif not service.is_effective_on(today):
                bad.append("%s: service not effective on %s" % (exam.display_name, today))
        problems = []
        if unmapped:
            problems.append("No billing service is mapped for: %s" % ", ".join(sorted(unmapped)))
        if bad:
            problems.append("Misconfigured billing service for: %s" % "; ".join(sorted(bad)))
        if problems:
            raise UserError(
                "Radiology billing configuration error. No charges were created.\n\n"
                + "\n".join(problems)
                + "\n\nMap each examination to an active radiology billing service and retry."
            )
        return True


class HospitalRadiologyRequestBilling(models.Model):
    _inherit = "hospital.radiology.request"

    encounter_id = fields.Many2one(
        "hospital.encounter",
        string="Encounter",
        ondelete="restrict",
        copy=False,
        domain="[('patient_id', '=', patient_id)]",
    )
    charge_line_ids = fields.One2many("hospital.charge.line", compute="_compute_charge_line_ids", string="Radiology Charges", compute_sudo=True)
    charge_count = fields.Integer(compute="_compute_charge_line_ids", compute_sudo=True)
    receipt_ids = fields.Many2many("hospital.charge.receipt", string="Payment Receipts", compute="_compute_receipts", compute_sudo=True)
    receipt_count = fields.Integer(compute="_compute_receipts", compute_sudo=True)
    billing_blocked = fields.Boolean(compute="_compute_billing_blocked", compute_sudo=True)
    billing_clearance_message = fields.Char(compute="_compute_billing_blocked", compute_sudo=True)

    def _charge_domain(self):
        self.ensure_one()
        return [("source_model", "=", SOURCE_MODEL), ("source_res_id", "=", self.id or 0), ("source_event", "=", RAD_EVENT)]

    def _compute_charge_line_ids(self):
        Charge = self.env["hospital.charge.line"].sudo().with_context(active_test=False)
        for request in self:
            charges = Charge.search(request._charge_domain()) if request.id else Charge
            request.charge_line_ids = charges
            request.charge_count = len(charges)

    def _compute_receipts(self):
        Alloc = self.env["hospital.charge.receipt.allocation"].sudo()
        for request in self:
            receipts = Alloc.search([("charge_line_id", "in", request.charge_line_ids.ids)]).mapped("receipt_id") if request.charge_line_ids else Alloc.env["hospital.charge.receipt"]
            request.receipt_ids = receipts
            request.receipt_count = len(receipts)

    def _compute_billing_blocked(self):
        engine = self.env["hospital.billing.engine"].sudo()
        for request in self:
            if request.state != "scheduled" or not request.encounter_id:
                request.billing_blocked = False
                request.billing_clearance_message = False
                continue
            result = engine.check_financial_clearance(request.encounter_id, charges=request.charge_line_ids)
            request.billing_blocked = not result["cleared"]
            request.billing_clearance_message = result["reason"]

    def company_id_for_billing(self):
        self.ensure_one()
        return self.encounter_id.company_id or self.env.company

    def _line_charge(self, line):
        return self.env["hospital.charge.line"].sudo().search([("source_key", "=", "%s:%s:%s:%s" % (SOURCE_MODEL, self.id, line.id, RAD_EVENT))], limit=1)

    def _resolve_encounter(self):
        self.ensure_one()
        if self.encounter_id:
            return self.encounter_id
        engine = self.env["hospital.billing.engine"].sudo()
        if self.appointment_id:
            if self.appointment_id.patient_id != self.patient_id:
                raise ValidationError("Radiology request %s cites an appointment for another patient. No charges were created." % self.name)
            encounter = engine.get_or_create_encounter(self.appointment_id)
            self.sudo().write({"encounter_id": encounter.id})
            return encounter
        raise UserError("Radiology request %s has no appointment/encounter. Link the visit before confirming billing." % self.name)

    def _assert_billing_integrity(self):
        self.ensure_one()
        req = self.sudo()
        enc = req.encounter_id
        if enc and enc.patient_id != req.patient_id:
            raise ValidationError("Encounter %s belongs to %s, but radiology request %s is for %s." % (enc.name, enc.patient_id.display_name, req.name, req.patient_id.display_name))
        if req.appointment_id and enc and enc.appointment_id and enc.appointment_id != req.appointment_id:
            raise ValidationError("Radiology request %s cites appointment %s but encounter %s belongs to %s." % (req.name, req.appointment_id.display_name, enc.name, enc.appointment_id.display_name))
        return True

    def _ensure_radiology_billing(self):
        engine = self.env["hospital.billing.engine"].sudo()
        for request in self:
            lines = request.line_ids.filtered(lambda l: l.state != "cancelled")
            if not lines:
                raise UserError("Radiology request %s has no active examinations to confirm." % request.name)
            lines.mapped("exam_id").sudo()._assert_billable(request.company_id_for_billing())
            encounter = request._resolve_encounter()
            request._assert_billing_integrity()
            engine.get_or_create_billing_account(encounter)
            for line in lines:
                service = line.exam_id.billing_service_id
                charge = engine.create_or_update_charge(
                    encounter,
                    SOURCE_MODEL,
                    request.id,
                    RAD_EVENT,
                    line.exam_id.display_name,
                    source_line_id=line.id,
                    service=service,
                    qty_requested=1.0,
                )
                engine.activate_charge(charge)
            request.invalidate_recordset(["charge_line_ids", "charge_count"])
        return True

    def _assert_all_lines_charged(self):
        self.ensure_one()
        uncharged = []
        for line in self.line_ids.filtered(lambda l: l.state != "cancelled"):
            charge = self._line_charge(line)
            if not charge or charge.charge_state not in LIVE_CHARGE_STATES:
                uncharged.append(line.exam_id.display_name)
        if uncharged:
            raise ValidationError(
                "Radiology request %s cannot proceed: active examination(s) have no valid charge:\n\n%s\n\nCancel this request and raise a new one."
                % (self.name, "\n".join("  - %s" % item for item in uncharged))
            )
        return True

    def _clearance_error(self, clearance):
        self.ensure_one()
        details = []
        for charge in self.charge_line_ids:
            if charge.charge_state not in LIVE_CHARGE_STATES:
                continue
            if charge.authorization_state == "pending":
                details.append("Unpaid examination/service: %s -- payer authorization pending" % charge.description)
                continue
            due = charge.amount_due_for_clearance
            if due > 0:
                details.append("Unpaid examination/service: %s -- Patient-payable amount %.2f %s" % (charge.description, due, charge.currency_id.name or ""))
        body = "\n".join("  - %s" % d for d in details) or "  - %s" % clearance["reason"]
        return UserError(
            "Radiology service for %s cannot start.\n\n%s\n\nCollect payment or obtain authorization before clicking Mark In Progress. No radiology service, delivery, result, or inventory state was changed."
            % (self.patient_id.display_name, body)
        )

    def _assert_financially_cleared_for_service(self, persist=False):
        self.ensure_one()
        self._assert_all_lines_charged()
        clearance = self.env["hospital.billing.engine"].sudo().check_financial_clearance(
            self.encounter_id, persist=persist, charges=self.charge_line_ids
        )
        if not clearance["cleared"]:
            raise self._clearance_error(clearance)
        receipts = self.env["hospital.charge.receipt.allocation"].sudo().search(
            [("charge_line_id", "in", self.charge_line_ids.ids)]
        ).mapped("receipt_id")
        bad_receipts = receipts.filtered(
            lambda r: r.state == "confirmed"
            and "accounting_move_id" in r._fields
            and (not r.accounting_move_id or r.accounting_move_id.state != "posted")
        )
        if bad_receipts:
            raise UserError(
                "Radiology service for %s cannot start because receipt(s) %s are confirmed but not posted to accounting. Collect payment through the controlled workflow or post the receipt accounting entry first."
                % (self.patient_id.display_name, ", ".join(bad_receipts.mapped("name")))
            )
        return clearance

    def action_confirm_request(self):
        result = super().action_confirm_request()
        self.filtered(lambda r: r.state == "requested")._ensure_radiology_billing()
        return result

    def action_mark_in_progress(self):
        engine = self.env["hospital.billing.engine"].sudo()
        for request in self.filtered(lambda r: r.state == "scheduled"):
            request._assert_financially_cleared_for_service(persist=True)
        result = super().action_mark_in_progress()
        for request in self.filtered(lambda r: r.state == "in_progress"):
            if request.encounter_id and request.encounter_id.state in ("planned", "checked_in"):
                request.encounter_id.sudo().action_start()
            for charge in request.charge_line_ids:
                engine.mark_charge_in_progress(charge)
        return result

    def _released_request_lines(self):
        self.ensure_one()
        lines = self.env["hospital.radiology.request.line"]
        for result in self.result_ids.filtered(lambda r: r.state == "released"):
            for result_line in result.line_ids:
                if result_line.request_line_id:
                    lines |= result_line.request_line_id
                else:
                    candidates = self.line_ids.filtered(lambda l: l.exam_id == result_line.exam_id and l.state != "cancelled")
                    if len(candidates) == 1:
                        lines |= candidates
        return lines

    def _sync_completion_from_results(self):
        now = fields.Datetime.now()
        for request in self:
            active_lines = request.line_ids.filtered(lambda l: l.state != "cancelled")
            if not active_lines:
                continue
            released_lines = request._released_request_lines()
            if all(line in released_lines for line in active_lines):
                vals = {"state": "completed"}
                if not request.completed_at:
                    vals.update({"completed_at": now, "completed_by_id": self.env.user.id})
                if request.state != "completed" or not request.completed_at:
                    request.with_context(skip_radiology_request_write_audit=True).write(vals)
                    request._create_audit_log(action_type="state_change", description="Radiology request completed after all active examinations were released.")
        return True

    def action_record_payment(self):
        self.ensure_one()
        return {"type": "ir.actions.act_window", "name": "Record Payment / Advance", "res_model": "hospital.charge.payment.wizard", "view_mode": "form", "target": "new", "context": {"default_source_radiology_request_id": self.id}}

    def action_view_charges(self):
        self.ensure_one()
        charges = self.env["hospital.charge.line"].sudo().with_context(active_test=False).search(self._charge_domain())
        action = {"type": "ir.actions.act_window", "name": "Radiology Charges", "res_model": "hospital.charge.line", "context": {"active_test": False}, "domain": self._charge_domain()}
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


class HospitalRadiologyRequestLineBilling(models.Model):
    _inherit = "hospital.radiology.request.line"

    @staticmethod
    def _locked_message(request, verb):
        return "Radiology request %s is '%s'. Examination lines cannot be %s after confirmation; charges were already raised." % (request.name, request.state, verb)

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            request = self.env["hospital.radiology.request"].browse(vals.get("request_id"))
            if request.exists() and request.state not in LINE_EDITABLE_STATES:
                raise UserError(self._locked_message(request, "added"))
        return super().create(vals_list)

    def write(self, vals):
        protected = {"exam_id", "request_id"}
        if protected & set(vals):
            for line in self:
                if line.request_id and line.request_id.state not in LINE_EDITABLE_STATES:
                    raise UserError(self._locked_message(line.request_id, "changed"))
        if vals.get("state") == "cancelled":
            engine = self.env["hospital.billing.engine"].sudo()
            for line in self:
                charge = line.request_id._line_charge(line) if line.request_id else False
                if charge and charge.charge_state not in ("cancelled", "reversed"):
                    engine.cancel_charge(charge, reason="Radiology examination cancelled on request %s" % line.request_id.name)
        return super().write(vals)

    def unlink(self):
        for line in self:
            if line.request_id and line.request_id.state not in LINE_EDITABLE_STATES:
                raise UserError(self._locked_message(line.request_id, "removed"))
        return super().unlink()


class HospitalRadiologyResultBilling(models.Model):
    _inherit = "hospital.radiology.result"

    def _resolve_request_line(self, result_line):
        result_line.ensure_one()
        if result_line.request_line_id:
            return result_line.request_line_id
        request = self.request_id
        candidates = request.line_ids.filtered(lambda l: l.exam_id == result_line.exam_id and l.state != "cancelled")
        if len(candidates) == 1:
            result_line.sudo().write({"request_line_id": candidates.id})
            return candidates
        if not candidates:
            raise UserError("Result line '%s' does not correspond to any active examination on request %s." % (result_line.exam_id.display_name, request.display_name))
        raise UserError("Ambiguous radiology result line '%s'; the exam was ordered more than once. Set Request Line before release." % result_line.exam_id.display_name)

    def _assert_service_started_and_cleared(self):
        for result in self:
            request = result.request_id
            if request.state not in ("in_progress", "completed"):
                raise UserError("Radiology result %s cannot be validated/released before request %s is Marked In Progress and financially cleared." % (result.name, request.name))
            request._assert_financially_cleared_for_service(persist=False)
        return True

    def action_validate(self):
        self._assert_service_started_and_cleared()
        return super().action_validate()

    def action_release(self):
        self._assert_service_started_and_cleared()
        plan = {}
        for result in self:
            plan[result.id] = [result._resolve_request_line(line) for line in result.line_ids]
        result = super().action_release()
        engine = self.env["hospital.billing.engine"].sudo()
        for res in self.filtered(lambda r: r.state == "released"):
            for req_line in plan.get(res.id, []):
                charge = res.request_id._line_charge(req_line)
                if not charge or charge.charge_state in ("cancelled", "reversed"):
                    continue
                if charge.delivery_state == "delivered" and abs(charge.qty_delivered - 1.0) < 0.0001:
                    continue
                engine.mark_charge_delivered(charge, qty_delivered=1.0)
            res.request_id._sync_completion_from_results()
        return result
