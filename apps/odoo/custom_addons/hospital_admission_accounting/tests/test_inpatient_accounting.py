"""Inpatient accounting: the operational settlement, proven in the ledger.

Every scenario runs the real workflow as real users (doctor estimate, Cashier
advance / settlement, physician readiness, Admissions finalization,
Accountant posting / refund) and then reads the GL -- never a total the
module computed for itself. The fixture gives each test its OWN accounts, so
an account balance is exactly this test's postings.

  A  advance 30,000 < care 40,000, settle 10,000          -> L 0, AR 0, rev 40,000
  B  advance 40,000 = care 40,000                         -> L 0, AR 0
  C  advance 50,000 + other 1,750 > care 8,350 (Tesema)   -> refund 43,400, L 0
  advances are liability, never revenue; partial / multiple / additional
  replay and concurrency: receipt, close, refund
  fail-closed: mapping, inconsistent ledger, archived journal, locked period,
               missing invoice product -- nothing posted, nothing refunded,
               admission / bed / care untouched
  multi-company, roles, traceability, payer refusal, delivered-only revenue,
  pooled application never mirrored, legacy refunds
"""
import json
import uuid
from unittest.mock import patch

from odoo import fields
from odoo.exceptions import AccessError
from odoo.tests import tagged

from odoo.addons.hospital_admission.models.admission_cashier import CashierSettlementError
from odoo.addons.hospital_admission.tests.test_admission_advance_settlement import AdvanceCase

TOL = 0.005


@tagged("post_install", "-at_install", "inpatient_accounting")
class InpatientLedgerCase(AdvanceCase):

    def setUp(self):
        super().setUp()
        self.fx = self._map_services_for_accounting()

    # -- workflow -------------------------------------------------------
    def _discharge(self, admission):
        if not admission.sudo().medical_discharge_ready:
            self._ready(admission)
        admission.with_user(self.receptionist)._desk_finalize_discharge(
            str(uuid.uuid4()), admission.sudo().workflow_revision
        )
        self.env.invalidate_all()
        self.assertEqual(admission.sudo().state, "discharged")
        return admission

    def _post(self, admission, user=None, key=None):
        result = admission.with_user(user or self.accountant)._accounting_post(key or str(uuid.uuid4()))
        self.env.invalidate_all()
        return result

    def _legacy(self, admission, method="cash", reference=None, when=None, user=None, key=None):
        result = admission.with_user(user or self.accountant)._accounting_post_legacy(
            key or str(uuid.uuid4()), method, payment_reference=reference, accounting_date=when
        )
        self.env.invalidate_all()
        return result

    def _legacy_refund(self, admission, amount):
        """A refund recorded the way it was before refund accounting existed:
        operational only."""
        with patch.object(type(self.env["hospital.admission"]), "_accounting_before_refund", return_value=None):
            return self._refund(admission, amount)

    def _tesema(self):
        """ADM00004's shape: advance 50,000; other payments 1,750 (a prepaid,
        undelivered consultation 1,500 and a delivered lab 250); delivered care
        8,350; discharged with 43,400 refund due."""
        admission = self._stay(estimate=50000.0, advance=50000.0)
        consult = self._charge(admission, 1, 1500.0, service=self.lab_service,
                               source="hospital.appointment")
        lab = self._charge(admission, 1, 250.0, service=self.lab_service,
                           source="hospital.laboratory.request")
        self._pay(admission, 1750.0)
        self._deliver(lab, 1)
        self._care(admission, 8100.0)
        self._discharge(admission)
        summary = self._summary(admission)
        self.assertEqual(
            (summary["actual_delivered"], summary["patient_funds"], summary["refundable_balance"]),
            (8350.0, 51750.0, 43400.0),
        )
        self.assertEqual(consult.sudo().qty_delivered, 0.0)
        return admission

    # -- ledger reads ---------------------------------------------------
    def _balance(self, account):
        lines = self.env["account.move.line"].sudo().search(
            [("account_id", "=", account.id), ("parent_state", "=", "posted")]
        )
        return round(sum(lines.mapped("balance")), 2)

    def _ledger(self):
        fx = self.fx
        return {
            "liability": -self._balance(fx["advance"]),
            "revenue": -self._balance(fx["revenue"]),
            "cash": self._balance(fx["cash"]),
            "bank": self._balance(fx["bank"]),
        }

    def _receivable(self, admission):
        record = admission.sudo()
        account = record._billing_account()
        partner = record._ledger_partner()
        moves = record._ledger_invoices(account, partner)
        return round(sum(moves.mapped("line_ids").filtered(
            lambda line: line.account_id.account_type == "asset_receivable"
        ).mapped("amount_residual")), 2)

    def _ops(self, admission, kind=None):
        domain = [("admission_id", "=", admission.id)]
        if kind:
            domain.append(("kind", "=", kind))
        return self.env["hospital.admission.accounting.operation"].sudo().search(domain)

    def _moves(self):
        return self.env["account.move"].sudo().search_count([])

    def _world(self, admission):
        """What accounting must never move."""
        self.env.invalidate_all()
        record = admission.sudo()
        summary = record._inpatient_financial_summary()
        account = record._billing_account()
        return {
            "state": record.state,
            "encounter_state": record.encounter_id.state,
            "bed": (record.bed_id.id, record.bed_id.state, record.bed_id.current_admission_id.id),
            "actual_delivered": summary["actual_delivered"],
            "refundable": summary["refundable_balance"],
            "funds": summary["patient_funds"],
            "charges": [
                (line.id, line.qty_delivered, line.amount_eligible, line.amount_refunded_from_advance)
                for line in self.env["hospital.charge.line"].sudo().search(
                    [("billing_account_id", "=", account.id)], order="id"
                )
            ],
            "receipts_posted": self.env["hospital.charge.receipt"].sudo().search_count(
                [("billing_account_id", "=", account.id), ("accounting_move_id", "!=", False)]
            ),
            "refund_rows": len(record._accounting_refunds_out()),
            "ops": len(self._ops(admission)),
        }

    def _config(self):
        return self.env["hospital.billing.accounting.config"].sudo()._company_advance_config(self.company)


