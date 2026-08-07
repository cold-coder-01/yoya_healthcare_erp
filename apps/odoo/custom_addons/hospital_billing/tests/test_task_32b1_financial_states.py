from odoo.exceptions import AccessError, UserError
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestTask32B1FinancialStates(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.account = cls.env["hospital.billing.account"].search(
            [("name", "=", "ACC00001")], limit=1
        )
        cls.receptionist = cls.env["res.users"].create(
            {
                "name": "32B-1 Receptionist Test",
                "login": "task32b1_receptionist",
                "company_id": cls.env.company.id,
                "company_ids": [(6, 0, cls.env.company.ids)],
                "groups_id": [
                    (6, 0, [
                        cls.env.ref("base.group_user").id,
                        cls.env.ref(
                            "hospital_management.group_hospital_receptionist"
                        ).id,
                    ])
                ],
            }
        )

    def test_almaz_funding_is_not_accounting_settlement(self):
        if not self.account:
            self.skipTest("ACC00001 is specific to the manual-UAT regression clone")
        self.assertEqual(self.account.amount_received, 1200.0)
        self.assertEqual(self.account.amount_prepayment_held, 1200.0)
        self.assertEqual(self.account.operational_funding_state, "funded")
        self.assertEqual(self.account.accounting_receipt_state, "unposted")
        self.assertEqual(self.account.invoice_state, "not_invoiced")
        self.assertEqual(self.account.settlement_state, "not_applicable")
        self.assertEqual(self.account.fiscal_state, "not_started")
        self.assertEqual(len(self.account.charge_line_ids), 3)
        for charge in self.account.charge_line_ids:
            self.assertEqual(charge.payment_state, "paid")
            self.assertEqual(charge.operational_funding_state, "funded")
            self.assertEqual(charge.accounting_receipt_state, "unposted")
            self.assertEqual(charge.invoice_state, "not_invoiced")
            self.assertEqual(charge.settlement_state, "not_applicable")
            self.assertEqual(charge.fiscal_state, "not_started")

    def test_derived_states_cannot_be_forged(self):
        if not self.account:
            self.skipTest("ACC00001 is specific to the manual-UAT regression clone")
        charge = self.account.charge_line_ids[:1].with_user(self.receptionist)
        with self.assertRaises(AccessError):
            charge.write({"payment_state": "unpaid"})
        with self.assertRaises(AccessError):
            charge.write({"operational_funding_state": "not_funded"})
        with self.assertRaises(AccessError):
            charge.write({"accounting_receipt_state": "posted"})
        with self.assertRaises(AccessError):
            charge.write({"settlement_state": "settled"})
        with self.assertRaises(AccessError):
            charge.write({"fiscal_state": "completed"})
        account = self.account.with_user(self.receptionist)
        with self.assertRaises(AccessError):
            account.write({"operational_funding_state": "not_funded"})
        with self.assertRaises(AccessError):
            account.write({"accounting_receipt_state": "posted"})
        with self.assertRaises(AccessError):
            account.write({"invoice_state": "invoiced"})
        with self.assertRaises(AccessError):
            account.write({"settlement_state": "settled"})
        with self.assertRaises(AccessError):
            account.write({"fiscal_state": "completed"})
        receipt = self.account.receipt_ids[:1].with_user(self.receptionist)
        with self.assertRaises(AccessError):
            receipt.write({"accounting_posted": True})

    def test_operational_funding_cannot_settle_account(self):
        if not self.account:
            self.skipTest("ACC00001 is specific to the manual-UAT regression clone")
        with self.assertRaises(UserError):
            self.account.action_settle()
