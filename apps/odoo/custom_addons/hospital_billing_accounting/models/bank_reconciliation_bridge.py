# -*- coding: utf-8 -*-
import hashlib
import uuid

from odoo import api, fields, models, _
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tools import float_compare, float_is_zero

from .invoice_batch import assert_invoice_authorized

BANK_METHOD = 'bank_transfer'
AMOUNT_TOLERANCE = 0.0001
CONTROLLED_CTX = 'hospital_bank_reconciliation_controlled'


class HospitalBillingAccountingConfigBankBridge(models.Model):
    _inherit = 'hospital.billing.accounting.config'

    bank_statement_journal_id = fields.Many2one(
        'account.journal',
        string='Bank Statement Journal',
        domain="[('type','=','bank'), ('company_id','=', company_id)]",
        check_company=True,
        tracking=True,
        help='Actual bank journal used when imported statements confirm Bank receipts.',
    )
    bank_receipt_clearing_account_id = fields.Many2one(
        'account.account',
        string='Bank Receipt Clearing Account',
        domain="[('account_type','in',('asset_current','asset_receivable','liability_current')), ('reconcile','=',True)]",
        tracking=True,
        help='Interim account debited when a Bank-classified hospital receipt is confirmed, then cleared by bank statement reconciliation.',
    )

    def _assert_bank_reconciliation_configuration(self):
        for config in self:
            missing = []
            if not config.bank_statement_journal_id:
                missing.append('Bank Statement Journal')
            if not config.bank_receipt_clearing_account_id:
                missing.append('Bank Receipt Clearing Account')
            if missing:
                raise UserError(_('Missing bank-reconciliation configuration: %s.') % ', '.join(missing))
            if config.bank_statement_journal_id.company_id != config.company_id:
                raise UserError(_('Bank Statement Journal belongs to another company.'))
            if config.bank_statement_journal_id.type != 'bank':
                raise UserError(_('Bank Statement Journal must be a bank journal.'))
            if not config.bank_statement_journal_id.default_account_id:
                raise UserError(_('Bank Statement Journal requires a default bank account.'))
            if not config.bank_receipt_clearing_account_id.reconcile:
                raise UserError(_('Bank Receipt Clearing Account must allow reconciliation.'))