@tagged("post_install", "-at_install", "inpatient_accounting")
class TestSettlementCases(InpatientLedgerCase):

    def test_advances_are_liability_never_revenue(self):
        """Partial, multiple and additional advances: each receipt is
        Dr cash / Cr Patient Advance Liability and nothing else."""
        admission = self._stay(estimate=50000.0, advance=10000.0)       # partial
        self._advance(admission, 15000.0)                               # additional
        self._advance(admission, 25000.0)                               # additional
        receipts = admission.sudo()._cashier_advance_receipts()
        self.assertEqual(len(receipts), 3)
        receipts.with_user(self.accountant)._post_receipt_accounting(config=self._config())
        self.env.invalidate_all()
        for receipt in receipts:
            lines = receipt.accounting_move_id.line_ids
            self.assertEqual(set(lines.mapped("account_id")), {self.fx["cash"], self.fx["advance"]})
            self.assertEqual(receipt.accounting_move_id.state, "posted")
        self.assertEqual(self._ledger(), {"liability": 50000.0, "revenue": 0.0, "cash": 50000.0, "bank": 0.0})
        # The operational settlement is unchanged by posting.
        self.assertEqual(self._summary(admission)["advance_received"], 50000.0)

    def test_a_advance_below_responsibility_settles_to_zero(self):
        admission = self._stay(estimate=40000.0, advance=30000.0, actual=40000.0)
        self._ready(admission)
        self._settle(admission, 10000.0)
        self._discharge(admission)
        op, replayed = self._post(admission)
        self.assertFalse(replayed)
        self.assertEqual(op.kind, "close")
        self.assertEqual(len(op.receipt_ids), 2)                     # advance + settlement
        self.assertEqual(len(op.invoice_ids), 1)
        self.assertAlmostEqual(sum(op.application_ids.mapped("amount")), 40000.0, places=2)
        self.assertEqual(self._ledger(), {"liability": 0.0, "revenue": 40000.0, "cash": 40000.0, "bank": 0.0})
        self.assertEqual(self._receivable(admission), 0.0)
        self.assertEqual(admission.sudo()._accounting_status()["status"], "posted")

    def test_b_exact_settlement_discharged_can_post(self):
        admission = self._stay(estimate=40000.0, advance=40000.0, actual=40000.0)
        self._discharge(admission)
        self.assertEqual(self._summary(admission)["settlement_state"], "even")
        status = admission.sudo()._accounting_status()
        self.assertEqual((status["status"], status["may_post"]), ("not_posted", True))
        self._post(admission)
        self.assertEqual(self._ledger(), {"liability": 0.0, "revenue": 40000.0, "cash": 40000.0, "bank": 0.0})
        self.assertEqual(self._receivable(admission), 0.0)

    def test_c_excess_advance_refund_is_atomic_with_its_accounting(self):
        admission = self._tesema()
        world = self._world(admission)
        _, replayed = self._refund(admission, 43400.0, method="bank_transfer", reference="TRF-7781")
        self.assertFalse(replayed)
        ledger = self._ledger()
        self.assertEqual(ledger["liability"], 0.0)
        self.assertEqual(ledger["revenue"], 8350.0)                  # never reduced by the refund
        self.assertEqual(ledger["cash"], 51750.0)
        self.assertEqual(ledger["bank"], -43400.0)
        self.assertEqual(self._receivable(admission), 0.0)
        # The refund entry reconciles the receipts' liability.
        [refund] = self._ops(admission, "refund")
        debit = refund.move_id.line_ids.filtered(lambda line: line.account_id == self.fx["advance"])
        self.assertTrue(debit.reconciled)
        self.assertEqual((refund.payment_method, refund.payment_reference), ("bank_transfer", "TRF-7781"))
        # Operational facts: delivered care, admission, bed, encounter.
        after = self._world(admission)
        for key in ("state", "encounter_state", "bed", "actual_delivered"):
            self.assertEqual(after[key], world[key], key)
        self.assertEqual(after["refundable"], 0.0)
        summary = self._summary(admission)
        self.assertEqual(summary["actual_delivered"], 8350.0)
        self.assertEqual(admission.sudo()._accounting_status()["status"], "posted")
        [row] = admission.sudo()._accounting_refunds_out()
        self.assertTrue(row["accounting_posted"])
        self.assertEqual(row["accounting_reference"], refund.move_id.name)

    def test_partial_refund_leaves_the_rest_in_liability(self):
        admission = self._tesema()
        self._refund(admission, 40000.0)
        self.assertEqual(self._ledger()["liability"], 3400.0)
        self.assertEqual(self._summary(admission)["refundable_balance"], 3400.0)
        self._refund(admission, 3400.0)
        self.assertEqual(self._ledger()["liability"], 0.0)
        self.assertEqual(len(self._ops(admission, "close")), 2)
        # The second close found nothing new to invoice or apply.
        second = self._ops(admission, "close").sorted("id")[-1]
        self.assertFalse(second.invoice_ids or second.application_ids)


