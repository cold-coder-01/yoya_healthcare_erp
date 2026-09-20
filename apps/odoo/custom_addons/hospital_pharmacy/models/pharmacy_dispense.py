import hashlib
import json
import uuid

from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError
from odoo.tools import float_compare, float_round

from .pharmacy_authority import (
    DISPENSE_IDENTITY_FIELDS,
    DISPENSE_INTENT_EDITABLE_STATES,
    DISPENSE_LINE_IDENTITY_FIELDS,
    PRESCRIPTION_EDITABLE_STATES,
    PRESCRIPTION_LINE_LOCKED_FIELDS,
    PRESCRIPTION_LOCKED_FIELDS,
    QTY_PRECISION_DIGITS,
    PharmacyWorkflowError,
    dispense_composition_capability,
    dispense_workflow_capability,
    has_revision_capability,
    operation_capability,
    qty_eq,
    qty_gt,
    revision_capability,
    has_dispense_composition_capability,
    has_dispense_workflow_capability,
    has_prescription_workflow_capability,
    m2o_id,
    prescription_workflow_capability,
    same_value,
)

DISPENSE_STATE_WRITE_REFUSED = (
    "Pharmacy dispense %s cannot be moved from '%s' to '%s' by editing its "
    "state. Use the dispense's workflow actions (Mark Ready, Validate Dispense, "
    "Cancel, Reset to Draft), which run the billing, clearance and stock checks "
    "that transition requires. Nothing was changed."
)
DISPENSE_IDENTITY_WRITE_REFUSED = (
    "Pharmacy dispense %s: '%s' records who and what this dispense is for and "
    "cannot be changed after the dispense is created. Nothing was changed."
)
PRESCRIPTION_STATE_WRITE_REFUSED = (
    "Prescription %s cannot be moved from '%s' to '%s' by editing its state. "
    "Use the prescription's workflow actions. Nothing was changed."
)
PRESCRIPTION_LOCKED_REFUSED = (
    "Prescription %s is %s and has been sent to pharmacy, so its clinical "
    "content ('%s') can no longer be edited. Cancel it and write a new "
    "prescription instead. Nothing was changed."
)


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
    # Pharmacy Slice 2. Incremented exactly once by each successful Pharmacy Desk
    # Prepare or Validate, and by nothing else. A desk mutation carries the
    # revision it was loaded at; a different current value means someone else
    # changed the dispense in between, and the mutation is refused rather than
    # applied to quantities the pharmacist never saw.
    workflow_revision = fields.Integer(default=0, readonly=True, copy=False)

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
            # A dispense is BORN draft. Creating one directly in any later state
            # would skip Mark Ready, clearance, charge delivery and stock
            # consumption at once, so it is refused on every channel -- sudo and
            # RPC alike -- unless _write_state()'s capability is raised.
            requested_state = vals.get("state")
            if (
                requested_state
                and requested_state != "draft"
                and not has_dispense_workflow_capability()
            ):
                raise UserError(
                    "A pharmacy dispense is created as a draft and moved through "
                    "its workflow actions. It cannot be created directly in state "
                    "'%s'." % requested_state
                )
            if vals.get("workflow_revision") and not has_revision_capability():
                raise UserError(
                    "A pharmacy dispense starts at workflow revision 0."
                )
            if not vals.get("name") or vals.get("name") == "New":
                vals["name"] = (
                    sequence.next_by_code("hospital.pharmacy.dispense.sequence") or "New"
                )
        records = super().create(vals_list)
        records._check_identity_matches_prescription()
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
        self._assert_authoritative_write(vals)
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

    def _assert_authoritative_write(self, vals):
        """STATE AND IDENTITY AUTHORITY. Runs before super() and looks at
        neither the context nor sudo.

        `state` changes only inside _write_state(), i.e. only through the
        workflow actions that run the checks each transition needs. The identity
        fields are written once, at creation, and never relinked: moving a
        dispense to another prescription or patient would carry its charges,
        its delivered quantities and its consumed stock to someone else's
        record. Writing the value a record already has is not a change and is
        allowed, so a form that echoes the current values does not break.
        """
        if "workflow_revision" in vals and not has_revision_capability():
            for record in self:
                if vals["workflow_revision"] != record.workflow_revision:
                    raise UserError(
                        "Pharmacy dispense %s: the workflow revision is maintained "
                        "by the Pharmacy Desk workflow and cannot be edited. "
                        "Nothing was changed." % record.display_name
                    )
        if "state" in vals and not has_dispense_workflow_capability():
            for record in self:
                if vals["state"] != record.state:
                    raise UserError(
                        DISPENSE_STATE_WRITE_REFUSED
                        % (record.display_name, record.state, vals["state"])
                    )
        for field_name in DISPENSE_IDENTITY_FIELDS:
            if field_name not in vals:
                continue
            for record in self:
                if not same_value(record, field_name, vals[field_name]):
                    raise UserError(
                        DISPENSE_IDENTITY_WRITE_REFUSED
                        % (record.display_name, record._fields[field_name].string)
                    )

    def _check_identity_matches_prescription(self):
        """A dispense filed against a prescription belongs to that prescription's
        patient. Checked at creation, the only moment identity can be set.

        sudo() ON THE PRESCRIPTION READ, narrowly: this is a property of the data,
        not of what the creating user may read, and it only ever refuses.
        """
        for record in self:
            prescription = record.prescription_id.sudo()
            if prescription and prescription.patient_id != record.patient_id:
                raise UserError(
                    "Pharmacy dispense %s is for a different patient than "
                    "prescription %s. A dispense must belong to its "
                    "prescription's patient." % (record.display_name, prescription.name)
                )

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
        """REFUSED FROM `ready`. Partially Dispensed is a DELIVERY fact.

        This used to move ready -> partial while handing over nothing at all: no
        charge delivered, no stock consumed. The record then claimed medication
        had changed hands when none had, and every consumer that reads `partial`
        as "the patient holds some of this" -- the prescription cancellation
        guard, the Doctor Desk's "Partially dispensed" status -- was told
        something false.

        THE SMALLEST CORRECTION: the method no longer moves state. `partial` is
        now produced in exactly one place, action_mark_dispensed(), and only
        when that validation really delivered an increment (hospital_billing
        records billing_delivered_quantity and hospital_inventory records
        inventory_consumed_quantity in the same transaction). The method is kept
        -- rather than deleted -- so an existing caller gets a sentence that
        says what to do instead of an AttributeError.

        Authorization is still asserted FIRST, so a non-operator is refused as an
        authorization failure exactly as before. Records not in `ready` are left
        untouched, as they always were.
        """
        self._assert_pharmacy_operator("mark a pharmacy dispense partially dispensed")
        for record in self.filtered(lambda r: r.state == "ready"):
            raise UserError(
                "Pharmacy dispense %s cannot be marked Partially Dispensed by hand. "
                "Enter the quantity actually handed over and use Validate "
                "Dispense: the dispense becomes Partially Dispensed automatically "
                "when less than the prescribed quantity is delivered. Nothing was "
                "changed." % record.name
            )

    def action_mark_dispensed(self):
        """Validate quantities and set the final state automatically.

        The system decides between fully and partially dispensed from the
        recorded quantities; the user never picks the partial state manually.
        """
        self._assert_pharmacy_operator("validate a pharmacy dispense")
        for record in self.filtered(lambda r: r.state in ("ready", "partial")):
            record._validate_dispense_quantities()
            # THREE DECIMALS, the precision every high-water mark and every
            # reconciliation uses (Pharmacy Slice 2). At two, 19.996 of 20
            # rounded to "fully dispensed" while billing and stock still saw
            # 0.004 outstanding.
            fully_dispensed = all(
                qty_eq(line.dispensed_quantity, line.prescribed_quantity)
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
            if qty_gt(0.0, line.dispensed_quantity):
                raise UserError(
                    f"Dispensed quantity for {line.medicine_id.display_name} "
                    "cannot be negative."
                )
            if qty_gt(line.dispensed_quantity, line.prescribed_quantity):
                raise UserError(
                    f"Dispensed quantity for {line.medicine_id.display_name} "
                    f"({line.dispensed_quantity}) exceeds the prescribed "
                    f"quantity ({line.prescribed_quantity})."
                )
        if all(qty_eq(line.dispensed_quantity, 0.0) for line in self.line_ids):
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

        `partial` IS DELIBERATELY NOT REFUSED HERE, and the reason is that the
        state is ambiguous IN EXISTING DATA. action_mark_dispensed() sets it
        when some medication really was handed over; before Pharmacy Slice 0,
        action_mark_partial() also set it straight from `ready` while
        delivering nothing at all, and such records still exist. Refusing on the
        state alone would make a dispense a pharmacist merely FLAGGED as partial
        impossible to cancel ever again: hospital_billing would cancel its
        charges, this method would then raise, and the whole transaction would
        roll back every time it was tried.

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
        """THE controlled path for a dispense state change. Private, so not
        callable over RPC; the capability covers this one write only."""
        old_state = self.state
        with dispense_workflow_capability():
            self.with_context(skip_dispense_write_audit=True).write(
                {"state": new_state}
            )
        self._create_audit_log(
            action_type="state_change",
            description="Pharmacy dispense state changed.",
            old_value=f"State: {old_state}",
            new_value=f"State: {self.state}",
        )

    # ------------------------------------------------------------------
    # Pharmacy Desk mutations (Pharmacy Slice 2)
    # ------------------------------------------------------------------
    # TWO PRIVATE ENTRY POINTS, _desk_prepare() and _desk_validate(). Private so
    # that no RPC client can call them around the Desk's HTTP gate; the Desk
    # controller is their only caller and wraps each call in ONE savepoint.
    #
    # EACH RUNS THE SAME SHAPE:
    #
    #   1. operator authorization            (before any row is touched)
    #   2. payload shape                     (fixed codes, nothing locked yet)
    #   3. _desk_lock_for_mutation()         (the deterministic lock order)
    #   4. idempotency replay                (a replay runs NOTHING again)
    #   5. revision, state, integrity        (under the lock, on fresh values)
    #   6. the workflow itself               (existing authoritative methods)
    #   7. revision + 1, flush, serialize    (a serialization failure rolls
    #   8. the operation row                  everything back, including 6)
    #
    # THE LOCK ORDER IS FIXED HERE, in the base module, and not left to super()
    # chaining. hospital_billing and hospital_inventory do not depend on each
    # other, so the order their overrides would stack in is a load-order
    # accident; two transactions stacking them differently could deadlock. The
    # billing and stock steps are therefore separate hooks, called in sequence.
    DESK_PREPARE_STATES = ("draft", "ready", "partial")
    DESK_VALIDATE_STATES = ("ready", "partial")
    DESK_TOKEN_MAX_LENGTH = 64

    @api.model
    def _desk_clean_token(self, token):
        if token is None or (isinstance(token, str) and not token.strip()):
            raise PharmacyWorkflowError("pharmacy_operation_token_required")
        if not isinstance(token, str) or len(token.strip()) > self.DESK_TOKEN_MAX_LENGTH:
            raise PharmacyWorkflowError("pharmacy_invalid_payload")
        try:
            # Canonical form, so a retry that changes only the letter case of the
            # same UUID is still recognised as the same request.
            return str(uuid.UUID(token.strip()))
        except ValueError:
            raise PharmacyWorkflowError("pharmacy_invalid_payload") from None

    @api.model
    def _desk_clean_revision(self, value):
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise PharmacyWorkflowError("pharmacy_invalid_payload")
        return value

    @api.model
    def _desk_clean_lines(self, entries):
        """[(line_id, quantity)] sorted by line id, quantities at 3 decimals.

        SHAPE ONLY: every entry is exactly {line_id, intended_quantity}; nothing
        clinical, financial or stock-related is accepted. Whether the ids are
        THIS dispense's lines and whether the quantities are allowed are decided
        under the lock, on fresh values.
        """
        if not isinstance(entries, list) or not entries:
            raise PharmacyWorkflowError("pharmacy_invalid_payload")
        seen = set()
        cleaned = []
        for entry in entries:
            if not isinstance(entry, dict) or set(entry) != {"line_id", "intended_quantity"}:
                raise PharmacyWorkflowError("pharmacy_invalid_payload")
            line_id = entry["line_id"]
            quantity = entry["intended_quantity"]
            if isinstance(line_id, bool) or not isinstance(line_id, int) or line_id <= 0:
                raise PharmacyWorkflowError("pharmacy_invalid_payload")
            if isinstance(quantity, bool) or not isinstance(quantity, (int, float)):
                raise PharmacyWorkflowError("pharmacy_invalid_payload")
            quantity = float(quantity)
            if quantity != quantity or quantity in (float("inf"), float("-inf")):
                raise PharmacyWorkflowError("pharmacy_invalid_payload")
            if line_id in seen:
                raise PharmacyWorkflowError("pharmacy_duplicate_line")
            seen.add(line_id)
            cleaned.append((line_id, float_round(quantity, precision_digits=QTY_PRECISION_DIGITS)))
        return sorted(cleaned)

    def _desk_assert_operator(self):
        try:
            self._assert_pharmacy_operator("operate the Pharmacy Desk")
        except AccessError:
            raise PharmacyWorkflowError("pharmacy_desk_not_authorized") from None

    def _desk_digest(self, operation_type, expected_revision, lines=()):
        """The canonical request. The ACTOR is part of it: the same token
        presented by someone else is a different request, never a replay."""
        self.ensure_one()
        canonical = json.dumps(
            {
                "type": operation_type,
                "dispense": self.id,
                "expected_revision": expected_revision,
                "lines": [[line_id, "%.3f" % quantity] for line_id, quantity in lines],
                "actor": self.env.uid,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def _desk_find_replay(self, operation_type, token, digest):
        """The earlier successful operation this request replays, or False.

        sudo() ON THE LOOKUP: a token is globally unique, so the question "has
        this token been used" must see every row, not only the caller's. Nothing
        from the row is returned to the caller.
        """
        self.ensure_one()
        operation = self.env["hospital.pharmacy.operation"].sudo().search(
            [("operation_token", "=", token)], limit=1
        )
        if not operation:
            return False
        if (
            operation.dispense_id.id != self.id
            or operation.operation_type != operation_type
            or operation.performed_by_id.id != self.env.uid
            or operation.request_digest != digest
        ):
            raise PharmacyWorkflowError("pharmacy_idempotency_conflict")
        return operation

    def _desk_lock_for_mutation(self, include_stock=False):
        """THE deterministic lock order for every desk mutation on one dispense.

          1. the dispense header, FOR UPDATE
          2. a no-op UPDATE of that header -- the Laboratory Desk's pattern. Odoo
             runs at REPEATABLE READ with the snapshot taken at the request's
             first query; the no-op write makes a concurrent waiter's lock fail
             with a serialization error that Odoo's HTTP layer replays in a
             fresh snapshot, instead of letting it act on stale values
          3. the dispense lines, ordered by id, FOR UPDATE
          4-7. billing scope (hospital_billing's hook)
          8-10. stock scope, Validate only (hospital_inventory's hook)
          11. every cache invalidated, so each later check reads locked rows
        """
        self.ensure_one()
        cr = self.env.cr
        self.env.flush_all()
        cr.execute(
            "SELECT id FROM hospital_pharmacy_dispense WHERE id = %s FOR UPDATE",
            (self.id,),
        )
        if not cr.fetchone():
            raise PharmacyWorkflowError("pharmacy_dispense_not_found")
        cr.execute(
            "UPDATE hospital_pharmacy_dispense SET write_date = write_date WHERE id = %s",
            (self.id,),
        )
        cr.execute(
            "SELECT id FROM hospital_pharmacy_dispense_line WHERE dispense_id = %s "
            "ORDER BY id FOR UPDATE",
            (self.id,),
        )
        self.env.invalidate_all()
        self._desk_lock_billing_scope()
        if include_stock:
            self._desk_lock_stock_scope()
        self.env.invalidate_all()

    def _desk_lock_billing_scope(self):
        """Steps 4-7. hospital_billing implements it."""
        return None

    def _desk_lock_stock_scope(self):
        """Steps 8-10. hospital_inventory implements it."""
        return None

    def _desk_assert_integrity(self):
        """Refuse a record whose facts contradict each other. Never repairs.

        The MODEL's own floor under the Desk's richer anomaly classification:
        a desk mutation must not act on a record even if a caller skipped the
        Desk's policy check.
        """
        self.ensure_one()
        prescription = self.prescription_id.sudo()
        if (
            not self.line_ids
            or not prescription
            or prescription.patient_id != self.patient_id
            or prescription.state != "confirmed"
        ):
            raise PharmacyWorkflowError("pharmacy_dispense_needs_review")
        delivered_any = False
        for line in self.line_ids:
            if not line._desk_line_consistent():
                raise PharmacyWorkflowError("pharmacy_dispense_needs_review")
            if qty_gt(line._dispense_intent_floor(), 0.0):
                delivered_any = True
        # `partial` is a delivery fact; one with nothing delivered is legacy.
        if self.state == "partial" and not delivered_any:
            raise PharmacyWorkflowError("pharmacy_dispense_needs_review")
        if self.state in ("draft", "ready") and delivered_any:
            raise PharmacyWorkflowError("pharmacy_dispense_needs_review")

    def _desk_assert_current(self, expected_revision, states, policy_check):
        if self.workflow_revision != expected_revision:
            raise PharmacyWorkflowError("pharmacy_dispense_revision_conflict")
        if self.state not in states:
            raise PharmacyWorkflowError("pharmacy_dispense_state_conflict")
        self._desk_assert_integrity()
        if policy_check:
            policy_check(self)

    def _desk_assert_mappings(self, lines):
        """Billing and stock configuration for `lines`. Owned by the modules
        that decide what configured means; nothing here."""
        return None

    def _desk_sync_billing(self):
        """Make every line's charge bill exactly its intended quantity."""
        return None

    def _desk_assert_validate_billing(self):
        """Charges exactly synchronized, and financially cleared under lock."""
        return None

    def _desk_assert_validate_stock(self, lines):
        """Enough locked Pharmacy Store stock for every pending increment."""
        return None

    def _desk_assert_delivered(self, lines):
        """After validation, every pending line's evidence reached its intent."""
        return None

    def _desk_bump_revision(self):
        with revision_capability():
            self.with_context(skip_dispense_write_audit=True).write(
                {"workflow_revision": self.workflow_revision + 1}
            )

    def _desk_record_operation(self, operation_type, token, digest):
        """Write the replay row. LAST, after the workflow and the serialization
        succeeded. A unique-token collision -- a token raced onto another
        dispense -- is an idempotency conflict, not a crash."""
        from psycopg2 import IntegrityError

        values = {
            "dispense_id": self.id,
            "operation_type": operation_type,
            "operation_token": token,
            "request_digest": digest,
            "result_revision": self.workflow_revision,
            "performed_by_id": self.env.uid,
        }
        try:
            with self.env.cr.savepoint(), operation_capability():
                self.env["hospital.pharmacy.operation"].sudo().create(values)
        except IntegrityError:
            raise PharmacyWorkflowError("pharmacy_idempotency_conflict") from None

    def _desk_finish(self, operation_type, token, digest, serialize):
        self._desk_bump_revision()
        self._create_audit_log(
            action_type="update",
            description="Pharmacy Desk %s completed (revision %s)."
            % (operation_type, self.workflow_revision),
        )
        self.env.flush_all()
        payload = serialize(self) if serialize else None
        self._desk_record_operation(operation_type, token, digest)
        self.env.flush_all()
        return payload, False

    def _desk_prepare(self, line_quantities, operation_token, expected_revision,
                      policy_check=None, serialize=None):
        """PREPARE: set every line's cumulative intended quantity, bill it, Ready.

        Returns (payload, replayed). No stock is touched. Every refusal is a
        PharmacyWorkflowError with a fixed code; any failure after the first
        write is rolled back by the caller's savepoint together with every
        write before it.
        """
        self.ensure_one()
        self._desk_assert_operator()
        token = self._desk_clean_token(operation_token)
        revision = self._desk_clean_revision(expected_revision)
        entries = self._desk_clean_lines(line_quantities)

        self._desk_lock_for_mutation()
        digest = self._desk_digest("prepare", revision, entries)
        if self._desk_find_replay("prepare", token, digest):
            return (serialize(self) if serialize else None), True
        self._desk_assert_current(revision, self.DESK_PREPARE_STATES, policy_check)

        lines_by_id = {line.id: line for line in self.line_ids}
        if {line_id for line_id, _qty in entries} != set(lines_by_id):
            raise PharmacyWorkflowError("pharmacy_invalid_payload")

        pending = self.env["hospital.pharmacy.dispense.line"]
        for line_id, quantity in entries:
            line = lines_by_id[line_id]
            floor = line._dispense_intent_floor()
            if (
                qty_gt(0.0, quantity)
                or qty_gt(quantity, line.prescribed_quantity)
                or qty_gt(floor, quantity)
            ):
                raise PharmacyWorkflowError("pharmacy_quantity_invalid")
            if qty_gt(quantity, floor):
                pending |= line
        if not pending:
            raise PharmacyWorkflowError("pharmacy_no_positive_increment")
        self._desk_assert_mappings(pending)

        for line_id, quantity in entries:
            line = lines_by_id[line_id]
            if not qty_eq(line.dispensed_quantity, quantity):
                line.write({"dispensed_quantity": quantity})
        self._desk_sync_billing()
        self.action_mark_ready()
        return self._desk_finish("prepare", token, digest, serialize)

    def _desk_validate(self, operation_token, expected_revision,
                       policy_check=None, serialize=None):
        """VALIDATE: hand over exactly the prepared increment. Returns
        (payload, replayed). Quantities are NOT accepted: validation hands over
        what Prepare billed, and nothing else."""
        self.ensure_one()
        self._desk_assert_operator()
        token = self._desk_clean_token(operation_token)
        revision = self._desk_clean_revision(expected_revision)

        self._desk_lock_for_mutation(include_stock=True)
        digest = self._desk_digest("validate", revision)
        if self._desk_find_replay("validate", token, digest):
            return (serialize(self) if serialize else None), True
        self._desk_assert_current(revision, self.DESK_VALIDATE_STATES, policy_check)

        pending = self.line_ids.filtered(
            lambda line: qty_gt(line.dispensed_quantity, line._dispense_intent_floor())
        )
        if not pending:
            raise PharmacyWorkflowError("pharmacy_no_positive_increment")
        self._desk_assert_mappings(pending)
        self._desk_assert_validate_billing()
        self._desk_assert_validate_stock(pending)

        self.action_mark_dispensed()
        self.env.flush_all()
        self.env.invalidate_all()
        if self.state not in ("partial", "dispensed"):
            raise PharmacyWorkflowError("pharmacy_mutation_response_failed")
        self._desk_assert_delivered(pending)
        return self._desk_finish("validate", token, digest, serialize)

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

    # ------------------------------------------------------------------
    # Authority (Pharmacy Slice 0)
    # ------------------------------------------------------------------
    # THREE DIFFERENT WRITERS TOUCH A DISPENSE LINE, and this model tells them
    # apart instead of trusting any of them wholesale:
    #
    #   1. the pharmacist's PREPARATION INTENT -- `dispensed_quantity`, the
    #      cumulative quantity they intend to have handed over. An ordinary,
    #      direct write, validated here against the prescribed quantity and the
    #      delivery high-water marks.
    #   2. hospital_billing's DELIVERY high-water -- `charge_line_id` and
    #      `billing_delivered_quantity`. Guarded in hospital_billing, which owns
    #      them, by its own non-forgeable capability.
    #   3. hospital_inventory's CONSUMPTION high-water --
    #      `inventory_consumed_quantity`. Guarded in hospital_inventory the same
    #      way.
    #
    # What the line IS -- its dispense, its medicine, its prescribed quantity --
    # is frozen on any prescription-linked dispense and on any dispense that has
    # left draft. A manual (prescription-less) draft dispense keeps its fully
    # editable lines, which is what its form has always promised.

    @api.model_create_multi
    def create(self, vals_list):
        # Checked AFTER super(): the ACL refusal a doctor meets on this model
        # must still arrive as the AccessError it has always been, and the
        # parent's state is only reliably readable once the row exists. A
        # refusal here rolls the whole transaction back.
        lines = super().create(vals_list)
        lines._check_line_creation_allowed()
        lines._check_intended_quantity()
        return lines

    def write(self, vals):
        self._assert_line_identity_write(vals)
        intent_changing = "dispensed_quantity" in vals and any(
            not same_value(line, "dispensed_quantity", vals["dispensed_quantity"])
            for line in self
        )
        if intent_changing:
            for line in self:
                state = line.dispense_id.sudo().state
                if state not in DISPENSE_INTENT_EDITABLE_STATES:
                    raise UserError(
                        "The intended quantity of %s on pharmacy dispense %s cannot "
                        "change: the dispense is %s. Nothing was changed."
                        % (
                            line.medicine_id.display_name,
                            line.dispense_id.sudo().name,
                            state,
                        )
                    )
        result = super().write(vals)
        if intent_changing or "prescribed_quantity" in vals:
            self._check_intended_quantity()
        return result

    def _check_line_creation_allowed(self):
        """Lines are composed from the prescription, or added to a manual draft.

        A prescription-linked dispense receives its lines ONLY from
        _get_or_create_pharmacy_dispense(), which raises the composition
        capability around the one create it performs. Anything else would put a
        medicine in front of the pharmacist that no prescriber ordered. A
        dispense that has left draft accepts no new line at all: charges,
        clearance and delivery have been computed over the lines it had.
        """
        if has_dispense_composition_capability():
            return
        for line in self:
            dispense = line.dispense_id.sudo()
            if dispense.state != "draft":
                raise UserError(
                    "Medicine lines cannot be added to pharmacy dispense %s: it "
                    "is %s. Nothing was changed." % (dispense.name, dispense.state)
                )
            if dispense.prescription_id:
                raise UserError(
                    "Medicine lines cannot be added to pharmacy dispense %s by "
                    "hand: its lines come from prescription %s. Ask the "
                    "prescriber for a new prescription. Nothing was changed."
                    % (dispense.name, dispense.prescription_id.name)
                )

    def _assert_line_identity_write(self, vals):
        for field_name in DISPENSE_LINE_IDENTITY_FIELDS:
            if field_name not in vals:
                continue
            for line in self:
                if same_value(line, field_name, vals[field_name]):
                    continue
                dispense = line.dispense_id.sudo()
                frozen = (
                    field_name == "dispense_id"
                    or dispense.prescription_id
                    or dispense.state != "draft"
                )
                if frozen:
                    raise UserError(
                        "'%s' of %s on pharmacy dispense %s cannot be changed: it "
                        "comes from the prescription or the dispense has left "
                        "draft. Nothing was changed."
                        % (
                            line._fields[field_name].string,
                            line.medicine_id.display_name,
                            dispense.name,
                        )
                    )

    def _dispense_intent_floor(self):
        """The least cumulative quantity this line may intend: what has already
        been delivered. Zero here; hospital_billing and hospital_inventory raise
        it to their own high-water marks, so an intent can never be lowered
        beneath medication the patient already holds."""
        self.ensure_one()
        return 0.0

    def _desk_line_consistent(self):
        """Whether this line's quantities agree with each other. Pure read.

        0 < prescribed, 0 <= high-water <= intended <= prescribed. hospital_billing
        and hospital_inventory extend it with their own evidence rules.
        """
        self.ensure_one()
        prescribed = self.prescribed_quantity or 0.0
        intended = self.dispensed_quantity or 0.0
        return (
            qty_gt(prescribed, 0.0)
            and not qty_gt(0.0, intended)
            and not qty_gt(intended, prescribed)
            and not qty_gt(self._dispense_intent_floor(), intended)
        )

    def _check_intended_quantity(self):
        """0 <= high-water <= intended <= prescribed, per line."""
        for line in self:
            intended = line.dispensed_quantity or 0.0
            prescribed = line.prescribed_quantity or 0.0
            name = line.medicine_id.display_name
            if float_compare(intended, 0.0, precision_digits=QTY_PRECISION_DIGITS) < 0:
                raise UserError(
                    "The intended quantity of %s cannot be negative. Nothing was "
                    "changed." % name
                )
            if float_compare(intended, prescribed, precision_digits=QTY_PRECISION_DIGITS) > 0:
                raise UserError(
                    "The intended quantity of %s (%s) exceeds the prescribed "
                    "quantity (%s). Nothing was changed." % (name, intended, prescribed)
                )
            floor = line._dispense_intent_floor()
            if float_compare(intended, floor, precision_digits=QTY_PRECISION_DIGITS) < 0:
                raise UserError(
                    "The intended quantity of %s (%s) cannot be lower than the "
                    "quantity already handed over (%s). Intended quantities are "
                    "cumulative. Nothing was changed." % (name, intended, floor)
                )

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
        vals = self._prepare_pharmacy_dispense_vals()
        # NO PHARMACIST AT COMPOSITION (Pharmacy Slice 0). pharmacist_id defaults
        # to env.user, and composition runs inside the PRESCRIBER's transaction,
        # so every composed dispense used to name the confirming doctor as its
        # pharmacist -- a false provenance fact that reports then printed as
        # "Dispensed by". Nobody has worked the dispense yet; it says so.
        vals["pharmacist_id"] = False
        # The composition capability opens exactly one thing: creating the lines
        # of THIS prescription-linked dispense, from values derived above.
        with dispense_composition_capability():
            return self.env["hospital.pharmacy.dispense"].sudo().create(vals)

    # ------------------------------------------------------------------
    # Authority (Pharmacy Slice 0)
    # ------------------------------------------------------------------
    def _has_pharmacy_dispense(self):
        """Whether pharmacy has received this prescription. An unscoped
        existence test, for the reason _get_existing_pharmacy_dispense()
        documents; nothing is returned to the caller."""
        self.ensure_one()
        return bool(self.id and self._get_existing_pharmacy_dispense())

    def _clinical_content_locked(self):
        """The prescription's clinical content is frozen once it has left draft
        OR once a dispense has been composed from it.

        The second half is what makes Reset to Draft safe to keep. Resetting a
        cancelled prescription and re-confirming it returns the EXISTING
        dispense rather than composing a new one, so editing the medicines in
        between would leave the pharmacy filling lines the prescriber had since
        rewritten. The reset still works; the content simply stays what the
        pharmacy received.
        """
        self.ensure_one()
        return self.state not in PRESCRIPTION_EDITABLE_STATES or self._has_pharmacy_dispense()

    @api.model_create_multi
    def create(self, vals_list):
        # A prescription is BORN draft: confirmation is what composes its
        # dispense, and creating one already confirmed would skip that.
        for vals in vals_list:
            requested_state = vals.get("state")
            if (
                requested_state
                and requested_state != "draft"
                and not has_prescription_workflow_capability()
            ):
                raise UserError(
                    "A prescription is created as a draft and confirmed through "
                    "its workflow. It cannot be created directly in state '%s'."
                    % requested_state
                )
        return super().create(vals_list)

    def write(self, vals):
        if "state" in vals and not has_prescription_workflow_capability():
            for prescription in self:
                if vals["state"] != prescription.state:
                    raise UserError(
                        PRESCRIPTION_STATE_WRITE_REFUSED
                        % (prescription.display_name, prescription.state, vals["state"])
                    )
        locked = [name for name in PRESCRIPTION_LOCKED_FIELDS if name in vals]
        if locked:
            for prescription in self:
                changed = [
                    name for name in locked
                    if name in prescription._fields
                    and not same_value(prescription, name, vals[name])
                ]
                if changed and prescription._clinical_content_locked():
                    raise UserError(
                        PRESCRIPTION_LOCKED_REFUSED
                        % (
                            prescription.display_name,
                            prescription.state,
                            prescription._fields[changed[0]].string,
                        )
                    )
        return super().write(vals)

    def action_confirm(self):
        drafts = self.filtered(lambda record: record.state == "draft")
        for prescription in drafts:
            if not prescription._get_existing_pharmacy_dispense():
                prescription._prepare_pharmacy_dispense_vals()
        with prescription_workflow_capability():
            result = super().action_confirm()
        for prescription in self.filtered(lambda record: record.state == "confirmed"):
            prescription._get_or_create_pharmacy_dispense()
        return result

    def action_mark_dispensed(self):
        """The prescription header may say `dispensed` only when pharmacy has.

        THE CONTRADICTION THIS CLOSES. The base button moved the header to
        `dispensed` on nothing but a click, which is how the UAT database came
        to hold a prescription marked dispensed whose dispense is only partial.
        The dispense is the authority on delivery; the header may follow it and
        may not run ahead of it. Nothing in the runtime calls this method -- the
        Doctor Desk derives its status from the dispense -- so this only
        constrains the Odoo form button.
        """
        for prescription in self.filtered(lambda record: record.state == "confirmed"):
            dispenses = prescription._get_existing_pharmacy_dispense()
            if len(dispenses) != 1 or dispenses.state != "dispensed":
                raise UserError(
                    "Prescription %s cannot be marked dispensed: its pharmacy "
                    "dispense has not been fully dispensed. The prescription "
                    "follows the pharmacy's Validate Dispense, not the other way "
                    "round. Nothing was changed." % prescription.name
                )
        with prescription_workflow_capability():
            return super().action_mark_dispensed()

    def action_reset_to_draft(self):
        with prescription_workflow_capability():
            return super().action_reset_to_draft()

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
        with prescription_workflow_capability():
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

    # ------------------------------------------------------------------
    # Authority (Pharmacy Slice 0)
    # ------------------------------------------------------------------
    # A prescribed medicine is frozen with its prescription: once the
    # prescription has left draft or pharmacy has received it, no line may be
    # added, removed or rewritten -- by the form, by RPC or by sudo() code. The
    # dispense was composed from these values; changing them afterwards would
    # leave the pharmacy filling an order that no longer exists.

    @staticmethod
    def _locked_prescription(prescription):
        prescription = prescription.sudo()
        return bool(prescription) and prescription._clinical_content_locked()

    @api.model_create_multi
    def create(self, vals_list):
        Prescription = self.env["hospital.prescription"]
        for vals in vals_list:
            parent = Prescription.browse(m2o_id(vals.get("prescription_id")))
            if self._locked_prescription(parent):
                raise UserError(
                    PRESCRIPTION_LOCKED_REFUSED
                    % (parent.sudo().display_name, parent.sudo().state, "Medicine Lines")
                )
        return super().create(vals_list)

    def write(self, vals):
        locked = [name for name in PRESCRIPTION_LINE_LOCKED_FIELDS if name in vals]
        if locked:
            for line in self:
                changed = [
                    name for name in locked if not same_value(line, name, vals[name])
                ]
                if not changed:
                    continue
                if self._locked_prescription(line.prescription_id):
                    raise UserError(
                        PRESCRIPTION_LOCKED_REFUSED
                        % (
                            line.prescription_id.sudo().display_name,
                            line.prescription_id.sudo().state,
                            line._fields[changed[0]].string,
                        )
                    )
                if "prescription_id" in changed:
                    target = self.env["hospital.prescription"].browse(
                        m2o_id(vals["prescription_id"])
                    )
                    if self._locked_prescription(target):
                        raise UserError(
                            PRESCRIPTION_LOCKED_REFUSED
                            % (target.sudo().display_name, target.sudo().state, "Medicine Lines")
                        )
        return super().write(vals)

    def unlink(self):
        for line in self:
            if self._locked_prescription(line.prescription_id):
                raise UserError(
                    PRESCRIPTION_LOCKED_REFUSED
                    % (
                        line.prescription_id.sudo().display_name,
                        line.prescription_id.sudo().state,
                        "Medicine Lines",
                    )
                )
        return super().unlink()

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
