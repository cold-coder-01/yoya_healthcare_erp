"""Task 32B-1: create explicit patient-advance chart/journal foundations.

No operational receipt is posted and no account.move is created. The migration
only creates named configuration masters after collision checks and maps every
existing source configuration for the same company to those masters.
"""

import logging

from odoo import SUPERUSER_ID, api
from odoo.exceptions import ValidationError

_logger = logging.getLogger(__name__)


def _bind_xmlid(env, name, record):
    data = env["ir.model.data"].search(
        [("module", "=", "hospital_billing_accounting"), ("name", "=", name)],
        limit=1,
    )
    if data:
        if data.model != record._name or data.res_id != record.id:
            raise ValidationError(f"XML ID hospital_billing_accounting.{name} is occupied.")
        return
    env["ir.model.data"].create(
        {
            "module": "hospital_billing_accounting",
            "name": name,
            "model": record._name,
            "res_id": record.id,
            "noupdate": True,
        }
    )


def _ensure_account(env, company, code, name):
    Account = env["account.account"].with_company(company)
    account = Account.search([("code", "=", code)], limit=1)
    if account:
        if account.name != name or account.account_type != "liability_current":
            raise ValidationError(
                f"Account code {code} already exists as {account.display_name}; "
                f"expected '{name}' with type Current Liabilities."
            )
        if not account.reconcile:
            account.reconcile = True
    else:
        account = Account.create(
            {
                "code": code,
                "name": name,
                "account_type": "liability_current",
                "reconcile": True,
            }
        )
    _bind_xmlid(env, f"account_{code}_company_{company.id}", account)
    return account


def _ensure_journal(env, company, code, name):
    Journal = env["account.journal"].with_company(company)
    journal = Journal.search([("company_id", "=", company.id), ("code", "=", code)], limit=1)
    if journal:
        if journal.name != name or journal.type != "general":
            raise ValidationError(
                f"Journal code {code} is occupied by {journal.display_name}; "
                f"expected '{name}' with type Miscellaneous."
            )
    else:
        journal = Journal.create(
            {"name": name, "code": code, "type": "general", "company_id": company.id}
        )
    _bind_xmlid(env, f"journal_{code.lower()}_company_{company.id}", journal)
    return journal


def _shared_method_account(configs, field_name, label):
    accounts = configs.mapped(field_name)
    if len(accounts) != 1:
        raise ValidationError(
            f"Task 32B-1 requires one explicit company-wide {label}; found "
            f"{len(accounts)} configured values. Resolve this before upgrading."
        )
    return accounts


def migrate(cr, version):
    if not version:
        return
    env = api.Environment(cr, SUPERUSER_ID, {})
    Config = env["hospital.billing.accounting.config"].with_context(active_test=False)
    companies = Config.search([]).mapped("company_id")
    for company in companies:
        configs = Config.search([("company_id", "=", company.id)])
        active_configs = configs.filtered("active")
        cash = _shared_method_account(active_configs, "cash_account_id", "cash account")
        bank = _shared_method_account(active_configs, "bank_account_id", "bank/card account")
        mobile = _shared_method_account(
            active_configs, "mobile_money_account_id", "mobile-money account"
        )
        advance = _ensure_account(env, company, "305410", "Patient Advances")
        credit = _ensure_account(
            env, company, "305420", "Patient Credits and Refundable Advances"
        )
        advance_journal = _ensure_journal(env, company, "PADV", "Patient Advances")
        refund_journal = _ensure_journal(env, company, "PREF", "Patient Advance Refunds")
        configs.write(
            {
                "cash_account_id": cash.id,
                "bank_account_id": bank.id,
                "mobile_money_account_id": mobile.id,
                "patient_advance_liability_account_id": advance.id,
                "patient_credit_liability_account_id": credit.id,
                "advance_receipt_journal_id": advance_journal.id,
                "advance_application_journal_id": advance_journal.id,
                "advance_refund_journal_id": refund_journal.id,
            }
        )
        for config in configs:
            config._assert_advance_configuration()
        _logger.info(
            "Task 32B-1 configured company %s: 305410/305420, PADV/PREF; no moves posted.",
            company.display_name,
        )
