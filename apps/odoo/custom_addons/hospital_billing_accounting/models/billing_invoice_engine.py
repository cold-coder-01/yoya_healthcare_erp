import json
import uuid
from collections import OrderedDict

from odoo import Command, api, fields, models
from odoo.exceptions import UserError, ValidationError
from odoo.tools import float_compare

from .invoice_batch import QTY_TOLERANCE, assert_invoice_authorized


EXPECTED_TAX_LABELS = {
    "standard": "tax03",
    "zero_rated": "tax04",
    "exempt": "tax06",
    "out_of_scope": "tax11",
}


class HospitalBillingInvoiceEngine(models.AbstractModel):
    _inherit = "hospital.billing.engine"

    @api.model
    def _lock_invoice_token(self, company, token):
        self.env.cr.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
            ["hospital.invoice.batch:%s:%s" % (company.id, token)],
        )

    @api.model
    def _lock_billing_scope(self, account, charges):
        self.env.cr.execute(
            "SELECT id FROM hospital_billing_account WHERE id = %s FOR UPDATE",
            [account.id],
        )
        if charges:
            self.env.cr.execute(
                "SELECT id FROM hospital_charge_line WHERE id IN %s "
                "ORDER BY id FOR UPDATE",
                [tuple(sorted(charges.ids))],
            )
        account.invalidate_recordset()
        charges.invalidate_recordset()

    @api.model
    def _resolve_invoice_partner(self, account):
        if account.payer_type == "self_pay":
            partner = account.patient_id.accounting_partner_id
            label = "patient accounting customer"
        else:
            partner = account.payer_id
            label = "commercial payer"
        if not partner:
            raise UserError(
                "No %s is configured for patient %s. Invoice generation is blocked."
                % (label, account.patient_id.display_name)
            )
        partner = partner.commercial_partner_id
        if partner.company_id and partner.company_id != account.company_id:
            raise UserError("The invoice customer belongs to another company.")
        return partner

    @api.model
    def _source_config(self, charge):
        source_type = charge.service_id.service_type or "other"
        configs = self.env["hospital.billing.accounting.config"].search(
            [
                ("company_id", "=", charge.company_id.id),
                ("source_type", "=", source_type),
                ("active", "=", True),
            ],
            limit=2,
        )
        if len(configs) != 1:
            raise UserError(
                "Exactly one active accounting mapping is required for service type "
                "'%s'; found %s." % (source_type, len(configs))
            )
        configs._assert_invoice_configuration()
        return configs

    @api.model
    def _validate_tax_mapping(self, charge, taxes):
        service = charge.service_id
        if not taxes:
            pharmacy_zero_tax = (
                service.service_type == "pharmacy"
                and charge.tax_treatment in ("exempt", "out_of_scope", "zero_rated")
                and abs(charge.tax_rate or 0.0) <= 0.0001
            )
            if pharmacy_zero_tax:
                return True
            raise UserError(
                "Billing service %s has no authoritative Odoo sale tax mapping."
                % service.display_name
            )
        if any(
            not tax.active
            or tax.type_tax_use != "sale"
            or tax.company_id != charge.company_id
            or tax.amount < 0
            for tax in taxes
        ):
            raise UserError(
                "Billing service %s has an inactive, non-sale, cross-company or "
                "withholding tax mapping." % service.display_name
            )
        expected_label = EXPECTED_TAX_LABELS[charge.tax_treatment]
        labels = {tax.invoice_label for tax in taxes}
        if expected_label not in labels:
            raise UserError(
                "Charge %s snapshots tax treatment '%s', but its mapped Odoo taxes "
                "do not contain Ethiopian tax category %s."
                % (charge.display_name, charge.tax_treatment, expected_label)
            )
        positive_rates = [tax.amount for tax in taxes if tax.amount > 0]
        if charge.tax_treatment == "standard":
            if (
                len(positive_rates) != 1
                or abs(positive_rates[0] - charge.tax_rate) > 0.0001
            ):
                raise UserError(
                    "Charge %s tax snapshot %.4f%% does not match its authoritative "
                    "Odoo VAT mapping." % (charge.display_name, charge.tax_rate)
                )
        elif positive_rates or abs(charge.tax_rate) > 0.0001:
            raise UserError(
                "Charge %s is %s but carries a positive tax rate or tax mapping."
                % (charge.display_name, charge.tax_treatment)
            )
        return True

    @api.model
    def _prepare_charge_invoice_spec(self, charge, partner):
        if charge.charge_state != "active":
            raise UserError("Charge %s is not active." % charge.display_name)
        if charge.authorization_state in ("rejected", "bypassed"):
            raise UserError("Charge %s is not authorized for invoicing." % charge.display_name)
        if charge.qty_delivered <= QTY_TOLERANCE:
            raise UserError("Charge %s has no delivered quantity." % charge.display_name)
        quantity = charge.qty_invoice_eligible
        if quantity <= QTY_TOLERANCE:
            raise UserError("Charge %s has no uninvoiced delivered quantity." % charge.display_name)
        service = charge.service_id
        if not service or not service.active:
            raise UserError(
                "Charge %s has no active billing service mapping." % charge.display_name
            )
        if service.company_id and service.company_id != charge.company_id:
            raise UserError("Charge %s service belongs to another company." % charge.display_name)
        if service.currency_id and service.currency_id != charge.currency_id:
            raise UserError("Charge %s service currency differs from the encounter." % charge.display_name)
        product = service.invoice_product_id
        if not product and service.service_type != "pharmacy":
            raise UserError(
                "Billing service %s requires one active saleable Invoice Product."
                % service.display_name
            )
        if product and (not product.active or not product.sale_ok):
            raise UserError(
                "Billing service %s requires one active saleable Invoice Product."
                % service.display_name
            )
        if product and product.company_id and product.company_id != charge.company_id:
            raise UserError("Charge %s invoice product belongs to another company." % charge.display_name)
        config = self._source_config(charge)
        taxes = service.invoice_tax_ids
        self._validate_tax_mapping(charge, taxes)
        if partner.property_account_receivable_id != config.receivable_account_id:
            raise UserError(
                "Customer %s uses receivable account %s, but %s requires %s. "
                "Resolve the customer/account mapping before invoicing."
                % (
                    partner.display_name,
                    partner.property_account_receivable_id.display_name,
                    config.display_name,
                    config.receivable_account_id.display_name,
                )
            )
        if product:
            product_income = product._get_product_accounts()["income"]
            if product_income and product_income != config.revenue_account_id:
                raise UserError(
                    "Invoice product %s resolves to income account %s, but the hospital "
                    "mapping requires %s."
                    % (
                        product.display_name,
                        product_income.display_name,
                        config.revenue_account_id.display_name,
                    )
                )
        uom = charge.uom_id or (product.uom_id if product else False)
        if product and uom and product.uom_id and uom.category_id != product.uom_id.category_id:
            raise UserError("Charge %s uses an incompatible unit of measure." % charge.display_name)
        analytic = charge.invoice_analytic_distribution or {}
        tax_fingerprint = json.dumps(
            [
                {
                    "id": tax.id,
                    "amount": tax.amount,
                    "label": tax.invoice_label,
                    "include": tax.price_include,
                }
                for tax in taxes.sorted("id")
            ],
            sort_keys=True,
            separators=(",", ":"),
        )
        group_key = (
            product.id if product else 0,
            charge.description or service.name,
            uom.id if uom else 0,
            round(charge.unit_price, 6),
            round(charge.discount or 0.0, 6),
            tuple(taxes.sorted("id").ids),
            config.revenue_account_id.id,
            json.dumps(analytic, sort_keys=True, separators=(",", ":")),
            charge.company_id.id,
            charge.currency_id.id,
        )
        return {
            "charge": charge,
            "quantity": quantity,
            "service": service,
            "product": product,
            "uom": uom,
            "taxes": taxes,
            "config": config,
            "analytic": analytic,
            "tax_fingerprint": tax_fingerprint,
            "group_key": group_key,
        }

    @api.model
    def _validate_scope(self, account, charges):
        account.ensure_one()
        if account.state in ("closed", "cancelled"):
            raise UserError("Billing account %s is %s." % (account.display_name, account.state))
        if not account.encounter_id or not account.patient_id:
            raise UserError("The billing account requires an encounter and patient.")
        if account.company_id != account.encounter_id.company_id:
            raise UserError("Encounter and billing-account companies differ.")
        if account.currency_id != account.encounter_id.currency_id:
            raise UserError("Encounter and billing-account currencies differ.")
        foreign = charges.filtered(
            lambda charge: charge.billing_account_id != account
            or charge.patient_id != account.patient_id
            or charge.company_id != account.company_id
            or charge.currency_id != account.currency_id
        )
        if foreign:
            raise UserError(
                "Invoice requests cannot mix patients, encounters, companies or currencies."
            )
        return True

    @api.model
    def _sanitize_batch_error(self, exc):
        text = str(exc).replace("\n", " ").strip()
        return ("%s: %s" % (type(exc).__name__, text))[:240]

    @api.model
    def create_invoice(self, billing_account, charges=None, request_token=None):
        assert_invoice_authorized(self.env, "generate delivered-charge invoices")
        account = billing_account
        if isinstance(account, int):
            account = self.env["hospital.billing.account"].browse(account)
        account.ensure_one()
        token = (request_token or uuid.uuid4().hex).strip()
        if not token:
            raise UserError("Invoice request token cannot be empty.")

        requested = (
            account.charge_line_ids
            if charges is None
            else self.env["hospital.charge.line"].browse(charges)
            if isinstance(charges, (list, tuple))
            else charges
        )
        requested = requested.exists()
        self._validate_scope(account, requested)
        partner = self._resolve_invoice_partner(account)

        self._lock_invoice_token(account.company_id, token)
        Batch = self.env["hospital.invoice.batch"].with_context(active_test=False)
        batch = Batch.search(
            [("company_id", "=", account.company_id.id), ("request_token", "=", token)],
            limit=1,
        )
        if batch:
            if (
                batch.billing_account_id != account
                or batch.patient_id != account.patient_id
                or batch.commercial_partner_id != partner
            ):
                raise UserError("The retry token belongs to another invoice request.")
            if batch.state == "cancelled":
                raise UserError("The invoice batch is cancelled and cannot be retried.")
            if batch.invoice_id:
                return batch.invoice_id.with_user(self.env.user)

        self._lock_billing_scope(account, requested)
        eligible = requested.filtered(
            lambda charge: charge.charge_state == "active"
            and charge.qty_invoice_eligible > QTY_TOLERANCE
        )
        if not eligible:
            raise UserError("No delivered, uninvoiced charge quantity is eligible.")

        specs = [self._prepare_charge_invoice_spec(charge, partner) for charge in eligible]
        configs = self.env["hospital.billing.accounting.config"].browse(
            [spec["config"].id for spec in specs]
        )
        journals = configs.mapped("invoice_journal_id")
        receivables = configs.mapped("receivable_account_id")
        if len(journals) != 1 or len(receivables) != 1:
            raise UserError(
                "All charges on one encounter invoice must use one invoice journal "
                "and one patient receivable account."
            )

        if not batch:
            batch = Batch.sudo().create(
                {
                    "request_token": token,
                    "encounter_id": account.encounter_id.id,
                    "billing_account_id": account.id,
                    "patient_id": account.patient_id.id,
                    "commercial_partner_id": partner.id,
                    "company_id": account.company_id.id,
                    "currency_id": account.currency_id.id,
                    "requested_by_id": self.env.user.id,
                    "state": "draft",
                }
            )
        elif batch.state == "failed":
            batch.sudo().write({"retry_count": batch.retry_count + 1})
        batch.sudo().write({"state": "processing", "error_summary": False})

        try:
            with self.env.cr.savepoint(flush=True):
                groups = OrderedDict()
                for spec in specs:
                    groups.setdefault(spec["group_key"], []).append(spec)
                line_commands = []
                grouped_specs = []
                for sequence, group in enumerate(groups.values(), start=1):
                    first = group[0]
                    quantity = sum(spec["quantity"] for spec in group)
                    line_commands.append(
                        Command.create(
                            {
                                "sequence": sequence * 10,
                                "product_id": first["product"].id if first["product"] else False,
                                "name": first["charge"].description
                                or first["service"].name,
                                "quantity": quantity,
                                "product_uom_id": first["uom"].id if first["uom"] else False,
                                "price_unit": first["charge"].unit_price,
                                "discount": first["charge"].discount,
                                "tax_ids": [(6, 0, first["taxes"].ids)],
                                "account_id": first["config"].revenue_account_id.id,
                                "analytic_distribution": first["analytic"] or False,
                            }
                        )
                    )
                    grouped_specs.append(group)

                invoice = self.env["account.move"].sudo().create(
                    {
                        "move_type": "out_invoice",
                        "partner_id": partner.id,
                        "company_id": account.company_id.id,
                        "currency_id": account.currency_id.id,
                        "journal_id": journals.id,
                        "invoice_date": fields.Date.context_today(self),
                        "ref": "%s / %s" % (account.encounter_id.name, batch.name),
                        "hospital_managed_invoice": True,
                        "hospital_invoice_batch_id": batch.id,
                        "hospital_encounter_id": account.encounter_id.id,
                        "hospital_billing_account_id": account.id,
                        "hospital_patient_id": account.patient_id.id,
                        "invoice_line_ids": line_commands,
                    }
                )
                invoice_lines = invoice.invoice_line_ids.sorted(
                    key=lambda line: (line.sequence, line.id)
                )
                if len(invoice_lines) != len(grouped_specs):
                    raise ValidationError("Invoice grouping produced an unexpected line count.")

                allocation_vals = []
                for line, group in zip(invoice_lines, grouped_specs):
                    for spec in group:
                        charge = spec["charge"]
                        quantity = spec["quantity"]
                        allocation_vals.append(
                            {
                                "charge_id": charge.id,
                                "batch_id": batch.id,
                                "move_id": invoice.id,
                                "move_line_id": line.id,
                                "allocation_type": "invoice",
                                "quantity": quantity,
                                "unit_price_snapshot": charge.unit_price,
                                "discount_snapshot": charge.discount,
                                "amount_untaxed_snapshot": quantity
                                * charge.unit_price
                                * (1.0 - (charge.discount or 0.0) / 100.0),
                                "tax_fingerprint": spec["tax_fingerprint"],
                                "product_id": spec["product"].id if spec["product"] else False,
                                "uom_id": spec["uom"].id if spec["uom"] else False,
                                "income_account_id": spec["config"].revenue_account_id.id,
                                "analytic_distribution_snapshot": spec["analytic"] or False,
                                "company_id": account.company_id.id,
                                "currency_id": account.currency_id.id,
                                "idempotency_key": "%s:invoice:%s" % (token, charge.id),
                                "source_model": charge.source_model,
                                "source_res_id": charge.source_res_id,
                                "source_line_id": charge.source_line_id,
                                "source_event": charge.source_event,
                                "source_key": charge.source_key,
                            }
                        )
                allocations = self.env[
                    "hospital.charge.invoice.allocation"
                ].sudo().create(allocation_vals)
                if self.env.context.get("hospital_test_fail_after_move"):
                    raise RuntimeError("Simulated failure after invoice construction")
                for charge in eligible:
                    reusable_credit = (
                        charge.qty_credited
                        if charge.allow_reinvoice_after_credit
                        else 0.0
                    )
                    maximum_reserved = charge.qty_billable + reusable_credit
                    if charge.qty_invoiced > maximum_reserved + QTY_TOLERANCE:
                        raise ValidationError(
                            "Concurrent invoice request exceeded delivered quantity for %s."
                            % charge.display_name
                        )
                batch.sudo().write(
                    {
                        "invoice_id": invoice.id,
                        "state": "completed",
                        "completed_at": fields.Datetime.now(),
                        "error_summary": False,
                    }
                )
            return invoice.with_user(self.env.user)
        except Exception as exc:
            batch.sudo().write(
                {
                    "state": "failed",
                    "error_summary": self._sanitize_batch_error(exc),
                    "completed_at": False,
                }
            )
            return self.env["account.move"]