@tagged("post_install", "-at_install", "inpatient_accounting")
class TestRevenueIsDeliveredCareOnly(InpatientLedgerCase):

    def test_undelivered_care_and_the_deposit_never_become_revenue(self):
        admission = self._stay(estimate=10000.0, advance=10000.0)
        self._charge(admission, 1, 3000.0, service=self.lab_service,
                     source="hospital.laboratory.request")            # ordered only
        medicine = self._charge(admission, 10, 100.0)                   # prescribed 10
        self._deliver(medicine, 4)                                       # dispensed 4
        self._discharge(admission)
        op, _ = self._post(admission)
        [invoice] = op.invoice_ids
        self.assertAlmostEqual(invoice.amount_total, 400.0, places=2)
        self.assertEqual(sum(invoice.invoice_line_ids.mapped("quantity")), 4.0)
        self.assertEqual(self._ledger()["revenue"], 400.0)
        deposit = admission.sudo()._deposit_charge()
        self.assertEqual(deposit.qty_invoiced, 0.0)
        self.assertNotIn(deposit, invoice.hospital_charge_allocation_ids.mapped("charge_id"))

    def test_the_pooled_application_is_never_mirrored_onto_care_lines(self):
        admission = self._stay(estimate=10000.0, advance=10000.0, actual=6000.0)
        self._discharge(admission)
        op, _ = self._post(admission)
        self.assertAlmostEqual(sum(op.application_ids.mapped("amount")), 6000.0, places=2)
        lines = admission.sudo()._billing_account().charge_line_ids
        lines.invalidate_recordset()
        self.assertEqual(sum(lines.mapped("amount_applied_to_invoice")), 0.0)
        # applied <= received holds on every line; the deposit still holds the cash.
        for line in lines:
            self.assertLessEqual(line.amount_applied_to_invoice, line.amount_received + TOL)
        self.assertEqual(admission.sudo()._deposit_charge().amount_prepayment_held, 10000.0)


