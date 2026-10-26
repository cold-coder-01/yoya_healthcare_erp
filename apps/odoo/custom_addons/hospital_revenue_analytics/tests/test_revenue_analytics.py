"""Revenue analytics: the model authority, scenario by scenario.

Numbered to the Phase 2 test plan. Each test drives the real billing,
invoicing and posting engines, then reads the analytics service.
"""
import uuid
from datetime import datetime, timedelta

from odoo import fields
from odoo.exceptions import AccessError
from odoo.tests import tagged

from ..models.revenue_analytics import RevenueAnalyticsError
from ..services.revenue_analytics_serializers import serialize_payload
from .common import (
    APR,
    D_APR,
    D_MAR,
    IN_STAY,
    MAR,
    PRE_ADMISSION,
    ADMITTED_AT,
    RevenueCase,
)


@tagged("post_install", "-at_install", "revenue_analytics")
class TestRecognizedRevenue(RevenueCase):

    def test_01_advance_receipt_is_never_revenue(self):
        _p, _a, encounter = self._visit(self.doc_a, self.dept_a)
        charge = self._charge(encounter, self.pharmacy_service, price=100.0,
                              source_model="hospital.pharmacy.dispense", delivered=False)
        account = self._account(encounter)
        account.sudo().record_operational_payment(100.0, "cash", intake_token=uuid.uuid4().hex)
        receipts = account.sudo().receipt_ids
        self.assertEqual(len(receipts), 1)
        config = self.env["hospital.billing.accounting.config"].sudo()._company_advance_config(self.company)
        receipts.with_user(self.accountant)._post_receipt_accounting(config=config)
        self._set(receipts, "received_at", datetime(2031, 3, 5, 9, 0))

        summary = self._summary()
        self.assertEqual(summary["revenue"]["net"], 0.0)
        self.assertEqual(summary["indicators"]["cash_received"], 100.0)
        self.assertEqual(self._quality()["legacy_manual_revenue"]["gl_revenue_total"], 0.0)

        self.engine.mark_charge_delivered(charge, qty_delivered=1.0)
        self._invoice(encounter)
        summary = self._summary()
        self.assertEqual(summary["revenue"]["net"], 100.0)
        self.assertEqual(summary["indicators"]["cash_received"], 100.0)

    def test_02_inpatient_deposit_is_excluded_from_service_revenue(self):
        _p, _a, encounter = self._visit(self.doc_a, self.dept_a)
        # Even a deposit charge that (wrongly) got delivered and invoiced is
        # money, never care: excluded everywhere, and visible as such.
        deposit = self._charge(encounter, self.deposit_service, qty=500.0, price=1.0,
                               source_model="hospital.admission", res_id=0,
                               event="inpatient_deposit")
        self._invoice(encounter, charges=deposit)
        self.assertEqual(self._charge_fact(deposit).excluded_reason, "deposit")
        summary = self._summary()
        self.assertEqual(summary["revenue"]["net"], 0.0)
        services = self._services()
        self.assertFalse(services["rows"])
        quality = self._quality()
        self.assertEqual(quality["excluded_categories_recognized"]["amount"], 500.0)
        self.assertEqual(quality["legacy_manual_revenue"]["attributed_excluded_categories"], 500.0)
        self.assertEqual(quality["legacy_manual_revenue"]["difference"], 0.0)

    def test_03_undelivered_charge_is_excluded(self):
        before = self._unbilled()["value"]
        _p, _a, encounter = self._visit(self.doc_a, self.dept_a)
        self._charge(encounter, self.lab_service, price=300.0, delivered=False, at=None)
        summary = self._summary()
        self.assertEqual(summary["revenue"]["net"], 0.0)
        self.assertEqual(summary["delivered_value"], 0.0)
        self.assertEqual(self._unbilled()["value"], before)

    def test_04_posted_delivered_charge_is_included(self):
        _p, _a, encounter = self._visit(self.doc_a, self.dept_a)
        self._charge(encounter, self.lab_service, price=100.0, at=PRE_ADMISSION)
        self._invoice(encounter)
        revenue = self._summary()["revenue"]
        self.assertEqual((revenue["gross"], revenue["credits"], revenue["net"]), (100.0, 0.0, 100.0))

    def test_05_draft_invoice_is_not_revenue(self):
        _p, _a, encounter = self._visit(self.doc_a, self.dept_a)
        before = self._unbilled()
        self._charge(encounter, self.lab_service, price=100.0, at=PRE_ADMISSION)
        invoice = self._invoice(encounter, post=False)
        self.assertEqual(invoice.state, "draft")
        self.assertEqual(self._summary()["revenue"]["net"], 0.0)
        after = self._unbilled()
        self.assertEqual(after["value"] - before["value"], 100.0)
        self.assertEqual(after["awaiting_posting_value"] - before["awaiting_posting_value"], 100.0)

    def test_06_cancelled_invoice_is_not_revenue(self):
        _p, _a, encounter = self._visit(self.doc_a, self.dept_a)
        before = self._unbilled()
        charge = self._charge(encounter, self.lab_service, price=100.0, at=PRE_ADMISSION)
        invoice = self._invoice(encounter, post=False)
        invoice.with_user(self.accountant).button_cancel()
        self.env.invalidate_all()
        self.assertEqual(invoice.state, "cancel")
        self.assertEqual(self._summary()["revenue"]["net"], 0.0)
        self.assertFalse(self._fact_rows(charge))
        after = self._unbilled()
        self.assertEqual(after["value"] - before["value"], 100.0)
        self.assertEqual(after["awaiting_posting_value"] - before["awaiting_posting_value"], 0.0)

    def test_07_partial_pharmacy_only_delivered_portion(self):
        _p, _a, encounter = self._visit(self.doc_a, self.dept_a)
        medicine = self._charge(encounter, self.pharmacy_service, qty=10.0, price=50.0,
                                delivered=4.0, at=PRE_ADMISSION,
                                source_model="hospital.pharmacy.dispense")
        self._invoice(encounter)
        self.assertEqual(self._summary()["revenue"]["net"], 200.0)
        before = self._unbilled()["value"]
        self.engine.mark_charge_delivered(medicine, qty_delivered=10.0)
        self.assertEqual(self._unbilled()["value"] - before, 300.0)
        self._invoice(encounter)
        self.assertEqual(self._summary()["revenue"]["net"], 500.0)
        self.assertEqual(sum(self._fact_rows(medicine).mapped("quantity")), 10.0)

    def test_08_merged_invoice_line_is_split_by_provenance(self):
        _p, _a, encounter = self._visit(self.doc_a, self.dept_a)
        one = self._charge(encounter, self.lab_service, qty=1.0, price=100.0, at=PRE_ADMISSION)
        two = self._charge(encounter, self.lab_service, qty=2.0, price=100.0, at=PRE_ADMISSION)
        invoice = self._invoice(encounter)
        self.assertEqual(len(invoice.invoice_line_ids), 1, "the engine must merge the two charges")
        self.assertEqual(self._fact_rows(one).revenue, 100.0)
        self.assertEqual(self._fact_rows(two).revenue, 200.0)

    def test_09_credit_note_reduces_revenue_in_its_own_period(self):
        _p, _a, encounter = self._visit(self.doc_a, self.dept_a)
        self._charge(encounter, self.lab_service, price=300.0, at=PRE_ADMISSION)
        invoice = self._invoice(encounter, on=D_MAR)
        self._credit(invoice, on=D_APR)
        march = self._summary(MAR)["revenue"]
        april = self._summary(APR)["revenue"]
        self.assertEqual((march["gross"], march["credits"], march["net"]), (300.0, 0.0, 300.0))
        self.assertEqual((april["gross"], april["credits"], april["net"]), (0.0, 300.0, -300.0))
        trend = self._trend(("2031-03-01", "2031-04-30"), grain="month")
        self.assertEqual([point["recognized"] for point in trend["points"]], [300.0, -300.0])
        self.assertEqual(self._summary(APR)["indicators"]["credit_notes"], 300.0)

    def test_10_unused_advance_refund_leaves_revenue_unchanged(self):
        case = self._inpatient_case(self.doc_b, self.ward_b)
        before = self._summary()
        self._refund_operation(case["admission"], 400.0)
        after = self._summary()
        self.assertEqual(after["revenue"], before["revenue"])
        self.assertEqual(after["indicators"]["advance_refunds"], 400.0)
        self.assertEqual(after["indicators"]["advance_refund_count"], 1)

    def test_37_revenue_is_net_of_discount(self):
        _p, _a, encounter = self._visit(self.doc_a, self.dept_a)
        self._charge(encounter, self.lab_service, price=1000.0, discount=10.0, at=PRE_ADMISSION)
        self._invoice(encounter)
        self.assertEqual(self._summary()["revenue"]["net"], 900.0)

    def test_38_vat_is_excluded_from_revenue(self):
        _p, _a, encounter = self._visit(self.doc_a, self.dept_a)
        charge = self._charge(encounter, self.vat_service, price=1000.0, at=PRE_ADMISSION)
        self.assertEqual((charge.tax_treatment, charge.tax_rate), ("standard", 15.0))
        invoice = self._invoice(encounter)
        self.assertEqual((invoice.amount_untaxed, invoice.amount_total), (1000.0, 1150.0))
        self.assertEqual(self._summary()["revenue"]["net"], 1000.0)
        reconciliation = self._quality()["legacy_manual_revenue"]
        self.assertEqual(reconciliation["gl_revenue_total"], 1000.0)
        self.assertEqual(reconciliation["difference"], 0.0)

    def test_39_allocations_tie_to_gl_revenue(self):
        _p, _a, encounter = self._visit(self.doc_a, self.dept_a)
        charges = self.env["hospital.charge.line"]
        for _index in range(3):
            charges |= self._charge(encounter, self.lab_service, price=33.33, discount=7.0,
                                    at=PRE_ADMISSION)
        invoice = self._invoice(encounter)
        revenue_lines = invoice.line_ids.filtered(lambda line: line.account_id == self.fx["revenue"])
        gl = -sum(revenue_lines.mapped("balance"))
        facts = self._fact_rows(charges)
        self.assertEqual(len(facts), 3)
        self.assertAlmostEqual(sum(facts.mapped("revenue")), gl, places=6)
        self.assertEqual(self._summary()["revenue"]["net"], self.company.currency_id.round(gl))
        reconciliation = self._quality()["legacy_manual_revenue"]
        self.assertEqual(reconciliation["gl_revenue_total"], self.company.currency_id.round(gl))
        self.assertEqual(reconciliation["difference"], 0.0)

    def test_31_invoice_replay_creates_no_duplicate_revenue(self):
        _p, _a, encounter = self._visit(self.doc_a, self.dept_a)
        charge = self._charge(encounter, self.lab_service, price=100.0, at=PRE_ADMISSION)
        token = uuid.uuid4().hex
        first = self._invoice(encounter, token=token)
        again = self._invoice(encounter, token=token)
        self.assertEqual(first, again)
        self.assertEqual(len(self._fact_rows(charge)), 1)
        self.assertEqual(self._summary()["revenue"]["net"], 100.0)


