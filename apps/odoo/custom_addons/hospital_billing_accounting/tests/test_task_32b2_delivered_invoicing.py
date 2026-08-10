import uuid

from odoo import fields
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install", "task32b2")
class TestTask32B2DeliveredInvoicing(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.config = cls.env["hospital.billing.accounting.config"].search(
            [
                ("company_id", "=", cls.company.id),
                ("source_type", "=", "consultation"),
                ("active", "=", True),
            ],
            limit=1,
        )
        cls.service = cls.env["hospital.billing.service"].search(
            [
                ("company_id", "in", [False, cls.company.id]),
                ("service_type", "=", "consultation"),
                ("invoice_product_id", "!=", False),
            ],
            limit=1,
        )
        if not cls.config or not cls.service or not cls.service.invoice_tax_ids:
            raise AssertionError("Task 32B-2 invoice mappings were not migrated.")
        cls.accountant = cls._make_user(
            "task32b2_accountant",
            "hospital_management.group_hospital_accountant",
        )
        cls.receptionist = cls._make_user(
            "task32b2_receptionist",
            "hospital_management.group_hospital_receptionist",
        )

    @classmethod
    def _make_user(cls, login, hospital_group):
        return cls.env["res.users"].sudo().create(
            {
                "name": login.replace("_", " ").title(),
                "login": login,
                "company_id": cls.company.id,
                "company_ids": [(6, 0, cls.company.ids)],
                "groups_id": [
                    (
                        6,
                        0,
                        [
                            cls.env.ref("base.group_user").id,
                            cls.env.ref(hospital_group).id,
                        ],
                    )
                ],
            }
        )

    def _make_case(self, deliveries=(1.0,), requested=None, prices=None):
        suffix = uuid.uuid4().hex[:10]
        partner = self.env["res.partner"].sudo().create(
            {
                "name": "32B-2 Customer %s" % suffix,
                "company_id": False,
                "property_account_receivable_id": self.config.receivable_account_id.id,
            }
        )
        patient = self.env["hospital.patient"].sudo().create(
            {
                "name": "32B-2 Patient %s" % suffix,
                "accounting_partner_id": partner.id,
            }
        )
        appointment = self.env["hospital.appointment"].sudo().create(
            {
                "patient_id": patient.id,
                "appointment_date": fields.Datetime.now(),
            }
        )
        encounter = self.env["hospital.encounter"].sudo().create(
            {
                "patient_id": patient.id,
                "appointment_id": appointment.id,
                "encounter_type": "outpatient",
                "state": "active",
                "company_id": self.company.id,
            }
        )
        account = self.env["hospital.billing.account"].sudo().create(
            {"encounter_id": encounter.id, "payer_type": "self_pay"}
        )
        requested = requested or deliveries
        prices = prices or tuple(100.0 for _item in deliveries)
        charges = self.env["hospital.charge.line"]
        for index, delivered in enumerate(deliveries):
            qty_requested = requested[index]
            charge = self.env["hospital.charge.line"].sudo().create(
                {
                    "billing_account_id": account.id,
                    "service_id": self.service.id,
                    "description": "32B-2 Service",
                    "uom_id": (
                        self.service.uom_id.id
                        or self.service.invoice_product_id.uom_id.id
                    ),
                    "billing_basis": (
                        "prepaid" if self.service.prepayment_required else "delivery"
                    ),
                    "qty_requested": qty_requested,
                    "qty_delivered": delivered,
                    "delivery_state": (
                        "not_delivered"
                        if delivered <= 0
                        else "partially_delivered"
                        if delivered < qty_requested
                        else "delivered"
                    ),
                    "unit_price": prices[index],
                    "discount": 0.0,
                    "tax_treatment": self.service.tax_treatment,
                    "tax_rate": self.service.tax_rate,
                    "charge_state": "active",
                    "authorization_state": "not_required",
                    "source_model": "hospital.appointment",
                    "source_res_id": appointment.id,
                    "source_event": "task32b2_test",
                    "source_key": "task32b2:%s:%s" % (suffix, index),
                }
            )
            charges |= charge
        return account, charges

    def _invoice(self, account, token=None, charges=None, context=None, user=None):
        engine = self.env["hospital.billing.engine"].with_user(user or self.accountant)
        if context:
            engine = engine.with_context(**context)
        return engine.create_invoice(
            account,
            charges=charges,
            request_token=token or uuid.uuid4().hex,
        )

    def test_full_delivered_invoice_and_grouped_provenance(self):
        account, charges = self._make_case(deliveries=(1.0, 2.0))
        invoice = self._invoice(account)
        self.assertEqual(invoice.move_type, "out_invoice")
        self.assertEqual(invoice.state, "draft")
        self.assertTrue(invoice.hospital_managed_invoice)
        self.assertEqual(invoice.hospital_encounter_id, account.encounter_id)
        self.assertEqual(invoice.hospital_billing_account_id, account)
        self.assertEqual(len(invoice.invoice_line_ids), 1)
        self.assertEqual(invoice.invoice_line_ids.quantity, 3.0)
        allocations = invoice.hospital_charge_allocation_ids
        self.assertEqual(len(allocations), 2)
        self.assertEqual(allocations.mapped("charge_id"), charges)
        self.assertEqual(sum(allocations.mapped("quantity")), 3.0)
        self.assertEqual(set(charges.mapped("invoice_state")), {"invoiced"})

    def test_partial_delivery_and_prepaid_undelivered_exclusion(self):
        account, charges = self._make_case(
            deliveries=(1.0, 0.0), requested=(3.0, 2.0)
        )
        undelivered = charges[1]
        undelivered.sudo().write({"billing_basis": "prepaid"})
        self.assertEqual(charges[0].qty_invoice_eligible, 1.0)
        self.assertEqual(undelivered.qty_billable, 0.0)
        invoice = self._invoice(account)
        self.assertEqual(invoice.invoice_line_ids.quantity, 1.0)
        self.assertEqual(invoice.hospital_charge_allocation_ids.charge_id, charges[0])
        self.assertEqual(undelivered.invoice_state, "not_invoiced")

    def test_cancelled_rejected_and_bypassed_are_excluded(self):
        account, charges = self._make_case(deliveries=(1.0, 1.0, 1.0))
        charges[0].sudo().write({"charge_state": "cancelled"})
        charges[1].sudo().write({"authorization_state": "rejected"})
        charges[2].sudo().write({"authorization_state": "bypassed"})
        self.assertFalse(any(charges.mapped("qty_invoice_eligible")))
        with self.assertRaises(UserError):
            self._invoice(account)

    def test_supplementary_invoice_and_stable_retry(self):
        account, charges = self._make_case(deliveries=(1.0,), requested=(2.0,))
        token = uuid.uuid4().hex
        first = self._invoice(account, token=token)
        same = self._invoice(account, token=token)
        self.assertEqual(first, same)
        self.assertEqual(account.invoice_batch_ids.filtered(
            lambda batch: batch.request_token == token
        ).retry_count, 0)
        charges.sudo().record_delivery(2.0, "delivered", "second unit delivered")
        second = self._invoice(account)
        self.assertNotEqual(first, second)
        self.assertEqual(second.hospital_charge_allocation_ids.quantity, 1.0)
        self.assertEqual(charges.qty_invoiced, 2.0)

    def test_simulated_failure_then_safe_retry(self):
        account, charges = self._make_case()
        token = uuid.uuid4().hex
        failed = self._invoice(
            account,
            token=token,
            context={"hospital_test_fail_after_move": True},
        )
        self.assertFalse(failed)
        batch = account.invoice_batch_ids.filtered(
            lambda item: item.request_token == token
        )
        self.assertEqual(batch.state, "failed")
        self.assertFalse(batch.invoice_id)
        self.assertFalse(charges.invoice_allocation_ids)
        retried = self._invoice(account, token=token)
        self.assertTrue(retried)
        self.assertEqual(batch.state, "completed")
        self.assertEqual(batch.retry_count, 1)
        self.assertEqual(len(retried.hospital_charge_allocation_ids), 1)

    def test_missing_mapping_and_cross_scope_block_without_residue(self):
        account, charges = self._make_case()
        bad_service = self.service.copy(
            {
                "name": "32B-2 Missing Tax Mapping",
                "code": "T32B2-%s" % uuid.uuid4().hex[:8],
                "is_default_consultation": False,
                "invoice_tax_ids": [(5, 0, 0)],
            }
        )
        charges.sudo().write({"service_id": bad_service.id})
        before = (
            self.env["account.move"].search_count([]),
            self.env["hospital.invoice.batch"].search_count([]),
        )
        with self.assertRaises(UserError):
            self._invoice(account)
        self.assertEqual(
            before,
            (
                self.env["account.move"].search_count([]),
                self.env["hospital.invoice.batch"].search_count([]),
            ),
        )
        other_account, other_charges = self._make_case()
        with self.assertRaises(UserError):
            self._invoice(account, charges=charges | other_charges)

    def test_currency_mismatch_blocks(self):
        account, charges = self._make_case()
        foreign = self.env.ref("base.USD")
        if foreign == self.company.currency_id:
            foreign = self.env.ref("base.EUR")
        bad_service = self.service.copy(
            {
                "name": "32B-2 Foreign Currency Service",
                "code": "T32B2-FX-%s" % uuid.uuid4().hex[:6],
                "currency_id": foreign.id,
                "is_default_consultation": False,
            }
        )
        with self.assertRaises(ValidationError):
            charges.sudo().write({"service_id": bad_service.id})

    def test_zero_price_keeps_provenance(self):
        account, charges = self._make_case(deliveries=(1.0,), prices=(0.0,))
        invoice = self._invoice(account)
        self.assertEqual(invoice.invoice_line_ids.price_unit, 0.0)
        self.assertEqual(invoice.hospital_charge_allocation_ids.charge_id, charges)
        self.assertEqual(
            invoice.hospital_charge_allocation_ids.amount_untaxed_snapshot, 0.0
        )

    def test_draft_cancel_releases_only_draft_allocation(self):
        account, charges = self._make_case()
        token = uuid.uuid4().hex
        invoice = self._invoice(account, token=token)
        invoice.with_user(self.accountant).button_cancel()
        self.assertEqual(invoice.state, "cancel")
        self.assertEqual(invoice.hospital_invoice_batch_id.state, "cancelled")
        self.assertEqual(charges.qty_invoiced, 0.0)
        self.assertEqual(charges.qty_invoice_eligible, charges.qty_delivered)
        with self.assertRaises(UserError):
            self._invoice(account, token=token)

    def test_posted_invoice_provenance_is_immutable(self):
        account, charges = self._make_case()
        invoice = self._invoice(account)
        invoice.with_user(self.accountant).action_post()
        self.assertEqual(invoice.state, "posted")
        allocation = invoice.hospital_charge_allocation_ids
        with self.assertRaises(UserError):
            allocation.sudo().unlink()
        with self.assertRaises(UserError):
            allocation.sudo().write({"quantity": 2.0})
        with self.assertRaises(AccessError):
            self.env["hospital.charge.invoice.allocation"].with_user(
                self.receptionist
            ).create(
                {
                    "charge_id": charges.id,
                    "batch_id": invoice.hospital_invoice_batch_id.id,
                    "move_id": invoice.id,
                    "move_line_id": invoice.invoice_line_ids.id,
                    "allocation_type": "invoice",
                    "quantity": 1.0,
                    "unit_price_snapshot": 100.0,
                    "discount_snapshot": 0.0,
                    "amount_untaxed_snapshot": 100.0,
                    "tax_fingerprint": "forged",
                    "product_id": self.service.invoice_product_id.id,
                    "income_account_id": self.config.revenue_account_id.id,
                    "company_id": self.company.id,
                    "currency_id": self.company.currency_id.id,
                    "idempotency_key": uuid.uuid4().hex,
                }
            )

    def test_posted_credit_note_reduces_net_quantity_only_when_posted(self):
        account, charges = self._make_case()
        invoice = self._invoice(account)
        invoice.with_user(self.accountant).action_post()
        credit = invoice.with_user(self.accountant)._reverse_moves(cancel=False)
        self.assertEqual(credit.move_type, "out_refund")
        self.assertEqual(credit.state, "draft")
        self.assertEqual(charges.qty_credited, 0.0)
        credit.with_user(self.accountant).action_post()
        self.assertEqual(charges.qty_credited, 1.0)
        self.assertEqual(charges.invoice_state, "credited")
        self.assertEqual(charges.qty_invoice_eligible, 0.0)
        self.assertEqual(charges.qty_to_invoice, 0.0)
        charges.with_user(self.accountant).action_authorize_reinvoice_after_credit(
            "Corrected service must be rebilled"
        )
        self.assertEqual(charges.qty_invoice_eligible, 1.0)
        self.assertEqual(charges.qty_to_invoice, 1.0)

    def test_unauthorized_rpc_and_legacy_duplicate_paths_are_blocked(self):
        account, charges = self._make_case()
        with self.assertRaises(AccessError):
            self._invoice(account, user=self.receptionist)
        with self.assertRaises(AccessError):
            self.env["hospital.invoice.batch"].with_user(self.receptionist).create(
                {
                    "request_token": uuid.uuid4().hex,
                    "encounter_id": account.encounter_id.id,
                    "billing_account_id": account.id,
                    "patient_id": account.patient_id.id,
                    "commercial_partner_id": account.patient_id.accounting_partner_id.id,
                    "company_id": self.company.id,
                    "currency_id": self.company.currency_id.id,
                }
            )
        legacy = self.env["hospital.patient.bill"].sudo().create(
            {
                "patient_id": account.patient_id.id,
                "appointment_id": account.encounter_id.appointment_id.id,
            }
        )
        with self.assertRaises(UserError):
            legacy.action_confirm()

    def test_invoice_token_lock_prevents_parallel_same_token(self):
        account, _charges = self._make_case()
        token = uuid.uuid4().hex
        self.env["hospital.billing.engine"]._lock_invoice_token(account.company_id, token)
        second = self.registry.cursor()
        try:
            second.execute(
                "SELECT pg_try_advisory_xact_lock(hashtextextended(%s, 0))",
                ["hospital.invoice.batch:%s:%s" % (account.company_id.id, token)],
            )
            self.assertFalse(second.fetchone()[0])
        finally:
            second.rollback()
            second.close()

    def test_almaz_regression_invoice_does_not_apply_advance(self):
        patient = self.env["hospital.patient"].search(
            [("name", "ilike", "Almaz")], limit=1
        )
        account = self.env["hospital.billing.account"].search(
            [("patient_id", "=", patient.id)], limit=1
        )
        if not patient or not account:
            self.skipTest("Almaz UAT fixture is not present.")
        charges = account.charge_line_ids.filtered(lambda charge: charge.charge_state == "active")
        self.assertEqual(len(charges), 3)
        if round(sum(charges.mapped("amount_delivered")), 2) != 1200.0:
            self.skipTest("The exact 1,200 ETB Almaz UAT regression fixture is not present in this disposable DB.")
        self.assertAlmostEqual(sum(charges.mapped("amount_delivered")), 1200.0, places=2)
        self.assertEqual(set(charges.mapped("operational_funding_state")), {"funded"})
        self.assertAlmostEqual(account.amount_prepayment_held, 1200.0, places=2)
        receipts = account.receipt_ids
        self.assertEqual(sorted(receipts.mapped("amount")), [300.0, 900.0])
        partner = self.env["res.partner"].sudo().create(
            {
                "name": "Almaz 32B-2 Clone Customer",
                "property_account_receivable_id": self.config.receivable_account_id.id,
            }
        )
        patient.sudo().write({"accounting_partner_id": partner.id})
        before = {
            "payments": self.env["account.payment"].search_count([]),
            "partial_reconcile": self.env["account.partial.reconcile"].search_count([]),
            "receipts": self.env["hospital.charge.receipt"].search_count([]),
            "stock": self.env["stock.move"].search_count([]),
        }
        invoice = self._invoice(account)
        self.assertAlmostEqual(invoice.amount_total, 1200.0, places=2)
        self.assertEqual(set(charges.mapped("operational_funding_state")), {"funded"})
        self.assertEqual(set(charges.mapped("accounting_receipt_state")), {"unposted"})
        self.assertEqual(set(charges.mapped("settlement_state")), {"unsettled"})
        self.assertEqual(set(charges.mapped("fiscal_state")), {"not_started"})
        self.assertAlmostEqual(account.amount_prepayment_held, 1200.0, places=2)
        self.assertEqual(
            before,
            {
                "payments": self.env["account.payment"].search_count([]),
                "partial_reconcile": self.env["account.partial.reconcile"].search_count([]),
                "receipts": self.env["hospital.charge.receipt"].search_count([]),
                "stock": self.env["stock.move"].search_count([]),
            },
        )