@tagged("post_install", "-at_install", "inpatient_accounting")
class TestIdempotencyAndConcurrency(InpatientLedgerCase):

    def test_receipt_posting_replays_and_rereads_under_lock(self):
        admission = self._stay(estimate=20000.0, advance=10000.0)
        self._advance(admission, 10000.0)
        first, second = admission.sudo()._cashier_advance_receipts()
        move = first.with_user(self.accountant)._post_receipt_accounting(config=self._config())
        self.assertEqual(first.with_user(self.accountant)._post_receipt_accounting(), move)
        # A concurrent poster committed a move for `second` that this
        # transaction's cache has not seen. Lock-then-reread finds it.
        self.assertFalse(second.accounting_move_id)
        self.env.flush_all()
        self.cr.execute(
            "UPDATE hospital_charge_receipt SET accounting_move_id = %s WHERE id = %s", (move.id, second.id)
        )
        before = self._moves()
        found = second.with_user(self.accountant)._post_receipt_accounting(config=self._config())
        self.assertEqual(found, move)
        self.assertEqual(self._moves(), before)

    def test_close_replays_and_a_second_close_posts_nothing(self):
        admission = self._stay(estimate=10000.0, advance=10000.0, actual=10000.0)
        self._discharge(admission)
        key = str(uuid.uuid4())
        op, replayed = self._post(admission, key=key)
        self.assertFalse(replayed)
        moves = self._moves()
        again, replayed = self._post(admission, key=key)
        self.assertTrue(replayed)
        self.assertEqual(again, op)
        self.assertEqual(self._moves(), moves)
        # Another accountant, a new key, the same (already posted) stay.
        other = self._make_user("adm_accountant_2", ["hospital_management.group_hospital_accountant"])
        with self.assertRaises(CashierSettlementError) as caught:
            self._post(admission, user=other)
        self.assertEqual(caught.exception.code, "accounting_nothing_to_post")
        self.assertEqual(self._moves(), moves)
        # The same key presented by someone else is a conflict, not a replay.
        with self.assertRaises(CashierSettlementError) as caught:
            self._post(admission, user=other, key=key)
        self.assertEqual(caught.exception.code, "inpatient_operation_conflict")

    def test_refund_replays_and_a_concurrent_refund_cannot_double_cash_out(self):
        admission = self._tesema()
        key = str(uuid.uuid4())
        self._refund(admission, 43400.0, key=key)
        _, replayed = self._refund(admission, 43400.0, key=key)
        self.assertTrue(replayed)
        self.assertEqual(len(self._ops(admission, "refund")), 1)
        other = self._make_user("adm_accountant_3", ["hospital_management.group_hospital_accountant"])
        with self.assertRaises(CashierSettlementError) as caught:
            self._refund(admission, 43400.0, user=other)
        self.assertEqual(caught.exception.code, "inpatient_nothing_refundable")
        self.assertEqual(len(self._ops(admission, "refund")), 1)
        self.assertEqual(self._ledger()["bank"] + self._ledger()["cash"], 51750.0 - 43400.0)


