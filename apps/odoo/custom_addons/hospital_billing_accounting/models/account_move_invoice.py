import json

from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tools import float_compare

from .invoice_batch import QTY_TOLERANCE, assert_invoice_authorized


class AccountMoveHospitalInvoice(models.Model):
    _inherit = "account.move"

    hospital_managed_invoice = fields.Boolean(readonly=True, copy=False, index=True)
    hospital_invoice_batch_id = fields.Many2one(
        "hospital.invoice.batch", readonly=True, copy=False, ondelete="restrict", index=True
    )
    hospital_encounter_id = fields.Many2one(
        "hospital.encounter", readonly=True, copy=False, ondelete="restrict", index=True
    )
    hospital_billing_account_id = fields.Many2one(
        "hospital.billing.account", readonly=True, copy=False, ondelete="restrict", index=True
    )
    hospital_patient_id = fields.Many2one(
        "hospital.patient", readonly=True, copy=False, ondelete="restrict", index=True
    )
    hospital_credit_origin_invoice_id = fields.Many2one(
        "account.move", readonly=True, copy=False, ondelete="restrict", index=True
    )
    hospital_charge_allocation_ids = fields.One2many(
        "hospital.charge.invoice.allocation", "move_id", readonly=True
    )

    @api.model_create_multi
    def create(self, vals_list):
        hospital_fields = {
            "hospital_managed_invoice",
            "hospital_invoice_batch_id",
            "hospital_encounter_id",
            "hospital_billing_account_id",
            "hospital_patient_id",
            "hospital_credit_origin_invoice_id",
        }
        if not self.env.su and any(hospital_fields & set(vals) for vals in vals_list):
            raise AccessError(
                "Hospital invoice links can only be created by the controlled "
                "invoice-batch service."
            )
        return super().create(vals_list)

    def write(self, vals):
        protected = {
            "hospital_managed_invoice",
            "hospital_invoice_batch_id",
            "hospital_encounter_id",
            "hospital_billing_account_id",
            "hospital_patient_id",
            "hospital_credit_origin_invoice_id",
            "partner_id",
            "company_id",
            "currency_id",
            "move_type",
            "journal_id",
        }
        if (
            not self.env.su
            and protected & set(vals)
            and any(move.hospital_managed_invoice for move in self)
        ):
            raise AccessError(
                "Hospital invoice identity and accounting scope are immutable."
            )
        return super().write(vals)

    def unlink(self):
        if any(move.hospital_managed_invoice for move in self):
            raise UserError(
                "Hospital invoices are batch audit records and cannot be deleted. "
                "Cancel a draft invoice or create a credit note for a posted invoice."
            )
        return super().unlink()

    def _hospital_tax_fingerprint(self, taxes):
        return json.dumps(
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

    def _lock_hospital_provenance_charges(self):
        allocations = self.env["hospital.charge.invoice.allocation"].search(
            [("move_id", "in", self.ids)]
        )
        charge_ids = sorted(set(allocations.mapped("charge_id").ids))
        if charge_ids:
            self.env.cr.execute(
                "SELECT id FROM hospital_charge_line WHERE id IN %s "
                "ORDER BY id FOR UPDATE",
                [tuple(charge_ids)],
            )
            allocations.mapped("charge_id").invalidate_recordset()
        return allocations

    def _validate_hospital_provenance(self):
        Allocation = self.env["hospital.charge.invoice.allocation"]
        for move in self.filtered("hospital_managed_invoice"):
            if move.move_type not in ("out_invoice", "out_refund"):
                raise ValidationError("Hospital batches support customer invoices and credits only.")
            expected_type = "invoice" if move.move_type == "out_invoice" else "credit"
            allocations = Allocation.search(
                [("move_id", "=", move.id), ("allocation_type", "=", expected_type)]
            )
            invoice_lines = move.invoice_line_ids.filtered(
                lambda line: line.display_type in (False, "product")
            )
            if not invoice_lines or not allocations:
                raise ValidationError(
                    "Hospital invoice provenance is missing; posting is blocked."
                )
            if allocations.mapped("batch_id") != move.hospital_invoice_batch_id:
                raise ValidationError("Hospital invoice and provenance batches differ.")
            if allocations.mapped("charge_id.billing_account_id") != move.hospital_billing_account_id:
                raise ValidationError("Hospital invoice provenance crosses billing accounts.")
            for line in invoice_lines:
                line_allocations = allocations.filtered(
                    lambda allocation: allocation.move_line_id == line
                )
                allocated = sum(line_allocations.mapped("quantity"))
                if float_compare(
                    allocated, line.quantity, precision_rounding=QTY_TOLERANCE
                ):
                    raise ValidationError(
                        "Invoice line '%s' quantity %.3f has provenance for %.3f."
                        % (line.name, line.quantity, allocated)
                    )
                for allocation in line_allocations:
                    if (
                        allocation.product_id != line.product_id
                        or allocation.income_account_id != line.account_id
                        or allocation.uom_id != line.product_uom_id
                        or abs(allocation.unit_price_snapshot - line.price_unit) > 0.0001
                        or abs(allocation.discount_snapshot - line.discount) > 0.0001
                        or allocation.tax_fingerprint
                        != move._hospital_tax_fingerprint(line.tax_ids)
                    ):
                        raise ValidationError(
                            "Invoice line '%s' differs from its immutable charge snapshots."
                            % line.name
                        )
            for charge in allocations.mapped("charge_id"):
                charge.invalidate_recordset(
                    [
                        "qty_invoiced",
                        "qty_credited",
                        "qty_billable",
                        "allow_reinvoice_after_credit",
                    ]
                )
                permitted = charge.qty_billable + (
                    charge.qty_credited if charge.allow_reinvoice_after_credit else 0.0
                )
                if charge.qty_invoiced > permitted + QTY_TOLERANCE:
                    raise ValidationError(
                        "Cumulative invoice allocations exceed delivered eligible "
                        "quantity for %s." % charge.display_name
                    )
        return True

    def action_post(self):
        hospital_moves = self.filtered("hospital_managed_invoice")
        if hospital_moves:
            assert_invoice_authorized(self.env, "post hospital customer invoices")
            if not self.env.su and not self.env.user.has_group(
                "account.group_account_invoice"
            ):
                raise AccessError(
                    "Standard Odoo Billing permission is also required to post invoices."
                )
            hospital_moves._lock_hospital_provenance_charges()
            hospital_moves._validate_hospital_provenance()
        result = super().action_post()
        if hospital_moves:
            allocations = self.env["hospital.charge.invoice.allocation"].search(
                [("move_id", "in", hospital_moves.ids)]
            )
            allocations.invalidate_recordset(["state"])
            allocations.mapped("charge_id").invalidate_recordset()
        return result

    def button_draft(self):
        if any(move.hospital_managed_invoice and move.state == "posted" for move in self):
            raise UserError(
                "Posted hospital invoices cannot return to draft. Use a standard "
                "credit note; fiscal reversal belongs to Task 32B-4."
            )
        return super().button_draft()

    def button_cancel(self):
        hospital = self.filtered("hospital_managed_invoice")
        if any(move.state == "posted" for move in hospital):
            raise UserError(
                "Posted hospital invoices cannot be cancelled. Correct them through "
                "a standard credit note."
            )
        result = super().button_cancel()
        invoice_batches = hospital.filtered(
            lambda move: move.move_type == "out_invoice"
        ).mapped("hospital_invoice_batch_id")
        if invoice_batches:
            invoice_batches.sudo().write({"state": "cancelled"})
        allocations = self.env["hospital.charge.invoice.allocation"].search(
            [("move_id", "in", hospital.ids)]
        )
        allocations.invalidate_recordset(["state"])
        allocations.mapped("charge_id").invalidate_recordset()
        return result

    def _reverse_moves(self, default_values_list=None, cancel=False):
        hospital = self.filtered("hospital_managed_invoice")
        if hospital:
            assert_invoice_authorized(self.env, "create hospital credit-note provenance")
            if cancel:
                raise UserError(
                    "Automatic refund-and-reconcile is outside Task 32B-2. Create a "
                    "draft standard credit note without payment reversal."
                )
            if any(move.state != "posted" or move.move_type != "out_invoice" for move in hospital):
                raise UserError("Only posted hospital customer invoices can be credited.")
        reversals = super()._reverse_moves(default_values_list=default_values_list, cancel=cancel)
        Allocation = self.env["hospital.charge.invoice.allocation"]
        for source, reversal in zip(self, reversals):
            if not source.hospital_managed_invoice:
                continue
            reversal.sudo().write(
                {
                    "hospital_managed_invoice": True,
                    "hospital_invoice_batch_id": source.hospital_invoice_batch_id.id,
                    "hospital_encounter_id": source.hospital_encounter_id.id,
                    "hospital_billing_account_id": source.hospital_billing_account_id.id,
                    "hospital_patient_id": source.hospital_patient_id.id,
                    "hospital_credit_origin_invoice_id": source.id,
                }
            )
            source_lines = source.invoice_line_ids.filtered(
                lambda line: line.display_type in (False, "product")
            ).sorted(key=lambda line: (line.sequence, line.id))
            credit_lines = reversal.invoice_line_ids.filtered(
                lambda line: line.display_type in (False, "product")
            ).sorted(key=lambda line: (line.sequence, line.id))
            if len(source_lines) != len(credit_lines):
                raise ValidationError("Credit note line structure differs from its invoice.")
            vals_list = []
            for source_line, credit_line in zip(source_lines, credit_lines):
                credit_line.sudo().write(
                    {"hospital_source_invoice_line_id": source_line.id}
                )
                originals = Allocation.search(
                    [
                        ("move_line_id", "=", source_line.id),
                        ("allocation_type", "=", "invoice"),
                        ("state", "=", "posted"),
                    ]
                )
                if not originals:
                    raise ValidationError(
                        "Posted invoice line has no charge provenance to credit."
                    )
                for original in originals:
                    prior_credit = sum(
                        Allocation.search(
                            [
                                ("original_allocation_id", "=", original.id),
                                ("allocation_type", "=", "credit"),
                                ("state", "=", "posted"),
                            ]
                        ).mapped("quantity")
                    )
                    available = original.quantity - prior_credit
                    if available <= QTY_TOLERANCE:
                        continue
                    vals_list.append(
                        {
                            "charge_id": original.charge_id.id,
                            "batch_id": original.batch_id.id,
                            "move_id": reversal.id,
                            "move_line_id": credit_line.id,
                            "allocation_type": "credit",
                            "quantity": available,
                            "unit_price_snapshot": original.unit_price_snapshot,
                            "discount_snapshot": original.discount_snapshot,
                            "amount_untaxed_snapshot": available
                            * original.unit_price_snapshot
                            * (1.0 - original.discount_snapshot / 100.0),
                            "tax_fingerprint": original.tax_fingerprint,
                            "product_id": original.product_id.id,
                            "uom_id": original.uom_id.id,
                            "income_account_id": original.income_account_id.id,
                            "analytic_distribution_snapshot": (
                                original.analytic_distribution_snapshot or False
                            ),
                            "company_id": original.company_id.id,
                            "currency_id": original.currency_id.id,
                            "idempotency_key": "%s:credit:%s"
                            % (reversal.id, original.id),
                            "original_allocation_id": original.id,
                            "source_model": original.source_model,
                            "source_res_id": original.source_res_id,
                            "source_line_id": original.source_line_id,
                            "source_event": original.source_event,
                            "source_key": original.source_key,
                        }
                    )
            if not vals_list:
                raise ValidationError("No uncredited invoice quantity remains.")
            Allocation.sudo().create(vals_list)
        return reversals


class AccountMoveLineHospitalInvoice(models.Model):
    _inherit = "account.move.line"

    hospital_source_invoice_line_id = fields.Many2one(
        "account.move.line", readonly=True, copy=False, ondelete="restrict", index=True
    )
    hospital_charge_allocation_ids = fields.One2many(
        "hospital.charge.invoice.allocation", "move_line_id", readonly=True
    )

    @api.model_create_multi
    def create(self, vals_list):
        if not self.env.su:
            move_ids = [vals.get("move_id") for vals in vals_list if vals.get("move_id")]
            if move_ids and any(
                self.env["account.move"].browse(move_ids).mapped(
                    "hospital_managed_invoice"
                )
            ):
                raise AccessError(
                    "Hospital invoice lines can only be created by the batch service."
                )
        return super().create(vals_list)

    def write(self, vals):
        protected = {
            "move_id",
            "product_id",
            "name",
            "quantity",
            "product_uom_id",
            "price_unit",
            "discount",
            "tax_ids",
            "account_id",
            "analytic_distribution",
            "hospital_source_invoice_line_id",
        }
        hospital = self.filtered(lambda line: line.move_id.hospital_managed_invoice)
        if hospital and protected & set(vals) and not self.env.su:
            raise AccessError(
                "Hospital invoice lines and provenance cannot be changed through RPC."
            )
        if (
            hospital.filtered(lambda line: line.move_id.state == "posted")
            and protected & set(vals)
        ):
            raise UserError("Posted hospital invoice lines are immutable.")
        return super().write(vals)

    def unlink(self):
        hospital = self.filtered(lambda line: line.move_id.hospital_managed_invoice)
        if hospital:
            raise UserError(
                "Hospital invoice lines cannot be deleted; cancel the draft invoice "
                "or credit the posted invoice."
            )
        return super().unlink()