@tagged("post_install", "-at_install", "revenue_analytics")
class TestDoctorAttribution(RevenueCase):

    def test_11_direct_doctor_revenue_is_consultation_only(self):
        _p, appointment, encounter = self._visit(self.doc_a, self.dept_a)
        self._consultation(encounter, appointment, 100.0, at=PRE_ADMISSION)
        self._charge(encounter, self.lab_service, price=200.0, at=PRE_ADMISSION)
        self._charge(encounter, self.pharmacy_service, price=300.0, at=PRE_ADMISSION,
                     source_model="hospital.pharmacy.dispense")
        self._invoice(encounter)
        row = self._row(self._doctors()["rows"], "doctor_id", self.doc_a.id)
        self.assertEqual(row["direct_revenue"], 100.0)
        self.assertEqual(row["episode_revenue"], 600.0)
        self.assertEqual(row["department_name"], self.dept_a.name)

    def test_11b_patients_treated_come_from_completed_consultations(self):
        patient, appointment, encounter = self._visit(self.doc_a, self.dept_a)
        consultation = self.env["hospital.consultation"].sudo().create({
            "encounter_id": encounter.id, "doctor_id": self.doc_a.id,
        })
        consultation.write({"state": "completed", "completed_at": PRE_ADMISSION})
        # A second, revenue-only patient for doc A must NOT count as treated.
        _p2, _a2, other = self._visit(self.doc_a, self.dept_a)
        self._charge(other, self.lab_service, price=200.0, at=PRE_ADMISSION)
        self._invoice(other)
        row = self._row(self._doctors()["rows"], "doctor_id", self.doc_a.id)
        self.assertEqual((row["patients_treated"], row["completed_consultations"]), (1, 1))
        self.assertEqual(row["episode_patients"], 1)
        top = self._summary()["top"]["doctor_by_patients"]
        self.assertEqual(top["id"], self.doc_a.id)

    def test_12_inpatient_care_never_becomes_direct_revenue(self):
        case = self._inpatient_case(self.doc_b, self.ward_b)
        rows = self._doctors()["rows"]
        doc_a = self._row(rows, "doctor_id", self.doc_a.id)
        doc_b = self._row(rows, "doctor_id", self.doc_b.id)
        self.assertEqual(doc_a["direct_revenue"], 100.0)     # the consultation only
        self.assertEqual(doc_b["direct_revenue"], 0.0)       # lab, medicine, bed: never direct
        self.assertEqual(doc_b["episode_revenue"], 250.0 + 300.0 + case["stay_value"])
        summary = self._summary()
        self.assertEqual(summary["top"]["doctor_by_direct_revenue"]["id"], self.doc_a.id)

    def test_13_outpatient_episode_revenue_uses_encounter_doctor(self):
        _p, appointment, encounter = self._visit(self.doc_b, self.dept_b)
        lab = self._charge(encounter, self.lab_service, price=200.0, at=PRE_ADMISSION)
        self._invoice(encounter)
        fact = self._charge_fact(lab)
        self.assertFalse(fact.is_inpatient)
        self.assertEqual(fact.episode_doctor_id, self.doc_b)
        self.assertFalse(fact.direct_doctor_id)

    def test_14_inpatient_episode_revenue_uses_admission_physician(self):
        case = self._inpatient_case(self.doc_b, self.ward_b)
        for charge in case["in_lab"] | case["medicine"] | case["stay"]:
            fact = self._charge_fact(charge)
            self.assertTrue(fact.is_inpatient, charge.description)
            self.assertEqual(fact.admission_id, case["admission"])
            self.assertEqual(fact.episode_doctor_id, self.doc_b, charge.description)
            self.assertFalse(fact.episode_doctor_fallback)

    def test_15_missing_admission_physician_falls_back_to_encounter_doctor(self):
        case = self._inpatient_case(None, self.ward_b)
        fact = self._charge_fact(case["in_lab"])
        self.assertTrue(fact.is_inpatient)
        self.assertEqual(fact.episode_doctor_id, self.doc_a)
        self.assertTrue(fact.episode_doctor_fallback)
        fallback = self._quality()["admission_doctor_fallback"]
        self.assertEqual(fallback["count"], 1)
        self.assertEqual(fallback["amount"], 250.0 + 300.0 + case["stay_value"])

    def test_16_doctor_mismatch_is_reported(self):
        self._inpatient_case(self.doc_b, self.ward_b)
        mismatch = self._quality()["doctor_mismatch"]
        self.assertEqual(mismatch["count"], 1)
        self.assertGreater(mismatch["amount"], 0.0)

    def test_17_pre_admission_charges_stay_with_outpatient_doctor(self):
        case = self._inpatient_case(self.doc_b, self.ward_b)
        for charge in case["consult"] | case["pre_lab"]:
            fact = self._charge_fact(charge)
            self.assertFalse(fact.is_inpatient, charge.description)
            self.assertEqual(fact.episode_doctor_id, self.doc_a)
        row = self._row(self._doctors()["rows"], "doctor_id", self.doc_a.id)
        self.assertEqual(row["episode_revenue"], 300.0)

    def test_18_inpatient_charges_use_admission_doctor(self):
        case = self._inpatient_case(self.doc_b, self.ward_b)
        self.assertEqual(self._charge_fact(case["in_lab"]).episode_doctor_id, self.doc_b)
        # The doctor filter selects the doctor's episode-attributed revenue.
        summary = self._summary(doctor=self.doc_b.id)
        self.assertEqual(summary["revenue"]["net"], 250.0 + 300.0 + case["stay_value"])

    def test_24_service_revenue_does_not_depend_on_doctor(self):
        _p, _a, encounter = self._visit(None, self.dept_a)
        self._charge(encounter, self.lab_service, price=200.0, at=PRE_ADMISSION)
        self._invoice(encounter)
        laboratory = self._row(self._services()["rows"], "category", "laboratory")
        self.assertEqual(laboratory["value"], 200.0)
        unassigned = self._row(self._doctors()["rows"], "doctor_id", None)
        self.assertEqual(unassigned["episode_revenue"], 200.0)
        self.assertEqual(self._quality()["missing_doctor"]["amount"], 200.0)


