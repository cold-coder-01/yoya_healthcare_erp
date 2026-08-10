
import uuid

from odoo import fields
from odoo.exceptions import AccessError, UserError
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install", "unified_pharmacy_accounting_bridge")
class TestUnifiedPharmacyAccountingBridge(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.currency = cls.company.currency_id
        cls.uom = cls.env["uom.uom"].sudo().search([], limit=1)
        cls.accountant = cls._make_user("upharm_accountant", "hospital_management.group_hospital_accountant")
        cls.pharmacist = cls._make_user("upharm_pharmacist", "hospital_management.group_hospital_pharmacist")
        cls.config = cls._configure_pharmacy_accounting()
        cls.service = cls.env["hospital.billing.service"].sudo().create({
            "name": "Bridge Pharmacy Medicine",
            "code": "BR-PH-%s" % uuid.uuid4().hex[:4],
            "service_type": "pharmacy",
            "default_price": 200.0,
            "company_id": cls.company.id,
            "currency_id": cls.currency.id,
            "uom_id": cls.uom.id,
            "prepayment_required": True,
            "tax_treatment": "exempt",
            "tax_rate": 0.0,
        })
        cls.medicine = cls.env["hospital.pharmacy.medicine"].sudo().create({
            "name": "Bridge Medicine",
            "code": "BRMED-%s" % uuid.uuid4().hex[:4],
            "billing_service_id": cls.service.id,
        })
        cls.category = cls.env.ref("hospital_inventory.category_pharmacy_medicine")
        cls.pharmacy_location = cls.env["hospital.inventory.location"].sudo().get_default_pharmacy_store()
        if not cls.pharmacy_location:
            cls.pharmacy_location = cls.env["hospital.inventory.location"].sudo().create({"name": "Bridge Pharmacy Store", "code": "BRPH-%s" % uuid.uuid4().hex[:4], "location_type": "pharmacy_store"})

    @classmethod
    def _make_user(cls, login, hospital_group):
        return cls.env["res.users"].sudo().create({
            "name": login,
            "login": "%s@example.test" % login,
            "company_id": cls.company.id,
            "company_ids": [(6, 0, cls.company.ids)],
            "groups_id": [(6, 0, [cls.env.ref("base.group_user").id, cls.env.ref(hospital_group).id])],
        })

    @classmethod
    def _account(cls, code, name, account_type, reconcile=False):
        existing = cls.env["account.account"].sudo().search([("code", "=", code)], limit=1)
        if existing:
            existing.write({"reconcile": reconcile or existing.reconcile})
            return existing
        return cls.env["account.account"].sudo().create({"code": code, "name": name, "account_type": account_type, "reconcile": reconcile, "company_ids": [(6, 0, cls.company.ids)]})

    @classmethod
    def _journal(cls, code, name, journal_type, default_account=False):
        existing = cls.env["account.journal"].sudo().search([("code", "=", code), ("company_id", "=", cls.company.id)], limit=1)
        if existing:
            return existing
        vals = {"name": name, "code": code, "type": journal_type, "company_id": cls.company.id}
        if default_account:
            vals["default_account_id"] = default_account.id
        return cls.env["account.journal"].sudo().create(vals)

    @classmethod
    def _configure_pharmacy_accounting(cls):
        config = cls.env["hospital.billing.accounting.config"].sudo().search([("company_id", "=", cls.company.id), ("source_type", "=", "pharmacy"), ("active", "=", True)], limit=1)
        if not config:
            config = cls.env["hospital.billing.accounting.config"].sudo().create({"name": "Bridge Pharmacy Config", "company_id": cls.company.id, "source_type": "pharmacy"})
        receivable = cls._account("BRPREC", "Bridge Patient Receivable", "asset_receivable", True)
        revenue = cls._account("BRPREV", "Bridge Pharmacy Revenue", "income")
        cash = cls._account("BRPCASH", "Bridge Cash", "asset_cash")
        advance = cls._account("BRPADV", "Bridge Patient Advance", "liability_current", True)
        credit = cls._account("BRPCRD", "Bridge Patient Credit", "liability_current", True)
        inventory = cls._account("BRPSTK", "Bridge Pharmacy Inventory", "asset_current")
        cogs = cls._account("BRPCOG", "Bridge Pharmacy COGS", "expense")
        invoice_journal = cls._journal("BRPI", "Bridge Pharmacy Invoices", "sale")
        receipt_journal = cls._journal("BRPR", "Bridge Pharmacy Receipts", "general", cash)
        application_journal = cls._journal("BRPA", "Bridge Pharmacy Applications", "general", advance)
        valuation_journal = cls._journal("BRPV", "Bridge Pharmacy Valuation", "general", inventory)
        config.write({
            "receivable_account_id": receivable.id,
            "revenue_account_id": revenue.id,
            "cash_account_id": cash.id,
            "bank_account_id": cash.id,
            "mobile_money_account_id": cash.id,
            "journal_id": application_journal.id,
            "invoice_journal_id": invoice_journal.id,
            "payment_journal_id": receipt_journal.id,
            "patient_advance_liability_account_id": advance.id,
            "patient_credit_liability_account_id": credit.id,
            "advance_receipt_journal_id": receipt_journal.id,
            "advance_application_journal_id": application_journal.id,
            "advance_refund_journal_id": application_journal.id,
            "inventory_valuation_journal_id": valuation_journal.id,
            "inventory_asset_account_id": inventory.id,
            "cogs_account_id": cogs.id,
        })
        return config

    def _case(self, delivered=2.0, receipts=(200.0, 200.0), consumptions=(40.0, 40.0)):
        suffix = uuid.uuid4().hex[:8]
        partner = self.env["res.partner"].sudo().create({"name": "Bridge Partner %s" % suffix, "property_account_receivable_id": self.config.receivable_account_id.id})
        patient = self.env["hospital.patient"].sudo().create({"name": "Bridge Patient %s" % suffix, "accounting_partner_id": partner.id})
        doctor = self.env["hospital.doctor"].sudo().create({"name": "Bridge Doctor %s" % suffix})
        appointment = self.env["hospital.appointment"].sudo().create({"patient_id": patient.id, "doctor_id": doctor.id, "appointment_date": fields.Datetime.now(), "state": "confirmed"})
        encounter = self.env["hospital.encounter"].sudo().create({"patient_id": patient.id, "appointment_id": appointment.id, "encounter_type": "outpatient", "state": "active", "company_id": self.company.id})
        account = self.env["hospital.billing.account"].sudo().create({"encounter_id": encounter.id, "payer_type": "self_pay"})
        dispense = self.env["hospital.pharmacy.dispense"].sudo().create({
            "patient_id": patient.id,
            "physician_id": doctor.id,
            "appointment_id": appointment.id,
            "encounter_id": encounter.id,
            "state": "dispensed",
            "line_ids": [(0, 0, {"medicine_id": self.medicine.id, "prescribed_quantity": 2.0, "dispensed_quantity": delivered})],
        })
        line = dispense.line_ids[:1]
        charge = self.env["hospital.charge.line"].sudo().create({
            "billing_account_id": account.id,
            "service_id": self.service.id,
            "description": "Bridge Medicine",
            "uom_id": self.uom.id,
            "billing_basis": "prepaid",
            "qty_requested": 2.0,
            "qty_delivered": delivered,
            "delivery_state": "delivered" if delivered else "pending",
            "unit_price": 200.0,
            "discount": 0.0,
            "tax_treatment": "exempt",
            "tax_rate": 0.0,
            "charge_state": "active",
            "authorization_state": "not_required",
            "source_model": "hospital.pharmacy.dispense",
            "source_res_id": dispense.id,
            "source_line_id": line.id,
            "source_event": "pharmacy_dispense",
            "source_key": "bridge:%s" % suffix,
        })
        line.sudo().write({"charge_line_id": charge.id, "billing_delivered_quantity": delivered, "inventory_consumed_quantity": delivered})
        for amount in receipts:
            receipt = self.env["hospital.charge.receipt"].sudo().create({"payment_method": "cash", "received_at": fields.Datetime.now(), "received_by_id": self.accountant.id, "state": "draft", "intake_token": uuid.uuid4().hex})
            self.env["hospital.charge.receipt.allocation"].sudo().create({"receipt_id": receipt.id, "charge_line_id": charge.id, "amount": amount})
            receipt.sudo().write({"state": "confirmed"})
        item = self.env["hospital.inventory.item"].sudo().create({"name": "Bridge Item %s" % suffix, "code": "BRI-%s" % suffix[:4], "item_type": "consumable", "accounting_category": "medicine", "category_id": self.category.id, "unit_of_measure": "unit", "standard_cost": 40.0, "currency_id": self.currency.id, "company_id": self.company.id})
        for cost in consumptions:
            batch = self.env["hospital.inventory.batch"].sudo().create({"item_id": item.id, "batch_number": "BRB-%s" % uuid.uuid4().hex[:5], "location_id": self.pharmacy_location.id, "expiry_date": fields.Date.add(fields.Date.today(), months=6), "quantity_on_hand": 5.0, "unit_cost": cost, "currency_id": self.currency.id, "state": "available"})
            consumption = self.env["hospital.stock.consumption"].sudo().create_for_source("pharmacy", {"patient_id": patient.id, "pharmacy_dispense_id": dispense.id, "source_location_id": self.pharmacy_location.id}, [{"item_id": item.id, "batch_id": batch.id, "quantity": 1.0, "unit_cost": cost, "currency_id": self.currency.id}])
            consumption.action_approve()
            consumption.action_consume()
        return dispense, charge

    def _counts(self):
        return {m: self.env[m].sudo().search_count([]) for m in ["account.move", "account.move.line", "account.payment", "account.partial.reconcile", "hospital.invoice.batch", "hospital.charge.invoice.allocation", "hospital.charge.receipt", "hospital.stock.consumption", "hospital.stock.movement"] if m in self.env}

    def test_full_pharmacy_posting_and_idempotency(self):
        dispense, charge = self._case()
        before = self._counts()
        result = self.env["hospital.billing.engine"].with_user(self.accountant).post_pharmacy_dispense_accounting(dispense, request_token="bridge-test-%s" % uuid.uuid4().hex)
        invoice = result["invoice"]
        self.assertEqual(invoice.state, "posted")
        self.assertEqual(invoice.payment_state, "paid")
        self.assertEqual(invoice.amount_total, 400.0)
        self.assertEqual(invoice.amount_residual, 0.0)
        self.assertEqual(len(invoice.invoice_line_ids), 1)
        self.assertEqual(invoice.invoice_line_ids.quantity, 2.0)
        self.assertEqual(invoice.invoice_line_ids.price_unit, 200.0)
        self.assertFalse(invoice.invoice_line_ids.tax_ids)
        self.assertEqual(invoice.invoice_line_ids.account_id, self.config.revenue_account_id)
        self.assertEqual(charge.invoice_allocation_ids.quantity, 2.0)
        self.assertEqual(len(charge.invoice_allocation_ids), 1)
        self.assertEqual(self.env["account.payment"].sudo().search_count([]), before["account.payment"])
        self.assertEqual(len(charge.billing_account_id.receipt_ids.mapped("accounting_move_id")), 2)
        applications = self.env["hospital.patient.advance.application"].sudo().search([("billing_account_id", "=", charge.billing_account_id.id), ("invoice_id", "=", invoice.id)])
        self.assertEqual(sum(applications.mapped("amount")), 400.0)
        self.assertEqual(applications.receipt_ids, charge.billing_account_id.receipt_ids)
        movements = self.env["hospital.stock.movement"].sudo().search([("consumption_id.pharmacy_dispense_id", "=", dispense.id)])
        self.assertTrue(all(m.inventory_accounting_state == "posted" for m in movements))
        valuation_moves = movements.mapped("hospital_valuation_move_id")
        self.assertEqual(len(valuation_moves), 1)
        self.assertEqual(sum(valuation_moves.line_ids.filtered(lambda l: l.account_id == self.config.cogs_account_id).mapped("debit")), 80.0)
        after = self._counts()
        self.env["hospital.billing.engine"].with_user(self.accountant).post_pharmacy_dispense_accounting(dispense, request_token="bridge-test-%s" % uuid.uuid4().hex)
        self.assertEqual(self._counts(), after)

    def test_undelivered_quantities_cannot_be_posted(self):
        dispense, _charge = self._case(delivered=0.0, receipts=(), consumptions=())
        with self.assertRaisesRegex(UserError, "No delivered"):
            self.env["hospital.billing.engine"].with_user(self.accountant).post_pharmacy_dispense_accounting(dispense)

    def test_pharmacy_user_cannot_post_accounting(self):
        dispense, _charge = self._case()
        with self.assertRaises(AccessError):
            self.env["hospital.billing.engine"].with_user(self.pharmacist).post_pharmacy_dispense_accounting(dispense)

