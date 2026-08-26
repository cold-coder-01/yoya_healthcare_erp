from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError
from odoo.tools import float_compare


# WHO MAY OPERATE A DISPENSE. Pharmacy is the department that owns the counter,
# and Manager/System Administrator keep the override they hold on every other
# workflow in this system.
#
# HOSPITAL DOCTOR IS DELIBERATELY ABSENT, AND THAT IS THE POINT. A doctor's act
# is the prescription; everything from "how many do we actually have" onwards is
# somebody else's job, with somebody else's money and somebody else's stock
# behind it. Before this tuple existed the doctor ACL granted write on the
# dispense and its lines, so a doctor could set dispensed_quantity over RPC,
# raise the medication charges through Mark Ready and move stock through
# Validate Dispense -- none of which any Doctor Desk screen ever offered.
PHARMACY_OPERATOR_GROUPS = (
    "hospital_management.group_hospital_pharmacist",
    "hospital_management.group_hospital_manager",
    "hospital_management.group_hospital_system_administrator",
)

# The states action_cancel() below still acts on. Named once so the billing and
# inventory layers can restate the SAME tuple instead of each inventing one.
DISPENSE_CANCELLABLE_STATES = ("draft", "ready", "partial")

# Dispense states that block a PRESCRIPTION from being withdrawn. `partial`
# counts here even though it is ambiguous inside this module (see
# HospitalPharmacyDispense.action_cancel): a prescriber withdrawing an order that
# pharmacy has already flagged as partly filled is the case that should stop and
# ask a human rather than guess. Partial dispensing is routine rather than
# exceptional, so this tuple is reached often and must be exactly right.
PRESCRIPTION_BLOCKING_DISPENSE_STATES = ("partial", "dispensed")


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

    def _assert_pharmacy_operator(self, action):
        """Real res.groups membership check. The dispense's only authorization
        primitive.

        WHY A MODEL GUARD AND NOT ONLY AN ACL. The ACL narrowing that accompanies
        this method already stops a doctor writing the record, and every action
        below ends in a write -- so a doctor calling action_mark_ready() over RPC
        would fail anyway. It would fail LATE, though: hospital_billing's
        override raises and activates the medication charges BEFORE it calls
        super(), so the refusal would arrive after a charge had been created and
        rolled back, reported as an opaque access error on `state`. This says no
        first, and says which role is required.

        It is also the durable half of the boundary. An ACL row is one CSV line
        away from being widened again by a module that only wanted a doctor to
        SEE a dispense; this states the invariant where the workflow lives.

        sudo() DELIBERATELY STILL PASSES, in the exact shape and for the exact
        reason hospital.charge.line._assert_group() documents: server-side code
        that has already established its own authority must be able to act.
        hospital.prescription.action_cancel() is precisely such a caller -- a
        doctor cancelling their own prescription is authorized to cancel it, and
        the linked dispense's cancellation is a CONSEQUENCE of that authorized
        act rather than an independent one. What must never pass is an ordinary
        RPC user who lacks the group, whatever context they supply: no context
        key opens this, because a context key is client input.
        """
        if self.env.su:
            return
        if not any(self.env.user.has_group(g) for g in PHARMACY_OPERATOR_GROUPS):
            raise AccessError(
                "You are not authorized to %s. Pharmacy dispensing is operated "
                "by the Hospital Pharmacist role. Required: one of %s."
                % (action, ", ".join(g.split(".")[-1] for g in PHARMACY_OPERATOR_GROUPS))
            )

    def action_mark_ready(self):
        self._assert_pharmacy_operator("mark a pharmacy dispense ready")
        for record in self.filtered(lambda r: r.state == "draft"):
            record._write_state("ready")

    def action_mark_partial(self):
        self._assert_pharmacy_operator("mark a pharmacy dispense partially dispensed")
        for record in self.filtered(lambda r: r.state == "ready"):
            record._write_state("partial")

    def action_mark_dispensed(self):
        """Validate quantities and set the final state automatically.

        The system decides between fully and partially dispensed from the
        recorded quantities; the user never picks the partial state manually.
        """
        self._assert_pharmacy_operator("validate a pharmacy dispense")
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
        """Cancel the dispense. THE CLINICAL HALF OF THE GUARD LIVES HERE.

        A `dispensed` record has handed its whole prescription to the patient,
        and this module refuses to cancel it on that ground alone, with no
        reference to money or stock, so the invariant holds in an installation
        carrying neither hospital_billing nor hospital_inventory.

        `partial` IS DELIBERATELY NOT REFUSED HERE, and the reason is that in
        THIS module the state is ambiguous. action_mark_dispensed() sets it when
        some medication really was handed over, but action_mark_partial() also
        sets it straight from `ready` while delivering nothing at all. Refusing
        on the state alone would make a dispense a pharmacist merely FLAGGED as
        partial impossible to cancel ever again: hospital_billing would cancel
        its charges, this method would then raise, and the whole transaction
        would roll back every time it was tried.

        So each layer refuses on evidence it actually owns. hospital_billing
        refuses a dispense whose CHARGES record delivered quantity, and
        hospital_inventory one whose lines record CONSUMED stock, and between
        them they catch every partial that genuinely handed medication over,
        including this one, because both facts are written by the same
        action_mark_dispensed() transaction that produced the state.

        The PRESCRIPTION-side policy is stricter and stays stricter: withdrawing
        a prescription is refused for `partial` outright, because a prescriber
        cancelling an order that pharmacy has flagged as partly filled is
        exactly the ambiguous case that should stop and ask a human.
        """
        for record in self.filtered(lambda r: r.state == "dispensed"):
            raise UserError(
                "Pharmacy dispense %s is %s: medication has already been handed "
                "to the patient and cannot be cancelled. Reversing dispensed "
                "medication requires a credit/reversal workflow, which is not "
                "available in this phase. No dispense state, charge, receipt or "
                "stock movement was changed."
                % (record.name, dict(record._fields["state"].selection)[record.state])
            )
        self._assert_pharmacy_operator("cancel a pharmacy dispense")
        for record in self.filtered(lambda r: r.state in DISPENSE_CANCELLABLE_STATES):
            record._write_state("cancelled")

    def action_reset_to_draft(self):
        """Reopen a cancelled dispense. NOT AVAILABLE ONCE THE PRESCRIPTION IS
        CANCELLED, and that restriction is new.

        THE PATH THIS CLOSES. Cancelling a prescription now cancels its linked
        dispense (see HospitalPrescriptionPharmacy.action_cancel below). Without
        this guard a pharmacist could reopen that dispense straight afterwards
        and walk it back through Mark Ready and Validate Dispense -- dispensing,
        billing and consuming stock against a prescription the prescriber had
        withdrawn. The cancellation would have moved a state and changed
        nothing that mattered.

        THE SMALLEST FIX THAT PRESERVES THE INVARIANT, deliberately. It does not
        touch the transition table, does not restrict reopening after an
        ordinary pharmacy-side cancellation, and adds no new state. A withdrawn
        prescription is re-prescribed, not resurrected.
        """
        self._assert_pharmacy_operator("reopen a cancelled pharmacy dispense")
        for record in self.filtered(lambda r: r.state == "cancelled"):
            prescription = record.prescription_id.sudo()
            if prescription and prescription.state == "cancelled":
                raise UserError(
                    "Pharmacy dispense %s cannot be reopened because "
                    "prescription %s has been cancelled. Ask the prescriber for "
                    "a new prescription." % (record.name, prescription.name)
                )
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
        """The dispense linked to this prescription, if any. sudo() ON PURPOSE.

        This answers a question about the DATA -- does the one row that
        unique(prescription_id) permits already exist -- not a question about
        what the acting user may see. Since yoya_clinical_bridge scopes a
        doctor's view of the dispense to their own orders, an unscoped read is
        the only way this can stay a reliable existence check: a rule-filtered
        empty result would send _get_or_create_pharmacy_dispense() on to CREATE
        a second dispense, and the caller would meet a raw IntegrityError from
        the unique constraint instead of the existing record.

        It only ever feeds an existence test or a get-or-create; it never
        returns pharmacy data to a caller who could not otherwise read it,
        because every consumer below re-reads through the caller's own env.
        """
        self.ensure_one()
        return self.env["hospital.pharmacy.dispense"].sudo().with_context(
            active_test=False
        ).search(
            [("prescription_id", "=", self.id)],
            order="id",
        )

    def _get_or_create_pharmacy_dispense(self):
        """THE only route by which a pharmacy dispense is ever created.

        sudo() ON THE CREATE, and this is the whole of the doctor-side security
        design. hospital_pharmacy's ACL gives Hospital Doctor read and nothing
        else on the dispense and its lines, so a doctor cannot conjure a
        dispense, inject a line into somebody else's, or edit a quantity by any
        ORM or RPC route. The one dispense a doctor is entitled to cause is the
        one their own prescription's confirmation composes, and it is composed
        HERE, from values this module derives -- never from a client payload.

        sudo() bypasses access rights; it does NOT change the user (Odoo's own
        wording), so create_uid and every audit entry still name the doctor who
        confirmed the prescription. Model constraints are unaffected and still
        run: _prepare_pharmacy_dispense_vals() has already refused a mismatched
        appointment, and _check_encounter_patient_company remains in force.
        """
        self.ensure_one()
        dispenses = self._get_existing_pharmacy_dispense()
        if len(dispenses) > 1:
            raise UserError(
                "This prescription has multiple linked pharmacy dispenses. "
                "Please ask a manager to resolve the duplicate records before continuing."
            )
        if dispenses:
            return dispenses[0]
        return self.env["hospital.pharmacy.dispense"].sudo().create(
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

    def action_cancel(self):
        """Withdrawing a prescription withdraws its dispense. ONE ACT.

        THE LEAK THIS CLOSES. The base transition moved the prescription to
        `cancelled` and stopped there, leaving the linked dispense fully
        operational in draft or ready: a pharmacist could go on to Mark Ready,
        take the patient's money and hand over medication the prescriber had
        already withdrawn, and -- from `ready` -- the medication charges stayed
        live and payable, stranding the visit in the cashier's SERVICE PAYMENTS
        lane. Confirmation composes the dispense automatically, so cancellation
        is the only place that composition can be undone; anything else would
        leave the two halves of one clinical decision disagreeing.

        THE DISPENSE GOES FIRST, and that ordering is the atomicity argument.
        Everything below runs inside the caller's transaction with no commit(),
        so a refusal from the dispense -- its own delivered-medication guard,
        hospital_billing's delivered-charge guard, hospital_inventory's consumed
        -stock guard -- raises before super() is ever reached and the
        prescription is left untouched. There is no ordering in which the
        prescription is cancelled and the dispense cancellation then fails.

        POLICY, in the order the branches are reached:

          no dispense          nothing to do; ordinary cancellation
          dispense draft       cancelled through its own action_cancel()
          dispense ready       same call; hospital_billing's override cancels
                               the medication charges on the way through
          dispense cancelled   already withdrawn; the prescription may follow
          partial / dispensed  REFUSED -- medication has been handed over

        sudo() ON THE DISPENSE CANCELLATION. hospital_pharmacy's ACL leaves a
        doctor read-only on the dispense, and _assert_pharmacy_operator() would
        refuse them besides. Both are correct for a DIRECT cancellation and
        wrong for this one: the actor has already been authorized to cancel the
        prescription by that model's own ACL and record rules, and this is a
        consequence of that act, not a second act. The guards that protect the
        PATIENT -- delivered medication, delivered charges, consumed stock --
        are state checks rather than permission checks and are unaffected by
        elevation, so nothing that matters is bypassed here.
        """
        for prescription in self.filtered(
            lambda record: record.state in ("draft", "confirmed")
        ):
            dispenses = prescription._get_existing_pharmacy_dispense()
            if len(dispenses) > 1:
                raise UserError(
                    "Prescription %s has multiple linked pharmacy dispenses and "
                    "cannot be cancelled safely. Please ask a manager to resolve "
                    "the duplicate records first." % prescription.name
                )
            # REFUSED HERE, EXPLICITLY, RATHER THAN LEFT TO THE FILTER BELOW.
            # `dispensed` is not in DISPENSE_CANCELLABLE_STATES, so a filtered
            # loop alone would silently SKIP it and go on to cancel the
            # prescription -- withdrawing an order whose medication the patient
            # is already holding. Naming both delivered states here also lets
            # the refusal say which prescription is at fault, which the
            # dispense's own guard cannot.
            for dispense in dispenses.filtered(
                lambda record: record.state in PRESCRIPTION_BLOCKING_DISPENSE_STATES
            ):
                raise UserError(
                    "Prescription %s cannot be cancelled: pharmacy dispense %s "
                    "is %s, so medication has already been handed to the "
                    "patient. Reversing dispensed medication requires a "
                    "credit/reversal workflow, which is not available in this "
                    "phase. Nothing was changed on the prescription, the "
                    "dispense, its charges or its stock."
                    % (
                        prescription.name,
                        dispense.name,
                        dict(dispense._fields["state"].selection)[dispense.state],
                    )
                )
            # `cancelled` is skipped rather than refused: a dispense the pharmacy
            # already withdrew is exactly the state this call is trying to reach.
            for dispense in dispenses.filtered(
                lambda record: record.state in DISPENSE_CANCELLABLE_STATES
            ):
                # sudo() restated at the call site rather than inherited from
                # _get_existing_pharmacy_dispense(): the elevation is a decision
                # this method is making, and it should not be able to disappear
                # because a helper's implementation changed.
                dispense.sudo().action_cancel()
        return super().action_cancel()

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