class HospitalChargeReceiptBankBridge(models.Model):
    _inherit = 'hospital.charge.receipt'

    bank_reconciliation_required = fields.Boolean(
        string='Bank Reconciliation Required',
        compute='_compute_bank_reconciliation_required',
        store=True,
        readonly=True,
        index=True,
    )
    bank_reconciliation_state = fields.Selection([
        ('not_required', 'Not Required'),
        ('awaiting_statement', 'Awaiting Statement'),
        ('suggested', 'Suggested'),
        ('matched', 'Matched'),
        ('reconciled', 'Reconciled'),
        ('variance', 'Variance'),
        ('reversed', 'Reversed'),
    ], string='Bank Reconciliation State', default='not_required', readonly=True, copy=False, index=True, tracking=True)
    expected_bank_journal_id = fields.Many2one('account.journal', string='Expected Bank', readonly=True, copy=False, check_company=True)
    bank_transaction_reference = fields.Char(string='Bank Transaction Reference', copy=False, index=True)
    bank_transaction_date = fields.Date(string='Bank Transaction Date', copy=False)
    bank_clearing_move_id = fields.Many2one('account.move', string='Bank Clearing Receipt Entry', readonly=True, copy=False, ondelete='restrict')
    bank_clearing_move_line_id = fields.Many2one('account.move.line', string='Bank Clearing Journal Item', readonly=True, copy=False, ondelete='restrict')
    matched_statement_line_id = fields.Many2one('walnut.bank.statement.line', string='Matched Statement Line', readonly=True, copy=False, ondelete='restrict')
    bank_reconciled_move_id = fields.Many2one('account.move', string='Bank Reconciliation Entry', readonly=True, copy=False, ondelete='restrict')
    bank_reconciled_at = fields.Datetime(string='Bank Reconciled At', readonly=True, copy=False)
    bank_reconciled_by = fields.Many2one('res.users', string='Bank Reconciled By', readonly=True, copy=False)
    bank_reconciliation_difference = fields.Monetary(string='Bank Difference', readonly=True, copy=False, currency_field='currency_id')

    @api.depends('payment_method')
    def _compute_bank_reconciliation_required(self):
        for receipt in self:
            receipt.bank_reconciliation_required = receipt.payment_method == BANK_METHOD

    def _resolve_liquidity_account(self, config):
        self.ensure_one()
        if self.payment_method == BANK_METHOD:
            config._assert_bank_reconciliation_configuration()
            return config.bank_receipt_clearing_account_id
        return super()._resolve_liquidity_account(config)

    def action_post_receipt_accounting(self):
        result = super().action_post_receipt_accounting()
        for receipt in self.sudo():
            if receipt.payment_method != BANK_METHOD:
                if receipt.bank_reconciliation_state != 'not_required':
                    receipt.with_context(**{CONTROLLED_CTX: True}).write({'bank_reconciliation_state': 'not_required'})
                continue
            config = receipt._get_advance_accounting_config()
            config._assert_bank_reconciliation_configuration()
            clearing_line = receipt.accounting_move_id.line_ids.filtered(
                lambda line: line.account_id == config.bank_receipt_clearing_account_id and line.debit > AMOUNT_TOLERANCE and not line.reconciled
            )[:1]
            if not clearing_line and receipt.bank_reconciliation_state != 'reconciled':
                raise UserError(_('Receipt %s has no open bank-clearing debit line.') % receipt.name)
            vals = {
                'bank_reconciliation_state': 'reconciled' if receipt.bank_reconciled_move_id else 'awaiting_statement',
                'expected_bank_journal_id': config.bank_statement_journal_id.id,
                'bank_clearing_move_id': receipt.accounting_move_id.id,
            }
            if clearing_line:
                vals['bank_clearing_move_line_id'] = clearing_line.id
            receipt.with_context(**{CONTROLLED_CTX: True}).write(vals)
        return result

    def write(self, vals):
        protected = {
            'bank_reconciliation_state', 'expected_bank_journal_id', 'bank_clearing_move_id',
            'bank_clearing_move_line_id', 'matched_statement_line_id', 'bank_reconciled_move_id',
            'bank_reconciled_at', 'bank_reconciled_by', 'bank_reconciliation_difference',
        }
        if protected & set(vals) and not (self.env.su and self.env.context.get(CONTROLLED_CTX)):
            raise AccessError(_('Bank reconciliation provenance is controlled by the accounting bridge.'))
        return super().write(vals)

    def _bank_match_references(self):
        self.ensure_one()
        return [value.strip() for value in [
            self.bank_transaction_reference,
            self.payment_reference,
            self.external_transaction_reference,
            self.fiscal_receipt_number,
            self.name,
        ] if value and value.strip()]


