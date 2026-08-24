from odoo import api, fields, models
from odoo.exceptions import UserError
from odoo.tools import float_compare


class HospitalPharmacyDispense(models.Model):
    _name = "hospital.pharmacy.dispense"
    _description = "Pharmacy Dispense"
    _order = "dispense_date desc, id desc"
    _sql_constraints = [
        (
            "prescription_unique",
            "unique(prescription_id)",
            "A prescription can have only one linked pharmacy dispense.",
        ),
    ]

    name = fields.Char(readonly=True, copy=False, default="New")
    patient_id = fields.Many2one(
        "hospital.patient",
        required=True,
        ondelete="restrict",
    )
    prescription_id = fields.Many2one("hospital.prescription")
    physician_id = fields.Many2one("hospital.doctor", string="Physician")
    pharmacist_id = fields.Many2one(
        "res.users",
        string="Pharmacist",
        default=lambda self: self.env.user,
    )
    appointment_id = fields.Many2one("hospital.appointment")
    dispense_date = fields.Datetime(default=fields.Datetime.now, required=True)
    priority = fields.Selection(
        [
            ("routine", "Routine"),
            ("urgent", "Urgent"),
            ("emergency", "Emergency"),
        ],
        default="routine",
        required=True,
    )
    state = fields.Selection(
        [
            ("draft", "Draft"),
            ("ready", "Ready"),
            ("partial", "Partially Dispensed"),
            ("dispensed", "Dispensed"),
            ("cancelled", "Cancelled"),
        ],
        default="draft",
        required=True,
    )
    line_ids = fields.One2many(
        "hospital.pharmacy.dispense.line",
        "dispense_id",
        string="Medicine Lines",
    )
    notes = fields.Text()
    active = fields.Boolean(default=True)

    @api.depends("name", "patient_id")
    def _compute_display_name(self):
        for dispense in self:
            if dispense.name and dispense.name != "New":
                dispense.display_name = dispense.name
            elif dispense.patient_id:
                dispense.display_name = f"New Dispense - {dispense.patient_id.display_name}"
            else:
                dispense.display_name = "New Dispense"

    @api.onchange("prescription_id")
    def _onchange_prescription_id(self):
        if not self.prescription_id:
            return
        if self.prescription_id.patient_id:
            self.patient_id = self.prescription_id.patient_id
        if self.prescription_id.physician_id:
            self.physician_id = self.prescription_id.physician_id
        if self.prescription_id.appointment_id and not self.appointment_id:
            self.appointment_id = self.prescription_id.appointment_id

    @api.model_create_multi
    def create(self, vals_list):
        sequence = self.env["ir.sequence"]
        for vals in vals_list:
            if not vals.get("name") or vals.get("name") == "New":
                vals["name"] = (
                    sequence.next_by_code("hospital.pharmacy.dispense.sequence") or "New"
                )
        records = super().create(vals_list)
        for record in records:
            record._create_audit_log(
                action_type="create",
                description="Pharmacy dispense created.",
                new_value=record._audit_summary(
                    ["name", "patient_id", "prescription_id", "state"]
                ),
            )
        return records

    def write(self, vals):
        tracked_vals = {
            key: value
            for key, value in vals.items()
            if key not in ("write_date", "write_uid", "display_name")
        }
        old_values = {
            record.id: record._audit_summary(tracked_vals.keys()) for record in self
        }
        result = super().write(vals)
        if tracked_vals and not self.env.context.get("skip_dispense_write_audit"):
            action_type = "archive" if vals.get("active") is False else "update"
            description = (
                "Pharmacy dispense archived."
                if action_type == "archive"
                else "Pharmacy dispense updated."
            )
            for record in self:
                record._create_audit_log(
                    action_type=action_type,
                    description=description,
                    old_value=old_values.get(record.id),
                    new_value=record._audit_summary(tracked_vals.keys()),
                )
        return result

    def unlink(self):
        if not self.env.user.has_group(
            "hospital_management.group_hospital_system_administrator"
        ):
            for record in self:
                record._create_audit_log(
                    action_type="delete_attempt",
                    description="Pharmacy dispense deletion blocked.",
                    old_value=record._audit_summary(["name", "patient_id", "state"]),
                )
            raise UserError(
                "Pharmacy dispense records are sensitive health records. "
                "Cancel or archive them instead of deleting."
            )
        return super().unlink()

    def action_mark_ready(self):
        for record in self.filtered(lambda r: r.state == "draft"):
            record._write_state("ready")

    def action_mark_partial(self):
        for record in self.filtered(lambda r: r.state == "ready"):
            record._write_state("partial")

    def action_mark_dispensed(self):
        """Validate quantities and set the final state automatically.

        The system decides between fully and partially dispensed from the
        recorded quantities; the user never picks the partial state manually.
        """
        for record in self.filtered(lambda r: r.state in ("ready", "partial")):
            record._validate_dispense_quantities()
            fully_dispensed = all(
                float_compare(
                    line.dispensed_quantity,
                    line.prescribed_quantity,
                    precision_digits=2,
                )
                == 0
                for line in record.line_ids
            )
            record._write_state("dispensed" if fully_dispensed else "partial")

    def _validate_dispense_quantities(self):
        self.ensure_one()
        if not self.line_ids:
            raise UserError(
                "This dispense has no medicine lines. Add lines before validating."
            )
        for line in self.line_ids:
            if float_compare(line.dispensed_quantity, 0.0, precision_digits=2) < 0:
                raise UserError(
                    f"Dispensed quantity for {line.medicine_id.display_name} "
                    "cannot be negative."
                )
            if (
                float_compare(
                    line.dispensed_quantity,
                    line.prescribed_quantity,
                    precision_digits=2,
                )
                > 0
            ):
                raise UserError(
                    f"Dispensed quantity for {line.medicine_id.display_name} "
                    f"({line.dispensed_quantity}) exceeds the prescribed "
                    f"quantity ({line.prescribed_quantity})."
                )
        if all(
            float_compare(line.dispensed_quantity, 0.0, precision_digits=2) == 0
            for line in self.line_ids
        ):
            raise UserError(
                "Enter the dispensed quantity on at least one medicine line "
                "before validating the dispense."
            )

    def action_cancel(self):
        for record in self.filtered(lambda r: r.state in ("draft", "ready", "partial")):
            record._write_state("cancelled")

    def action_reset_to_draft(self):
        for record in self.filtered(lambda r: r.state == "cancelled"):
            record._write_state("draft")

    def _write_state(self, new_state):
        old_state = self.state
        self.with_context(skip_dispense_write_audit=True).write({"state": new_state})
        self._create_audit_log(
            action_type="state_change",
            description="Pharmacy dispense state changed.",
            old_value=f"State: {old_state}",
            new_value=f"State: {self.state}",
        )

    def _audit_summary(self, field_names):
        values = []
        for field_name in field_names:
            if field_name in self._fields:
                value = self[field_name]
                if hasattr(value, "mapped"):
                    value = ", ".join(value.mapped("display_name"))
                values.append(f"{field_name}: {value}")
        return "; ".join(values)

    def _create_audit_log(self, action_type, description, old_value=False, new_value=False):
        try:
            audit_log = self.env["hospital.audit.log"]
        except KeyError:
            return
        for record in self:
            audit_log.with_context(audit_user_id=self.env.user.id).sudo().create_log(
                patient_id=record.patient_id.id if record.patient_id else False,
                model_name=record._name,
                record_id=record.id,
                action_type=action_type,
                description=description,
                old_value=old_value,
                new_value=new_value,
            )


