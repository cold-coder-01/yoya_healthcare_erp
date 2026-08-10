import uuid

from odoo import fields
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install", "task32b3")
class TestTask32B3PaymentAccounting(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.currency = cls.company.currency_id
        cls.cashier = cls._make_user("task32b3_cashier", "hospital_billing.group_hospital_cashier")
        cls.accountant = cls._make_user("task32b3_accountant", "hospital_management.group_hospital_accountant")
        cls.manager = cls._make_user("task32b3_manager", "hospital_management.group_hospital_manager")
        cls.admin = cls._make_user("task32b3_admin", "hospital_management.group_hospital_system_administrator")
        cls.receptionist = cls._make_user("task32b3_receptionist", "hospital_management.group_hospital_receptionist")
        cls.nurse = cls._make_user("task32b3_nurse", "hospital_management.group_hospital_nurse")
        cls.config = cls.env["hospital.billing.accounting.config"].sudo().search(
            [("company_id", "=", cls.company.id), ("source_type", "=", "consultation"), ("active", "=", True)], limit=1
        )
        cls.service = cls.env["hospital.billing.service"].sudo().search(
            [("company_id", "in", [False, cls.company.id]), ("service_type", "=", "consultation"), ("invoice_product_id", "!=", False)], limit=1
        )
        if not cls.config or not cls.service or not cls.service.invoice_tax_ids:
            raise AssertionError("Task 32B invoice mappings are required for 32B-3 tests.")
        cls.receivable = cls.config.receivable_account_id
        cls.cash_account = cls._account("T32B3CASH", "Task 32B3 Cash", "asset_cash")
        cls.bank_account = cls._account("T32B3BANK", "Task 32B3 Bank", "asset_cash")
        cls.bank_clearing_account = cls._account("T32B3CLR", "Task 32B3 Bank Clearing", "asset_current", reconcile=True)
        cls.mobile_account = cls._account("T32B3MOB", "Task 32B3 Mobile", "asset_cash")
        cls.advance_account = cls._account("T32B3ADV", "Task 32B3 Patient Advance", "liability_current", reconcile=True)
        cls.credit_account = cls._account("T32B3CRD", "Task 32B3 Patient Credit", "liability_current", reconcile=True)
        cls.receipt_journal = cls._journal("T32B3R", "Task 32B3 Receipt Journal", cls.cash_account)
        cls.application_journal = cls._journal("T32B3A", "Task 32B3 Application Journal", cls.advance_account)
        cls.bank_statement_journal = cls.env["account.journal"].sudo().search([("code", "=", "T33B3B"), ("company_id", "=", cls.company.id), ("type", "=", "bank")], limit=1) or cls.env["account.journal"].sudo().create({"name": "Task 32B3 Bank Statement Journal", "code": "T33B3B", "type": "bank", "company_id": cls.company.id, "default_account_id": cls.bank_account.id})
        config_vals = {
            "cash_account_id": cls.cash_account.id,
            "bank_account_id": cls.bank_account.id,
            "mobile_money_account_id": cls.mobile_account.id,
            "patient_advance_liability_account_id": cls.advance_account.id,
            "patient_credit_liability_account_id": cls.credit_account.id,
            "advance_receipt_journal_id": cls.receipt_journal.id,
            "advance_application_journal_id": cls.application_journal.id,
            "advance_refund_journal_id": cls.application_journal.id,
        }
        if "bank_statement_journal_id" in cls.config._fields:
            config_vals["bank_statement_journal_id"] = cls.bank_statement_journal.id
        if "bank_receipt_clearing_account_id" in cls.config._fields:
            config_vals["bank_receipt_clearing_account_id"] = cls.bank_clearing_account.id
        cls.config.sudo().write(config_vals)
    @classmethod
    def _make_user(cls, login, hospital_group):
        return cls.env["res.users"].sudo().create(
            {
                "name": login.replace("_", " ").title(),
                "login": login,
                "company_id": cls.company.id,
                "company_ids": [(6, 0, cls.company.ids)],
                "groups_id": [(6, 0, [cls.env.ref("base.group_user").id, cls.env.ref(hospital_group).id])],
            }
        )

    @classmethod
    def _account(cls, code, name, account_type, reconcile=False):
        existing = cls.env["account.account"].sudo().search([("code", "=", code)], limit=1)
        if existing:
            existing.write({"reconcile": reconcile or existing.reconcile})
            return existing
        return cls.env["account.account"].sudo().create(
            {"code": code, "name": name, "account_type": account_type, "reconcile": reconcile, "company_ids": [(6, 0, cls.company.ids)]}
        )

    @classmethod
    def _journal(cls, code, name, default_account):
        existing = cls.env["account.journal"].sudo().search([("code", "=", code), ("company_id", "=", cls.company.id)], limit=1)
        if existing:
            return existing
        return cls.env["account.journal"].sudo().create(
            {"name": name, "code": code, "type": "general", "company_id": cls.company.id, "default_account_id": default_account.id}
        )

    def _make_case(self, price=1000.0):
        suffix = uuid.uuid4().hex[:8]
        partner = self.env["res.partner"].sudo().create(
            {"name": "32B3 Patient Partner %s" % suffix, "property_account_receivable_id": self.receivable.id}
        )
        patient = self.env["hospital.patient"].sudo().create({"name": "32B3 Patient %s" % suffix, "accounting_partner_id": partner.id})
        appointment = self.env["hospital.appointment"].sudo().create({"patient_id": patient.id, "appointment_date": fields.Datetime.now()})
        encounter = self.env["hospital.encounter"].sudo().create(
            {"patient_id": patient.id, "appointment_id": appointment.id, "encounter_type": "outpatient", "state": "active", "company_id": self.company.id}
        )
        account = self.env["hospital.billing.account"].sudo().create({"encounter_id": encounter.id, "payer_type": "self_pay"})
        charge = self.env["hospital.charge.line"].sudo().create(
            {
                "billing_account_id": account.id,
                "service_id": self.service.id,
                "description": "32B3 Consultation",
                "uom_id": (self.service.uom_id.id or self.service.invoice_product_id.uom_id.id),
                "billing_basis": "delivery",
                "qty_requested": 1.0,
                "qty_delivered": 1.0,
                "delivery_state": "delivered",
                "unit_price": price,
                "discount": 0.0,
                "tax_treatment": self.service.tax_treatment,
                "tax_rate": self.service.tax_rate,
                "charge_state": "active",
                "authorization_state": "not_required",
                "source_model": "hospital.appointment",
                "source_res_id": appointment.id,
                "source_event": "task32b3_test",
                "source_key": "task32b3:%s" % suffix,
            }
        )
        return account, charge

    def _make_case_without_accounting_partner(self, price=300.0):
        suffix = uuid.uuid4().hex[:8]
        patient = self.env["hospital.patient"].sudo().create({"name": "32B3 Partnerless Patient %s" % suffix})
        appointment = self.env["hospital.appointment"].sudo().create({"patient_id": patient.id, "appointment_date": fields.Datetime.now()})
        encounter = self.env["hospital.encounter"].sudo().create(
            {"patient_id": patient.id, "appointment_id": appointment.id, "encounter_type": "outpatient", "state": "active", "company_id": self.company.id}
        )
        account = self.env["hospital.billing.account"].sudo().create({"encounter_id": encounter.id, "payer_type": "self_pay"})
        charge = self.env["hospital.charge.line"].sudo().create(
            {
                "billing_account_id": account.id,
                "service_id": self.service.id,
                "description": "32B3 Partnerless Consultation",
                "uom_id": (self.service.uom_id.id or self.service.invoice_product_id.uom_id.id),
                "billing_basis": "prepaid",
                "qty_requested": 1.0,
                "qty_delivered": 0.0,
                "delivery_state": "pending",
                "unit_price": price,
                "discount": 0.0,
                "tax_treatment": self.service.tax_treatment,
                "tax_rate": self.service.tax_rate,
                "charge_state": "active",
                "authorization_state": "not_required",
                "source_model": "hospital.appointment",
                "source_res_id": appointment.id,
                "source_event": "task32b3_partnerless_test",
                "source_key": "task32b3:partnerless:%s" % suffix,
            }
        )
        self.env.flush_all()
        self.env.cr.execute("UPDATE hospital_patient SET accounting_partner_id = NULL WHERE id = %s", [patient.id])
        self.env.invalidate_all()
        return account, charge

    def _make_prepaid_case_with_accounting_partner(self, price=300.0):
        suffix = uuid.uuid4().hex[:8]
        patient = self.env["hospital.patient"].sudo().create({"name": "32B3 Prepaid Partner Patient %s" % suffix})
        appointment = self.env["hospital.appointment"].sudo().create({"patient_id": patient.id, "appointment_date": fields.Datetime.now()})
        encounter = self.env["hospital.encounter"].sudo().create(
            {"patient_id": patient.id, "appointment_id": appointment.id, "encounter_type": "outpatient", "state": "active", "company_id": self.company.id}
        )
        account = self.env["hospital.billing.account"].sudo().create({"encounter_id": encounter.id, "payer_type": "self_pay"})
        charge = self.env["hospital.charge.line"].sudo().create(
            {
                "billing_account_id": account.id,
                "service_id": self.service.id,
                "description": "32B3 Prepaid Consultation",
                "uom_id": (self.service.uom_id.id or self.service.invoice_product_id.uom_id.id),
                "billing_basis": "prepaid",
                "qty_requested": 1.0,
                "qty_delivered": 0.0,
                "delivery_state": "pending",
                "unit_price": price,
                "discount": 0.0,
                "tax_treatment": self.service.tax_treatment,
                "tax_rate": self.service.tax_rate,
                "charge_state": "active",
                "authorization_state": "not_required",
                "source_model": "hospital.appointment",
                "source_res_id": appointment.id,
                "source_event": "task32b3_prepaid_test",
                "source_key": "task32b3:prepaid:%s" % suffix,
            }
        )
        return account, charge

    def _confirmed_operational_receipt(self, account, charge, amount, method="cash", reference=None, received_by=None):
        receipt = self.env["hospital.charge.receipt"].sudo().create(
            {
                "payment_method": method,
                "payment_reference": reference,
                "received_at": fields.Datetime.now(),
                "received_by_id": (received_by or self.cashier).id,
                "state": "draft",
                "intake_token": uuid.uuid4().hex,
            }
        )
        self.env["hospital.charge.receipt.allocation"].sudo().create({"receipt_id": receipt.id, "charge_line_id": charge.id, "amount": amount})
        receipt.sudo().write({"state": "confirmed"})
        return receipt

    def _receipt(self, account, charge, amount, method="cash", reference=None):
        receipt = self._confirmed_operational_receipt(account, charge, amount, method=method, reference=reference, received_by=self.accountant)
        move = receipt.with_user(self.accountant).action_post_receipt_accounting()
        return receipt, move

    def _invoice(self, account):
        invoice = self.env["hospital.billing.engine"].with_user(self.accountant).create_invoice(account, request_token=uuid.uuid4().hex)
        invoice.with_user(self.accountant).action_post()
        return invoice

    def _make_direct_claim(self, claim_amount=1000.0, approved_amount=1000.0, coverage_percent=100.0):
        suffix = uuid.uuid4().hex[:8]
        patient = self.env["hospital.patient"].sudo().create({"name": "32B3 Insured %s" % suffix})
        provider_partner = self.env["res.partner"].sudo().create({"name": "32B3 Insurer Partner %s" % suffix})
        provider = self.env["hospital.insurance.provider"].sudo().create(
            {"name": "32B3 Insurer %s" % suffix, "payer_type": "insurance", "partner_id": provider_partner.id}
        )
        payer_account = self._account("T32B3INS", "Task 32B3 Insurance Receivable", "asset_receivable", reconcile=True)
        self.env["hospital.insurance.accounting.config"].sudo().search(
            [("company_id", "=", self.company.id), ("provider_type", "=", "insurance")]
        ).unlink()
        ins_config = self.env["hospital.insurance.accounting.config"].sudo().create(
            {
                "name": "32B3 Insurance Config %s" % suffix,
                "provider_type": "insurance",
                "company_id": self.company.id,
                "patient_receivable_account_id": self.receivable.id,
                "insurance_receivable_account_id": payer_account.id,
                "journal_id": self.application_journal.id,
            }
        )
        bill = self.env["hospital.patient.bill"].sudo().create(
            {
                "patient_id": patient.id,
                "payer_type": "insurance",
                "insurance_provider_id": provider.id,
                "coverage_percent": coverage_percent,
                "state": "confirmed",
            }
        )
        bill_line = self.env["hospital.patient.bill.line"].sudo().create(
            {"bill_id": bill.id, "description": "Insured service", "source_type": "consultation", "quantity": 1.0, "unit_price": claim_amount}
        )
        if "accounting_state" in bill._fields:
            bill.sudo().write({"accounting_state": "posted"})
        claim = self.env["hospital.insurance.claim"].sudo().create(
            {
                "patient_id": patient.id,
                "bill_id": bill.id,
                "provider_id": provider.id,
                "approved_amount": approved_amount,
                "payer_responsibility_amount": claim_amount,
                "patient_responsibility_amount": max(0.0, claim_amount - coverage_percent * claim_amount / 100.0),
                "currency_id": self.currency.id,
                "state": "approved" if approved_amount >= claim_amount else "partially_approved",
                "line_ids": [
                    (
                        0,
                        0,
                        {
                            "bill_line_id": bill_line.id,
                            "description": "Insured service",
                            "subtotal": claim_amount,
                            "claimed_amount": claim_amount,
                            "coverage_percent": coverage_percent,
                            "approved_amount": approved_amount,
                        },
                    )
                ],
            }
        )
        claim.with_user(self.accountant).action_mark_ready_for_accounting()
        claim.with_user(self.accountant).action_reclassify_payer_receivable()
        claim.invalidate_recordset()
        return claim, bill, ins_config

    def test_receipt_posts_advance_and_is_idempotent(self):
        account, charge = self._make_case()
        receipt, move = self._receipt(account, charge, 400.0)
        self.assertEqual(move.state, "posted")
        self.assertEqual(receipt.accounting_move_id, move)
        self.assertEqual(sum(move.line_ids.mapped("debit")), 400.0)
        self.assertEqual(sum(move.line_ids.mapped("credit")), 400.0)
        self.assertEqual(move.line_ids.filtered(lambda line: line.account_id == self.cash_account).debit, 400.0)
        self.assertEqual(move.line_ids.filtered(lambda line: line.account_id == self.advance_account).credit, 400.0)
        self.assertEqual(receipt.with_user(self.accountant).action_post_receipt_accounting(), move)
        with self.assertRaises(AccessError):
            receipt.with_user(self.receptionist).write({"accounting_reference": "FORGED"})
        with self.assertRaises(UserError):
            receipt.sudo().write({"state": "cancelled"})

    def test_explicit_receipt_accounting_posting_authorization_boundary(self):
        account, charge = self._make_case(price=300.0)
        for user in (self.cashier, self.receptionist, self.nurse):
            receipt = self._confirmed_operational_receipt(account, charge, 10.0, received_by=self.cashier)
            with self.assertRaises(AccessError):
                receipt.with_user(user).action_post_receipt_accounting()
            self.assertFalse(receipt.accounting_posted)
            self.assertFalse(receipt.accounting_move_id)

        for user in (self.accountant, self.manager, self.admin):
            receipt = self._confirmed_operational_receipt(account, charge, 10.0, received_by=self.cashier)
            move = receipt.with_user(user).action_post_receipt_accounting()
            receipt.invalidate_recordset(["accounting_posted", "accounting_move_id", "accounting_posted_by_id"])
            self.assertEqual(move.state, "posted")
            self.assertTrue(receipt.accounting_posted)
            self.assertEqual(receipt.accounting_move_id, move)
            self.assertEqual(receipt.accounting_posted_by_id, user)

    def test_fresh_patient_accounting_partner_lifecycle_is_explicit(self):
        name = "32B3 Fresh Partner Lifecycle %s" % uuid.uuid4().hex[:6]
        partner_count_before = self.env["res.partner"].sudo().search_count([("name", "=", name)])
        patient = self.env["hospital.patient"].sudo().create({"name": name, "phone": "011-111", "email": "fresh@example.test", "address": "Fresh Street"})
        self.assertEqual(partner_count_before, 0)
        self.assertTrue(patient.accounting_partner_id)
        self.assertEqual(patient.accounting_partner_id.name, name)
        self.assertEqual(patient.accounting_partner_id.phone, "011-111")
        self.assertEqual(patient.accounting_partner_id.email, "fresh@example.test")
        self.assertEqual(patient.accounting_partner_id.street, "Fresh Street")
        self.assertEqual(patient.accounting_partner_id.customer_rank, 1)
        self.assertEqual(self.env["res.partner"].sudo().search_count([("name", "=", name)]), 1)
        existing_partner = patient.accounting_partner_id
        patient.sudo().action_create_link_accounting_customer()
        self.assertEqual(patient.accounting_partner_id, existing_partner)
        self.assertEqual(self.env["res.partner"].sudo().search_count([("name", "=", name)]), 1)
        replacement = self.env["res.partner"].sudo().create({"name": "32B3 Replacement %s" % uuid.uuid4().hex[:6]})
        with self.assertRaises(AccessError):
            patient.with_user(self.receptionist).write({"accounting_partner_id": replacement.id})
        with self.assertRaises(AccessError):
            patient.with_user(self.accountant).write({"accounting_partner_id": replacement.id})
        self.assertEqual(patient.accounting_partner_id, existing_partner)

    def test_existing_partnerless_patient_can_be_repaired_by_controlled_action(self):
        account, _charge = self._make_case_without_accounting_partner(price=300.0)
        patient = account.patient_id
        self.assertFalse(patient.accounting_partner_id)
        with self.assertRaises(AccessError):
            patient.with_user(self.receptionist).action_create_link_accounting_customer()
        patient.with_user(self.accountant).action_create_link_accounting_customer()
        patient.invalidate_recordset(["accounting_partner_id"])
        self.assertTrue(patient.accounting_partner_id)
        partner = patient.accounting_partner_id
        patient.with_user(self.accountant).action_create_link_accounting_customer()
        patient.invalidate_recordset(["accounting_partner_id"])
        self.assertEqual(patient.accounting_partner_id, partner)

    def test_partnerless_patient_operational_payment_survives_later_accounting_failure(self):
        account, charge = self._make_case_without_accounting_partner(price=300.0)
        self.assertFalse(account.patient_id.accounting_partner_id)
        self.assertAlmostEqual(charge.amount_due_for_clearance, 300.0, places=2)
        before_receipts = self.env["hospital.charge.receipt"].search_count([])
        before_allocations = self.env["hospital.charge.receipt.allocation"].search_count([])
        before_moves = self.env["account.move"].search_count([])
        before_apps = self.env["hospital.patient.advance.application"].search_count([])
        wizard = self.env["hospital.charge.payment.wizard"].with_user(self.cashier).create(
            {
                "source_charge_id": charge.id,
                "patient_id": account.patient_id.id,
                "encounter_id": account.encounter_id.id,
                "billing_account_id": account.id,
                "currency_id": account.currency_id.id,
                "payment_method": "cash",
                "received_at": fields.Datetime.now(),
                "line_ids": [(0, 0, {"charge_line_id": charge.id, "amount_due": 300.0, "amount_available": 300.0, "amount": 100.0})],
            }
        )
        wizard.action_confirm()
        receipt = wizard.receipt_id
        charge.invalidate_recordset(["amount_received", "amount_prepayment_held", "amount_due_for_clearance"])
        account.invalidate_recordset(["accounting_receipt_state", "operational_funding_state"])
        self.assertEqual(self.env["hospital.charge.receipt"].search_count([]), before_receipts + 1)
        self.assertEqual(self.env["hospital.charge.receipt.allocation"].search_count([]), before_allocations + 1)
        self.assertEqual(self.env["account.move"].search_count([]), before_moves)
        self.assertEqual(self.env["hospital.patient.advance.application"].search_count([]), before_apps)
        self.assertEqual(receipt.state, "confirmed")
        self.assertFalse(receipt.accounting_posted)
        self.assertFalse(receipt.accounting_move_id)
        self.assertEqual(account.accounting_receipt_state, "unposted")
        self.assertAlmostEqual(charge.amount_received, 100.0, places=2)
        self.assertAlmostEqual(charge.amount_prepayment_held, 100.0, places=2)
        self.assertAlmostEqual(charge.amount_due_for_clearance, 200.0, places=2)
        with self.assertRaises(UserError):
            receipt.with_user(self.accountant).action_post_receipt_accounting()
        self.assertEqual(self.env["account.move"].search_count([]), before_moves)
        self.assertEqual(receipt.state, "confirmed")
        self.assertFalse(receipt.accounting_move_id)

    def test_100_etb_payment_records_operationally_then_explicitly_posts_advance(self):
        account, charge = self._make_prepaid_case_with_accounting_partner(price=300.0)
        self.assertTrue(account.patient_id.accounting_partner_id)
        self.assertAlmostEqual(charge.amount_due_for_clearance, 300.0, places=2)
        before_moves = self.env["account.move"].search_count([])
        wizard = self.env["hospital.charge.payment.wizard"].with_user(self.cashier).create(
            {
                "source_charge_id": charge.id,
                "patient_id": account.patient_id.id,
                "encounter_id": account.encounter_id.id,
                "billing_account_id": account.id,
                "currency_id": account.currency_id.id,
                "payment_method": "cash",
                "received_at": fields.Datetime.now(),
                "line_ids": [(0, 0, {"charge_line_id": charge.id, "amount_due": 300.0, "amount_available": 300.0, "amount": 100.0})],
            }
        )
        wizard.action_confirm()
        receipt = wizard.receipt_id
        charge.invalidate_recordset(["amount_received", "amount_prepayment_held", "amount_due_for_clearance"])
        account.invalidate_recordset(["accounting_receipt_state", "operational_funding_state"])
        self.assertTrue(receipt)
        self.assertEqual(receipt.state, "confirmed")
        self.assertFalse(receipt.accounting_posted)
        self.assertFalse(receipt.accounting_move_id)
        self.assertEqual(self.env["account.move"].search_count([]), before_moves)
        self.assertEqual(account.accounting_receipt_state, "unposted")
        self.assertAlmostEqual(charge.amount_received, 100.0, places=2)
        self.assertAlmostEqual(charge.amount_prepayment_held, 100.0, places=2)
        self.assertAlmostEqual(charge.amount_due_for_clearance, 200.0, places=2)

        move = receipt.with_user(self.accountant).action_post_receipt_accounting()
        receipt.invalidate_recordset(["accounting_posted", "accounting_move_id", "accounting_reference", "accounting_posted_at", "accounting_posted_by_id"])
        account.invalidate_recordset(["accounting_receipt_state"])
        self.assertEqual(move.state, "posted")
        self.assertEqual(receipt.accounting_move_id, move)
        self.assertTrue(receipt.accounting_posted)
        self.assertEqual(receipt.accounting_reference, move.name)
        self.assertTrue(receipt.accounting_posted_at)
        self.assertEqual(receipt.accounting_posted_by_id, self.accountant)
        self.assertEqual(account.accounting_receipt_state, "posted")
        self.assertEqual(move.line_ids.filtered(lambda line: line.account_id == self.cash_account).debit, 100.0)
        self.assertEqual(move.line_ids.filtered(lambda line: line.account_id == self.advance_account).credit, 100.0)

    def test_partial_multiple_excess_application_and_retry(self):
        account, charge = self._make_case(price=1000.0)
        self._receipt(account, charge, 400.0)
        self._receipt(account, charge, 900.0)
        invoice = self._invoice(account)
        fiscal_before = self.env["hospital.fiscal.transaction"].search_count([])
        stock_before = self.env["stock.move"].search_count([])
        app1 = self.env["hospital.billing.engine"].with_user(self.accountant).apply_patient_advance_to_invoice(invoice, amount=400.0, request_token="APP-%s" % uuid.uuid4().hex)
        invoice.invalidate_recordset(["amount_residual"])
        self.assertAlmostEqual(invoice.amount_residual, 600.0, places=2)
        token = "APP-%s" % uuid.uuid4().hex
        app2 = self.env["hospital.billing.engine"].with_user(self.accountant).apply_patient_advance_to_invoice(invoice, request_token=token)
        self.assertEqual(app2, self.env["hospital.billing.engine"].with_user(self.accountant).apply_patient_advance_to_invoice(invoice, request_token=token))
        invoice.invalidate_recordset(["amount_residual"])
        self.assertAlmostEqual(invoice.amount_residual, 0.0, places=2)
        self.assertTrue(invoice.line_ids.filtered(lambda line: line.account_id.account_type == "asset_receivable").mapped("reconciled"))
        liability_residual = sum(
            line.amount_residual for line in (app1.move_id + app2.move_id).line_ids.filtered(lambda line: line.account_id == self.advance_account)
        )
        receipt_residual = sum(
            line.amount_residual for line in account.receipt_ids.mapped("accounting_move_id.line_ids").filtered(lambda line: line.account_id == self.advance_account)
        )
        self.assertAlmostEqual(receipt_residual + liability_residual, -300.0, places=2)
        self.assertEqual(self.env["account.payment"].search_count([]), 0)
        self.assertEqual(self.env["hospital.fiscal.transaction"].search_count([]), fiscal_before)
        self.assertEqual(self.env["stock.move"].search_count([]), stock_before)

    def test_billing_account_apply_advances_two_receipts_fully_settles_invoice(self):
        account, charge = self._make_case(price=2500.0)
        self._receipt(account, charge, 1000.0)
        self._receipt(account, charge, 1500.0)
        invoice = self._invoice(account)
        before_receipts = self.env["hospital.charge.receipt"].search_count([])
        before_payments = self.env["account.payment"].search_count([])
        before_invoices = self.env["account.move"].search_count([("move_type", "=", "out_invoice")])
        before_batches = self.env["hospital.invoice.batch"].search_count([])
        result = account.with_user(self.accountant).action_apply_patient_advances()
        self.assertEqual(result["res_model"], "hospital.patient.advance.application")
        invoice.invalidate_recordset(["amount_residual", "payment_state"])
        charge.invalidate_recordset(["amount_prepayment_held", "amount_applied_to_invoice", "receivable_balance", "settlement_state"])
        account.invalidate_recordset(["amount_prepayment_held", "amount_applied_to_invoice", "amount_outstanding", "settlement_state"])
        self.assertEqual(invoice.payment_state, "paid")
        self.assertAlmostEqual(invoice.amount_residual, 0.0, places=2)
        self.assertAlmostEqual(charge.amount_prepayment_held, 0.0, places=2)
        self.assertAlmostEqual(charge.amount_applied_to_invoice, 2500.0, places=2)
        self.assertAlmostEqual(account.amount_prepayment_held, 0.0, places=2)
        self.assertAlmostEqual(account.amount_applied_to_invoice, 2500.0, places=2)
        self.assertAlmostEqual(account.amount_outstanding, 0.0, places=2)
        self.assertEqual(charge.settlement_state, "settled")
        self.assertEqual(account.settlement_state, "settled")
        self.assertEqual(self.env["hospital.charge.receipt"].search_count([]), before_receipts)
        self.assertEqual(self.env["account.payment"].search_count([]), before_payments)
        self.assertEqual(self.env["account.move"].search_count([("move_type", "=", "out_invoice")]), before_invoices)
        self.assertEqual(self.env["hospital.invoice.batch"].search_count([]), before_batches)
        application = self.env["hospital.patient.advance.application"].search([("invoice_id", "=", invoice.id)], limit=1)
        self.assertEqual(application.amount, 2500.0)
        self.assertEqual(application.move_id.state, "posted")
        self.assertEqual(application.move_id.line_ids.filtered(lambda line: line.account_id == self.advance_account).debit, 2500.0)
        self.assertEqual(application.move_id.line_ids.filtered(lambda line: line.account_id == self.receivable).credit, 2500.0)
        before_apps = self.env["hospital.patient.advance.application"].search_count([])
        with self.assertRaisesRegex(UserError, "No posted (patient invoice has an open receivable|unapplied patient advance is available)"):
            account.with_user(self.accountant).action_apply_patient_advances()
        self.assertEqual(self.env["hospital.patient.advance.application"].search_count([]), before_apps)

    def test_billing_account_apply_advances_partial_and_excess(self):
        partial_account, partial_charge = self._make_case(price=1000.0)
        self._receipt(partial_account, partial_charge, 400.0)
        partial_invoice = self._invoice(partial_account)
        partial_account.with_user(self.accountant).action_apply_patient_advances()
        partial_invoice.invalidate_recordset(["amount_residual", "payment_state"])
        partial_charge.invalidate_recordset(["amount_prepayment_held", "amount_applied_to_invoice", "settlement_state"])
        partial_account.invalidate_recordset(["amount_outstanding", "settlement_state"])
        self.assertAlmostEqual(partial_invoice.amount_residual, 600.0, places=2)
        self.assertEqual(partial_invoice.payment_state, "partial")
        self.assertAlmostEqual(partial_charge.amount_applied_to_invoice, 400.0, places=2)
        self.assertEqual(partial_charge.settlement_state, "partially_settled")
        self.assertEqual(partial_account.settlement_state, "partially_settled")

        excess_account, excess_charge = self._make_case(price=1000.0)
        self._receipt(excess_account, excess_charge, 1300.0)
        excess_invoice = self._invoice(excess_account)
        excess_account.with_user(self.accountant).action_apply_patient_advances()
        excess_invoice.invalidate_recordset(["amount_residual", "payment_state"])
        excess_charge.invalidate_recordset(["amount_prepayment_held", "amount_applied_to_invoice", "settlement_state"])
        excess_account.invalidate_recordset(["amount_prepayment_held", "settlement_state"])
        self.assertAlmostEqual(excess_invoice.amount_residual, 0.0, places=2)
        self.assertEqual(excess_invoice.payment_state, "paid")
        self.assertAlmostEqual(excess_charge.amount_applied_to_invoice, 1000.0, places=2)
        self.assertAlmostEqual(excess_charge.amount_prepayment_held, 300.0, places=2)
        self.assertAlmostEqual(excess_account.amount_prepayment_held, 300.0, places=2)
        self.assertEqual(excess_account.settlement_state, "settled")

    def test_billing_account_apply_one_advance_across_multiple_invoices_in_order(self):
        account, first_charge = self._make_case(price=700.0)
        suffix = uuid.uuid4().hex[:8]
        second_charge = self.env["hospital.charge.line"].sudo().create(
            {
                "billing_account_id": account.id,
                "service_id": self.service.id,
                "description": "32B3 Follow-up Consultation",
                "uom_id": (self.service.uom_id.id or self.service.invoice_product_id.uom_id.id),
                "billing_basis": "delivery",
                "qty_requested": 1.0,
                "qty_delivered": 0.0,
                "delivery_state": "pending",
                "unit_price": 300.0,
                "discount": 0.0,
                "tax_treatment": self.service.tax_treatment,
                "tax_rate": self.service.tax_rate,
                "charge_state": "active",
                "authorization_state": "not_required",
                "source_model": "hospital.appointment",
                "source_res_id": account.encounter_id.appointment_id.id,
                "source_event": "task32b3_test_followup",
                "source_key": "task32b3:followup:%s" % suffix,
            }
        )
        receipt = self.env["hospital.charge.receipt"].sudo().create(
            {"payment_method": "cash", "received_at": fields.Datetime.now(), "received_by_id": self.accountant.id, "state": "draft", "intake_token": uuid.uuid4().hex}
        )
        self.env["hospital.charge.receipt.allocation"].sudo().create({"receipt_id": receipt.id, "charge_line_id": first_charge.id, "amount": 700.0})
        self.env["hospital.charge.receipt.allocation"].sudo().create({"receipt_id": receipt.id, "charge_line_id": second_charge.id, "amount": 300.0})
        receipt.sudo().write({"state": "confirmed"})
        receipt.with_user(self.accountant).action_post_receipt_accounting()
        first_invoice = self._invoice(account)
        second_charge.sudo().write({"qty_delivered": 1.0, "delivery_state": "delivered"})
        second_invoice = self._invoice(account)
        self.assertLess(first_invoice.id, second_invoice.id)
        account.with_user(self.accountant).action_apply_patient_advances()
        first_invoice.invalidate_recordset(["amount_residual", "payment_state"])
        second_invoice.invalidate_recordset(["amount_residual", "payment_state"])
        first_charge.invalidate_recordset(["amount_applied_to_invoice", "settlement_state"])
        second_charge.invalidate_recordset(["amount_applied_to_invoice", "settlement_state"])
        self.assertAlmostEqual(first_invoice.amount_residual, 0.0, places=2)
        self.assertAlmostEqual(second_invoice.amount_residual, 0.0, places=2)
        self.assertEqual(first_invoice.payment_state, "paid")
        self.assertEqual(second_invoice.payment_state, "paid")
        self.assertAlmostEqual(first_charge.amount_applied_to_invoice, 700.0, places=2)
        self.assertAlmostEqual(second_charge.amount_applied_to_invoice, 300.0, places=2)
        apps = self.env["hospital.patient.advance.application"].search([("billing_account_id", "=", account.id)], order="id")
        self.assertEqual(apps.mapped("amount"), [700.0, 300.0])
    def test_cross_patient_and_duplicate_external_reference_block(self):
        account, charge = self._make_case(price=500.0)
        other_account, other_charge = self._make_case(price=500.0)
        self._receipt(account, charge, 500.0, method="bank_transfer", reference="BANK-%s" % uuid.uuid4().hex)
        invoice = self._invoice(other_account)
        with self.assertRaises(UserError):
            self.env["hospital.billing.engine"].with_user(self.accountant).apply_patient_advance_to_invoice(invoice)
        reference = "DUP-%s" % uuid.uuid4().hex
        self._receipt(account, charge, 10.0, method="mobile_money", reference=reference)
        with self.assertRaises(ValidationError):
            self._receipt(other_account, other_charge, 10.0, method="mobile_money", reference=reference)

    def test_cross_company_patient_advance_application_is_blocked(self):
        account, charge = self._make_case(price=500.0)
        self._receipt(account, charge, 500.0)
        invoice = self._invoice(account)
        other_company = self.env["res.company"].sudo().create({"name": "32B3 Other Company %s" % uuid.uuid4().hex[:6]})
        before_moves = self.env["account.move"].search_count([])
        before_apps = self.env["hospital.patient.advance.application"].search_count([])
        self.env.cr.execute("UPDATE account_move SET company_id = %s WHERE id = %s", [other_company.id, invoice.id])
        invoice.invalidate_recordset(["company_id"])
        with self.assertRaises(UserError):
            self.env["hospital.billing.engine"].with_user(self.accountant).apply_patient_advance_to_invoice(invoice, request_token="XCOMP-%s" % uuid.uuid4().hex)
        self.assertEqual(before_moves, self.env["account.move"].search_count([]))
        self.assertEqual(before_apps, self.env["hospital.patient.advance.application"].search_count([]))

    def test_incompatible_currency_patient_advance_application_is_blocked(self):
        account, charge = self._make_case(price=500.0)
        self._receipt(account, charge, 500.0)
        invoice = self._invoice(account)
        usd = self.env.ref("base.USD")
        if usd == invoice.currency_id:
            usd = self.env["res.currency"].sudo().search([("id", "!=", invoice.currency_id.id), ("active", "=", True)], limit=1)
        before_moves = self.env["account.move"].search_count([])
        before_apps = self.env["hospital.patient.advance.application"].search_count([])
        self.env.cr.execute("UPDATE account_move SET currency_id = %s WHERE id = %s", [usd.id, invoice.id])
        invoice.invalidate_recordset(["currency_id"])
        with self.assertRaises(UserError):
            self.env["hospital.billing.engine"].with_user(self.accountant).apply_patient_advance_to_invoice(invoice, request_token="XCUR-%s" % uuid.uuid4().hex)
        self.assertEqual(before_moves, self.env["account.move"].search_count([]))
        self.assertEqual(before_apps, self.env["hospital.patient.advance.application"].search_count([]))

    def test_failed_receipt_posting_and_application_leave_no_partial_artifacts(self):
        account, charge = self._make_case(price=500.0)
        receipt = self.env["hospital.charge.receipt"].sudo().create(
            {"payment_method": "cash", "received_at": fields.Datetime.now(), "received_by_id": self.accountant.id, "state": "draft", "intake_token": uuid.uuid4().hex}
        )
        self.env["hospital.charge.receipt.allocation"].sudo().create({"receipt_id": receipt.id, "charge_line_id": charge.id, "amount": 500.0})
        receipt.sudo().write({"state": "confirmed"})
        before_moves = self.env["account.move"].search_count([])
        self.config.sudo().write({"cash_account_id": False})
        with self.assertRaises(UserError):
            receipt.with_user(self.accountant).action_post_receipt_accounting()
        self.assertEqual(before_moves, self.env["account.move"].search_count([]))
        self.assertFalse(receipt.accounting_move_id)
        self.config.sudo().write({"cash_account_id": self.cash_account.id})

        self._receipt(account, charge, 500.0)
        invoice = self._invoice(account)
        before_moves = self.env["account.move"].search_count([])
        before_apps = self.env["hospital.patient.advance.application"].search_count([])
        self.config.sudo().write({"advance_application_journal_id": False})
        with self.assertRaises(UserError):
            self.env["hospital.billing.engine"].with_user(self.accountant).apply_patient_advance_to_invoice(invoice, request_token="FAILAPP-%s" % uuid.uuid4().hex)
        self.assertEqual(before_moves, self.env["account.move"].search_count([]))
        self.assertEqual(before_apps, self.env["hospital.patient.advance.application"].search_count([]))
        self.config.sudo().write({"advance_application_journal_id": self.application_journal.id})

    def test_unauthorized_users_cannot_forge_advance_application_crud(self):
        account, charge = self._make_case(price=500.0)
        self._receipt(account, charge, 500.0)
        invoice = self._invoice(account)
        application = self.env["hospital.billing.engine"].with_user(self.accountant).apply_patient_advance_to_invoice(
            invoice, request_token="AUTHAPP-%s" % uuid.uuid4().hex
        )
        with self.assertRaises(AccessError):
            self.env["hospital.patient.advance.application"].with_user(self.receptionist).create(
                {
                    "request_token": "FORGED-%s" % uuid.uuid4().hex,
                    "billing_account_id": account.id,
                    "invoice_id": invoice.id,
                    "patient_id": account.patient_id.id,
                    "partner_id": invoice.partner_id.id,
                    "company_id": self.company.id,
                    "currency_id": self.currency.id,
                    "amount": 1.0,
                }
            )
        with self.assertRaises(AccessError):
            application.with_user(self.receptionist).write({"state": "posted"})
        with self.assertRaises(UserError):
            application.with_user(self.receptionist).unlink()
        with self.assertRaises(UserError):
            application.sudo().unlink()

    def test_insurance_direct_payment_reconciles_and_overpayment_is_insurer_credit(self):
        patient = self.env["hospital.patient"].sudo().create({"name": "32B3 Insured %s" % uuid.uuid4().hex[:6]})
        provider_partner = self.env["res.partner"].sudo().create({"name": "32B3 Insurer Partner"})
        provider = self.env["hospital.insurance.provider"].sudo().create({"name": "32B3 Insurer", "payer_type": "insurance", "partner_id": provider_partner.id})
        payer_account = self._account("T32B3INS", "Task 32B3 Insurance Receivable", "asset_receivable", reconcile=True)
        self.env["hospital.insurance.accounting.config"].sudo().search([("company_id", "=", self.company.id), ("provider_type", "=", "insurance")]).unlink()
        ins_config = self.env["hospital.insurance.accounting.config"].sudo().create(
            {"name": "32B3 Insurance Config", "provider_type": "insurance", "company_id": self.company.id, "patient_receivable_account_id": self.receivable.id, "insurance_receivable_account_id": payer_account.id, "journal_id": self.application_journal.id}
        )
        bill = self.env["hospital.patient.bill"].sudo().create({"patient_id": patient.id, "payer_type": "insurance", "insurance_provider_id": provider.id, "coverage_percent": 100.0, "state": "confirmed"})
        self.env["hospital.patient.bill.line"].sudo().create({"bill_id": bill.id, "description": "Insured service", "source_type": "consultation", "quantity": 1.0, "unit_price": 1000.0})
        if "accounting_state" in bill._fields:
            bill.sudo().write({"accounting_state": "posted"})
        claim = self.env["hospital.insurance.claim"].sudo().create(
            {"patient_id": patient.id, "bill_id": bill.id, "provider_id": provider.id, "approved_amount": 1000.0, "payer_responsibility_amount": 1000.0, "currency_id": self.currency.id, "state": "approved", "line_ids": [(0, 0, {"bill_line_id": bill.line_ids.id, "description": "Insured service", "subtotal": 1000.0, "coverage_percent": 100.0, "approved_amount": 1000.0})]}
        )
        claim.with_user(self.accountant).action_mark_ready_for_accounting()
        claim.with_user(self.accountant).action_reclassify_payer_receivable()
        claim.invalidate_recordset(["accounting_state", "accounting_move_id"])
        self.assertEqual(claim.accounting_state, "receivable_reclassified")
        self.assertTrue(claim.accounting_move_id)
        first_ref = "INS-%s" % uuid.uuid4().hex
        self.env["hospital.insurance.payer.payment.wizard"].with_user(self.accountant).create(
            {"claim_id": claim.id, "payment_amount": 400.0, "payment_journal_id": self.receipt_journal.id, "bank_account_id": self.bank_account.id, "payment_reference": first_ref}
        ).action_post_payer_payment()
        claim.invalidate_recordset(["amount_due_from_payer"])
        self.assertAlmostEqual(claim.amount_due_from_payer, 600.0, places=2)
        self.env["hospital.insurance.payer.payment.wizard"].with_user(self.accountant).create(
            {"claim_id": claim.id, "payment_amount": 800.0, "payment_journal_id": self.receipt_journal.id, "bank_account_id": self.bank_account.id, "payment_reference": "INS-%s" % uuid.uuid4().hex}
        ).action_post_payer_payment()
        claim.invalidate_recordset(["amount_due_from_payer", "insurer_credit_amount"])
        self.assertAlmostEqual(claim.amount_due_from_payer, 0.0, places=2)
        self.assertAlmostEqual(claim.insurer_credit_amount, 200.0, places=2)
        before_moves = self.env["account.move"].search_count([])
        self.env["hospital.insurance.payer.payment.wizard"].with_user(self.accountant).create(
            {"claim_id": claim.id, "payment_amount": 400.0, "payment_journal_id": self.receipt_journal.id, "bank_account_id": self.bank_account.id, "payment_reference": first_ref}
        ).action_post_payer_payment()
        self.assertEqual(before_moves, self.env["account.move"].search_count([]))
        self.assertTrue(claim.payer_payment_move_ids)
        self.assertEqual(ins_config.provider_type, "insurance")

    def test_patient_and_insurer_funds_cannot_cross_settle_receivables(self):
        patient_account, patient_charge = self._make_case(price=600.0)
        self._receipt(patient_account, patient_charge, 600.0)
        patient_invoice = self._invoice(patient_account)
        claim, _bill, _config = self._make_direct_claim(claim_amount=800.0, approved_amount=800.0)
        claim_receivable_lines = claim.accounting_move_id.line_ids.filtered(lambda line: line.account_id.account_type == "asset_receivable")
        self.assertTrue(claim_receivable_lines)
        with self.assertRaises(UserError):
            self.env["hospital.billing.engine"].with_user(self.accountant).apply_patient_advance_to_invoice(claim.accounting_move_id)

        patient_residual_before = patient_invoice.amount_residual
        self.env["hospital.insurance.payer.payment.wizard"].with_user(self.accountant).create(
            {
                "claim_id": claim.id,
                "payment_amount": 800.0,
                "payment_journal_id": self.receipt_journal.id,
                "bank_account_id": self.bank_account.id,
                "payment_reference": "INSX-%s" % uuid.uuid4().hex,
            }
        ).action_post_payer_payment()
        patient_invoice.invalidate_recordset(["amount_residual"])
        self.assertAlmostEqual(patient_invoice.amount_residual, patient_residual_before, places=2)
        self.assertFalse(any(patient_invoice.line_ids.filtered(lambda line: line.account_id.account_type == "asset_receivable").mapped("reconciled")))

    def test_rejected_or_reduced_insurer_amount_is_not_silently_reassigned_to_patient(self):
        claim, bill, _config = self._make_direct_claim(claim_amount=1000.0, approved_amount=600.0)
        bill.invalidate_recordset()
        claim.invalidate_recordset()
        patient_responsibility_before = bill.patient_responsibility_amount
        self.assertAlmostEqual(claim.rejected_amount_total, 400.0, places=2)
        self.assertAlmostEqual(claim.rejected_amount_unresolved, 400.0, places=2)
        self.assertAlmostEqual(claim.rejected_amount_transferred_to_patient, 0.0, places=2)
        self.env["hospital.insurance.payer.payment.wizard"].with_user(self.accountant).create(
            {
                "claim_id": claim.id,
                "payment_amount": 600.0,
                "payment_journal_id": self.receipt_journal.id,
                "bank_account_id": self.bank_account.id,
                "payment_reference": "REDUCED-%s" % uuid.uuid4().hex,
            }
        ).action_post_payer_payment()
        bill.invalidate_recordset()
        claim.invalidate_recordset()
        self.assertAlmostEqual(bill.patient_responsibility_amount, patient_responsibility_before, places=2)
        self.assertAlmostEqual(claim.rejected_amount_transferred_to_patient, 0.0, places=2)
        self.assertAlmostEqual(claim.rejected_amount_unresolved, 400.0, places=2)

        rejected_claim, rejected_bill, _rejected_config = self._make_direct_claim(claim_amount=300.0, approved_amount=300.0)
        rejected_patient_responsibility = rejected_bill.patient_responsibility_amount
        rejected_claim.sudo().write({"state": "rejected", "approved_amount": 0.0})
        rejected_claim.invalidate_recordset()
        with self.assertRaises(UserError):
            rejected_claim.with_user(self.accountant).action_reclassify_payer_receivable()
        rejected_bill.invalidate_recordset()
        self.assertAlmostEqual(rejected_bill.patient_responsibility_amount, rejected_patient_responsibility, places=2)
        self.assertAlmostEqual(rejected_claim.rejected_amount_transferred_to_patient, 0.0, places=2)

    def test_reimbursement_mode_creates_no_insurer_receivable(self):
        patient = self.env["hospital.patient"].sudo().create({"name": "32B3 Reimburse %s" % uuid.uuid4().hex[:6]})
        provider = self.env["hospital.insurance.provider"].sudo().create({"name": "32B3 Reimburse Payer", "payer_type": "insurance"})
        bill = self.env["hospital.patient.bill"].sudo().create({"patient_id": patient.id, "payer_type": "insurance", "insurance_provider_id": provider.id, "claim_settlement_mode": "patient_reimbursement", "coverage_percent": 80.0})
        self.env["hospital.patient.bill.line"].sudo().create({"bill_id": bill.id, "description": "Reimburse service", "source_type": "consultation", "quantity": 1.0, "unit_price": 500.0})
        with self.assertRaises(UserError):
            bill.action_create_insurance_claim()
