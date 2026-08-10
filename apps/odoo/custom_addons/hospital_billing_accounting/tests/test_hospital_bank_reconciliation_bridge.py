# -*- coding: utf-8 -*-
import uuid

from odoo import fields
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install', 'hospital_bank_bridge')
class TestHospitalBankReconciliationBridge(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.currency = cls.company.currency_id
        cls.accountant = cls._make_user('bank_bridge_accountant', 'hospital_management.group_hospital_accountant')
        cls.receptionist = cls._make_user('bank_bridge_receptionist', 'hospital_management.group_hospital_receptionist')
        cls.config = cls.env['hospital.billing.accounting.config'].sudo().search([
            ('company_id', '=', cls.company.id), ('source_type', '=', 'consultation'), ('active', '=', True)
        ], limit=1)
        cls.service = cls.env['hospital.billing.service'].sudo().search([
            ('company_id', 'in', [False, cls.company.id]), ('service_type', '=', 'consultation'), ('invoice_product_id', '!=', False)
        ], limit=1)
        if not cls.config or not cls.service or not cls.service.invoice_tax_ids:
            raise AssertionError('Consultation billing config/service required for bank bridge tests.')
        cls.receivable = cls.config.receivable_account_id
        cls.cash_account = cls._account('HBBRCASH', 'Hospital Bridge Cash', 'asset_cash')
        cls.mobile_account = cls._account('HBBRMOB', 'Hospital Bridge Mobile', 'asset_current')
        cls.clearing_account = cls._account('HBBRCLR', 'Hospital Bank Receipt Clearing', 'asset_current', reconcile=True)
        cls.advance_account = cls._account('HBBRADV', 'Hospital Bridge Patient Advance', 'liability_current', reconcile=True)
        cls.credit_account = cls._account('HBBRCRD', 'Hospital Bridge Patient Credit', 'liability_current', reconcile=True)
        cls.bank_default_account = cls._account('HBBRBANK', 'Hospital Bridge Actual Bank', 'asset_cash')
        cls.receipt_journal = cls._journal('HBBRR', 'Hospital Bridge Receipt Journal', 'general', cls.clearing_account)
        cls.application_journal = cls._journal('HBBRA', 'Hospital Bridge Advance Application', 'general', cls.advance_account)
        cls.bank_journal = cls._journal('HBBRB', 'Hospital Bridge Bank Statement', 'bank', cls.bank_default_account)
        cls.config.sudo().write({
            'cash_account_id': cls.cash_account.id,
            'bank_account_id': cls.clearing_account.id,
            'mobile_money_account_id': cls.mobile_account.id,
            'patient_advance_liability_account_id': cls.advance_account.id,
            'patient_credit_liability_account_id': cls.credit_account.id,
            'advance_receipt_journal_id': cls.receipt_journal.id,
            'advance_application_journal_id': cls.application_journal.id,
            'advance_refund_journal_id': cls.application_journal.id,
            'bank_statement_journal_id': cls.bank_journal.id,
            'bank_receipt_clearing_account_id': cls.clearing_account.id,
        })

    @classmethod
    def _make_user(cls, login, hospital_group):
        return cls.env['res.users'].sudo().create({
            'name': login.replace('_', ' ').title(),
            'login': login,
            'company_id': cls.company.id,
            'company_ids': [(6, 0, cls.company.ids)],
            'groups_id': [(6, 0, [cls.env.ref('base.group_user').id, cls.env.ref(hospital_group).id])],
        })

    @classmethod
    def _account(cls, code, name, account_type, reconcile=False):
        existing = cls.env['account.account'].sudo().search([('code', '=', code)], limit=1)
        if existing:
            existing.write({'reconcile': reconcile or existing.reconcile})
            return existing
        return cls.env['account.account'].sudo().create({
            'code': code, 'name': name, 'account_type': account_type, 'reconcile': reconcile,
            'company_ids': [(6, 0, cls.company.ids)],
        })

    @classmethod
    def _journal(cls, code, name, journal_type, default_account):
        existing = cls.env['account.journal'].sudo().search([('code', '=', code), ('company_id', '=', cls.company.id)], limit=1)
        if existing:
            existing.write({'default_account_id': default_account.id})
            return existing
        return cls.env['account.journal'].sudo().create({
            'name': name, 'code': code, 'type': journal_type, 'company_id': cls.company.id,
            'default_account_id': default_account.id,
        })

    def _case(self, price=400.0):
        suffix = uuid.uuid4().hex[:8]
        partner = self.env['res.partner'].sudo().create({
            'name': 'Bank Bridge Partner %s' % suffix,
            'property_account_receivable_id': self.receivable.id,
        })
        patient = self.env['hospital.patient'].sudo().create({'name': 'Bank Bridge Patient %s' % suffix, 'accounting_partner_id': partner.id})
        appointment = self.env['hospital.appointment'].sudo().create({'patient_id': patient.id, 'appointment_date': fields.Datetime.now()})
        encounter = self.env['hospital.encounter'].sudo().create({
            'patient_id': patient.id, 'appointment_id': appointment.id, 'encounter_type': 'outpatient',
            'state': 'active', 'company_id': self.company.id,
        })
        account = self.env['hospital.billing.account'].sudo().create({'encounter_id': encounter.id, 'payer_type': 'self_pay'})
        charge = self.env['hospital.charge.line'].sudo().create({
            'billing_account_id': account.id,
            'service_id': self.service.id,
            'description': 'Bank Bridge Consultation',
            'uom_id': (self.service.uom_id.id or self.service.invoice_product_id.uom_id.id),
            'billing_basis': 'delivery',
            'qty_requested': 1.0,
            'qty_delivered': 1.0,
            'delivery_state': 'delivered',
            'unit_price': price,
            'discount': 0.0,
            'tax_treatment': self.service.tax_treatment,
            'tax_rate': self.service.tax_rate,
            'charge_state': 'active',
            'authorization_state': 'not_required',
            'source_model': 'hospital.appointment',
            'source_res_id': appointment.id,
            'source_event': 'hospital_bank_bridge_test',
            'source_key': 'hospital-bank-bridge:%s' % suffix,
        })
        return account, charge

    def _receipt(self, account, charge, amount=400.0, method='bank_transfer', reference=None):
        receipt = self.env['hospital.charge.receipt'].sudo().create({
            'payment_method': method,
            'payment_reference': reference or uuid.uuid4().hex,
            'received_at': fields.Datetime.now(),
            'received_by_id': self.accountant.id,
            'state': 'draft',
            'intake_token': uuid.uuid4().hex,
        })
        self.env['hospital.charge.receipt.allocation'].sudo().create({'receipt_id': receipt.id, 'charge_line_id': charge.id, 'amount': amount})
        receipt.sudo().write({'state': 'confirmed'})
        move = receipt.with_user(self.accountant).action_post_receipt_accounting()
        return receipt, move

    def _line(self, reference, amount=400.0, journal=None):
        imp = self.env['walnut.bank.statement.import'].sudo().create({
            'bank_journal_id': (journal or self.bank_journal).id,
            'company_id': self.company.id,
            'state': 'parsed',
            'enable_wht_matching': False,
        })
        return self.env['walnut.bank.statement.line'].sudo().create({
            'import_id': imp.id,
            'transaction_date': fields.Date.today(),
            'reference': reference,
            'narration': 'Hospital bank transfer %s' % reference,
            'amount': amount,
            'debit': amount,
            'bank_amount': amount,
            'match_status': 'unmatched',
        })

    def _invoice_and_apply(self, account):
        invoice = self.env['hospital.billing.engine'].with_user(self.accountant).create_invoice(account, request_token=uuid.uuid4().hex)
        invoice.with_user(self.accountant).action_post()
        application = self.env['hospital.billing.engine'].with_user(self.accountant).apply_patient_advance_to_invoice(invoice, request_token=uuid.uuid4().hex)
        return invoice, application

    def test_bank_receipt_awaits_statement_and_cash_is_excluded(self):
        account, charge = self._case()
        bank_receipt, move = self._receipt(account, charge, method='bank_transfer', reference='BRIDGE-%s' % uuid.uuid4().hex[:8])
        self.assertTrue(bank_receipt.bank_reconciliation_required)
        self.assertEqual(bank_receipt.bank_reconciliation_state, 'awaiting_statement')
        clearing = move.line_ids.filtered(lambda line: line.account_id == self.clearing_account and line.debit > 0)
        self.assertTrue(clearing)
        self.assertEqual(bank_receipt.bank_clearing_move_line_id, clearing)
        cash_receipt, cash_move = self._receipt(account, charge, amount=10.0, method='cash', reference='CASH-%s' % uuid.uuid4().hex[:8])
        self.assertFalse(cash_receipt.bank_reconciliation_required)
        self.assertEqual(cash_receipt.bank_reconciliation_state, 'not_required')
        self.assertFalse(cash_move.line_ids.filtered(lambda line: line.account_id == self.clearing_account and line.debit > 0))
        line = self._line(cash_receipt.payment_reference, amount=10.0)
        line.import_id.action_auto_match()
        self.assertNotEqual(line.hospital_receipt_id, cash_receipt)

    def test_bank_statement_exact_match_reconciles_clearing_without_duplicate_receipt_or_invoice_payment(self):
        account, charge = self._case()
        ref = 'BNK-%s' % uuid.uuid4().hex[:10]
        receipt, _move = self._receipt(account, charge, reference=ref)
        invoice, _application = self._invoice_and_apply(account)
        self.assertEqual(invoice.payment_state, 'paid')
        before = {
            'receipts': self.env['hospital.charge.receipt'].sudo().search_count([]),
            'moves': self.env['account.move'].sudo().search_count([]),
            'payments': self.env['account.payment'].sudo().search_count([]),
            'fiscal': self.env['hospital.fiscal.transaction'].sudo().search_count([]) if 'hospital.fiscal.transaction' in self.env else 0,
        }
        line = self._line(ref)
        line.import_id.action_auto_match()
        self.assertEqual(line.hospital_receipt_id, receipt)
        self.assertEqual(line.hospital_receipt_clearing_line_id, receipt.bank_clearing_move_line_id)
        line.with_user(self.accountant).action_validate_line()
        invoice.invalidate_recordset()
        receipt.invalidate_recordset()
        self.assertEqual(receipt.bank_reconciliation_state, 'reconciled')
        self.assertTrue(receipt.bank_clearing_move_line_id.reconciled)
        self.assertEqual(invoice.payment_state, 'paid')
        self.assertEqual(invoice.amount_residual, 0.0)
        after_first = {key: before[key] if key not in ('moves',) else self.env['account.move'].sudo().search_count([]) for key in before}
        self.assertEqual(self.env['hospital.charge.receipt'].sudo().search_count([]), before['receipts'])
        self.assertEqual(self.env['account.payment'].sudo().search_count([]), before['payments'])
        self.assertEqual((self.env['hospital.fiscal.transaction'].sudo().search_count([]) if 'hospital.fiscal.transaction' in self.env else 0), before['fiscal'])
        self.assertEqual(after_first['moves'], before['moves'] + 1)
        line.with_user(self.accountant).action_validate_line()
        self.assertEqual(self.env['account.move'].sudo().search_count([]), after_first['moves'])

    def test_duplicate_reference_and_variance_behaviour(self):
        account, charge = self._case()
        ref = 'DUP-%s' % uuid.uuid4().hex[:10]
        receipt, _move = self._receipt(account, charge, reference=ref)
        line = self._line(ref)
        with self.assertRaises(ValidationError):
            self._line(ref)
        low = self._line('LOW-%s' % uuid.uuid4().hex[:8], amount=399.0)
        receipt.sudo().with_context(hospital_bank_reconciliation_controlled=True).write({'bank_transaction_reference': low.reference})
        low.import_id.action_auto_match()
        self.assertEqual(low.match_status, 'exception')
        self.assertEqual(low.hospital_reconciliation_outcome, 'variance')

    def test_wrong_journal_currency_and_unauthorized_validation_block(self):
        account, charge = self._case()
        ref = 'AUTH-%s' % uuid.uuid4().hex[:10]
        receipt, _move = self._receipt(account, charge, reference=ref)
        line = self._line(ref)
        line.import_id.action_auto_match()
        with self.assertRaises(AccessError):
            line.with_user(self.receptionist).action_validate_line()
        wrong_journal = self._journal('HBBRX', 'Hospital Bridge Wrong Bank', 'bank', self._account('HBBRXA', 'Hospital Bridge Other Bank', 'asset_cash'))
        wrong_line = self._line(ref + '-X', journal=wrong_journal)
        receipt.sudo().with_context(hospital_bank_reconciliation_controlled=True).write({'bank_transaction_reference': wrong_line.reference})
        wrong_line.import_id.action_auto_match()
        self.assertFalse(wrong_line.hospital_receipt_id)