@tagged("post_install", "-at_install", "inpatient_accounting")
class TestFailClosed(InpatientLedgerCase):

    def _assert_refund_refused(self, admission, code, **kwargs):
        world = self._world(admission)
        moves = self._moves()
        with self.assertRaises(CashierSettlementError) as caught:
            with self.env.cr.savepoint():
                self._refund(admission, 43400.0, **kwargs)
        self.assertEqual(caught.exception.code, code)
        self.env.invalidate_all()
        self.assertEqual(self._world(admission), world)        # nothing at all moved
        self.assertEqual(self._moves(), moves)
        return caught.exception

    def test_missing_mapping_refuses_and_records_nothing(self):
        admission = self._tesema()
        self._config().write({"advance_refund_journal_id": False})
        error = self._assert_refund_refused(admission, "accounting_configuration_incomplete")
        self.assertIn("Advance Refund journal", str(error))

    def test_an_inconsistent_advance_ledger_is_refused(self):
        admission = self._tesema()
        other = self.env["hospital.billing.accounting.config"].sudo().search(
            [("company_id", "=", self.company.id), ("source_type", "=", "pharmacy")]
        )
        stray = self.env["account.account"].sudo().create({
            "code": "TSTRAY%s" % uuid.uuid4().hex[:4], "name": "Stray liability",
            "account_type": "liability_current", "reconcile": True,
            "company_ids": [(6, 0, self.company.ids)],
        })
        other.write({"patient_advance_liability_account_id": stray.id})
        self._assert_refund_refused(admission, "accounting_configuration_incomplete")

    def test_an_archived_journal_is_refused(self):
        admission = self._tesema()
        self._config().advance_refund_journal_id.write({"active": False})
        self._assert_refund_refused(admission, "accounting_configuration_incomplete")

    def test_a_locked_period_is_refused(self):
        admission = self._tesema()
        self.env.flush_all()
        self.cr.execute(
            "UPDATE res_company SET fiscalyear_lock_date = %s WHERE id = %s",
            (fields.Date.context_today(self.env.user), self.company.id),
        )
        self.env.invalidate_all()
        self._assert_refund_refused(admission, "accounting_posting_failed")

    def test_a_missing_invoice_product_is_refused(self):
        admission = self._tesema()
        # The delivered lab's service loses its invoice product (the known
        # INPATIENT-BED-DAY / ADMISSION-FEE gap has the same shape).
        self.lab_service.sudo().write({"invoice_product_id": False})
        error = self._assert_refund_refused(admission, "accounting_configuration_incomplete")
        self.assertIn("Invoice Product", str(error))

    def test_post_accounting_failure_leaves_the_stay_alone(self):
        admission = self._stay(estimate=10000.0, advance=10000.0, actual=10000.0)
        self._discharge(admission)
        self._config().write({"cash_account_id": False})
        world = self._world(admission)
        with self.assertRaises(CashierSettlementError) as caught:
            with self.env.cr.savepoint():
                self._post(admission)
        self.assertEqual(caught.exception.code, "accounting_configuration_incomplete")
        self.assertEqual(self._world(admission), world)
        self.assertEqual(admission.sudo()._accounting_status()["status"], "not_posted")

    def test_post_accounting_is_for_discharged_stays_only(self):
        admission = self._stay(estimate=10000.0, advance=10000.0, actual=4000.0)
        with self.assertRaises(CashierSettlementError) as caught:
            self._post(admission)
        self.assertEqual(caught.exception.code, "accounting_post_not_open")


