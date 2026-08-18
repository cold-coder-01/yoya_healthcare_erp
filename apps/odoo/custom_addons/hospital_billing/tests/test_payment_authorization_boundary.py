"""The operational payment authorization boundary.

These tests pin down WHO may turn money into a confirmed receipt, and -- just
as importantly -- WHERE that decision is made. The boundary used to be
effectively ACL-only: action_confirm asserted nothing, and the guard on the
allocation model could not fire because the wizard created allocations through
sudo(). A client that could reach the model could take money, button or no
button.

So the negative tests here deliberately do not stop at "an error was raised".
They assert that no receipt survived, and one of them strips the ACL question
out entirely by giving a user full table access and no role, to prove the
Python guard is load-bearing on its own.
"""

import uuid

from odoo import fields
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import TransactionCase, tagged

from odoo.addons.hospital_billing.models import charge_line, charge_receipt
from odoo.addons.hospital_billing.models import pharmacy_billing

G_CASHIER = "hospital_billing.group_hospital_cashier"
G_RECEPTIONIST = "hospital_management.group_hospital_receptionist"


@tagged("post_install", "-at_install", "payment_authorization_boundary")
class TestPaymentAuthorizationBoundary(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.service = (
            cls.env["hospital.billing.service"]
            .sudo()
            .get_default_consultation_service(cls.company)
        )
        cls.service.sudo().write(
            {
                "default_price": 300.0,
                "fixed_fee": True,
                "prepayment_required": True,
                "coverage_auth_required": False,
                "active": True,
                "is_default_consultation": True,
            }
        )
        cls.cashier = cls._make_user("boundary_cashier", [G_CASHIER])
        cls.receptionist = cls._make_user("boundary_receptionist", [G_RECEPTIONIST])

    @classmethod
    def _make_user(cls, login, group_xmlids):
        return (
            cls.env["res.users"]
            .sudo()
            .create(
                {
                    "name": login,
                    "login": "%s_%s" % (login, uuid.uuid4().hex[:6]),
                    "company_id": cls.env.company.id,
                    "company_ids": [(6, 0, cls.env.company.ids)],
                    "groups_id": [
                        (
                            6,
                            0,
                            [cls.env.ref("base.group_user").id]
                            + [cls.env.ref(x).id for x in group_xmlids],
                        )
                    ],
                }
            )
        )

    def _new_charge(self):
        suffix = uuid.uuid4().hex[:8]
        patient = (
            self.env["hospital.patient"]
            .sudo()
            .create({"name": "Boundary Patient %s" % suffix})
        )
        appointment = (
            self.env["hospital.appointment"]
            .sudo()
            .create(
                {"patient_id": patient.id, "appointment_date": fields.Datetime.now()}
            )
        )
        appointment.action_confirm()
        appointment.invalidate_recordset()
        charge = appointment.consultation_charge_id.sudo()
        charge.invalidate_recordset()
        return charge

    def _wizard_for(self, user, charge):
        """Build the wizard exactly as the web client would: defaults, then create."""
        Wizard = (
            self.env["hospital.charge.payment.wizard"]
            .with_user(user)
            .with_context(default_source_charge_id=charge.id)
        )
        return Wizard.create(Wizard.default_get(list(Wizard._fields)))

    def _receipt_count(self):
        return self.env["hospital.charge.receipt"].sudo().search_count([])

    # ------------------------------------------------------------------
    # The target matrix
    # ------------------------------------------------------------------
    def test_cashier_records_allocates_and_confirms_one_receipt(self):
        charge = self._new_charge()
        wizard = self._wizard_for(self.cashier, charge)
        self.assertEqual(len(wizard.line_ids), 1)
        self.assertAlmostEqual(wizard.line_ids.amount, 300.0, places=2)

        wizard.action_confirm()

        receipt = wizard.receipt_id.sudo()
        self.assertTrue(receipt, "the cashier's payment produced no receipt")
        self.assertEqual(receipt.state, "confirmed")
        self.assertAlmostEqual(receipt.amount, 300.0, places=2)
        self.assertEqual(receipt.received_by_id, self.cashier)
        self.assertEqual(len(receipt.allocation_ids), 1)
        self.assertEqual(receipt.allocation_ids.charge_line_id, charge)

        charge.invalidate_recordset()
        self.assertAlmostEqual(charge.amount_received, 300.0, places=2)
        self.assertEqual(charge.payment_state, "paid")
        self.assertAlmostEqual(charge.amount_due_for_clearance, 0.0, places=2)

    def test_receptionist_cannot_record_a_payment_and_leaves_no_receipt(self):
        charge = self._new_charge()
        before = self._receipt_count()
        with self.assertRaises(AccessError):
            self._wizard_for(self.receptionist, charge)
        self.assertEqual(
            self._receipt_count(),
            before,
            "a refused payment must not leave a receipt behind",
        )

    def test_receptionist_may_still_observe_payment_and_clearance_state(self):
        """The receptionist explains the bill; they just cannot take the money."""
        charge = self._new_charge()
        self._wizard_for(self.cashier, charge).action_confirm()

        seen = charge.with_user(self.receptionist)
        seen.invalidate_recordset()
        self.assertAlmostEqual(seen.amount_received, 300.0, places=2)
        self.assertAlmostEqual(seen.amount_due_for_clearance, 0.0, places=2)
        self.assertEqual(seen.payment_state, "paid")
        self.assertTrue(seen.receipt_ids)

    def test_cashier_can_read_the_money_fields_they_must_collect_against(self):
        """Regression: the cashier group was absent from the money-read set, so a
        cashier could not see what to collect even where they could act."""
        charge = self._new_charge()
        seen = charge.with_user(self.cashier)
        seen.invalidate_recordset()
        self.assertAlmostEqual(seen.amount_estimated, 300.0, places=2)
        self.assertAlmostEqual(seen.amount_due_for_clearance, 300.0, places=2)
        self.assertAlmostEqual(seen.amount_received, 0.0, places=2)

    # ------------------------------------------------------------------
    # Where the boundary lives
    # ------------------------------------------------------------------
    def test_action_confirm_guard_holds_without_any_help_from_the_acl(self):
        """Strip the ACL out of the question.

        This user is given FULL table access to the wizard and its lines and
        holds no hospital role at all -- the shape of an RPC client that has
        found a model it can reach. Before the guard existed, that was enough
        to confirm a receipt.
        """
        charge = self._new_charge()
        rogue = self._make_user("boundary_rogue", [])
        rogue_group = self.env["res.groups"].sudo().create({"name": "Boundary Rogue"})
        rogue.sudo().write({"groups_id": [(4, rogue_group.id)]})
        for model_name in (
            "hospital.charge.payment.wizard",
            "hospital.charge.payment.wizard.line",
        ):
            self.env["ir.model.access"].sudo().create(
                {
                    "name": "boundary rogue %s" % model_name,
                    "model_id": self.env["ir.model"]._get(model_name).id,
                    "group_id": rogue_group.id,
                    "perm_read": True,
                    "perm_write": True,
                    "perm_create": True,
                    "perm_unlink": True,
                }
            )

        wizard = self._wizard_for(rogue, charge)
        before = self._receipt_count()
        with self.assertRaises(AccessError):
            wizard.action_confirm()
        self.assertEqual(self._receipt_count(), before)

    def test_receptionist_cannot_allocate_directly_against_a_charge(self):
        """The other door into the money path: skip the wizard, write allocations."""
        charge = self._new_charge()
        receipt = (
            self.env["hospital.charge.receipt"]
            .sudo()
            .create(
                {
                    "payment_method": "cash",
                    "received_at": fields.Datetime.now(),
                    "received_by_id": self.cashier.id,
                    "state": "draft",
                    "intake_token": uuid.uuid4().hex,
                }
            )
        )
        with self.assertRaises(AccessError):
            self.env["hospital.charge.receipt.allocation"].with_user(
                self.receptionist
            ).create(
                {
                    "receipt_id": receipt.id,
                    "charge_line_id": charge.id,
                    "amount": 100.0,
                }
            )

    def test_idempotent_confirm_yields_exactly_one_receipt(self):
        charge = self._new_charge()
        wizard = self._wizard_for(self.cashier, charge)
        wizard.action_confirm()
        first = wizard.receipt_id

        wizard.action_confirm()

        self.assertEqual(wizard.receipt_id, first)
        self.assertEqual(
            self.env["hospital.charge.receipt"]
            .sudo()
            .search_count([("intake_token", "=", wizard.intake_token)]),
            1,
        )
        charge.invalidate_recordset()
        self.assertAlmostEqual(charge.amount_received, 300.0, places=2)

    # ------------------------------------------------------------------
    # The cashier's authority stops at operational intake
    # ------------------------------------------------------------------
    def test_cashier_cannot_perform_accounting_acts(self):
        charge = self._new_charge()
        self._wizard_for(self.cashier, charge).action_confirm()
        charge.invalidate_recordset()

        with self.assertRaises(AccessError):
            charge.with_user(self.cashier).write({"amount_applied_to_invoice": 50.0})
        with self.assertRaises(AccessError):
            charge.with_user(self.cashier).write(
                {"amount_refunded_from_advance": 50.0}
            )

    def test_cashier_cannot_touch_accounting_bridge_state_on_a_receipt(self):
        charge = self._new_charge()
        wizard = self._wizard_for(self.cashier, charge)
        wizard.action_confirm()
        receipt = wizard.receipt_id

        with self.assertRaises(AccessError):
            receipt.with_user(self.cashier).write({"accounting_posted": True})
        with self.assertRaises(AccessError):
            receipt.with_user(self.cashier).write({"accounting_reference": "JE/1"})

    def test_cashier_cannot_confirm_or_delete_a_receipt_by_hand(self):
        charge = self._new_charge()
        receipt = (
            self.env["hospital.charge.receipt"]
            .sudo()
            .create(
                {
                    "payment_method": "cash",
                    "received_at": fields.Datetime.now(),
                    "received_by_id": self.cashier.id,
                    "state": "draft",
                    "intake_token": uuid.uuid4().hex,
                }
            )
        )
        with self.assertRaises(AccessError):
            receipt.with_user(self.cashier).write({"state": "confirmed"})

        self._wizard_for(self.cashier, charge).action_confirm()
        confirmed = charge.receipt_ids.sudo()[:1]
        with self.assertRaises(UserError):
            confirmed.unlink()

    # ------------------------------------------------------------------
    # The two roles stay separate
    # ------------------------------------------------------------------
    def test_the_roles_do_not_imply_one_another(self):
        self.assertFalse(
            self.cashier.has_group(G_RECEPTIONIST),
            "a cashier must not gain the receptionist workflow by being a cashier",
        )
        self.assertFalse(
            self.receptionist.has_group(G_CASHIER),
            "a receptionist must not become a cashier implicitly",
        )

    def test_manager_and_admin_retain_broader_authority(self):
        manager = self._make_user(
            "boundary_manager", ["hospital_management.group_hospital_manager"]
        )
        charge = self._new_charge()
        self._wizard_for(manager, charge).action_confirm()
        charge.invalidate_recordset()
        self.assertAlmostEqual(charge.amount_received, 300.0, places=2)
        self.assertTrue(manager.has_group(G_RECEPTIONIST))

    # ------------------------------------------------------------------
    # Account-level API launcher
    # ------------------------------------------------------------------
    def _account_for(self, charge):
        return charge.billing_account_id.sudo()

    def _add_account_charge(self, account, amount, description="Extra prepaid charge"):
        return self.env["hospital.charge.line"].sudo().create(
            {
                "billing_account_id": account.id,
                "description": "%s %s" % (description, uuid.uuid4().hex[:6]),
                "billing_basis": "prepaid",
                "charge_state": "active",
                "unit_price": amount,
                "qty_requested": 1.0,
            }
        )

    def test_account_method_cashier_records_full_payment(self):
        charge = self._new_charge()
        account = self._account_for(charge)

        receipt = account.with_user(self.cashier).record_operational_payment(
            300.0, "cash", intake_token=uuid.uuid4().hex
        )

        self.assertEqual(receipt.state, "confirmed")
        self.assertEqual(receipt.received_by_id, self.cashier)
        self.assertEqual(receipt.billing_account_id, account)
        self.assertEqual(receipt.encounter_id, account.encounter_id)
        self.assertEqual(receipt.patient_id, account.patient_id)
        self.assertEqual(receipt.company_id, account.company_id)
        self.assertFalse(receipt.accounting_posted)
        self.assertFalse(receipt.fiscalized)
        charge.invalidate_recordset()
        self.assertAlmostEqual(charge.amount_due_for_clearance, 0.0, places=2)

    def test_account_method_cashier_records_partial_payment(self):
        charge = self._new_charge()
        account = self._account_for(charge)

        receipt = account.with_user(self.cashier).record_operational_payment(
            125.0, "cash", intake_token=uuid.uuid4().hex
        )

        self.assertEqual(receipt.state, "confirmed")
        self.assertAlmostEqual(receipt.amount, 125.0, places=2)
        charge.invalidate_recordset()
        self.assertAlmostEqual(charge.amount_received, 125.0, places=2)
        self.assertAlmostEqual(charge.amount_due_for_clearance, 175.0, places=2)
        self.assertEqual(charge.payment_state, "partially_paid")

    def test_account_method_receptionist_denied(self):
        charge = self._new_charge()
        account = self._account_for(charge)
        before = self._receipt_count()

        with self.assertRaises(AccessError):
            account.with_user(self.receptionist).record_operational_payment(
                300.0, "cash", intake_token=uuid.uuid4().hex
            )

        self.assertEqual(self._receipt_count(), before)

    def test_account_method_rogue_with_acl_denied(self):
        charge = self._new_charge()
        account = self._account_for(charge)
        rogue = self._make_user("account_method_rogue", [])
        rogue_group = self.env["res.groups"].sudo().create({"name": "Account Method Rogue"})
        rogue.sudo().write({"groups_id": [(4, rogue_group.id)]})
        for model_name in (
            "hospital.billing.account",
            "hospital.charge.payment.wizard",
            "hospital.charge.payment.wizard.line",
            "hospital.charge.receipt",
            "hospital.charge.receipt.allocation",
        ):
            self.env["ir.model.access"].sudo().create(
                {
                    "name": "account method rogue %s" % model_name,
                    "model_id": self.env["ir.model"]._get(model_name).id,
                    "group_id": rogue_group.id,
                    "perm_read": True,
                    "perm_write": True,
                    "perm_create": True,
                    "perm_unlink": True,
                }
            )

        with self.assertRaises(AccessError):
            account.with_user(rogue).record_operational_payment(
                300.0, "cash", intake_token=uuid.uuid4().hex
            )

    def test_account_method_manager_allowed(self):
        manager = self._make_user(
            "account_method_manager", ["hospital_management.group_hospital_manager"]
        )
        charge = self._new_charge()
        account = self._account_for(charge)

        receipt = account.with_user(manager).record_operational_payment(
            300.0, "cash", intake_token=uuid.uuid4().hex
        )

        self.assertEqual(receipt.state, "confirmed")
        self.assertEqual(receipt.received_by_id, manager)

    def test_account_method_rejects_zero_and_negative_amounts(self):
        account = self._account_for(self._new_charge())
        for amount in (0.0, -1.0):
            with self.assertRaises(ValidationError):
                account.with_user(self.cashier).record_operational_payment(
                    amount, "cash", intake_token=uuid.uuid4().hex
                )

    def test_account_method_requires_reference_for_non_cash(self):
        account = self._account_for(self._new_charge())
        with self.assertRaises(ValidationError):
            account.with_user(self.cashier).record_operational_payment(
                100.0, "card", intake_token=uuid.uuid4().hex
            )

    def test_account_method_idempotency_returns_existing_receipt(self):
        charge = self._new_charge()
        account = self._account_for(charge)
        token = uuid.uuid4().hex

        first = account.with_user(self.cashier).record_operational_payment(
            300.0, "cash", intake_token=token
        )
        second = account.with_user(self.cashier).record_operational_payment(
            300.0, "cash", intake_token=token
        )

        self.assertEqual(first, second)
        self.assertEqual(
            self.env["hospital.charge.receipt"].sudo().search_count([("intake_token", "=", token)]),
            1,
        )
        self.assertEqual(
            self.env["hospital.charge.receipt.allocation"].sudo().search_count([("receipt_id", "=", first.id)]),
            1,
        )

    def test_account_method_idempotency_conflict_rejected(self):
        account = self._account_for(self._new_charge())
        token = uuid.uuid4().hex
        account.with_user(self.cashier).record_operational_payment(
            100.0, "cash", intake_token=token
        )

        with self.assertRaises(ValidationError):
            account.with_user(self.cashier).record_operational_payment(
                125.0, "cash", intake_token=token
            )

    def test_account_method_allocates_deterministically_across_account_charges(self):
        charge = self._new_charge()
        account = self._account_for(charge)
        second = self._add_account_charge(account, 200.0)

        receipt = account.with_user(self.cashier).record_operational_payment(
            350.0, "cash", intake_token=uuid.uuid4().hex
        )

        allocations = receipt.allocation_ids.sorted("id")
        self.assertEqual(allocations.mapped("charge_line_id"), charge | second)
        self.assertAlmostEqual(allocations[0].amount, 300.0, places=2)
        self.assertAlmostEqual(allocations[1].amount, 50.0, places=2)

    def test_account_method_cashier_overpayment_rejected(self):
        account = self._account_for(self._new_charge())
        before = self._receipt_count()
        with self.assertRaises(UserError):
            account.with_user(self.cashier).record_operational_payment(
                301.0, "cash", intake_token=uuid.uuid4().hex
            )
        self.assertEqual(self._receipt_count(), before)

    def test_account_method_manager_overpayment_requires_note(self):
        """Overpayment is a LEGACY-mode affordance, so the mode is stated here.

        This test used to inherit whatever payer_responsibility_mode the
        database happened to carry. Under 'enforce' an allocation above the
        patient's residual is refused outright by
        charge_receipt._check_within_patient_responsibility, so the note
        requirement could never be reached and the test failed for a reason
        that had nothing to do with notes.

        Setting the mode explicitly makes the test independent of UAT data.
        The enforce-side behaviour has its own coverage in the cashier suite
        (test_51_enforce_ceiling_returns_a_specific_error_code).
        """
        # Captured BEFORE the write, or the restore would put back the value
        # this test just set. TransactionCase rolls back anyway; this keeps the
        # intent readable rather than relying on that.
        original_mode = self.env.company.payer_responsibility_mode
        self.env.company.sudo().write({"payer_responsibility_mode": "off"})
        self.addCleanup(
            self.env.company.sudo().write,
            {"payer_responsibility_mode": original_mode},
        )
        manager = self._make_user(
            "account_method_overpay_manager", ["hospital_management.group_hospital_manager"]
        )
        account = self._account_for(self._new_charge())
        with self.assertRaises(UserError):
            account.with_user(manager).record_operational_payment(
                301.0, "cash", intake_token=uuid.uuid4().hex
            )

        receipt = account.with_user(manager).record_operational_payment(
            301.0, "cash", note="Patient paid rounded amount.", intake_token=uuid.uuid4().hex
        )
        self.assertEqual(receipt.state, "confirmed")
        self.assertAlmostEqual(receipt.amount, 301.0, places=2)
    # ------------------------------------------------------------------
    # Drift guard
    # ------------------------------------------------------------------
    def test_the_intake_tuple_is_one_object_not_three_copies(self):
        """Three independent copies of this tuple are how the cashier got locked
        out of the only path that moves money. They must stay one object."""
        self.assertIs(
            charge_receipt.INTAKE_GROUPS, charge_line.OPERATIONAL_INTAKE_GROUPS
        )
        self.assertIs(
            pharmacy_billing.RECEIPT_GROUPS, charge_line.OPERATIONAL_INTAKE_GROUPS
        )
        self.assertIn(G_CASHIER, charge_line.OPERATIONAL_INTAKE_GROUPS)
        self.assertNotIn(G_RECEPTIONIST, charge_line.OPERATIONAL_INTAKE_GROUPS)
        self.assertIn(G_CASHIER, charge_line.OPERATIONAL_MONEY_READ)
        self.assertIn(G_RECEPTIONIST, charge_line.OPERATIONAL_MONEY_READ)
