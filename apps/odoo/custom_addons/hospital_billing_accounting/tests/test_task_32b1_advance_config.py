from odoo.exceptions import AccessError
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestTask32B1AdvanceConfiguration(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.config = cls.env["hospital.billing.accounting.config"].search(
            [("company_id", "=", cls.env.company.id), ("source_type", "=", "consultation")],
            limit=1,
        )
        cls.receptionist = cls.env["res.users"].create(
            {
                "name": "32B-1 Config Security Test",
                "login": "task32b1_config_security",
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
        cls.accountant = cls.env["res.users"].create(
            {
                "name": "32B-1 Accountant Test",
                "login": "task32b1_accountant",
                "company_id": cls.env.company.id,
                "company_ids": [(6, 0, cls.env.company.ids)],
                "groups_id": [
                    (6, 0, [
                        cls.env.ref("base.group_user").id,
                        cls.env.ref(
                            "hospital_management.group_hospital_accountant"
                        ).id,
                    ])
                ],
            }
        )
        cls.manager = cls.env["res.users"].create(
            {
                "name": "32B-1 Manager Test",
                "login": "task32b1_manager",
                "company_id": cls.env.company.id,
                "company_ids": [(6, 0, cls.env.company.ids)],
                "groups_id": [
                    (6, 0, [
                        cls.env.ref("base.group_user").id,
                        cls.env.ref(
                            "hospital_management.group_hospital_manager"
                        ).id,
                    ])
                ],
            }
        )

    def test_explicit_advance_chart_and_journals(self):
        self.assertTrue(self.config)
        self.assertTrue(self.config.advance_configuration_complete)
        self.assertTrue(self.config._assert_advance_configuration())
        self.assertEqual(self.config.patient_advance_liability_account_id.code, "305410")
        self.assertEqual(self.config.patient_credit_liability_account_id.code, "305420")
        self.assertEqual(
            self.config.patient_advance_liability_account_id.account_type,
            "liability_current",
        )
        self.assertTrue(self.config.patient_advance_liability_account_id.reconcile)
        self.assertTrue(self.config.patient_credit_liability_account_id.reconcile)
        self.assertEqual(self.config.advance_receipt_journal_id.code, "PADV")
        self.assertEqual(self.config.advance_application_journal_id.code, "PADV")
        self.assertEqual(self.config.advance_refund_journal_id.code, "PREF")

    def test_receptionist_cannot_change_mapping(self):
        with self.assertRaises(AccessError):
            self.config.with_user(self.receptionist).write({"notes": "forged RPC write"})

    def test_accounting_roles_have_intended_mapping_permissions(self):
        self.assertTrue(
            self.config.with_user(self.accountant).write({"notes": "accountant review"})
        )
        self.assertTrue(
            self.config.with_user(self.manager).write({"notes": "manager review"})
        )
        with self.assertRaises(AccessError):
            self.config.with_user(self.accountant).unlink()
        with self.assertRaises(AccessError):
            self.config.with_user(self.manager).unlink()

    def test_validation_creates_no_accounting_transaction(self):
        Move = self.env["account.move"]
        Payment = self.env["account.payment"]
        before = (Move.search_count([]), Payment.search_count([]))
        self.config.action_validate_advance_configuration()
        self.assertEqual(before, (Move.search_count([]), Payment.search_count([])))