@tagged("post_install", "-at_install", "inpatient_accounting")
class TestScopeAndAuthority(InpatientLedgerCase):

    def test_a_payer_stay_is_refused_safely(self):
        admission = self._stay(estimate=10000.0, advance=10000.0, actual=4000.0)
        self._discharge(admission)
        self._raw("UPDATE hospital_encounter SET payer_type = 'insurance' WHERE id = %s",
                  (admission.sudo().encounter_id.id,))
        status = admission.sudo()._accounting_status()
        self.assertEqual((status["status"], status["may_post"]), ("outside_scope", False))
        moves = self._moves()
        with self.assertRaises(CashierSettlementError) as caught:
            self._post(admission)
        self.assertEqual(caught.exception.code, "payer_accounting_outside_scope")
        self.assertEqual(self._moves(), moves)
        self.assertEqual(admission.sudo().state, "discharged")

    def test_roles(self):
        admission = self._tesema()
        for user in (self.cashier, self.doctor_user, self.nurse, self.receptionist):
            with self.assertRaises(AccessError):
                self._post(admission, user=user)
            with self.assertRaises(AccessError):
                self._refund(admission, 100.0, user=user)
            with self.assertRaises(AccessError):
                self._legacy(admission, user=user)
            receipt = admission.sudo()._cashier_advance_receipts()[:1]
            with self.assertRaises(AccessError):
                receipt.with_user(user).action_post_receipt_accounting()
        # The Accountant posts and refunds, but never takes Cashier intake.
        live = self._stay(estimate=20000.0, advance=10000.0)
        with self.assertRaises(AccessError):
            self._advance(live, 1000.0, user=self.accountant)
        receipt, _ = self._advance(live, 1000.0, user=self.cashier)
        self.assertEqual(receipt.received_by_id, self.cashier)
        self._refund(admission, 43400.0, user=self.accountant)
        self.assertEqual(self._summary(admission)["refundable_balance"], 0.0)

    def test_references_trace_to_the_admission_and_visit(self):
        admission = self._tesema()
        self._refund(admission, 43400.0)
        record = admission.sudo()
        [close] = self._ops(admission, "close")
        [refund] = self._ops(admission, "refund")
        for op in (close, refund):
            self.assertEqual(
                (op.admission_id, op.encounter_id, op.patient_id, op.billing_account_id, op.company_id),
                (record, record.encounter_id, record.patient_id, record._billing_account(), record.company_id),
            )
            self.assertTrue(op.name.startswith("IPAC"))
        self.assertEqual(refund.close_id, close)
        self.assertIn(record.name, refund.move_id.ref)
        self.assertIn(record.encounter_id.name, refund.move_id.ref)
        log = refund.audit_log_id
        self.assertTrue(log.description.startswith("Refund of 43400.00"))
        self.assertEqual(json.loads(log.new_value)["operation_token"] in refund.request_token, True)
        [invoice] = close.invoice_ids
        self.assertEqual(invoice.hospital_encounter_id, record.encounter_id)
        self.assertEqual(close.application_ids.mapped("invoice_id"), invoice)
        # The provenance row is immutable.
        with self.assertRaises(Exception):
            refund.sudo().write({"amount": 1.0})
        with self.assertRaises(Exception):
            refund.sudo().unlink()

    def test_multi_company_isolation(self):
        admission = self._tesema()
        other = self.env["res.company"].sudo().create({"name": "IP Other Co %s" % uuid.uuid4().hex[:4]})
        # Company A's mappings are used; company B's are never consulted.
        self._refund(admission, 43400.0)
        for op in self._ops(admission):
            self.assertEqual(op.company_id, self.company)
            for move in op.invoice_ids | op.move_id | op.receipt_ids.mapped("accounting_move_id"):
                self.assertEqual(move.company_id, self.company)
                self.assertEqual(move.journal_id.company_id, self.company)
                self.assertTrue(all(self.company in line.account_id.company_ids for line in move.line_ids))
        Config = self.env["hospital.billing.accounting.config"].sudo()
        with self.assertRaises(Exception):
            Config._company_advance_config(other)            # no fallback to company A
        # A user outside the admission's company cannot post it.
        outsider = self.env["res.users"].sudo().create({
            "name": "IP outsider", "login": "ip_outsider_%s" % uuid.uuid4().hex[:6],
            "company_id": other.id, "company_ids": [(6, 0, other.ids)],
            "groups_id": [(6, 0, [self.env.ref("base.group_user").id,
                                  self.env.ref("hospital_management.group_hospital_accountant").id])],
        })
        with self.assertRaises(AccessError):
            admission.with_user(outsider)._accounting_post(str(uuid.uuid4()))

    def test_the_accounting_lane(self):
        admission = self._stay(estimate=10000.0, advance=10000.0, actual=10000.0)
        self._discharge(admission)
        rows, _ = self.env["hospital.admission"].with_user(self.accountant)._accounting_census()
        lanes = {facts["admission"]["id"]: facts["accounting_lane"] for facts in rows}
        self.assertEqual(lanes.get(admission.id), "accounting_pending")
        self._post(admission)
        rows, _ = self.env["hospital.admission"].with_user(self.accountant)._accounting_census()
        self.assertNotIn(admission.id, {facts["admission"]["id"] for facts in rows})