@tagged("post_install", "-at_install", "revenue_analytics")
class TestDepartmentAttribution(RevenueCase):

    def test_19_pre_admission_department_is_encounter_department(self):
        case = self._inpatient_case(self.doc_b, self.ward_b)
        self.assertEqual(self._charge_fact(case["pre_lab"]).episode_department_id, self.dept_a)
        row = self._row(self._departments()["rows"], "department_id", self.dept_a.id)
        self.assertEqual(row["value"], 300.0)
        self.assertEqual(row["patients"], 1)

    def test_20_inpatient_department_is_ward_department(self):
        case = self._inpatient_case(self.doc_b, self.ward_b)
        fact = self._charge_fact(case["in_lab"])
        self.assertEqual(fact.episode_ward_id, self.ward_b)
        self.assertEqual(fact.episode_department_id, self.dept_b)
        row = self._row(self._departments()["rows"], "department_id", self.dept_b.id)
        self.assertEqual(row["value"], 250.0 + 300.0 + case["stay_value"])

    def test_21_missing_ward_department_falls_back_to_encounter(self):
        case = self._inpatient_case(self.doc_b, self.ward_none)
        fact = self._charge_fact(case["in_lab"])
        self.assertEqual(fact.episode_department_id, self.dept_a)
        self.assertTrue(fact.episode_department_fallback)
        fallback = self._quality()["ward_department_fallback"]
        self.assertEqual(fallback["count"], 1)

    def test_21b_ward_is_resolved_at_service_time_across_a_transfer(self):
        patient, _appointment, encounter = self._visit(self.doc_a, self.dept_a)
        admission = self._admit(patient, self.doc_b, self.ward_a)
        room = self._make_room(self.ward_b, "RA-T")
        bed = self._make_bed(room, "RA-T")
        self.env.flush_all()
        self.cr.execute(
            "INSERT INTO hospital_admission_transfer (admission_id, transfer_date, "
            "from_ward_id, to_ward_id, to_room_id, to_bed_id, create_date, write_date) "
            "VALUES (%s, %s, %s, %s, %s, %s, now(), now())",
            [admission.id, ADMITTED_AT + timedelta(hours=30), self.ward_a.id,
             self.ward_b.id, room.id, bed.id],
        )
        before = self._charge(encounter, self.lab_service, price=10.0,
                              at=ADMITTED_AT + timedelta(hours=10))
        after = self._charge(encounter, self.lab_service, price=20.0,
                             at=ADMITTED_AT + timedelta(hours=40))
        self.assertEqual(self._charge_fact(before).episode_department_id, self.dept_a)
        self.assertEqual(self._charge_fact(after).episode_department_id, self.dept_b)

    def test_22_unassigned_department(self):
        _p, _a, encounter = self._visit(self.doc_a, None)
        self._charge(encounter, self.lab_service, price=200.0, at=PRE_ADMISSION)
        self._invoice(encounter)
        row = self._row(self._departments()["rows"], "department_id", None)
        self.assertEqual((row["department_name"], row["value"]), ("Unassigned", 200.0))
        self.assertEqual(self._summary(department="unassigned")["revenue"]["net"], 200.0)
        self.assertEqual(self._quality()["missing_department"]["amount"], 200.0)

    def test_23_patient_with_multiple_services_counts_once(self):
        patient, appointment, encounter = self._visit(self.doc_a, self.dept_a)
        self._consultation(encounter, appointment, 100.0, at=PRE_ADMISSION)
        self._charge(encounter, self.lab_service, price=200.0, at=PRE_ADMISSION)
        self._charge(encounter, self.rad_service, price=300.0, at=PRE_ADMISSION)
        self._invoice(encounter)
        summary = self._summary()
        self.assertEqual(summary["patients"]["unique"], 1)
        self.assertEqual(summary["patients"]["avg_revenue_per_patient"], 600.0)
        services = self._services()
        self.assertEqual(sum(row["orders"] for row in services["rows"]), 3)
        department = self._row(self._departments()["rows"], "department_id", self.dept_a.id)
        self.assertEqual(department["patients"], 1)


