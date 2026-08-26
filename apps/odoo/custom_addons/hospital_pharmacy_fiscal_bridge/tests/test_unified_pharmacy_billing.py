import uuid

from odoo import fields
from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install", "unified_pharmacy_billing")
class TestUnifiedPharmacyBilling(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.uom = cls.env["uom.uom"].sudo().search([], limit=1)
        cls.accountant = cls._make_user("pharmacy_accountant", "hospital_management.group_hospital_accountant")
        cls.pharmacist = cls._make_user("pharmacy_pharmacist", "hospital_management.group_hospital_pharmacist")
        cls.doctor = cls.env["hospital.doctor"].sudo().create({"name": "Pharmacy Test Doctor"})
        cls.service = cls.env["hospital.billing.service"].sudo().create({
            "name": "Unified Pharmacy Medicine",
            "code": "T-PHARM-MED",
            "service_type": "pharmacy",
            "default_price": 100.0,
            "company_id": cls.company.id,
            "currency_id": cls.company.currency_id.id,
            "uom_id": cls.uom.id,
            "prepayment_required": True,
            "tax_treatment": "exempt",
        })
        cls.medicine = cls.env["hospital.pharmacy.medicine"].sudo().create({
            "name": "Unified Test Medicine",
            "code": "UTM",
            "billing_service_id": cls.service.id,
            "sale_price": 100.0,
        })
        if "hospital.billing.accounting.config" in cls.env:
            cls._configure_advance_accounting()

    @classmethod
    def _make_user(cls, login, group_xmlid):
        group = cls.env.ref(group_xmlid)
        user = cls.env["res.users"].sudo().create({"name": login, "login": "%s@example.test" % login, "email": "%s@example.test" % login, "groups_id": [(6, 0, [group.id])]})
        user.sudo().write({"company_ids": [(4, cls.company.id)], "company_id": cls.company.id})
        return user

    @classmethod
    def _account(cls, code, name, account_type, reconcile=False):
        existing = cls.env["account.account"].sudo().search([("code", "=", code)], limit=1)
        if existing:
            existing.write({"reconcile": reconcile or existing.reconcile})
            return existing
        return cls.env["account.account"].sudo().create({"code": code, "name": name, "account_type": account_type, "reconcile": reconcile, "company_ids": [(6, 0, cls.company.ids)]})

    @classmethod
    def _journal(cls, code, name, default_account):
        existing = cls.env["account.journal"].sudo().search([("code", "=", code), ("company_id", "=", cls.company.id)], limit=1)
        if existing:
            return existing
        return cls.env["account.journal"].sudo().create({"name": name, "code": code, "type": "general", "company_id": cls.company.id, "default_account_id": default_account.id})

    @classmethod
    def _configure_advance_accounting(cls):
        config = cls.env["hospital.billing.accounting.config"].sudo().search([("company_id", "=", cls.company.id), ("source_type", "=", "pharmacy"), ("active", "=", True)], limit=1)
        if not config:
            config = cls.env["hospital.billing.accounting.config"].sudo().create({"name": "Pharmacy Test Accounting", "company_id": cls.company.id, "source_type": "pharmacy"})
        cash = cls._account("TPHRCASH", "Pharmacy Test Cash", "asset_cash")
        bank = cls._account("TPHRBANK", "Pharmacy Test Bank", "asset_cash")
        advance = cls._account("TPHRADV", "Pharmacy Test Patient Advance", "liability_current", True)
        credit = cls._account("TPHRCRD", "Pharmacy Test Patient Credit", "liability_current", True)
        receipt_journal = cls._journal("TPHRR", "Pharmacy Test Receipt Journal", cash)
        app_journal = cls._journal("TPHRA", "Pharmacy Test Application Journal", advance)
        config.write({
            "cash_account_id": cash.id,
            "bank_account_id": bank.id,
            "mobile_money_account_id": bank.id,
            "patient_advance_liability_account_id": advance.id,
            "patient_credit_liability_account_id": credit.id,
            "advance_receipt_journal_id": receipt_journal.id,
            "advance_application_journal_id": app_journal.id,
            "advance_refund_journal_id": app_journal.id,
            "receivable_account_id": config.receivable_account_id.id or cls._account("TPHRREC", "Pharmacy Test Receivable", "asset_receivable", True).id,
        })

    def _dispense(self, qty=2.0):
        suffix = uuid.uuid4().hex[:8]
        partner = self.env["res.partner"].sudo().create({"name": "Pharmacy Partner %s" % suffix})
        patient = self.env["hospital.patient"].sudo().create({"name": "Pharmacy Patient %s" % suffix, "accounting_partner_id": partner.id})
        appointment = self.env["hospital.appointment"].sudo().create({"patient_id": patient.id, "doctor_id": self.doctor.id, "appointment_date": fields.Datetime.now(), "state": "confirmed"})
        encounter = self.env["hospital.encounter"].sudo().create({"patient_id": patient.id, "appointment_id": appointment.id, "encounter_type": "outpatient", "primary_doctor_id": self.doctor.id, "company_id": self.company.id})
        dispense = self.env["hospital.pharmacy.dispense"].sudo().create({
            "patient_id": patient.id,
            "physician_id": self.doctor.id,
            "appointment_id": appointment.id,
            "encounter_id": encounter.id,
            "line_ids": [(0, 0, {"medicine_id": self.medicine.id, "prescribed_quantity": qty, "dispensed_quantity": qty})],
        })
        return dispense

    def _pay(self, dispense, amount=None):
        dispense._ensure_pharmacy_billing()
        charge = dispense.charge_line_ids[:1]
        receipt = self.env["hospital.charge.receipt"].sudo().create({"payment_method": "cash", "received_at": fields.Datetime.now(), "received_by_id": self.accountant.id, "state": "draft", "intake_token": uuid.uuid4().hex})
        self.env["hospital.charge.receipt.allocation"].sudo().create({"receipt_id": receipt.id, "charge_line_id": charge.id, "amount": amount or charge.amount_due_for_clearance})
        receipt.sudo().write({"state": "confirmed"})
        if hasattr(receipt, "action_post_receipt_accounting"):
            receipt.with_user(self.accountant).action_post_receipt_accounting()
        return receipt

    def test_dispense_resolves_encounter_billing_and_idempotent_charge_no_legacy_bill(self):
        dispense = self._dispense(qty=2.0)
        legacy_before = self.env["hospital.patient.bill"].sudo().search_count([])
        dispense.action_mark_ready()
        dispense._ensure_pharmacy_billing()
        dispense.invalidate_recordset(["charge_line_ids", "billing_account_id"])
        self.assertTrue(dispense.encounter_id)
        self.assertTrue(dispense.billing_account_id)
        self.assertEqual(len(dispense.charge_line_ids), 1)
        charge = dispense.charge_line_ids[:1]
        self.assertEqual(charge.source_model, "hospital.pharmacy.dispense")
        self.assertEqual(charge.source_line_id, dispense.line_ids.id)
        self.assertEqual(charge.qty_requested, 2.0)
        dispense._ensure_pharmacy_billing()
        self.assertEqual(len(dispense.charge_line_ids), 1)
        self.assertEqual(self.env["hospital.patient.bill"].sudo().search_count([]), legacy_before)

    def test_manual_payment_creates_unified_receipt_without_dispense_or_stock(self):
        dispense = self._dispense(qty=1.0)
        dispense.action_mark_ready()
        stock_before = self.env["hospital.stock.movement"].sudo().search_count([]) if "hospital.stock.movement" in self.env else 0
        receipt = self._pay(dispense)
        self.assertEqual(receipt.state, "confirmed")
        dispense.invalidate_recordset(["state"])
        self.assertEqual(dispense.state, "ready")
        if "hospital.stock.movement" in self.env:
            self.assertEqual(self.env["hospital.stock.movement"].sudo().search_count([]), stock_before)

    def test_unpaid_dispense_validation_blocked_by_shared_clearance(self):
        dispense = self._dispense(qty=1.0)
        dispense.action_mark_ready()
        with self.assertRaisesRegex(UserError, "financial clearance"):
            dispense.action_mark_dispensed()
        self.assertEqual(dispense.state, "ready")
        self.assertEqual(dispense.charge_line_ids[:1].qty_delivered, 0.0)

    def test_fiscal_success_creates_unified_receipt_but_does_not_dispense_or_consume(self):
        dispense = self._dispense(qty=1.0)
        dispense.action_mark_ready()
        legacy_before = self.env["hospital.patient.bill"].sudo().search_count([])
        stock_before = self.env["hospital.stock.movement"].sudo().search_count([]) if "hospital.stock.movement" in self.env else 0
        action = dispense.action_prepare_fiscal_payment()
        transaction = self.env["hospital.fiscal.transaction"].sudo().browse(action["res_id"])
        result = transaction.action_mark_paid_from_terminal({"amount_paid": transaction.amount_payable, "external_receipt_no": "FISC-%s" % uuid.uuid4().hex[:8], "external_transaction_id": uuid.uuid4().hex, "idempotency_key": uuid.uuid4().hex, "payment_method": "cash"})
        self.assertEqual(result["status"], "accepted")
        transaction.invalidate_recordset(["unified_receipt_id"])
        self.assertTrue(transaction.unified_receipt_id)
        self.assertEqual(transaction.unified_receipt_id.state, "confirmed")
        self.assertEqual(dispense.state, "ready")
        self.assertEqual(self.env["hospital.patient.bill"].sudo().search_count([]), legacy_before)
        if "hospital.stock.movement" in self.env:
            self.assertEqual(self.env["hospital.stock.movement"].sudo().search_count([]), stock_before)
        duplicate = transaction.action_mark_paid_from_terminal({"amount_paid": transaction.amount_payable, "external_receipt_no": transaction.external_receipt_no, "external_transaction_id": transaction.external_transaction_id, "idempotency_key": transaction.idempotency_key, "payment_method": "cash"})
        self.assertEqual(duplicate["status"], "duplicate")
        self.assertEqual(self.env["hospital.charge.receipt"].sudo().search_count([("intake_token", "=", "fiscal:%s" % transaction.name)]), 1)


    def test_paid_validate_dispense_delivers_charge_and_consumes_stock_once(self):
        pharmacy_category = self.env.ref("hospital_inventory.category_pharmacy_medicine")
        item = self.env["hospital.inventory.item"].sudo().create({
            "name": "Unified Test Medicine Inventory",
            "code": "UTMI-%s" % uuid.uuid4().hex[:6],
            "item_type": "medicine",
            "category_id": pharmacy_category.id,
            "unit_of_measure": "unit",
        })
        self.medicine.sudo().write({"inventory_item_id": item.id})
        pharmacy_location = self.env["hospital.inventory.location"].sudo().get_default_pharmacy_store()
        if not pharmacy_location:
            pharmacy_location = self.env["hospital.inventory.location"].sudo().create({
                "name": "Unified Test Pharmacy Store",
                "code": "UTPH-%s" % uuid.uuid4().hex[:4],
                "location_type": "pharmacy_store",
            })
        batch = self.env["hospital.inventory.batch"].sudo().create({
            "item_id": item.id,
            "batch_number": "B-%s" % uuid.uuid4().hex[:8],
            "location_id": pharmacy_location.id,
            "quantity_on_hand": 5.0,
            "unit_cost": 10.0,
            "currency_id": self.company.currency_id.id,
            "state": "available",
        })
        dispense = self._dispense(qty=1.0)
        dispense.action_mark_ready()
        self._pay(dispense)
        move_before = self.env["hospital.stock.movement"].sudo().search_count([])
        consumption_before = self.env["hospital.stock.consumption"].sudo().search_count([])
        dispense.action_mark_dispensed()
        dispense.invalidate_recordset(["state", "charge_line_ids"])
        self.assertEqual(dispense.state, "dispensed")
        charge = dispense._pharmacy_charges()[:1]
        self.assertEqual(charge.qty_delivered, 1.0)
        self.assertEqual(dispense.line_ids[:1].inventory_consumed_quantity, 1.0)
        batch.invalidate_recordset(["quantity_on_hand", "available_quantity"])
        self.assertEqual(batch.quantity_on_hand, 4.0)
        self.assertEqual(self.env["hospital.stock.consumption"].sudo().search_count([]), consumption_before + 1)
        self.assertEqual(self.env["hospital.stock.movement"].sudo().search_count([]), move_before + 1)
        dispense.action_mark_dispensed()
        self.assertEqual(self.env["hospital.stock.consumption"].sudo().search_count([]), consumption_before + 1)
        self.assertEqual(self.env["hospital.stock.movement"].sudo().search_count([]), move_before + 1)

    def test_historical_legacy_fiscal_transaction_with_bill_still_prepares(self):
        suffix = uuid.uuid4().hex[:8]
        partner = self.env["res.partner"].sudo().create({"name": "Legacy Pharmacy Partner %s" % suffix})
        patient = self.env["hospital.patient"].sudo().create({"name": "Legacy Pharmacy Patient %s" % suffix, "accounting_partner_id": partner.id})
        dispense = self.env["hospital.pharmacy.dispense"].sudo().create({
            "patient_id": patient.id,
            "physician_id": self.doctor.id,
            "line_ids": [(0, 0, {"medicine_id": self.medicine.id, "prescribed_quantity": 1.0, "dispensed_quantity": 1.0})],
        })
        self.assertFalse(dispense.unified_billing_enabled)
        # Bypass Mark Ready here: legacy compatibility being asserted is fiscal
        # transaction creation against a patient bill, not unified charge prep.
        dispense.with_context(skip_dispense_write_audit=True).write({"state": "ready"})
        action = dispense.action_prepare_fiscal_payment()
        transaction = self.env["hospital.fiscal.transaction"].sudo().browse(action["res_id"])
        self.assertTrue(transaction.bill_id)
        self.assertEqual(transaction.state, "ready")
