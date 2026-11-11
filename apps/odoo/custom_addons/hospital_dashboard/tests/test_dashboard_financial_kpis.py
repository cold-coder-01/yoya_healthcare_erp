import unittest
import uuid
from datetime import timedelta

from odoo import fields
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install", "dashboard_financial")
class TestHospitalDashboardFinancialKpis(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        required = [
            "hospital.dashboard",
            "hospital.billing.accounting.config",
            "hospital.billing.engine",
            "hospital.charge.receipt",
            "hospital.charge.receipt.allocation",
            "hospital.charge.line",
            "hospital.billing.account",
        ]
        missing = [model for model in required if model not in cls.env]
        if missing:
            raise unittest.SkipTest("Dashboard financial KPI tests require: %s" % ", ".join(missing))
        cls.company = cls.env.company
        cls.currency = cls.company.currency_id
        cls.accountant = cls._make_user("dash_fin_accountant", "hospital_management.group_hospital_accountant")
        cls.config = cls.env["hospital.billing.accounting.config"].sudo().search(
            [("company_id", "=", cls.company.id), ("source_type", "=", "consultation"), ("active", "=", True)], limit=1
        )
        cls.service = cls.env["hospital.billing.service"].sudo().search(
            [("company_id", "in", [False, cls.company.id]), ("service_type", "=", "consultation"), ("invoice_product_id", "!=", False)], limit=1
        )
        if not cls.config or not cls.service or not cls.service.invoice_tax_ids:
            raise unittest.SkipTest("Configured consultation billing service/accounting config required.")
        cls.receivable = cls.config.receivable_account_id
        cls.cash_account = cls._account("TDASHCASH", "Task Dashboard Cash", "asset_cash")
        cls.bank_account = cls._account("TDASHBANK", "Task Dashboard Bank", "asset_cash")
        cls.advance_account = cls._account("TDASHADV", "Task Dashboard Patient Advance", "liability_current", reconcile=True)
        cls.credit_account = cls._account("TDASHCRD", "Task Dashboard Patient Credit", "liability_current", reconcile=True)
        cls.receipt_journal = cls._journal("TDASHR", "Task Dashboard Receipt Journal", cls.cash_account)
        cls.application_journal = cls._journal("TDASHA", "Task Dashboard Application Journal", cls.advance_account)
        cls.config.sudo().write(
            {
                "cash_account_id": cls.cash_account.id,
                "bank_account_id": cls.bank_account.id,
                "mobile_money_account_id": cls.bank_account.id,
                "patient_advance_liability_account_id": cls.advance_account.id,
                "patient_credit_liability_account_id": cls.credit_account.id,
                "advance_receipt_journal_id": cls.receipt_journal.id,
                "advance_application_journal_id": cls.application_journal.id,
                "advance_refund_journal_id": cls.application_journal.id,
            }
        )

    @classmethod
    def _make_user(cls, login, group_xmlid):
        group = cls.env.ref(group_xmlid)
        user = cls.env["res.users"].sudo().create(
            {"name": login, "login": "%s@example.test" % login, "email": "%s@example.test" % login, "groups_id": [(6, 0, [group.id])]}
        )
        user.sudo().write({"company_ids": [(4, cls.company.id)], "company_id": cls.company.id})
        return user

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

    def _dashboard_values(self):
        data = self.env["hospital.dashboard"].with_user(self.accountant).with_context(tz="Africa/Nairobi").get_dashboard_data({"range": "today"})
        kpis = {kpi["key"]: kpi["value"] for kpi in data["kpis"]}
        return {
            "revenue": kpis.get("revenue", 0.0),
            "collected": kpis.get("collected", 0.0),
            "outstanding": kpis.get("outstanding", 0.0),
            "advances_held": kpis.get("advances_held", 0.0),
            "raw": data,
        }

    def _assert_delta(self, before, after, key, amount):
        self.assertAlmostEqual(after[key] - before[key], amount, places=2, msg="%s delta" % key)

    def _make_case(self, price=300.0):
        suffix = uuid.uuid4().hex[:8]
        partner = self.env["res.partner"].sudo().create({"name": "Dashboard Patient Partner %s" % suffix, "property_account_receivable_id": self.receivable.id})
        patient = self.env["hospital.patient"].sudo().create({"name": "Dashboard Patient %s" % suffix, "accounting_partner_id": partner.id})
        appointment = self.env["hospital.appointment"].sudo().create({"patient_id": patient.id, "appointment_date": fields.Datetime.now()})
        encounter = self.env["hospital.encounter"].sudo().create(
            {"patient_id": patient.id, "appointment_id": appointment.id, "encounter_type": "outpatient", "state": "active", "company_id": self.company.id}
        )
        account = self.env["hospital.billing.account"].sudo().create({"encounter_id": encounter.id, "payer_type": "self_pay"})
        charge = self.env["hospital.charge.line"].sudo().create(
            {
                "billing_account_id": account.id,
                "service_id": self.service.id,
                "description": "Dashboard Consultation",
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
                "source_event": "dashboard_financial_test",
                "source_key": "dashboard:%s" % suffix,
            }
        )
        return account, charge

    def _receipt(self, charge, amount, received_at=None, post=True, state="confirmed"):
        receipt = self.env["hospital.charge.receipt"].sudo().create(
            {"payment_method": "cash", "received_at": received_at or fields.Datetime.now(), "received_by_id": self.accountant.id, "state": "draft", "intake_token": uuid.uuid4().hex}
        )
        self.env["hospital.charge.receipt.allocation"].sudo().create({"receipt_id": receipt.id, "charge_line_id": charge.id, "amount": amount})
        receipt.sudo().write({"state": state})
        if post and state == "confirmed":
            receipt.with_user(self.accountant).action_post_receipt_accounting()
        return receipt

    def _invoice(self, account, post=True):
        invoice = self.env["hospital.billing.engine"].with_user(self.accountant).create_invoice(account, request_token=uuid.uuid4().hex)
        if post:
            invoice.with_user(self.accountant).action_post()
        return invoice

    def test_manual_receipts_collected_and_held_before_invoice(self):
        before = self._dashboard_values()
        _account, charge = self._make_case(price=300.0)
        self._receipt(charge, 100.0)
        self._receipt(charge, 200.0)
        after = self._dashboard_values()
        self._assert_delta(before, after, "collected", 300.0)
        self._assert_delta(before, after, "revenue", 0.0)
        self._assert_delta(before, after, "advances_held", 300.0)

    def test_invoice_and_advance_application_complete_workflow(self):
        before = self._dashboard_values()
        account, charge = self._make_case(price=300.0)
        self._receipt(charge, 100.0)
        self._receipt(charge, 200.0)
        invoice = self._invoice(account)
        invoiced = self._dashboard_values()
        self._assert_delta(before, invoiced, "revenue", 300.0)
        self._assert_delta(before, invoiced, "collected", 300.0)
        self._assert_delta(before, invoiced, "advances_held", 300.0)
        self._assert_delta(before, invoiced, "outstanding", 300.0)
        self.env["hospital.billing.engine"].with_user(self.accountant).apply_patient_advance_to_invoice(invoice, amount=300.0, request_token=uuid.uuid4().hex)
        applied = self._dashboard_values()
        self._assert_delta(before, applied, "revenue", 300.0)
        self._assert_delta(before, applied, "collected", 300.0)
        self._assert_delta(before, applied, "advances_held", 0.0)
        self._assert_delta(before, applied, "outstanding", 0.0)

    def test_draft_cancelled_receipts_and_draft_invoice_are_excluded(self):
        before = self._dashboard_values()
        account, charge = self._make_case(price=300.0)
        self._receipt(charge, 100.0, post=False, state="draft")
        self._receipt(charge, 200.0, post=False, state="cancelled")
        self._invoice(account, post=False)
        after = self._dashboard_values()
        self._assert_delta(before, after, "collected", 0.0)
        self._assert_delta(before, after, "revenue", 0.0)
        self._assert_delta(before, after, "outstanding", 0.0)

    def test_another_company_records_are_excluded(self):
        before = self._dashboard_values()
        other = self.env["res.company"].sudo().create({"name": "Dashboard Other Company %s" % uuid.uuid4().hex[:6], "currency_id": self.currency.id})
        income = self.env["account.account"].sudo().create({"code": "TDASHOI", "name": "Other Income", "account_type": "income", "company_ids": [(6, 0, other.ids)]})
        receivable = self.env["account.account"].sudo().create({"code": "TDASHOR", "name": "Other Receivable", "account_type": "asset_receivable", "reconcile": True, "company_ids": [(6, 0, other.ids)]})
        journal = self.env["account.journal"].sudo().create({"name": "Other Sales", "code": "TDOIS", "type": "sale", "company_id": other.id, "default_account_id": income.id})
        partner = self.env["res.partner"].sudo().create({"name": "Other Company Dashboard Patient", "property_account_receivable_id": receivable.id})
        move = self.env["account.move"].sudo().with_company(other).create(
            {
                "move_type": "out_invoice",
                "partner_id": partner.id,
                "company_id": other.id,
                "currency_id": self.currency.id,
                "journal_id": journal.id,
                "date": fields.Date.context_today(self.env.user),
                "invoice_date": fields.Date.context_today(self.env.user),
                "invoice_line_ids": [(0, 0, {"name": "Other company service", "quantity": 1.0, "price_unit": 999.0, "account_id": income.id})],
            }
        )
        move.action_post()
        self.env.flush_all()
        self.env.cr.execute("UPDATE account_move SET hospital_managed_invoice = TRUE WHERE id = %s", [move.id])
        self.env.invalidate_all()
        after = self._dashboard_values()
        self._assert_delta(before, after, "revenue", 0.0)
        self._assert_delta(before, after, "outstanding", 0.0)

    def test_today_boundaries_use_user_timezone(self):
        dashboard = self.env["hospital.dashboard"].with_user(self.accountant).with_context(tz="Africa/Nairobi")
        start, _end = dashboard._range_bounds("today")
        before = self._dashboard_values()
        _account, charge = self._make_case(price=300.0)
        self._receipt(charge, 50.0, received_at=start - timedelta(seconds=1))
        self._receipt(charge, 70.0, received_at=start + timedelta(seconds=1))
        after = self._dashboard_values()
        self._assert_delta(before, after, "collected", 70.0)

    def test_existing_nonfinancial_dashboard_kpis_remain_functional(self):
        data = self._dashboard_values()["raw"]
        kpis = {kpi["key"]: kpi for kpi in data["kpis"]}
        for key in ("patients", "appointments", "admissions", "laboratory", "pharmacy"):
            self.assertIn(key, kpis)
            self.assertIn("value", kpis[key])
        self.assertIn("patient_flow", data)
        self.assertIn("clinical", data)