class WalnutBankStatementLineHospitalBridge(models.Model):
    _inherit = 'walnut.bank.statement.line'

    bank_journal_id = fields.Many2one('account.journal', related='import_id.bank_journal_id', store=True, readonly=True)
    import_fingerprint = fields.Char(string='Import Fingerprint', compute='_compute_import_fingerprint', store=True, index=True)
    hospital_receipt_id = fields.Many2one('hospital.charge.receipt', string='Hospital Bank Receipt', copy=False, ondelete='restrict')
    hospital_patient_id = fields.Many2one('hospital.patient', string='Patient', related='hospital_receipt_id.patient_id', store=True, readonly=True)
    hospital_encounter_id = fields.Many2one('hospital.encounter', string='Encounter', related='hospital_receipt_id.encounter_id', store=True, readonly=True)
    hospital_billing_account_id = fields.Many2one('hospital.billing.account', string='Billing Account', related='hospital_receipt_id.billing_account_id', store=True, readonly=True)
    hospital_invoice_id = fields.Many2one('account.move', string='Hospital Invoice', copy=False, ondelete='restrict')
    hospital_receipt_clearing_line_id = fields.Many2one('account.move.line', string='Receipt Clearing Item', copy=False, ondelete='restrict')
    hospital_bank_reconciliation_move_id = fields.Many2one('account.move', string='Hospital Bank Reconciliation Entry', copy=False, readonly=True, ondelete='restrict')
    hospital_expected_amount = fields.Monetary(string='Expected Hospital Amount', currency_field='company_currency_id', copy=False)
    hospital_bank_amount = fields.Monetary(string='Statement Bank Amount', currency_field='company_currency_id', copy=False)
    hospital_difference_amount = fields.Monetary(string='Hospital Difference', currency_field='company_currency_id', copy=False)
    company_currency_id = fields.Many2one('res.currency', related='company_id.currency_id', readonly=True)
    hospital_reconciliation_outcome = fields.Selection([
        ('none', 'None'),
        ('suggested', 'Suggested'),
        ('matched', 'Matched'),
        ('reconciled', 'Reconciled'),
        ('variance', 'Variance'),
        ('unmatched', 'Unmatched'),
    ], string='Hospital Outcome', default='none', copy=False, index=True)

    _sql_constraints = [
        ('hospital_bank_import_fingerprint_unique', 'unique(company_id, bank_journal_id, import_fingerprint)', 'This bank statement transaction was already imported for this company and journal.'),
    ]

    @api.depends('company_id', 'bank_journal_id', 'transaction_date', 'reference', 'narration', 'bank_amount', 'amount', 'debit', 'credit')
    def _compute_import_fingerprint(self):
        for line in self:
            raw = '|'.join([
                str(line.company_id.id or ''), str(line.bank_journal_id.id or ''), str(line.transaction_date or ''),
                (line.reference or '').strip().upper(), (line.narration or '').strip().upper(),
                '%.2f' % (line.bank_amount or 0.0), '%.2f' % (line.amount or 0.0),
                '%.2f' % (line.debit or 0.0), '%.2f' % (line.credit or 0.0),
            ])
            line.import_fingerprint = hashlib.sha256(raw.encode('utf-8')).hexdigest()

    @api.constrains('company_id', 'bank_journal_id', 'reference')
    def _check_duplicate_bank_reference(self):
        for line in self:
            ref = (line.reference or '').strip()
            if not ref or not line.bank_journal_id or not line.company_id:
                continue
            duplicate = self.search([
                ('id', '!=', line.id), ('company_id', '=', line.company_id.id),
                ('bank_journal_id', '=', line.bank_journal_id.id), ('reference', '=', ref),
            ], limit=1)
            if duplicate:
                raise ValidationError(_('Bank reference %s is already imported for this company and bank journal.') % ref)

    def _hospital_candidate_domain(self, bank_amt):
        self.ensure_one()
        return [
            ('payment_method', '=', BANK_METHOD),
            ('bank_reconciliation_required', '=', True),
            ('bank_reconciliation_state', 'in', ('awaiting_statement', 'suggested', 'matched')),
            ('state', '=', 'confirmed'),
            ('company_id', '=', self.company_id.id),
            ('currency_id', '=', self.company_id.currency_id.id),
            ('expected_bank_journal_id', '=', self.bank_journal_id.id),
            ('accounting_move_id.state', '=', 'posted'),
        ]

    def _match_hospital_bank_receipt(self):
        self.ensure_one()
        if not self.bank_journal_id or self.bank_journal_id.type != 'bank':
            return False
        bank_amt = abs(self.bank_amount or self.amount or 0.0)
        if float_is_zero(bank_amt, precision_rounding=self.company_id.currency_id.rounding):
            return False
        ref = (self.reference or '').strip()
        domain = self._hospital_candidate_domain(bank_amt)
        Receipt = self.env['hospital.charge.receipt'].sudo()
        candidates = Receipt.browse()
        if ref:
            candidates = Receipt.search(domain + ['|', '|', '|', '|',
                ('bank_transaction_reference', '=', ref), ('payment_reference', '=', ref),
                ('external_transaction_reference', '=', ref), ('fiscal_receipt_number', '=', ref), ('name', '=', ref),
            ])
        if not candidates:
            narration = '%s %s' % (self.narration or '', self.reference or '')
            tokens = [token for token in narration.replace('/', ' ').replace('-', ' ').split() if token]
            refs = [token for token in tokens if token.startswith(('ACC', 'ENC', 'RCPT'))]
            if refs:
                candidates = Receipt.search(domain + ['|', '|', ('name', 'in', refs), ('billing_account_id.name', 'in', refs), ('encounter_id.name', 'in', refs)])
        if not candidates:
            candidates = Receipt.search(domain + [('amount', '=', abs(bank_amt))], limit=2)
            if len(candidates) == 1:
                self.write({
                    'hospital_receipt_id': candidates.id,
                    'hospital_receipt_clearing_line_id': candidates.bank_clearing_move_line_id.id,
                    'partner_id': candidates.patient_id.accounting_partner_id.commercial_partner_id.id,
                    'hospital_expected_amount': candidates.amount,
                    'hospital_bank_amount': bank_amt,
                    'hospital_difference_amount': bank_amt - candidates.amount,
                    'confidence_score': 55,
                    'hospital_reconciliation_outcome': 'suggested',
                    'match_status': 'suggested',
                    'exception_reason': False,
                })
                return True
            if len(candidates) > 1:
                self.write({'match_status': 'exception', 'hospital_reconciliation_outcome': 'unmatched', 'exception_reason': _('Ambiguous Bank receipt candidates; accountant selection required.')})
                return True
            return False
        if len(candidates) > 1:
            self.write({'match_status': 'exception', 'hospital_reconciliation_outcome': 'unmatched', 'exception_reason': _('Ambiguous Bank receipt candidates; accountant selection required.')})
            return True
        receipt = candidates[0]
        diff = self.company_id.currency_id.round(bank_amt - receipt.amount)
        if not float_is_zero(diff, precision_rounding=self.company_id.currency_id.rounding):
            outcome = 'variance'
            status = 'exception'
            reason = _('Bank amount differs from expected hospital Bank receipt amount; variance requires accountant review.')
            receipt.sudo().with_context(**{CONTROLLED_CTX: True}).write({'bank_reconciliation_state': 'variance', 'matched_statement_line_id': self.id, 'bank_reconciliation_difference': diff})
        else:
            outcome = 'matched'
            status = 'matched'
            reason = False
            receipt.sudo().with_context(**{CONTROLLED_CTX: True}).write({'bank_reconciliation_state': 'matched', 'matched_statement_line_id': self.id, 'bank_reconciliation_difference': 0.0})
        self.write({
            'hospital_receipt_id': receipt.id,
            'hospital_receipt_clearing_line_id': receipt.bank_clearing_move_line_id.id,
            'hospital_invoice_id': receipt.billing_account_id.invoice_batch_ids.mapped('invoice_id').filtered(lambda inv: inv.state == 'posted')[:1].id,
            'matched_invoice_id': False,
            'partner_id': receipt.patient_id.accounting_partner_id.commercial_partner_id.id,
            'invoice_amount': 0.0,
            'wht_detected': False,
            'wht_amount': 0.0,
            'hospital_expected_amount': receipt.amount,
            'hospital_bank_amount': bank_amt,
            'hospital_difference_amount': diff,
            'confidence_score': 100 if ref else 55,
            'hospital_reconciliation_outcome': outcome,
            'match_status': status,
            'exception_reason': reason,
        })
        return True

    def action_validate_line(self):
        self.ensure_one()
        if self.hospital_receipt_id:
            return self._validate_hospital_bank_reconciliation()
        return super().action_validate_line()

    def _validate_hospital_bank_reconciliation(self):
        self.ensure_one()
        assert_invoice_authorized(self.env, 'validate hospital bank reconciliation')
        if self.is_locked and self.hospital_bank_reconciliation_move_id:
            return True
        if self.match_status not in ('matched', 'suggested'):
            raise UserError(_('Only matched hospital Bank receipt lines can be validated.'))
        receipt = self.hospital_receipt_id.sudo()
        if receipt.payment_method != BANK_METHOD or not receipt.bank_reconciliation_required:
            raise UserError(_('Only Bank-classified hospital receipts can be reconciled from bank statements.'))
        if receipt.company_id != self.company_id:
            raise UserError(_('Statement company differs from the hospital receipt company.'))
        if receipt.currency_id != self.company_id.currency_id:
            raise UserError(_('Statement currency differs from the hospital receipt currency.'))
        if receipt.expected_bank_journal_id != self.bank_journal_id:
            raise UserError(_('Statement bank journal differs from the expected receipt bank.'))
        bank_amt = abs(self.bank_amount or self.amount or 0.0)
        diff = receipt.currency_id.round(bank_amt - receipt.amount)
        if not float_is_zero(diff, precision_rounding=receipt.currency_id.rounding):
            receipt.with_context(**{CONTROLLED_CTX: True}).write({'bank_reconciliation_state': 'variance', 'bank_reconciliation_difference': diff, 'matched_statement_line_id': self.id})
            self.sudo().write({'hospital_reconciliation_outcome': 'variance', 'match_status': 'exception', 'exception_reason': _('Statement amount variance requires manual review.'), 'hospital_difference_amount': diff})
            return True
        if receipt.bank_reconciled_move_id:
            self.sudo().write({'hospital_bank_reconciliation_move_id': receipt.bank_reconciled_move_id.id, 'posted_payment_move_id': receipt.bank_reconciled_move_id.id, 'match_status': 'validated', 'hospital_reconciliation_outcome': 'reconciled', 'is_locked': True})
            return True
        clearing_line = receipt.bank_clearing_move_line_id
        if not clearing_line or clearing_line.reconciled:
            raise UserError(_('Receipt %s has no open bank-clearing line to reconcile.') % receipt.name)
        journal = self.bank_journal_id
        bank_account = journal.default_account_id
        if not bank_account:
            raise UserError(_('Bank journal %s has no default account.') % journal.display_name)
        with self.env.cr.savepoint(flush=True):
            self.env.cr.execute('SELECT id FROM hospital_charge_receipt WHERE id = %s FOR UPDATE', [receipt.id])
            self.env.cr.execute('SELECT id FROM walnut_bank_statement_line WHERE id = %s FOR UPDATE', [self.id])
            move = self.env['account.move'].sudo().create({
                'move_type': 'entry',
                'journal_id': journal.id,
                'date': self.transaction_date or fields.Date.context_today(self),
                'company_id': self.company_id.id,
                'currency_id': receipt.currency_id.id,
                'ref': _('Bank Statement Confirmation - %s') % (self.reference or receipt.name),
                'line_ids': [
                    (0, 0, {'name': self.narration or self.reference or receipt.name, 'account_id': bank_account.id, 'partner_id': receipt.patient_id.accounting_partner_id.commercial_partner_id.id, 'debit': receipt.amount, 'credit': 0.0}),
                    (0, 0, {'name': _('Clear bank receipt - %s') % receipt.name, 'account_id': clearing_line.account_id.id, 'partner_id': clearing_line.partner_id.id, 'debit': 0.0, 'credit': receipt.amount}),
                ],
            })
            move.action_post()
            clearing_credit = move.line_ids.filtered(lambda line: line.account_id == clearing_line.account_id and line.credit > AMOUNT_TOLERANCE)
            (clearing_line + clearing_credit).reconcile()
            receipt.with_context(**{CONTROLLED_CTX: True}).write({
                'bank_reconciliation_state': 'reconciled',
                'matched_statement_line_id': self.id,
                'bank_reconciled_move_id': move.id,
                'bank_reconciled_at': fields.Datetime.now(),
                'bank_reconciled_by': self.env.user.id,
                'bank_transaction_reference': receipt.bank_transaction_reference or self.reference,
                'bank_transaction_date': self.transaction_date,
                'bank_reconciliation_difference': 0.0,
            })
            self.sudo().write({'hospital_bank_reconciliation_move_id': move.id, 'posted_payment_move_id': move.id, 'hospital_reconciliation_outcome': 'reconciled', 'match_status': 'validated', 'is_locked': True, 'hospital_difference_amount': 0.0})
        return True


