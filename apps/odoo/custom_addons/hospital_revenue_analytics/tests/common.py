"""Revenue analytics fixture: real charges, real invoices, real GL.

Every scenario drives the EXISTING engines -- hospital.billing.engine raises
and delivers the charge, the invoice engine builds the customer invoice and
its provenance, Odoo posts it -- and then reads the analytics. Nothing here
writes a figure the analytics will read back, except two deliberate raw
UPDATEs that move WHEN something happened (delivered_at, admission_date,
received_at) so a scenario can sit in a controlled period.

ISOLATION. The accounting fixture (hospital_admission's
InpatientAccountingFixture) gives each test its OWN revenue, cash and advance
accounts and repoints every accounting configuration at them, so the GL
reconciliation sees only this test's postings. Recognized revenue is posted
in 2031, a period no other data reaches. Figures that are as-of balances over
all history (the unbilled backlog) or keyed by creation time (card / gap
counts) are asserted as DELTAS around the scenario.
"""
import uuid
from datetime import date, datetime, timedelta

from odoo import fields

from odoo.addons.hospital_admission.tests.common import AdmissionCase
from odoo.addons.hospital_admission_accounting.models.admission_accounting_operation import (
    accounting_operation_capability,
)

G_FINANCE = "hospital_revenue_analytics.group_hospital_revenue_analytics"
G_EXECUTIVE = "hospital_revenue_analytics.group_hospital_executive"
G_CASHIER = "hospital_billing.group_hospital_cashier"

MAR = ("2031-03-01", "2031-03-31")
APR = ("2031-04-01", "2031-04-30")
D_MAR = date(2031, 3, 15)
D_APR = date(2031, 4, 10)
PRE_ADMISSION = datetime(2031, 3, 9, 9, 0)
ADMITTED_AT = datetime(2031, 3, 10, 8, 0)
IN_STAY = datetime(2031, 3, 11, 9, 0)


