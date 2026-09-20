"""Pharmacy Slice 2: the atomic Prepare and Validate workflow, at the model.

WHAT IS HELD HERE
-----------------
  * Prepare sets CUMULATIVE intent, synchronizes EVERY charge (including
    positive -> 0) and makes the dispense Ready; it never touches stock.
  * Validate hands over exactly the prepared increment: billing delivers to the
    absolute cumulative target, stock consumes only the increment, and the state
    is derived at three decimals.
  * Every refusal is a fixed code, and every failure after the first write rolls
    back EVERYTHING -- intent, charges, state, revision, operation row.
  * Idempotency: the same token replays without running again; the same token
    for anything else is a conflict; the revision guards stale tabs.

These tests call the model's private desk methods directly, inside a savepoint
exactly as the Desk controller does. The HTTP contract, the role gate and the
serialized response are covered by yoya_emr_api's test_pharmacy_desk_mutations_api.
"""
import uuid
from unittest.mock import patch

from odoo import fields
from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged

from odoo.addons.hospital_pharmacy.models.pharmacy_authority import PharmacyWorkflowError


def token():
    return str(uuid.uuid4())


@tagged("post_install", "-at_install", "pharmacy_desk_mutations")
class PharmacyDeskMutationCase(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.uom = cls.env["uom.uom"].sudo().search([], limit=1)
        cls.doctor_user = cls._user("pdm_doctor", "hospital_management.group_hospital_doctor")
        cls.pharmacist = cls._user("pdm_pharm", "hospital_management.group_hospital_pharmacist")
        cls.pharmacist_2 = cls._user("pdm_pharm2", "hospital_management.group_hospital_pharmacist")
        cls.manager = cls._user("pdm_manager", "hospital_management.group_hospital_manager")
        cls.accountant = cls._user("pdm_acct", "hospital_management.group_hospital_accountant")
        cls.doctor = cls.env["hospital.doctor"].sudo().create(
            {"name": "Mutation Test Doctor", "user_id": cls.doctor_user.id}
        )
        cls.location = cls.env["hospital.inventory.location"].sudo().get_default_pharmacy_store()
        cls.category = cls.env.ref("hospital_inventory.category_pharmacy_medicine")
        cls.medicine = cls._medicine("Mutation Med A", stock=500.0)
        cls.medicine_b = cls._medicine("Mutation Med B", stock=500.0)

    @classmethod
    def _user(cls, login, *groups):
        return cls.env["res.users"].sudo().create({
            "name": login,
            "login": "%s_%s@example.test" % (login, uuid.uuid4().hex[:6]),
            "company_id": cls.company.id,
            "company_ids": [(6, 0, cls.company.ids)],
            "groups_id": [(6, 0, [cls.env.ref("base.group_user").id] + [cls.env.ref(g).id for g in groups])],
        })

    @classmethod
    def _medicine(cls, name, stock=None, billing=True, inventory=True):
        suffix = uuid.uuid4().hex[:6]
        vals = {"name": "%s %s" % (name, suffix), "code": "PDM-%s" % suffix, "dosage_form": "tablet", "route": "oral"}
        if billing:
            vals["billing_service_id"] = cls.env["hospital.billing.service"].sudo().create({
                "name": "%s service %s" % (name, suffix),
                "code": "T-PDM-%s" % suffix,
                "service_type": "pharmacy",
                "default_price": 100.0,
                "company_id": cls.company.id,
                "currency_id": cls.company.currency_id.id,
                "uom_id": cls.uom.id,
                "prepayment_required": True,
                "tax_treatment": "exempt",
            }).id
        medicine = cls.env["hospital.pharmacy.medicine"].sudo().create(vals)
        if inventory:
            item = cls.env["hospital.inventory.item"].sudo().create({
                "name": "%s item %s" % (name, suffix),
                "code": "PDI-%s" % suffix,
                "item_type": "medicine",
                "accounting_category": "medicine",
                "category_id": cls.category.id,
                "unit_of_measure": "tablet",
                "standard_cost": 10.0,
                "currency_id": cls.company.currency_id.id,
                "company_id": cls.company.id,
            })
            medicine.sudo().write({"inventory_item_id": item.id})
            if stock:
                cls._batch(item, stock)
        return medicine

    @classmethod
    def _batch(cls, item, quantity, days=365):
        return cls.env["hospital.inventory.batch"].sudo().create({
            "item_id": item.id,
            "batch_number": "PDB-%s" % uuid.uuid4().hex[:8],
            "location_id": cls.location.id,
            "received_date": fields.Date.today(),
            "expiry_date": fields.Date.add(fields.Date.today(), days=days),
            "quantity_on_hand": quantity,
            "unit_cost": 10.0,
            "currency_id": cls.company.currency_id.id,
            "state": "available",
        })

    # ------------------------------------------------------------------
    def _dispense(self, lines=None):
        """A confirmed prescription (by the doctor) and the dispense it composed.
        `lines` is [(medicine, qty)]."""
        lines = lines or [(self.medicine, 10.0)]
        suffix = uuid.uuid4().hex[:8]
        partner = self.env["res.partner"].sudo().create({"name": "PDM %s" % suffix})
        patient = self.env["hospital.patient"].sudo().create(
            {"name": "PDM Patient %s" % suffix, "accounting_partner_id": partner.id}
        )
        appointment = self.env["hospital.appointment"].sudo().create({
            "patient_id": patient.id, "doctor_id": self.doctor.id,
            "appointment_date": fields.Datetime.now(), "state": "confirmed",
        })
        self.env["hospital.encounter"].sudo().create({
            "patient_id": patient.id, "appointment_id": appointment.id,
            "encounter_type": "outpatient", "primary_doctor_id": self.doctor.id,
            "company_id": self.company.id,
        })
        prescription = self.env["hospital.prescription"].with_user(self.doctor_user).create({
            "patient_id": patient.id, "physician_id": self.doctor.id, "appointment_id": appointment.id,
            "line_ids": [
                (0, 0, {"medicine_id": m.id, "medicine_name": m.name, "quantity": q, "sequence": (i + 1) * 10})
                for i, (m, q) in enumerate(lines)
            ],
        })
        prescription.with_user(self.doctor_user).action_confirm()
        return prescription.sudo().pharmacy_dispense_ids[:1].sudo()

    def _entries(self, dispense, *quantities):
        lines = dispense.line_ids.sorted("id")
        return [{"line_id": line.id, "intended_quantity": q} for line, q in zip(lines, quantities)]

    def _prepare(self, dispense, *quantities, user=None, op_token=None, revision=None, serialize=None):
        with self.env.cr.savepoint():
            result = dispense.with_user(user or self.pharmacist)._desk_prepare(
                self._entries(dispense, *quantities),
                op_token or token(),
                dispense.workflow_revision if revision is None else revision,
                serialize=serialize,
            )
        self.env.invalidate_all()
        return result

    def _validate(self, dispense, user=None, op_token=None, revision=None, serialize=None):
        with self.env.cr.savepoint():
            result = dispense.with_user(user or self.pharmacist)._desk_validate(
                op_token or token(),
                dispense.workflow_revision if revision is None else revision,
                serialize=serialize,
            )
        self.env.invalidate_all()
        return result

    def _pay(self, dispense):
        receipt = self.env["hospital.charge.receipt"].sudo().create({
            "payment_method": "cash", "received_at": fields.Datetime.now(),
            "received_by_id": self.accountant.id, "state": "draft", "intake_token": uuid.uuid4().hex,
        })
        paid = False
        for charge in dispense._pharmacy_charges().filtered(lambda c: c.charge_state == "active"):
            due = charge.amount_due_for_clearance
            if due > 0:
                paid = True
                self.env["hospital.charge.receipt.allocation"].sudo().create(
                    {"receipt_id": receipt.id, "charge_line_id": charge.id, "amount": due}
                )
        if paid:
            receipt.sudo().write({"state": "confirmed"})
        self.env.invalidate_all()

    def _code(self, ctx):
        return ctx.exception.code

    def _snapshot(self, dispense):
        self.env.invalidate_all()
        return {
            "state": dispense.state,
            "revision": dispense.workflow_revision,
            "lines": [
                (l.dispensed_quantity, l.billing_delivered_quantity, l.inventory_consumed_quantity)
                for l in dispense.line_ids.sorted("id")
            ],
            "charges": [
                (c.id, c.charge_state, c.qty_requested, c.qty_delivered)
                for c in dispense._pharmacy_charges().sorted("id")
            ],
            "operations": self.env["hospital.pharmacy.operation"].sudo().search_count(
                [("dispense_id", "=", dispense.id)]
            ),
            "consumptions": self.env["hospital.stock.consumption"].sudo().search_count(
                [("pharmacy_dispense_id", "=", dispense.id)]
            ),
        }


@tagged("post_install", "-at_install", "pharmacy_desk_mutations")
class TestDeskPrepare(PharmacyDeskMutationCase):

    def test_prepare_partial_cumulative(self):
        dispense = self._dispense()
        _payload, replayed = self._prepare(dispense, 4.0)
        self.assertFalse(replayed)
        self.assertEqual(dispense.state, "ready")
        self.assertEqual(dispense.workflow_revision, 1)
        self.assertEqual(dispense.line_ids.dispensed_quantity, 4.0)
        charge = dispense.line_ids.charge_line_id
        self.assertEqual(charge.qty_requested, 4.0)
        self.assertEqual(dispense.line_ids.inventory_consumed_quantity, 0.0, "Prepare consumes nothing")
        self.assertFalse(dispense.inventory_consumption_ids)
        operation = self.env["hospital.pharmacy.operation"].sudo().search([("dispense_id", "=", dispense.id)])
        self.assertEqual(len(operation), 1)
        self.assertEqual(operation.performed_by_id, self.pharmacist)
        self.assertEqual(operation.result_revision, 1)

    def test_prepare_full_cumulative(self):
        dispense = self._dispense()
        self._prepare(dispense, 10.0)
        self.assertEqual(dispense.line_ids.charge_line_id.qty_requested, 10.0)

    def test_prepare_refusals(self):
        dispense = self._dispense()
        cases = [
            ((0.0,), "pharmacy_no_positive_increment"),
            ((-1.0,), "pharmacy_quantity_invalid"),
            ((10.001,), "pharmacy_quantity_invalid"),
        ]
        for quantities, code in cases:
            with self.assertRaises(PharmacyWorkflowError) as ctx:
                self._prepare(dispense, *quantities)
            self.assertEqual(self._code(ctx), code, quantities)
        self.assertEqual(dispense.state, "draft")
        self.assertEqual(dispense.workflow_revision, 0)

    def test_payload_shape_refusals(self):
        dispense = self._dispense([(self.medicine, 10.0), (self.medicine_b, 5.0)])
        a, b = dispense.line_ids.sorted("id")
        bad_payloads = {
            "pharmacy_duplicate_line": [
                {"line_id": a.id, "intended_quantity": 1}, {"line_id": a.id, "intended_quantity": 2}],
            "pharmacy_invalid_payload": [{"line_id": a.id, "intended_quantity": 1}],  # b omitted
        }
        for code, payload in bad_payloads.items():
            with self.assertRaises(PharmacyWorkflowError) as ctx:
                dispense.with_user(self.pharmacist)._desk_prepare(payload, token(), 0)
            self.assertEqual(self._code(ctx), code)
        unknown = [{"line_id": a.id, "intended_quantity": 1}, {"line_id": b.id + 99999, "intended_quantity": 1}]
        with self.assertRaises(PharmacyWorkflowError) as ctx:
            dispense.with_user(self.pharmacist)._desk_prepare(unknown, token(), 0)
        self.assertEqual(self._code(ctx), "pharmacy_invalid_payload")
        extra_key = [{"line_id": a.id, "intended_quantity": 1, "unit_price": 0},
                     {"line_id": b.id, "intended_quantity": 1}]
        with self.assertRaises(PharmacyWorkflowError) as ctx:
            dispense.with_user(self.pharmacist)._desk_prepare(extra_key, token(), 0)
        self.assertEqual(self._code(ctx), "pharmacy_invalid_payload")
        for bad_token, code in (("", "pharmacy_operation_token_required"),
                                (None, "pharmacy_operation_token_required"),
                                ("not-a-uuid", "pharmacy_invalid_payload"),
                                ("x" * 100, "pharmacy_invalid_payload")):
            with self.assertRaises(PharmacyWorkflowError) as ctx:
                dispense.with_user(self.pharmacist)._desk_prepare(self._entries(dispense, 1, 1), bad_token, 0)
            self.assertEqual(self._code(ctx), code, bad_token)

    def test_stale_revision_and_disallowed_state(self):
        dispense = self._dispense()
        self._prepare(dispense, 4.0)
        with self.assertRaises(PharmacyWorkflowError) as ctx:
            self._prepare(dispense, 5.0, revision=0)
        self.assertEqual(self._code(ctx), "pharmacy_dispense_revision_conflict")
        dispense.with_user(self.pharmacist).action_cancel()
        with self.assertRaises(PharmacyWorkflowError) as ctx:
            self._prepare(dispense, 5.0)
        self.assertEqual(self._code(ctx), "pharmacy_dispense_state_conflict")

    def test_billing_mapping_missing(self):
        unbilled = self._medicine("Unbilled", stock=50.0, billing=False)
        dispense = self._dispense([(unbilled, 5.0)])
        with self.assertRaises(PharmacyWorkflowError) as ctx:
            self._prepare(dispense, 5.0)
        self.assertEqual(self._code(ctx), "pharmacy_billing_mapping_missing")
        self.assertEqual(dispense.line_ids.dispensed_quantity, 0.0)

    def test_inventory_mapping_missing(self):
        unstocked = self._medicine("Unstocked", inventory=False)
        dispense = self._dispense([(unstocked, 5.0)])
        with self.assertRaises(PharmacyWorkflowError) as ctx:
            self._prepare(dispense, 5.0)
        self.assertEqual(self._code(ctx), "pharmacy_inventory_mapping_missing")

    def test_charge_follows_intent_up_down_and_to_zero(self):
        dispense = self._dispense([(self.medicine, 10.0), (self.medicine_b, 6.0)])
        a, b = dispense.line_ids.sorted("id")
        self._prepare(dispense, 5.0, 0.0)
        charge_a = a.charge_line_id
        self.assertEqual(charge_a.qty_requested, 5.0)
        self.assertFalse(b.charge_line_id, "a zero line raises no charge")
        # increase A, zero -> positive for B
        self._prepare(dispense, 8.0, 3.0)
        self.assertEqual(charge_a.qty_requested, 8.0)
        self.assertEqual(b.charge_line_id.qty_requested, 3.0)
        # decrease A, positive -> zero for B: THE stale-charge gap
        self._prepare(dispense, 2.0, 0.0)
        self.assertEqual(charge_a.qty_requested, 2.0)
        self.assertEqual(b.charge_line_id.qty_requested, 0.0)
        self.assertEqual(b.charge_line_id.charge_state, "active", "zeroed, never cancelled")
        self.assertEqual(dispense.workflow_revision, 3)

    def test_charge_reduce_above_delivered_after_partial(self):
        dispense = self._dispense()
        self._prepare(dispense, 4.0)
        self._pay(dispense)
        self._validate(dispense)
        self.assertEqual(dispense.state, "partial")
        self._prepare(dispense, 10.0)
        charge = dispense.line_ids.charge_line_id
        self.assertEqual(charge.qty_requested, 10.0)
        self._prepare(dispense, 6.0)
        self.assertEqual(charge.qty_requested, 6.0)
        self.assertEqual(charge.qty_delivered, 4.0)
        with self.assertRaises(PharmacyWorkflowError) as ctx:
            self._prepare(dispense, 3.0)
        self.assertEqual(self._code(ctx), "pharmacy_quantity_invalid", "below delivered")

    def test_frozen_and_invoiced_charge_conflict(self):
        for freeze in ("cancelled", "invoiced"):
            dispense = self._dispense()
            self._prepare(dispense, 4.0)
            charge = dispense.line_ids.charge_line_id
            if freeze == "cancelled":
                self.env["hospital.billing.engine"].sudo().cancel_charge(charge, reason="test")
            else:
                charge.sudo().write({"invoice_state": "invoiced"})
            before = self._snapshot(dispense)
            with self.assertRaises(PharmacyWorkflowError) as ctx:
                self._prepare(dispense, 6.0)
            self.assertEqual(self._code(ctx), "pharmacy_charge_conflict", freeze)
            self.assertEqual(self._snapshot(dispense), before, "intent rolled back with it")

    def test_replay_and_conflicts(self):
        dispense = self._dispense()
        t = token()
        self._prepare(dispense, 4.0, op_token=t)
        self.assertEqual(dispense.workflow_revision, 1)
        # Same token, same payload, same actor: a replay. The CLIENT resends the
        # revision it originally sent.
        _payload, replayed = self._prepare(dispense, 4.0, op_token=t, revision=0)
        self.assertTrue(replayed)
        self.assertEqual(dispense.workflow_revision, 1, "a replay never increments")
        self.assertEqual(self.env["hospital.pharmacy.operation"].sudo().search_count(
            [("operation_token", "=", t)]), 1)
        # Same token, different payload.
        with self.assertRaises(PharmacyWorkflowError) as ctx:
            self._prepare(dispense, 5.0, op_token=t, revision=0)
        self.assertEqual(self._code(ctx), "pharmacy_idempotency_conflict")
        # Same token, different actor.
        with self.assertRaises(PharmacyWorkflowError) as ctx:
            self._prepare(dispense, 4.0, op_token=t, revision=0, user=self.pharmacist_2)
        self.assertEqual(self._code(ctx), "pharmacy_idempotency_conflict")
        # Same token, different operation type.
        with self.assertRaises(PharmacyWorkflowError) as ctx:
            self._validate(dispense, op_token=t, revision=0)
        self.assertEqual(self._code(ctx), "pharmacy_idempotency_conflict")
        # Same token, different dispense.
        other = self._dispense()
        with self.assertRaises(PharmacyWorkflowError) as ctx:
            self._prepare(other, 4.0, op_token=t, revision=0)
        self.assertEqual(self._code(ctx), "pharmacy_idempotency_conflict")
        # A canonical-case variant of the same UUID is the same token.
        _payload, replayed = self._prepare(dispense, 4.0, op_token=t.upper(), revision=0)
        self.assertTrue(replayed)

    def test_serialization_failure_rolls_everything_back(self):
        dispense = self._dispense()
        before = self._snapshot(dispense)

        def boom(_dispense):
            raise ValueError("serializer exploded")

        with self.assertRaises(ValueError):
            self._prepare(dispense, 4.0, serialize=boom)
        self.assertEqual(self._snapshot(dispense), before)
        self.assertEqual(dispense.state, "draft")

    def test_serializer_sees_committed_values(self):
        dispense = self._dispense()
        seen = {}

        def capture(d):
            seen.update(revision=d.workflow_revision, state=d.state, qty=d.line_ids.dispensed_quantity)
            return {"ok": True}

        payload, _replayed = self._prepare(dispense, 4.0, serialize=capture)
        self.assertEqual(payload, {"ok": True})
        self.assertEqual(seen, {"revision": 1, "state": "ready", "qty": 4.0})

    def test_operator_only(self):
        dispense = self._dispense()
        for user in (self.doctor_user, self.accountant):
            with self.assertRaises(PharmacyWorkflowError) as ctx:
                self._prepare(dispense, 4.0, user=user)
            self.assertEqual(self._code(ctx), "pharmacy_desk_not_authorized")
        self._prepare(dispense, 4.0, user=self.manager)

    def test_operation_rows_are_immutable(self):
        dispense = self._dispense()
        self._prepare(dispense, 4.0)
        operation = self.env["hospital.pharmacy.operation"].sudo().search([("dispense_id", "=", dispense.id)])
        with self.assertRaises(UserError):
            operation.write({"result_revision": 99})
        with self.assertRaises(UserError):
            operation.unlink()
        with self.assertRaises(UserError):
            self.env["hospital.pharmacy.operation"].sudo().create({
                "dispense_id": dispense.id, "operation_type": "prepare", "operation_token": token(),
                "request_digest": "x", "result_revision": 1, "performed_by_id": self.pharmacist.id,
            })

    def test_revision_is_not_writable(self):
        dispense = self._dispense()
        with self.assertRaises(UserError):
            dispense.sudo().with_context(skip_dispense_write_audit=True).write({"workflow_revision": 5})


@tagged("post_install", "-at_install", "pharmacy_desk_mutations")
class TestDeskValidate(PharmacyDeskMutationCase):

    def _ready_paid(self, lines=None, *quantities):
        dispense = self._dispense(lines)
        self._prepare(dispense, *quantities)
        self._pay(dispense)
        return dispense

    def test_full_delivery(self):
        dispense = self._ready_paid(None, 10.0)
        _payload, replayed = self._validate(dispense)
        self.assertFalse(replayed)
        line = dispense.line_ids
        self.assertEqual(dispense.state, "dispensed")
        self.assertEqual((line.billing_delivered_quantity, line.inventory_consumed_quantity), (10.0, 10.0))
        self.assertEqual(line.charge_line_id.qty_delivered, 10.0)
        self.assertEqual(dispense.workflow_revision, 2)
        self.assertEqual(len(dispense.inventory_consumption_ids), 1)

    def test_partial_then_second_then_final_increment(self):
        dispense = self._ready_paid(None, 4.0)
        self._validate(dispense)
        line = dispense.line_ids
        self.assertEqual(dispense.state, "partial")
        self.assertEqual((line.billing_delivered_quantity, line.inventory_consumed_quantity), (4.0, 4.0))
        self._prepare(dispense, 7.0)
        self._pay(dispense)
        self._validate(dispense)
        self.assertEqual(dispense.state, "partial")
        self.assertEqual((line.billing_delivered_quantity, line.inventory_consumed_quantity), (7.0, 7.0))
        consumed = sum(dispense.inventory_consumption_ids.mapped("line_ids.quantity"))
        self.assertEqual(consumed, 7.0, "stock consumed only the increments")
        self._prepare(dispense, 10.0)
        self._pay(dispense)
        self._validate(dispense)
        self.assertEqual(dispense.state, "dispensed")
        self.assertEqual(line.charge_line_id.qty_delivered, 10.0, "billing delivers the absolute target")
        self.assertEqual(sum(dispense.inventory_consumption_ids.mapped("line_ids.quantity")), 10.0)
        self.assertEqual(dispense.workflow_revision, 6)

    def test_three_decimal_boundary(self):
        """19.996 of 20 is NOT fully dispensed."""
        dispense = self._ready_paid([(self.medicine, 20.0)], 19.996)
        self._validate(dispense)
        self.assertEqual(dispense.state, "partial")
        self.assertAlmostEqual(dispense.line_ids.inventory_consumed_quantity, 19.996, places=3)
        self._prepare(dispense, 20.0)
        self._pay(dispense)
        self._validate(dispense)
        self.assertEqual(dispense.state, "dispensed")
        # Rounded to three decimals on the way in: 20.0004 is 20.000.
        other = self._ready_paid([(self.medicine, 20.0)], 20.0004)
        self.assertEqual(other.line_ids.dispensed_quantity, 20.0)

    def test_multi_medicine(self):
        dispense = self._ready_paid([(self.medicine, 10.0), (self.medicine_b, 4.0)], 10.0, 2.0)
        self._validate(dispense)
        self.assertEqual(dispense.state, "partial")
        a, b = dispense.line_ids.sorted("id")
        self.assertEqual(a.inventory_consumed_quantity, 10.0)
        self.assertEqual(b.inventory_consumed_quantity, 2.0)

    def test_two_lines_share_one_inventory_item(self):
        shared = self._medicine("Shared", stock=None)
        self._batch(shared.inventory_item_id, 8.0)
        dispense = self._ready_paid([(shared, 5.0), (shared, 5.0)], 5.0, 5.0)
        before = self._snapshot(dispense)
        with self.assertRaises(PharmacyWorkflowError) as ctx:
            self._validate(dispense)
        self.assertEqual(self._code(ctx), "pharmacy_stock_insufficient",
                         "5 + 5 from one shelf of 8 is short, though each line alone fits")
        self.assertEqual(self._snapshot(dispense), before)
        self._batch(shared.inventory_item_id, 2.0, days=30)
        self._validate(dispense)
        self.assertEqual(dispense.state, "dispensed")
        batches = self.env["hospital.inventory.batch"].sudo().search(
            [("item_id", "=", shared.inventory_item_id.id)], order="expiry_date, id")
        self.assertEqual(batches.mapped("quantity_on_hand"), [0.0, 0.0], "FEFO drained both batches")

    def test_billing_blocked_when_unpaid(self):
        dispense = self._dispense()
        self._prepare(dispense, 4.0)
        before = self._snapshot(dispense)
        with self.assertRaises(PharmacyWorkflowError) as ctx:
            self._validate(dispense)
        self.assertEqual(self._code(ctx), "pharmacy_billing_blocked")
        self.assertNotIn("100", str(ctx.exception), "no amount in the refusal")
        self.assertEqual(self._snapshot(dispense), before)

    def test_clearance_is_decided_under_the_responsibility_lock(self):
        dispense = self._ready_paid(None, 4.0)
        Account = type(self.env["hospital.billing.account"])
        engine = type(self.env["hospital.billing.engine"])
        calls = []
        real_lock = Account._lock_responsibility_scope

        def spy_lock(this, account_id):
            calls.append("lock")
            return real_lock(this, account_id)

        def not_cleared(this, encounter, **kwargs):
            calls.append("clearance")
            return {"cleared": False, "state": "pending", "amount_due": 123.45, "reason": "Pay 123.45 ETB"}

        with patch.object(Account, "_lock_responsibility_scope", spy_lock), \
                patch.object(engine, "check_financial_clearance", not_cleared):
            with self.assertRaises(PharmacyWorkflowError) as ctx:
                self._validate(dispense)
        self.assertEqual(self._code(ctx), "pharmacy_billing_blocked")
        self.assertNotIn("123", str(ctx.exception))
        self.assertLess(calls.index("lock"), calls.index("clearance"))

    def test_wrong_charge_is_refused(self):
        dispense = self._ready_paid(None, 4.0)
        dispense.line_ids.charge_line_id.sudo().with_context(pharmacy_quantity_sync=True).write({"qty_requested": 3.0})
        with self.assertRaises(PharmacyWorkflowError) as ctx:
            self._validate(dispense)
        self.assertEqual(self._code(ctx), "pharmacy_charge_conflict")

    def test_mapping_removed_after_prepare(self):
        dispense = self._ready_paid(None, 4.0)
        service = self.medicine.billing_service_id
        self.medicine.sudo().write({"billing_service_id": False})
        try:
            with self.assertRaises(PharmacyWorkflowError) as ctx:
                self._validate(dispense)
            self.assertEqual(self._code(ctx), "pharmacy_billing_mapping_missing")
        finally:
            self.medicine.sudo().write({"billing_service_id": service.id})
        item = self.medicine.inventory_item_id
        self.medicine.sudo().write({"inventory_item_id": False})
        try:
            with self.assertRaises(PharmacyWorkflowError) as ctx:
                self._validate(dispense)
            self.assertEqual(self._code(ctx), "pharmacy_inventory_mapping_missing")
        finally:
            self.medicine.sudo().write({"inventory_item_id": item.id})

    def test_missing_store_and_insufficient_stock(self):
        scarce = self._medicine("Scarce", stock=None)
        self._batch(scarce.inventory_item_id, 3.0)
        dispense = self._ready_paid([(scarce, 10.0)], 4.0)
        with self.assertRaises(PharmacyWorkflowError) as ctx:
            self._validate(dispense)
        self.assertEqual(self._code(ctx), "pharmacy_stock_insufficient")
        self.assertNotIn("3", str(ctx.exception).replace("Pharmacy", ""), "no shortage figure")
        Location = type(self.env["hospital.inventory.location"])
        with patch.object(Location, "get_default_pharmacy_store", lambda this: this.browse()):
            with self.assertRaises(PharmacyWorkflowError) as ctx:
                self._validate(dispense)
        self.assertEqual(self._code(ctx), "pharmacy_stock_insufficient")

    def test_stale_revision_state_and_no_increment(self):
        dispense = self._ready_paid(None, 10.0)
        with self.assertRaises(PharmacyWorkflowError) as ctx:
            self._validate(dispense, revision=0)
        self.assertEqual(self._code(ctx), "pharmacy_dispense_revision_conflict")
        t = token()
        self._validate(dispense, op_token=t)
        # The original token replays; a NEW token for the same act is refused.
        _payload, replayed = self._validate(dispense, op_token=t, revision=1)
        self.assertTrue(replayed)
        with self.assertRaises(PharmacyWorkflowError) as ctx:
            self._validate(dispense)
        self.assertEqual(self._code(ctx), "pharmacy_dispense_state_conflict")
        self.assertEqual(len(dispense.inventory_consumption_ids), 1, "delivered once")

        partial = self._ready_paid(None, 4.0)
        self._validate(partial)
        with self.assertRaises(PharmacyWorkflowError) as ctx:
            self._validate(partial)
        self.assertEqual(self._code(ctx), "pharmacy_no_positive_increment")

    def test_simultaneous_validation_attempts(self):
        """Two tabs loaded at the same revision: the second loses on the
        revision, whatever token it carries, and nothing is delivered twice."""
        dispense = self._ready_paid(None, 4.0)
        loaded = dispense.workflow_revision
        self._validate(dispense, revision=loaded)
        with self.assertRaises(PharmacyWorkflowError) as ctx:
            self._validate(dispense, revision=loaded, user=self.pharmacist_2)
        self.assertEqual(self._code(ctx), "pharmacy_dispense_revision_conflict")
        self.assertEqual(dispense.line_ids.inventory_consumed_quantity, 4.0)

    def _assert_rollback(self, target, attribute, exc=UserError("boom")):
        dispense = self._ready_paid(None, 4.0)
        before = self._snapshot(dispense)
        batches_before = self.medicine.inventory_item_id and self.env["hospital.inventory.batch"].sudo().search(
            [("item_id", "=", self.medicine.inventory_item_id.id)]).mapped("quantity_on_hand")

        def raiser(*args, **kwargs):
            raise exc

        with patch.object(target, attribute, raiser):
            with self.assertRaises(type(exc)):
                self._validate(dispense)
        self.assertEqual(self._snapshot(dispense), before, attribute)
        self.assertEqual(
            self.env["hospital.inventory.batch"].sudo().search(
                [("item_id", "=", self.medicine.inventory_item_id.id)]).mapped("quantity_on_hand"),
            batches_before,
        )

    def test_billing_delivery_exception_rolls_back(self):
        self._assert_rollback(type(self.env["hospital.billing.engine"]), "mark_charge_delivered")

    def test_stock_allocation_exception_rolls_back(self):
        self._assert_rollback(type(self.env["hospital.pharmacy.dispense"]), "_auto_assign_consumption_batches")

    def test_batch_deduction_exception_rolls_back(self):
        self._assert_rollback(type(self.env["hospital.inventory.batch"]), "deduct_quantity")

    def test_high_water_exception_rolls_back(self):
        self._assert_rollback(
            type(self.env["hospital.pharmacy.dispense"]), "_desk_assert_delivered",
            PharmacyWorkflowError("pharmacy_mutation_response_failed"),
        )

    def test_serialization_failure_rolls_back_validate(self):
        dispense = self._ready_paid(None, 4.0)
        before = self._snapshot(dispense)

        def boom(_dispense):
            raise ValueError("serializer exploded")

        with self.assertRaises(ValueError):
            self._validate(dispense, serialize=boom)
        self.assertEqual(self._snapshot(dispense), before)

    def test_lock_order(self):
        """Header, no-op header update, lines, encounter, advisory lock, charges,
        items, batches -- in that order."""
        dispense = self._ready_paid([(self.medicine, 10.0), (self.medicine_b, 5.0)], 4.0, 2.0)
        statements = []
        cr = self.env.cr
        real_execute = type(cr).execute

        def spy(this, query, params=None, log_exceptions=True):
            text = str(query)
            if "FOR UPDATE" in text or "pg_advisory_xact_lock" in text or "SET write_date = write_date" in text:
                statements.append(text)
            return real_execute(this, query, params, log_exceptions)

        with patch.object(type(cr), "execute", spy):
            self._validate(dispense)
        markers = [
            "FROM hospital_pharmacy_dispense WHERE",
            "UPDATE hospital_pharmacy_dispense SET write_date",
            "FROM hospital_pharmacy_dispense_line",
            "FROM hospital_encounter",
            "pg_advisory_xact_lock",
            "FROM hospital_charge_line",
            "FROM hospital_inventory_item",
            "FROM hospital_inventory_batch",
        ]
        positions = []
        for marker in markers:
            index = next((i for i, text in enumerate(statements) if marker in text), None)
            self.assertIsNotNone(index, marker)
            positions.append(index)
        self.assertEqual(positions, sorted(positions), statements[:12])