class WalnutBankStatementImportHospitalBridge(models.Model):
    _inherit = 'walnut.bank.statement.import'

    def _match_single_line(self, line):
        if line._match_hospital_bank_receipt():
            return
        return super()._match_single_line(line)

    def _post_and_reconcile_line(self, line):
        if line.hospital_receipt_id:
            return line._validate_hospital_bank_reconciliation()
        return super()._post_and_reconcile_line(line)

class HospitalBillingAccountBankBridge(models.Model):
    _inherit = 'hospital.billing.account'

    bank_receipts_awaiting_statement = fields.Integer(string='Bank Receipts Awaiting Statement', compute='_compute_bank_reconciliation_indicators')
    bank_receipts_reconciled = fields.Integer(string='Bank Receipts Reconciled', compute='_compute_bank_reconciliation_indicators')
    bank_receipt_variances = fields.Integer(string='Bank Variances', compute='_compute_bank_reconciliation_indicators')

    def _compute_bank_reconciliation_indicators(self):
        for account in self:
            receipts = account.receipt_ids.filtered(lambda receipt: receipt.bank_reconciliation_required)
            account.bank_receipts_awaiting_statement = len(receipts.filtered(lambda receipt: receipt.bank_reconciliation_state in ('awaiting_statement', 'suggested', 'matched')))
            account.bank_receipts_reconciled = len(receipts.filtered(lambda receipt: receipt.bank_reconciliation_state == 'reconciled'))
            account.bank_receipt_variances = len(receipts.filtered(lambda receipt: receipt.bank_reconciliation_state == 'variance'))

    def action_view_bank_reconciliation_lines(self):
        self.ensure_one()
        lines = self.env['walnut.bank.statement.line'].sudo().search([('hospital_billing_account_id', '=', self.id)])
        return {
            'type': 'ir.actions.act_window',
            'name': _('Bank Reconciliation Lines'),
            'res_model': 'walnut.bank.statement.line',
            'view_mode': 'list,form',
            'domain': [('id', 'in', lines.ids)],
            'context': {'default_hospital_billing_account_id': self.id},
        }


class HospitalChargeReceiptBankBridgeActions(models.Model):
    _inherit = 'hospital.charge.receipt'

    def action_open_bank_statement_line(self):
        self.ensure_one()
        if not self.matched_statement_line_id:
            raise UserError(_('No bank statement line is linked to this receipt.'))
        return {
            'type': 'ir.actions.act_window',
            'name': _('Bank Statement Line'),
            'res_model': 'walnut.bank.statement.line',
            'view_mode': 'form',
            'res_id': self.matched_statement_line_id.id,
        }

    def action_open_bank_reconciliation_entry(self):
        self.ensure_one()
        if not self.bank_reconciled_move_id:
            raise UserError(_('No bank reconciliation entry is linked to this receipt.'))
        return {
            'type': 'ir.actions.act_window',
            'name': _('Bank Reconciliation Entry'),
            'res_model': 'account.move',
            'view_mode': 'form',
            'res_id': self.bank_reconciled_move_id.id,
        }