@tagged("post_install", "-at_install", "inpatient_accounting")
class TestLegacyAccounting(InpatientLedgerCase):

    def test_legacy_refund_needs_an_explicit_method_then_posts_once(self):
        admission = self._tesema()
        self._legacy_refund(admission, 43400.0)
        status = admission.sudo()._accounting_status()
        self.assertEqual((status["status"], status["may_post_legacy"]), ("legacy_pending", True))
        self.assertEqual(status["legacy_refund_total"], 43400.0)
        # Ordinary posting is refused: the legacy refund comes first.
        with self.assertRaises(CashierSettlementError) as caught:
            self._post(admission)
        self.assertEqual(caught.exception.code, "accounting_legacy_required")
        # No method is ever assumed.
        for method, reference, code in (
            (None, None, "inpatient_refund_method_required"),
            ("cheque", None, "inpatient_refund_method_required"),
            ("bank_transfer", None, "inpatient_refund_reference_required"),
        ):
            with self.assertRaises(CashierSettlementError) as caught:
                self._legacy(admission, method=method, reference=reference)
            self.assertEqual(caught.exception.code, code)
        with self.assertRaises(CashierSettlementError) as caught:
            self._legacy(admission, when="2999-01-01")
        self.assertEqual(caught.exception.code, "accounting_date_invalid")
        self.assertFalse(self._ops(admission))

        world = self._world(admission)
        key = str(uuid.uuid4())
        when = fields.Date.context_today(self.env.user)
        close, replayed = self._legacy(admission, method="cash", key=key, when=when)
        self.assertFalse(replayed)
        self.assertEqual(self._ledger(), {"liability": 0.0, "revenue": 8350.0, "cash": 8350.0, "bank": 0.0})
        self.assertEqual(self._receivable(admission), 0.0)
        [legacy] = self._ops(admission, "legacy_refund")
        self.assertEqual((legacy.amount, legacy.payment_method, legacy.accounting_date), (43400.0, "cash", when))
        self.assertEqual(legacy.close_id, close)
        # The operational refund was not repeated; delivered care is untouched.
        after = self._world(admission)
        for key_name in ("state", "bed", "actual_delivered", "refundable", "funds", "charges", "refund_rows"):
            self.assertEqual(after[key_name], world[key_name], key_name)
        self.assertEqual(admission.sudo()._accounting_status()["status"], "posted")
        # Replay: same result, nothing new; a changed method on the same key conflicts.
        moves = self._moves()
        again, replayed = self._legacy(admission, method="cash", key=key, when=when)
        self.assertTrue(replayed)
        self.assertEqual(again, close)
        self.assertEqual(self._moves(), moves)
        with self.assertRaises(CashierSettlementError) as caught:
            self._legacy(admission, method="mobile_money", reference="MM-1", key=key)
        self.assertEqual(caught.exception.code, "inpatient_operation_conflict")
        with self.assertRaises(CashierSettlementError) as caught:
            self._legacy(admission)
        self.assertEqual(caught.exception.code, "accounting_legacy_not_applicable")

    def test_legacy_failure_posts_nothing(self):
        admission = self._tesema()
        self._legacy_refund(admission, 43400.0)
        self._config().write({"mobile_money_account_id": False})
        world = self._world(admission)
        moves = self._moves()
        with self.assertRaises(CashierSettlementError):
            with self.env.cr.savepoint():
                self._legacy(admission, method="cash")
        self.assertEqual(self._world(admission), world)
        self.assertEqual(self._moves(), moves)
        self.assertEqual(admission.sudo()._accounting_status()["status"], "legacy_pending")