@tagged("post_install", "-at_install", "revenue_analytics")
class TestTimeScopeAndQuality(RevenueCase):

    def test_25_local_date_boundaries_follow_addis_ababa(self):
        _p, _a, encounter = self._visit(self.doc_a, self.dept_a)
        # 21:30 UTC on 31 March is 00:30 EAT on 1 April.
        self._charge(encounter, self.lab_service, price=100.0, at=datetime(2031, 3, 31, 21, 30))
        delivered_march = self._summary(MAR, basis="delivered")["delivered_value"]
        delivered_april_first = self._summary(("2031-04-01", "2031-04-01"))["delivered_value"]
        self.assertEqual(delivered_march, 0.0)
        self.assertEqual(delivered_april_first, 100.0)
        # The accounting date is a date: no time-zone shift on recognized revenue.
        self._invoice(encounter, on=D_MAR)
        self.assertEqual(self._summary(MAR)["revenue"]["net"], 100.0)

    def test_26_company_isolation(self):
        _p, _a, encounter = self._visit(self.doc_a, self.dept_a)
        self._charge(encounter, self.lab_service, price=100.0, at=PRE_ADMISSION)
        self._invoice(encounter)
        other = self.env["res.company"].sudo().create({"name": "RA Other Hospital %s" % uuid.uuid4().hex[:4]})
        with self.assertRaises(RevenueAnalyticsError) as refused:
            self._summary(company=other.id)
        self.assertEqual(refused.exception.code, "company_not_allowed")
        self.finance_user.sudo().write({"company_ids": [(4, other.id)]})
        self.assertEqual(self._summary(company=other.id)["revenue"]["net"], 0.0)
        self.assertEqual(self._summary(company=self.company.id)["revenue"]["net"], 100.0)

    def test_32_legacy_manual_gl_revenue_is_unattributed_only(self):
        _p, _a, encounter = self._visit(self.doc_a, self.dept_a)
        self._charge(encounter, self.lab_service, price=100.0, at=PRE_ADMISSION)
        self._invoice(encounter)
        self._manual_revenue(700.0)
        reconciliation = self._quality()["legacy_manual_revenue"]
        self.assertEqual(reconciliation["gl_revenue_total"], 800.0)
        self.assertEqual(reconciliation["attributed_total"], 100.0)
        self.assertEqual(reconciliation["unattributed_amount"], 700.0)
        self.assertEqual(reconciliation["difference"], 0.0)
        self.assertEqual(self._summary()["revenue"]["net"], 100.0)
        self.assertEqual(self._services()["total"], 100.0)
        self.assertEqual(self._departments()["total"], 100.0)
        self.assertEqual(self._doctors()["totals"]["episode_revenue"], 100.0)

    def test_33_sponsored_revenue_is_excluded_from_the_headline(self):
        payer = self.env["res.partner"].sudo().create({
            "name": "RA Corporate Payer %s" % uuid.uuid4().hex[:4],
            "company_id": False,
            "property_account_receivable_id": self.fx["receivable"].id,
        })
        _p, _a, encounter = self._visit(self.doc_a, self.dept_a, payer=payer)
        self._charge(encounter, self.lab_service, price=200.0, at=PRE_ADMISSION)
        invoice = self._invoice(encounter)
        self.assertEqual(invoice.partner_id.commercial_partner_id, payer)
        summary = self._summary()
        self.assertEqual(summary["revenue"]["net"], 0.0)
        self.assertEqual(summary["excluded_scope"]["sponsored_revenue"], 200.0)
        self.assertEqual(summary["scope"]["payer_scope"], "self_pay")
        quality = self._quality()
        self.assertEqual(quality["sponsored_excluded"]["amount"], 200.0)
        self.assertEqual(quality["legacy_manual_revenue"]["attributed_sponsored"], 200.0)

    def test_34_registration_card_is_excluded_and_flagged(self):
        today = self._svc()._local_today().isoformat()
        period = (today, today)
        before = self._quality(period)["registration_card"]
        _p, _a, encounter = self._visit(self.doc_a, self.dept_a)
        self._charge(encounter, self.card_service, price=50.0, delivered=False,
                     source_model="hospital.patient.card.issue")
        self._account(encounter).sudo().record_operational_payment(
            50.0, "cash", intake_token=uuid.uuid4().hex
        )
        after = self._quality(period)["registration_card"]
        self.assertEqual(after["charges"] - before["charges"], 1)
        self.assertEqual(after["funded"] - before["funded"], 1)
        self.assertEqual(round(after["cash_held"] - before["cash_held"], 2), 50.0)
        self.assertFalse(self._row(self._services(period)["rows"], "category", "registration"))

    def test_34b_consultation_delivery_gap_is_flagged(self):
        today = self._svc()._local_today().isoformat()
        period = (today, today)
        before = self._quality(period)["consultation_gap"]
        _p, appointment, encounter = self._visit(self.doc_a, self.dept_a)
        self._consultation(encounter, appointment, 100.0, delivered=False)
        self._account(encounter).sudo().record_operational_payment(
            100.0, "cash", intake_token=uuid.uuid4().hex
        )
        after = self._quality(period)["consultation_gap"]
        self.assertEqual(after["charges"] - before["charges"], 1)
        self.assertEqual(after["funded"] - before["funded"], 1)

    def test_35_delivered_not_invoiced_kpi(self):
        before = self._unbilled()
        _p, _a, encounter = self._visit(self.doc_a, self.dept_a)
        posted = self._charge(encounter, self.lab_service, price=100.0, at=PRE_ADMISSION)
        drafted = self._charge(encounter, self.lab_service, price=200.0, at=PRE_ADMISSION,
                               event="ra_draft")
        self._charge(encounter, self.rad_service, price=300.0, at=PRE_ADMISSION)
        self._invoice(encounter, charges=posted)
        self._invoice(encounter, charges=drafted, post=False)
        after = self._unbilled()
        self.assertEqual(after["value"] - before["value"], 500.0)
        self.assertEqual(after["awaiting_posting_value"] - before["awaiting_posting_value"], 200.0)
        self.assertEqual(self._summary()["unbilled"]["value"] - before["value"], 500.0)
        self.assertEqual(self._summary()["revenue"]["net"], 100.0)

    def test_36_recognized_and_delivered_bases_date_differently(self):
        _p, _a, encounter = self._visit(self.doc_a, self.dept_a)
        self._charge(encounter, self.lab_service, price=100.0, at=datetime(2031, 3, 28, 9, 0))
        self._invoice(encounter, on=datetime(2031, 4, 3).date())
        march, april = self._summary(MAR), self._summary(APR)
        self.assertEqual((march["revenue"]["net"], march["delivered_value"]), (0.0, 100.0))
        self.assertEqual((april["revenue"]["net"], april["delivered_value"]), (100.0, 0.0))
        trend = self._trend(("2031-03-01", "2031-04-30"), grain="month")
        self.assertEqual(
            [(p["recognized"], p["delivered"]) for p in trend["points"]],
            [(0.0, 100.0), (100.0, 0.0)],
        )
        # The doctor table on the delivered basis attributes by delivery date.
        row = self._row(self._doctors(MAR, basis="delivered")["rows"], "doctor_id", self.doc_a.id)
        self.assertEqual(row["episode_revenue"], 100.0)

    def test_filters_are_validated(self):
        for raw, code in (
            ({"date_from": "2031-04-01", "date_to": "2031-03-01"}, "invalid_date_range"),
            ({"date_from": "yesterday"}, "invalid_date"),
            ({"date_from": "2020-01-01", "date_to": "2031-01-01"}, "date_range_too_long"),
            ({"category": "registration"}, "invalid_category"),
            ({"basis": "cash"}, "invalid_basis"),
        ):
            with self.assertRaises(RevenueAnalyticsError) as refused:
                self._svc().summary(raw)
            self.assertEqual(refused.exception.code, code)
        with self.assertRaises(RevenueAnalyticsError):
            self._svc().trend({"date_from": "2031-01-01", "date_to": "2031-12-31"}, grain="day")


