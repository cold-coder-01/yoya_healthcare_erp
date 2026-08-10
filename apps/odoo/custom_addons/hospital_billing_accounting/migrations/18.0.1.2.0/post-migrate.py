"""Task 32B-2 deterministic invoice configuration.

Creates only accounting masters: one standard customer-invoice journal and one
explicit service product per configured hospital billing service. It maps the
existing Ethiopian sale taxes by their established invoice labels. No invoice,
payment, receipt entry, reconciliation, fiscal record or stock record is made.
"""

import logging

from odoo import SUPERUSER_ID, Command, api
from odoo.exceptions import ValidationError

_logger = logging.getLogger(__name__)

EXPECTED_TAX_LABELS = {
    "standard": "tax03",
    "zero_rated": "tax04",
    "exempt": "tax06",
    "out_of_scope": "tax11",
}


def _bind_xmlid(env, name, record):
    data = env["ir.model.data"].search(
        [("module", "=", "hospital_billing_accounting"), ("name", "=", name)],
        limit=1,
    )
    if data:
        if data.model != record._name or data.res_id != record.id:
            raise ValidationError(
                "XML ID hospital_billing_accounting.%s is occupied." % name
            )
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


def _ensure_invoice_journal(env, company):
    Journal = env["account.journal"].with_company(company)
    journals = Journal.search(
        [("company_id", "=", company.id), ("code", "=", "HINV")], limit=2
    )
    if len(journals) > 1:
        raise ValidationError("More than one HINV journal exists for %s." % company.name)
    journal = journals[:1]
    if journal:
        if journal.name != "Hospital Customer Invoices" or journal.type != "sale":
            raise ValidationError(
                "Journal code HINV is occupied by %s; expected the Sales journal "
                "'Hospital Customer Invoices'." % journal.display_name
            )
    else:
        journal = Journal.create(
            {
                "name": "Hospital Customer Invoices",
                "code": "HINV",
                "type": "sale",
                "company_id": company.id,
            }
        )
    _bind_xmlid(env, "journal_hinv_company_%s" % company.id, journal)
    return journal


def _source_config(env, company, source_type):
    configs = env["hospital.billing.accounting.config"].search(
        [
            ("company_id", "=", company.id),
            ("source_type", "=", source_type or "other"),
            ("active", "=", True),
        ],
        limit=2,
    )
    if len(configs) != 1:
        raise ValidationError(
            "Exactly one active %s accounting mapping is required for %s; found %s."
            % (source_type, company.name, len(configs))
        )
    configs._assert_invoice_configuration()
    return configs


def _tax_for_service(env, service):
    expected_label = EXPECTED_TAX_LABELS[service.tax_treatment]
    taxes = env["account.tax"].search(
        [
            ("company_id", "=", service.company_id.id),
            ("type_tax_use", "=", "sale"),
            ("active", "=", True),
            ("invoice_label", "=", expected_label),
        ],
        limit=2,
    )
    if len(taxes) != 1:
        raise ValidationError(
            "Service %s requires exactly one active sale tax with invoice label %s; "
            "found %s." % (service.display_name, expected_label, len(taxes))
        )
    tax = taxes
    if service.tax_treatment == "standard":
        if abs(tax.amount - service.tax_rate) > 0.0001:
            raise ValidationError(
                "Service %s tax snapshot %.4f%% differs from %s at %.4f%%."
                % (service.display_name, service.tax_rate, tax.display_name, tax.amount)
            )
    elif abs(tax.amount) > 0.0001 or abs(service.tax_rate) > 0.0001:
        raise ValidationError(
            "Service %s is %s but carries a non-zero tax rate."
            % (service.display_name, service.tax_treatment)
        )
    return tax


def _ensure_service_product(env, service, config, tax):
    Product = env["product.product"].with_company(service.company_id)
    code = "HOSP-%s" % (service.code or "SVC-%s" % service.id)
    name = "Hospital %s" % service.name
    products = Product.search([("default_code", "=", code)], limit=2)
    if len(products) > 1:
        raise ValidationError("Product code %s is duplicated." % code)
    product = products[:1]
    if product:
        if (
            product.name != name
            or product.type != "service"
            or (product.company_id and product.company_id != service.company_id)
        ):
            raise ValidationError(
                "Product code %s is occupied by %s; expected service product '%s'."
                % (code, product.display_name, name)
            )
    else:
        uom = service.uom_id or env.ref("uom.product_uom_unit")
        product = Product.create(
            {
                "name": name,
                "default_code": code,
                "type": "service",
                "sale_ok": True,
                "purchase_ok": False,
                "list_price": service.default_price,
                "company_id": service.company_id.id,
                "uom_id": uom.id,
                "uom_po_id": uom.id,
                "property_account_income_id": config.revenue_account_id.id,
                "taxes_id": [Command.set(tax.ids)],
            }
        )
    product.product_tmpl_id.write(
        {
            "property_account_income_id": config.revenue_account_id.id,
            "taxes_id": [Command.set(tax.ids)],
        }
    )
    _bind_xmlid(
        env,
        "product_service_%s_company_%s" % (service.id, service.company_id.id),
        product,
    )
    return product


def migrate(cr, version):
    if not version:
        return
    env = api.Environment(cr, SUPERUSER_ID, {})
    Config = env["hospital.billing.accounting.config"].with_context(active_test=False)
    Service = env["hospital.billing.service"].with_context(active_test=False)
    companies = Config.search([]).mapped("company_id")
    for company in companies:
        journal = _ensure_invoice_journal(env, company)
        configs = Config.search([("company_id", "=", company.id)])
        configs.write({"invoice_journal_id": journal.id})
        services = Service.search([("company_id", "=", company.id)])
        for service in services:
            config = _source_config(env, company, service.service_type)
            tax = _tax_for_service(env, service)
            product = _ensure_service_product(env, service, config, tax)
            service.write(
                {
                    "invoice_product_id": product.id,
                    "invoice_tax_ids": [Command.set(tax.ids)],
                }
            )
        _logger.info(
            "Task 32B-2 configured %s: HINV and %s explicit service products; "
            "no business transaction created.",
            company.display_name,
            len(services),
        )
