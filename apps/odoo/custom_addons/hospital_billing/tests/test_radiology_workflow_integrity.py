import uuid

from odoo import fields
from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install", "radiology_workflow_integrity")
class TestRadiologyWorkflowIntegrity(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.accountant = cls._make_user("rad_accountant", "hospital_management.group_hospital_accountant")
        cls.manager = cls._make_user("rad_manager", "hospital_management.group_hospital_manager")
        cls.doctor = cls.env["hospital.doctor"].sudo().create({"name": "Radiology Test Doctor"})
        cls.uom = cls.env["uom.uom"].sudo().search([], limit=1)
        cls.service = cls.env["hospital.billing.service"].sudo().create(
            {
                "name": "CT Brain Test Service",
                "code": "T-RAD-CTBRAIN",
                "service_type": "radiology",
                "default_price": 2500.0,
                "company_id": cls.company.id,
                "currency_id": cls.company.currency_id.id,
                "uom_id": cls.uom.id,
                "prepayment_required": True,
                "tax_treatment": "exempt",
            }
        )
        cls.auth_service = cls.env["hospital.billing.service"].sudo().create(
            {
                "name": "Authorized CT Brain Test Service",
                "code": "T-RAD-CTAUTH",
                "service_type": "radiology",
                "default_price": 2500.0,
                "company_id": cls.company.id,
                "currency_id": cls.company.currency_id.id,
                "uom_id": cls.uom.id,
                "prepayment_required": False,
                "coverage_auth_required": True,
                "tax_treatment": "exempt",
            }
        )
        cls.exam = cls.env["hospital.radiology.exam"].sudo().create(
            {"name": "CT-BRAIN", "code": "CT-BRAIN", "modality": "ct", "billing_service_id": cls.service.id}
        )
        cls.auth_exam = cls.env["hospital.radiology.exam"].sudo().create(
            {"name": "CT-BRAIN AUTH", "code": "CT-BRAIN-A", "modality": "ct", "billing_service_id": cls.auth_service.id}
        )
        if "hospital.billing.accounting.config" in cls.env:
            cls._configure_advance_accounting()

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
        return cls.env["account.journal"].sudo().create({"name": name, "code": code, "type": "general", "company_id": cls.company.id, "default_account_id": default_account.id})

    @classmethod
    def _configure_advance_accounting(cls):
        config = cls.env["hospital.billing.accounting.config"].sudo().search(
            [("company_id", "=", cls.company.id), ("source_type", "=", "radiology"), ("active", "=", True)], limit=1
        )
        if not config:
            config = cls.env["hospital.billing.accounting.config"].sudo().create(
                {"name": "Radiology Test Accounting", "company_id": cls.company.id, "source_type": "radiology"}
            )
        cash = cls._account("TRADCASH", "Radiology Test Cash", "asset_cash")
        bank = cls._account("TRADBANK", "Radiology Test Bank", "asset_cash")
        advance = cls._account("TRADADV", "Radiology Test Patient Advance", "liability_current", reconcile=True)
        credit = cls._account("TRADCRD", "Radiology Test Patient Credit", "liability_current", reconcile=True)
        receipt_journal = cls._journal("TRADR", "Radiology Test Receipt Journal", cash)
        app_journal = cls._journal("TRADA", "Radiology Test Application Journal", advance)
        config.write(
            {
                "cash_account_id": cash.id,
                "bank_account_id": bank.id,
                "mobile_money_account_id": bank.id,
                "patient_advance_liability_account_id": advance.id,
                "patient_credit_liability_account_id": credit.id,
                "advance_receipt_journal_id": receipt_journal.id,
                "advance_application_journal_id": app_journal.id,
                "advance_refund_journal_id": app_journal.id,
                "receivable_account_id": config.receivable_account_id.id or cls._account("TRADREC", "Radiology Test Receivable", "asset_receivable", True).id,
            }
        )

    def _request(self, exams=None, payer_type="self_pay", emergency=False):
        suffix = uuid.uuid4().hex[:8]
        partner = self.env["res.partner"].sudo().create({"name": "Radiology Patient Partner %s" % suffix})
        patient = self.env["hospital.patient"].sudo().create({"name": "Radiology Patient %s" % suffix, "accounting_partner_id": partner.id})
        appointment = self.env["hospital.appointment"].sudo().create({"patient_id": patient.id, "doctor_id": self.doctor.id, "appointment_date": fields.Datetime.now(), "state": "confirmed"})
        encounter = self.env["hospital.encounter"].sudo().create(
            {
                "patient_id": patient.id,
                "appointment_id": appointment.id,
                "encounter_type": "outpatient",
                "primary_doctor_id": self.doctor.id,
                "company_id": self.company.id,
                "payer_type": payer_type,
                "payer_id": partner.id if payer_type != "self_pay" else False,
                "emergency_bypass": emergency,
                "emergency_bypass_reason": "Emergency radiology" if emergency else False,
            }
        )
        lines = [(0, 0, {"exam_id": exam.id}) for exam in (exams or [self.exam])]
        request = self.env["hospital.radiology.request"].sudo().create(
            {"patient_id": patient.id, "physician_id": self.doctor.id, "appointment_id": appointment.id, "encounter_id": encounter.id, "line_ids": lines}
        )
        request.action_confirm_request()
        request.action_schedule()
        request.invalidate_recordset(["charge_line_ids"])
        return request

    def _pay(self, request, amount, post=True, state="confirmed", charge=False):
        charge = charge or request.charge_line_ids[:1]
        receipt = self.env["hospital.charge.receipt"].sudo().create({"payment_method": "cash", "received_at": fields.Datetime.now(), "received_by_id": self.accountant.id, "state": "draft", "intake_token": uuid.uuid4().hex})
        self.env["hospital.charge.receipt.allocation"].sudo().create({"receipt_id": receipt.id, "charge_line_id": charge.id, "amount": amount})
        receipt.sudo().write({"state": state})
        if post and state == "confirmed" and hasattr(receipt, "action_post_receipt_accounting"):
            receipt.with_user(self.accountant).action_post_receipt_accounting()
        return receipt

    def _pay_each_charge(self, request, amount=2500.0):
        for charge in request.charge_line_ids:
            if charge.charge_state != "cancelled":
                self._pay(request, amount, charge=charge)

    def _result(self, request, lines=None):
        return self.env["hospital.radiology.result"].sudo().create({"request_id": request.id, "patient_id": request.patient_id.id, "physician_id": request.physician_id.id, "line_ids": lines or False})

    def test_fresh_request_line_confirm_creates_single_charge_with_price_and_defaults(self):
        suffix = uuid.uuid4().hex[:8]
        partner = self.env["res.partner"].sudo().create({"name": "Radiology Confirm Partner %s" % suffix})
        patient = self.env["hospital.patient"].sudo().create({"name": "Radiology Confirm Patient %s" % suffix, "accounting_partner_id": partner.id})
        appointment = self.env["hospital.appointment"].sudo().create({"patient_id": patient.id, "doctor_id": self.doctor.id, "appointment_date": fields.Datetime.now(), "state": "confirmed"})
        encounter = self.env["hospital.encounter"].sudo().create({"patient_id": patient.id, "appointment_id": appointment.id, "encounter_type": "outpatient", "primary_doctor_id": self.doctor.id, "company_id": self.company.id})
        request = self.env["hospital.radiology.request"].sudo().create(
            {"patient_id": patient.id, "physician_id": self.doctor.id, "appointment_id": appointment.id, "encounter_id": encounter.id, "line_ids": [(0, 0, {"exam_id": self.exam.id})]}
        )
        self.assertEqual(request.line_ids.state, "active")
        result_count_before = self.env["hospital.radiology.result"].sudo().search_count([("request_id", "=", request.id)])
        request.action_confirm_request()
        request.invalidate_recordset(["charge_line_ids"])
        self.assertEqual(request.state, "requested")
        self.assertEqual(len(request.charge_line_ids), 1)
        charge = request.charge_line_ids[:1]
        self.assertEqual(charge.service_id, self.service)
        self.assertEqual(charge.amount_estimated, 2500.0)
        self.assertEqual(charge.qty_requested, 1.0)
        self.assertEqual(charge.source_line_id, request.line_ids.id)
        self.assertEqual(self.env["hospital.radiology.result"].sudo().search_count([("request_id", "=", request.id)]), result_count_before)
        request._ensure_radiology_billing()
        request.invalidate_recordset(["charge_line_ids"])
        self.assertEqual(len(request.charge_line_ids), 1)
        request.action_schedule()
        with self.assertRaisesRegex(UserError, "Unpaid examination/service"):
            request.action_mark_in_progress()

    def test_request_line_state_db_default_and_clear_incomplete_line_error(self):
        suffix = uuid.uuid4().hex[:8]
        partner = self.env["res.partner"].sudo().create({"name": "Radiology DB Default Partner %s" % suffix})
        patient = self.env["hospital.patient"].sudo().create({"name": "Radiology DB Default Patient %s" % suffix, "accounting_partner_id": partner.id})
        appointment = self.env["hospital.appointment"].sudo().create({"patient_id": patient.id, "doctor_id": self.doctor.id, "appointment_date": fields.Datetime.now(), "state": "confirmed"})
        encounter = self.env["hospital.encounter"].sudo().create({"patient_id": patient.id, "appointment_id": appointment.id, "encounter_type": "outpatient", "primary_doctor_id": self.doctor.id, "company_id": self.company.id})
        request = self.env["hospital.radiology.request"].sudo().create({"patient_id": patient.id, "physician_id": self.doctor.id, "appointment_id": appointment.id, "encounter_id": encounter.id})
        self.env.cr.execute(
            """
            INSERT INTO hospital_radiology_request_line
                (request_id, exam_id, sequence, body_part, contrast_required, create_uid, write_uid, create_date, write_date)
            VALUES (%s, %s, %s, %s, %s, %s, %s, now() at time zone 'UTC', now() at time zone 'UTC')
            RETURNING id, state
            """,
            (request.id, self.exam.id, 10, "Brain", False, self.env.uid, self.env.uid),
        )
        line_id, state = self.env.cr.fetchone()
        self.assertEqual(state, "active")
        self.assertEqual(self.env["hospital.radiology.request.line"].browse(line_id).state, "active")
        with self.assertRaisesRegex(Exception, "Exam|exam_id"):
            self.env["hospital.radiology.request.line"].sudo().create({"request_id": request.id})

    def test_failed_confirmation_rolls_back_state_charge_and_result_side_effects(self):
        suffix = uuid.uuid4().hex[:8]
        partner = self.env["res.partner"].sudo().create({"name": "Radiology Failed Confirm Partner %s" % suffix})
        patient = self.env["hospital.patient"].sudo().create({"name": "Radiology Failed Confirm Patient %s" % suffix, "accounting_partner_id": partner.id})
        appointment = self.env["hospital.appointment"].sudo().create({"patient_id": patient.id, "doctor_id": self.doctor.id, "appointment_date": fields.Datetime.now(), "state": "confirmed"})
        encounter = self.env["hospital.encounter"].sudo().create({"patient_id": patient.id, "appointment_id": appointment.id, "encounter_type": "outpatient", "primary_doctor_id": self.doctor.id, "company_id": self.company.id})
        unmapped_exam = self.env["hospital.radiology.exam"].sudo().create({"name": "Unmapped Radiology Exam %s" % suffix, "code": "UNMAP-%s" % suffix, "modality": "ct"})
        request = self.env["hospital.radiology.request"].sudo().create(
            {"patient_id": patient.id, "physician_id": self.doctor.id, "appointment_id": appointment.id, "encounter_id": encounter.id, "line_ids": [(0, 0, {"exam_id": unmapped_exam.id})]}
        )
        charge_count = self.env["hospital.charge.line"].sudo().search_count(request._charge_domain())
        result_count = self.env["hospital.radiology.result"].sudo().search_count([("request_id", "=", request.id)])
        with self.assertRaisesRegex(UserError, "No billing service is mapped"):
            with self.env.cr.savepoint():
                request.action_confirm_request()
        request.invalidate_recordset(["state"])
        self.assertEqual(request.state, "draft")
        self.assertEqual(self.env["hospital.charge.line"].sudo().search_count(request._charge_domain()), charge_count)
        self.assertEqual(self.env["hospital.radiology.result"].sudo().search_count([("request_id", "=", request.id)]), result_count)

    def test_unpaid_and_partial_cash_patient_cannot_start_without_side_effects(self):
        request = self._request()
        charge = request.charge_line_ids[:1]
        self.assertEqual(charge.amount_estimated, 2500.0)
        before = (request.state, charge.delivery_state, charge.qty_delivered, charge.service_started_at)
        with self.assertRaisesRegex(UserError, "Unpaid examination/service"):
            request.action_mark_in_progress()
        charge.invalidate_recordset(["delivery_state", "qty_delivered", "service_started_at"])
        self.assertEqual((request.state, charge.delivery_state, charge.qty_delivered, charge.service_started_at), before)
        self._pay(request, 1000.0)
        with self.assertRaisesRegex(UserError, "Patient-payable amount"):
            request.action_mark_in_progress()

    def test_full_posted_payment_permits_start_and_unposted_or_cancelled_do_not(self):
        request = self._request()
        self._pay(request, 2500.0, post=False)
        with self.assertRaisesRegex(UserError, "not posted to accounting"):
            request.action_mark_in_progress()
        request2 = self._request()
        self._pay(request2, 2500.0, post=False, state="cancelled")
        with self.assertRaises(UserError):
            request2.action_mark_in_progress()
        request3 = self._request()
        self._pay(request3, 2500.0)
        request3.action_mark_in_progress()
        self.assertEqual(request3.state, "in_progress")
        self.assertEqual(request3.charge_line_ids[:1].delivery_state, "in_progress")

    def test_authorized_coverage_and_emergency_bypass(self):
        request = self._request(exams=[self.auth_exam], payer_type="insurance")
        with self.assertRaisesRegex(UserError, "authorization pending"):
            request.action_mark_in_progress()
        self.env["hospital.billing.engine"].with_user(self.accountant).authorize_charge(request.charge_line_ids[:1], authorized_by=self.accountant)
        request.action_mark_in_progress()
        self.assertEqual(request.state, "in_progress")
        emergency = self._request(emergency=True)
        emergency.action_mark_in_progress()
        self.assertEqual(emergency.encounter_id.emergency_bypass_authorized_by, self.env.user)
        self.assertTrue(emergency.encounter_id.emergency_bypass_reason)

    def test_result_validation_release_requires_clearance_and_release_completes_and_delivers_once(self):
        request = self._request()
        result = self._result(request)
        result.action_mark_entered()
        with self.assertRaisesRegex(UserError, "Marked In Progress"):
            result.action_validate()
        self._pay(request, 2500.0)
        request.action_mark_in_progress()
        result.action_validate()
        self.assertEqual(result.state, "validated")
        result.action_release()
        charge = request.charge_line_ids[:1]
        self.assertEqual(result.state, "released")
        self.assertEqual(request.state, "completed")
        self.assertEqual(charge.delivery_state, "delivered")
        self.assertEqual(charge.qty_delivered, 1.0)
        self.assertEqual(charge.qty_to_invoice, 1.0)
        request._sync_completion_from_results()
        self.assertEqual(charge.qty_delivered, 1.0)
        self.assertEqual(len(request.charge_line_ids), 1)
        with self.assertRaisesRegex(UserError, "frozen"):
            result.write({"findings": "changed"})

    def test_multi_exam_completion_cancelled_line_and_no_duplicate_charge(self):
        second = self.env["hospital.radiology.exam"].sudo().create({"name": "MRI Test", "code": "MRI-T", "modality": "mri", "billing_service_id": self.service.id})
        request = self._request(exams=[self.exam, second])
        self.assertEqual(len(request.charge_line_ids), 2)
        self._pay_each_charge(request, 2500.0)
        request.action_mark_in_progress()
        first_line = request.line_ids[0]
        result1 = self._result(request, [(0, 0, {"exam_id": first_line.exam_id.id, "request_line_id": first_line.id})])
        result1.action_mark_entered(); result1.action_validate(); result1.action_release()
        self.assertEqual(request.state, "in_progress")
        request.line_ids[1].write({"state": "cancelled"})
        request._sync_completion_from_results()
        self.assertEqual(request.state, "completed")
        self.assertEqual(sum(request.charge_line_ids.mapped("qty_delivered")), 1.0)
        self.assertEqual(len(request.charge_line_ids), 2)

    def test_company_isolation_blocks_cross_company_service(self):
        other = self.env["res.company"].sudo().create({"name": "Radiology Other Co", "currency_id": self.company.currency_id.id})
        other_service = self.env["hospital.billing.service"].sudo().create({"name": "Other Co Radiology", "service_type": "radiology", "default_price": 100.0, "company_id": other.id})
        bad_exam = self.env["hospital.radiology.exam"].sudo().create({"name": "Other Co Exam", "modality": "ct", "billing_service_id": other_service.id})
        with self.assertRaisesRegex(UserError, "not %s" % self.company.name):
            self._request(exams=[bad_exam])

    def test_uncleared_radiology_consumption_is_blocked_when_inventory_installed(self):
        if "hospital.stock.consumption" not in self.env:
            return
        request = self._request()
        consumption = self.env["hospital.stock.consumption"].sudo().create({"consumption_type": "radiology", "patient_id": request.patient_id.id, "radiology_request_id": request.id})
        with self.assertRaisesRegex(UserError, "financially cleared"):
            consumption.action_approve()