@tagged("post_install", "-at_install", "revenue_analytics")
class TestAnalyticsAccess(RevenueCase):

    def test_27_accountant_gets_finance_analytics(self):
        service = self._svc(self.accountant)
        self.assertEqual(service.access_level(), "finance")
        service.data_quality(self._raw_filters())
        service.unbilled(self._raw_filters())
        self.assertTrue(service.session()["capabilities"]["data_quality"])

    def test_27b_system_administrator_gets_both(self):
        self.assertTrue(self.admin.has_group("hospital_revenue_analytics.group_hospital_executive"))
        self.assertEqual(self._svc(self.admin).access_level(), "finance")

    def test_28_executive_gets_aggregate_analytics(self):
        _p, appointment, encounter = self._visit(self.doc_a, self.dept_a)
        self._consultation(encounter, appointment, 100.0, at=PRE_ADMISSION)
        self._invoice(encounter)
        service = self._svc(self.executive)
        self.assertEqual(service.access_level(), "executive")
        summary = service.summary(self._raw_filters())
        self.assertEqual(summary["revenue"]["net"], 100.0)
        self.assertEqual(summary["top"]["doctor_by_direct_revenue"]["name"], self.doc_a.name)
        rows = service.doctors(self._raw_filters())["rows"]
        self.assertEqual(self._row(rows, "doctor_id", self.doc_a.id)["doctor_name"], self.doc_a.name)
        service.services(self._raw_filters())
        service.departments(self._raw_filters())
        service.trend(self._raw_filters())

    def test_29_executive_cannot_obtain_patient_detail(self):
        service = self._svc(self.executive)
        session = service.session()
        self.assertFalse(session["capabilities"]["patient_drilldown"])
        self.assertFalse(session["capabilities"]["data_quality"])
        with self.assertRaises(AccessError):
            service.data_quality(self._raw_filters())
        with self.assertRaises(AccessError):
            service.unbilled(self._raw_filters())
        # Defence in depth: an executive payload is stripped of identifiers.
        stripped = serialize_payload(
            {"rows": [{"patient_id": 7, "encounter_id": 8, "doctor_name": "X"}]}, "executive"
        )
        self.assertEqual(stripped, {"rows": [{"doctor_name": "X"}]})
        kept = serialize_payload({"rows": [{"patient_id": 7}]}, "finance")
        self.assertEqual(kept, {"rows": [{"patient_id": 7}]})

    def test_30_clinical_cashier_and_manager_roles_are_refused(self):
        for user in (self.doctor_user, self.nurse, self.cashier, self.manager,
                     self.receptionist, self.pharmacist, self.lab_tech):
            service = self._svc(user)
            self.assertIsNone(service.access_level(), user.login)
            self.assertFalse(service.session()["capabilities"]["analytics"])
            with self.assertRaises(AccessError):
                service.summary(self._raw_filters())
            with self.assertRaises(AccessError):
                service.doctors(self._raw_filters())

    def test_no_mutation_path_exists(self):
        """The analytics models are read-only views: nobody can write them."""
        fact = self.env["hospital.revenue.fact"]
        self.assertFalse(fact._auto)
        self.assertFalse(self.env["hospital.revenue.charge.fact"]._auto)
        for user in (self.finance_user, self.executive, self.accountant):
            for model in ("hospital.revenue.fact", "hospital.revenue.charge.fact"):
                self.assertFalse(
                    self.env[model].with_user(user).has_access("create"), (user.login, model)
                )
                self.assertFalse(self.env[model].with_user(user).has_access("write"))