class RevenueCase(AdmissionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        tag = uuid.uuid4().hex[:5]
        cls.engine = cls.env["hospital.billing.engine"].sudo()
        cls.finance_user = cls._make_user("ra_finance", [G_FINANCE])
        cls.executive = cls._make_user("ra_executive", [G_EXECUTIVE])
        cls.cashier = cls._make_user("ra_cashier", [G_CASHIER])

        cls.dept_a = cls.department
        cls.dept_b = cls.other_department
        Doctor = cls.env["hospital.doctor"].sudo()
        cls.doc_a = Doctor.create({"name": "RA Doctor A %s" % tag, "department_id": cls.dept_a.id})
        cls.doc_b = Doctor.create({"name": "RA Doctor B %s" % tag, "department_id": cls.dept_b.id})
        cls.ward_a = cls.ward
        cls.ward_b = cls.ward_other
        cls.ward_none = cls._make_ward("RA No Department Ward", None)

        cls.consult_service = cls._ra_service("RA Consultation", "consultation", prepaid=True)
        cls.lab_service = cls._ra_service("RA Lab", "laboratory")
        cls.rad_service = cls._ra_service("RA Radiology", "radiology")
        cls.pharmacy_service = cls._ra_service("RA Medicine", "pharmacy", prepaid=True)
        cls.card_service = cls._ra_service("RA Patient Card", "registration", prepaid=True)
        cls.vat_service = cls._ra_service(
            "RA VAT Service", "other", tax_treatment="standard", tax_rate=15.0
        )
        cls.deposit_service = cls.env.ref("hospital_admission.billing_service_inpatient_deposit")

    @classmethod
    def _ra_service(cls, name, kind, prepaid=False, tax_treatment="exempt", tax_rate=0.0):
        return cls.env["hospital.billing.service"].sudo().create({
            "name": "%s %s" % (name, uuid.uuid4().hex[:5]),
            "service_type": kind,
            "default_price": 100.0,
            "prepayment_required": prepaid,
            "tax_treatment": tax_treatment,
            "tax_rate": tax_rate,
            "company_id": cls.company.id,
        })

    def setUp(self):
        super().setUp()
        self.fx = self._map_services_for_accounting()
        env = self.env(su=True)
        tag = uuid.uuid4().hex[:4].upper()
        self.vat_account = env["account.account"].create({
            "code": "RAVAT%s" % tag, "name": "RA VAT Payable %s" % tag,
            "account_type": "liability_current", "company_ids": [(6, 0, self.company.ids)],
        })
        repartition = [
            (0, 0, {"repartition_type": "base"}),
            (0, 0, {"repartition_type": "tax", "account_id": self.vat_account.id}),
        ]
        self.vat_tax = env["account.tax"].create({
            "name": "RA VAT 15%% %s" % tag, "amount": 15.0, "amount_type": "percent",
            "type_tax_use": "sale", "invoice_label": "tax03", "company_id": self.company.id,
            "invoice_repartition_line_ids": repartition,
            "refund_repartition_line_ids": [tuple(line) for line in repartition],
        })
        self.vat_service.sudo().write({
            "invoice_product_id": self.fx["product"].id,
            "invoice_tax_ids": [(6, 0, self.vat_tax.ids)],
        })

    # ------------------------------------------------------------------
    # Scenario builders
    # ------------------------------------------------------------------
    def _visit(self, doctor=None, department=None, payer=None, patient=None):
        """A fresh outpatient visit: appointment + encounter, doctor and
        department as given."""
        patient = patient or self._patient("RA Patient")
        appointment = self.env["hospital.appointment"].sudo().create({
            "patient_id": patient.id,
            "appointment_date": fields.Datetime.now(),
            "doctor_id": doctor.id if doctor else False,
            "department_id": department.id if department else False,
        })
        values = {
            "appointment_id": appointment.id,
            "primary_doctor_id": doctor.id if doctor else False,
            "department_id": department.id if department else False,
        }
        if payer:
            values.update({"payer_type": "corporate", "payer_id": payer.id})
        encounter = self._encounter(patient, **values)
        return patient, appointment, encounter

    def _line(self):
        return int(uuid.uuid4().int % 10**8)

    def _charge(self, encounter, service, qty=1.0, price=100.0, delivered="all", at=None,
                source_model="hospital.laboratory.request", res_id=None, event="ra_test",
                line=None, discount=0.0):
        charge = self.engine.create_or_update_charge(
            encounter,
            source_model,
            encounter.id if res_id is None else res_id,
            event,
            "RA %s" % service.name,
            source_line_id=self._line() if line is None else line,
            service=service,
            qty_requested=qty,
            unit_price=price,
            discount=discount,
        )
        self.engine.activate_charge(charge)
        if delivered:
            self.engine.mark_charge_delivered(charge, qty_delivered=qty if delivered == "all" else delivered)
        if at:
            self._set(charge, "delivered_at", at)
        return charge

    def _consultation(self, encounter, appointment, price=100.0, at=None, delivered="all"):
        return self._charge(
            encounter, self.consult_service, price=price, at=at, delivered=delivered,
            source_model="hospital.appointment", res_id=appointment.id,
            event="consultation", line=0,
        )

    def _set(self, record, column, value):
        """Move WHEN something happened. Raw, flushed first, cache dropped
        without flushing after (see AdmissionCase._raw)."""
        self._raw(
            'UPDATE "%s" SET "%s" = %%s WHERE id = %%s' % (record._table, column),
            [value, record.id],
        )

    def _account(self, encounter):
        return self.engine.get_or_create_billing_account(encounter)

    def _invoice(self, encounter, charges=None, on=D_MAR, post=True, token=None):
        account = self._account(encounter)
        token = token or uuid.uuid4().hex
        invoice = self.env["hospital.billing.engine"].with_user(self.accountant).create_invoice(
            account, charges=charges, request_token=token
        )
        if not invoice:
            batch = self.env["hospital.invoice.batch"].sudo().search(
                [("request_token", "=", token)], limit=1
            )
            self.fail("Invoice construction failed: %s" % batch.error_summary)
        if invoice.state == "draft":
            invoice.sudo().write({"invoice_date": on, "date": on})
            if post:
                invoice.with_user(self.accountant).action_post()
        self.env.invalidate_all()
        return invoice

    def _credit(self, invoice, on=D_APR):
        reversal = invoice.with_user(self.accountant)._reverse_moves(
            [{"invoice_date": on, "date": on}]
        )
        reversal.with_user(self.accountant).action_post()
        self.env.invalidate_all()
        return reversal

    def _admit(self, patient, physician, ward, at=ADMITTED_AT):
        """Admit through the real confirmation (it adopts the patient's open
        visit), then place the admission time at `at`."""
        bed = self._make_bed(self._make_room(ward, "RA-R"), "RA-B")
        admission = self._draft(patient=patient, bed=bed, encounter=False,
                                physician_id=(physician or self.doctor).id)
        admission.action_confirm_admission()
        self._set(admission, "admission_date", at)
        if not physician:
            self._set(admission, "physician_id", None)
        return admission

    def _stay_charges(self, admission, hours=50):
        charges = admission.sudo()._sync_stay_charges(ADMITTED_AT + timedelta(hours=hours))
        self.env.invalidate_all()
        return charges.filtered(lambda charge: charge.source_event in ("bed_day", "admission_fee"))

    def _inpatient_case(self, physician, ward):
        """doc A's outpatient visit, then an admission under `physician`:

            pre-admission   consultation 100, lab 200      (2031-03-09)
            admitted        2031-03-10 08:00
            in the stay     lab 250, medicine 2 x 150      (2031-03-11)
                            admission fee + bed days        (period starts)
        all invoiced and posted on 2031-03-15."""
        patient, appointment, encounter = self._visit(self.doc_a, self.dept_a)
        consult = self._consultation(encounter, appointment, 100.0, at=PRE_ADMISSION)
        pre_lab = self._charge(encounter, self.lab_service, price=200.0, at=PRE_ADMISSION)
        admission = self._admit(patient, physician, ward)
        in_lab = self._charge(encounter, self.lab_service, price=250.0, at=IN_STAY)
        medicine = self._charge(encounter, self.pharmacy_service, qty=2.0, price=150.0, at=IN_STAY,
                                source_model="hospital.pharmacy.dispense")
        stay = self._stay_charges(admission)
        self.assertTrue(stay, "the stay posted no bed-day / fee charges")
        self._invoice(encounter)
        return {
            "patient": patient,
            "encounter": encounter,
            "admission": admission,
            "consult": consult,
            "pre_lab": pre_lab,
            "in_lab": in_lab,
            "medicine": medicine,
            "stay": stay,
            "stay_value": round(sum(stay.mapped("amount_eligible")), 2),
        }

    def _refund_operation(self, admission, amount, on=D_MAR):
        """A posted inpatient refund entry (Dr Patient Advance Liability /
        Cr cash) with its accounting-operation provenance, as the admission
        bridge records one."""
        fx = self.fx
        move = self.env["account.move"].sudo().create({
            "move_type": "entry",
            "date": on,
            "journal_id": fx["values"]["advance_refund_journal_id"],
            "company_id": self.company.id,
            "line_ids": [
                (0, 0, {"name": "RA refund", "account_id": fx["advance"].id,
                        "debit": amount, "credit": 0.0}),
                (0, 0, {"name": "RA refund paid", "account_id": fx["cash"].id,
                        "debit": 0.0, "credit": amount}),
            ],
        })
        move.action_post()
        record = admission.sudo()
        with accounting_operation_capability():
            return self.env["hospital.admission.accounting.operation"].sudo().create({
                "kind": "refund",
                "request_token": uuid.uuid4().hex,
                "admission_id": record.id,
                "encounter_id": record.encounter_id.id,
                "patient_id": record.patient_id.id,
                "billing_account_id": self._account(record.encounter_id).id,
                "company_id": self.company.id,
                "currency_id": self.company.currency_id.id,
                "amount": amount,
                "accounting_date": on,
                "move_id": move.id,
                "performed_by_id": self.env.uid,
            })

    def _manual_revenue(self, amount, on=D_MAR):
        """A legacy / manual journal crediting the configured revenue account
        with NO charge provenance (the shape a legacy patient bill posts)."""
        fx = self.fx
        move = self.env["account.move"].sudo().create({
            "move_type": "entry",
            "date": on,
            "journal_id": fx["values"]["advance_receipt_journal_id"],
            "company_id": self.company.id,
            "line_ids": [
                (0, 0, {"name": "RA legacy bill", "account_id": fx["cash"].id,
                        "debit": amount, "credit": 0.0}),
                (0, 0, {"name": "RA legacy revenue", "account_id": fx["revenue"].id,
                        "debit": 0.0, "credit": amount}),
            ],
        })
        move.action_post()
        return move

    # ------------------------------------------------------------------
    # Reading the analytics
    # ------------------------------------------------------------------
    def _svc(self, user=None):
        return self.env["hospital.revenue.analytics"].with_user(user or self.finance_user)

    @staticmethod
    def _raw_filters(period=MAR, **extra):
        return dict({"date_from": period[0], "date_to": period[1]}, **extra)

    def _summary(self, period=MAR, user=None, **extra):
        return self._svc(user).summary(self._raw_filters(period, **extra))

    def _doctors(self, period=MAR, user=None, **extra):
        return self._svc(user).doctors(self._raw_filters(period, **extra))

    def _services(self, period=MAR, user=None, group=None, **extra):
        return self._svc(user).services(self._raw_filters(period, **extra), group=group)

    def _departments(self, period=MAR, user=None, **extra):
        return self._svc(user).departments(self._raw_filters(period, **extra))

    def _unbilled(self, period=MAR, user=None, **extra):
        return self._svc(user).unbilled(self._raw_filters(period, **extra))

    def _quality(self, period=MAR, user=None, **extra):
        return self._svc(user).data_quality(self._raw_filters(period, **extra))

    def _trend(self, period=MAR, grain=None, user=None, **extra):
        return self._svc(user).trend(self._raw_filters(period, **extra), grain=grain)

    @staticmethod
    def _row(rows, key, value):
        found = [row for row in rows if row.get(key) == value]
        return found[0] if found else None

    def _fact_rows(self, charges):
        return self.env["hospital.revenue.fact"].sudo().search([("charge_id", "in", charges.ids)])

    def _charge_fact(self, charge):
        return self.env["hospital.revenue.charge.fact"].sudo().browse(charge.id)