class HospitalPharmacyDispenseLine(models.Model):
    _name = "hospital.pharmacy.dispense.line"
    _description = "Pharmacy Dispense Line"
    _order = "sequence, id"

    dispense_id = fields.Many2one(
        "hospital.pharmacy.dispense",
        required=True,
        ondelete="cascade",
    )
    medicine_id = fields.Many2one(
        "hospital.pharmacy.medicine",
        required=True,
    )
    prescribed_quantity = fields.Float()
    dispensed_quantity = fields.Float()
    dosage = fields.Char()
    frequency = fields.Char()
    duration = fields.Char()
    route = fields.Char()
    instruction = fields.Text()
    sequence = fields.Integer(default=10)

    def unlink(self):
        if not self.env.user.has_group(
            "hospital_management.group_hospital_system_administrator"
        ):
            for line in self:
                if line.dispense_id:
                    line.dispense_id._create_audit_log(
                        action_type="delete_attempt",
                        description="Pharmacy dispense line deletion blocked.",
                        old_value=f"Medicine: {line.medicine_id.display_name}",
                    )
            raise UserError(
                "Pharmacy dispense lines are sensitive health records. "
                "Cancel or archive the dispense instead of deleting lines."
            )
        return super().unlink()


class HospitalPrescriptionPharmacy(models.Model):
    _inherit = "hospital.prescription"

    pharmacy_dispense_ids = fields.One2many(
        "hospital.pharmacy.dispense",
        "prescription_id",
        string="Pharmacy Dispenses",
    )
    pharmacy_dispense_count = fields.Integer(
        compute="_compute_pharmacy_dispense_count",
        string="Dispensing",
    )

    def _compute_pharmacy_dispense_count(self):
        Dispense = self.env["hospital.pharmacy.dispense"]
        for prescription in self:
            if not prescription.id:
                prescription.pharmacy_dispense_count = 0
                continue
            prescription.pharmacy_dispense_count = Dispense.search_count(
                [("prescription_id", "=", prescription.id)]
            )

    def _prepare_pharmacy_dispense_line_vals(self):
        """Map prescription lines to dispense line values.

        Legacy free-text lines (no medicine_id) are skipped because dispense
        lines require a catalog medicine. Dispensed quantity starts at 0.0 —
        the pharmacist records the actual dispensed amount at the counter.
        """
        self.ensure_one()
        route_labels = dict(
            self.env["hospital.prescription.line"]._fields["route"].selection
        )
        line_vals = []
        for line in self.line_ids:
            if not line.medicine_id:
                continue
            line_vals.append(
                (
                    0,
                    0,
                    {
                        "medicine_id": line.medicine_id.id,
                        "prescribed_quantity": line.quantity,
                        "dispensed_quantity": 0.0,
                        "dosage": line.dosage,
                        "frequency": line.frequency,
                        "duration": line.duration,
                        "route": route_labels.get(line.route, "") if line.route else "",
                        "instruction": line.instructions,
                        "sequence": line.sequence,
                    },
                )
            )
        return line_vals

    def _prepare_pharmacy_dispense_vals(self):
        self.ensure_one()
        line_vals = self._prepare_pharmacy_dispense_line_vals()
        if not line_vals:
            raise UserError(
                "This prescription has no catalog medicine lines that can be sent "
                "to pharmacy dispensing."
            )
        if self.appointment_id and self.appointment_id.patient_id != self.patient_id:
            raise UserError(
                "The selected appointment belongs to another patient. "
                "Correct the prescription appointment before sending it to pharmacy."
            )
        return {
            "patient_id": self.patient_id.id,
            "prescription_id": self.id,
            "physician_id": self.physician_id.id if self.physician_id else False,
            "appointment_id": self.appointment_id.id if self.appointment_id else False,
            "line_ids": line_vals,
        }

    def _get_existing_pharmacy_dispense(self):
        self.ensure_one()
        return self.env["hospital.pharmacy.dispense"].with_context(active_test=False).search(
            [("prescription_id", "=", self.id)],
            order="id",
        )

    def _get_or_create_pharmacy_dispense(self):
        self.ensure_one()
        dispenses = self._get_existing_pharmacy_dispense()
        if len(dispenses) > 1:
            raise UserError(
                "This prescription has multiple linked pharmacy dispenses. "
                "Please ask a manager to resolve the duplicate records before continuing."
            )
        if dispenses:
            return dispenses[0]
        return self.env["hospital.pharmacy.dispense"].create(
            self._prepare_pharmacy_dispense_vals()
        )

    def action_confirm(self):
        drafts = self.filtered(lambda record: record.state == "draft")
        for prescription in drafts:
            if not prescription._get_existing_pharmacy_dispense():
                prescription._prepare_pharmacy_dispense_vals()
        result = super().action_confirm()
        for prescription in self.filtered(lambda record: record.state == "confirmed"):
            prescription._get_or_create_pharmacy_dispense()
        return result

    def action_view_pharmacy_dispense(self):
        self.ensure_one()
        context = {
            "default_prescription_id": self.id,
            "default_patient_id": self.patient_id.id,
            "default_physician_id": self.physician_id.id if self.physician_id else False,
            "default_appointment_id": (
                self.appointment_id.id if self.appointment_id else False
            ),
        }
        dispense = self._get_or_create_pharmacy_dispense()
        return {
            "type": "ir.actions.act_window",
            "name": "Pharmacy Dispense",
            "res_model": "hospital.pharmacy.dispense",
            "view_mode": "form",
            "res_id": dispense.id,
            "target": "current",
            "context": context,
        }


class HospitalPrescriptionLinePharmacy(models.Model):
    _inherit = "hospital.prescription.line"

    # Medicine catalog routes and prescription line routes are different
    # selections; only mapped values may be assigned.
    _PHARMACY_ROUTE_MAP = {
        "oral": "oral",
        "iv": "injection",
        "im": "injection",
        "subcutaneous": "injection",
        "topical": "topical",
        "ophthalmic": "eye_drop",
        "otic": "ear_drop",
        "inhalation": "inhalation",
        "other": "other",
    }

    medicine_id = fields.Many2one(
        "hospital.pharmacy.medicine",
        string="Medicine",
        required=True,
        domain=[("active", "=", True)],
        help="Medicine selected from the pharmacy catalog.",
    )

    @api.onchange("medicine_id")
    def _onchange_medicine_id(self):
        medicine = self.medicine_id
        if not medicine:
            return
        self.medicine_name = medicine.name
        if not self.dosage and medicine.strength:
            self.dosage = medicine.strength
        if not self.route and medicine.route:
            self.route = self._PHARMACY_ROUTE_MAP.get(medicine.route)
