"""Shared fixtures for the Admissions Slice 0 suites.

EVERY TEST HERE GOES THROUGH A REAL res.users WITH REAL GROUPS and calls the
model directly rather than a controller. That is the point: the boundary has to
hold for anyone who can reach the ORM, not merely for someone clicking a screen
that does not show the button. Nothing below passes a context flag, because a
context flag is client input and could never be the boundary.

The ward built here is given a department, because the nurse record rule in
yoya_clinical_bridge scopes by ward department and a ward without one is
invisible to every nurse. A fixture that omitted it would make the nurse tests
pass for the wrong reason.
"""
import uuid

from odoo import fields
from odoo.tests import TransactionCase

G_RECEPTIONIST = "hospital_management.group_hospital_receptionist"
G_DOCTOR = "hospital_management.group_hospital_doctor"
G_NURSE = "hospital_management.group_hospital_nurse"
G_PHARMACIST = "hospital_management.group_hospital_pharmacist"
G_LAB = "hospital_management.group_hospital_lab_technician"
G_ACCOUNTANT = "hospital_management.group_hospital_accountant"
G_MANAGER = "hospital_management.group_hospital_manager"
G_ADMIN = "hospital_management.group_hospital_system_administrator"
G_DPO = "hospital_management.group_hospital_data_protection_officer"


class InpatientAccountingFixture:
    """Shared by the admission model suites and the yoya_emr_api HTTP suites."""

    # ------------------------------------------------------------------
    # Inpatient accounting fixture
    # ------------------------------------------------------------------
    # With hospital_admission_accounting installed a refund is ATOMIC WITH
    # ITS ACCOUNTING and fails closed on a missing mapping. Suites that record
    # refunds therefore need one complete, company-scoped mapping: test
    # accounts and journals, every active configuration row pointing at them,
    # and an invoice product + exempt sale tax on the billing services in
    # play. Without the accounting modules this is a no-op. Created inside the
    # running TEST (cached on the test instance, never on the class), so it is
    # rolled back with that test and can never leak into the next one.
    def _accounting_fixture(self):
        if "hospital.admission.accounting.operation" not in self.env:
            return None
        cached = self.__dict__.get("_accounting")
        if cached is not None:
            # A first call made inside assertRaises ran in a savepoint that
            # was rolled back: rebuild instead of trusting a vanished fixture.
            if cached["advance"].exists() and cached["product"].exists():
                return cached
            self.__dict__.pop("_accounting_mapped_services", None)
        env = self.env(su=True)
        company = getattr(self, "company", None) or self.env.company
        tag = uuid.uuid4().hex[:4].upper()

        def account(code, name, account_type, reconcile=False):
            return env["account.account"].create({
                "code": "%s%s" % (code, tag), "name": "%s %s" % (name, tag),
                "account_type": account_type, "reconcile": reconcile,
                "company_ids": [(6, 0, company.ids)],
            })

        def journal(code, name, kind, default_account):
            return env["account.journal"].create({
                "name": "%s %s" % (name, tag), "code": "%s%s" % (code, tag[:3]),
                "type": kind, "company_id": company.id,
                "default_account_id": default_account.id,
            })

        cash = account("TIPC", "IP Cash", "asset_cash")
        bank = account("TIPB", "IP Bank", "asset_cash")
        mobile = account("TIPM", "IP Mobile", "asset_cash")
        advance = account("TIPA", "IP Patient Advances", "liability_current", reconcile=True)
        credit = account("TIPR", "IP Patient Credit", "liability_current", reconcile=True)
        revenue = account("TIPI", "IP Healthcare Revenue", "income")
        probe = env["res.partner"].with_company(company).create({"name": "IP receivable probe %s" % tag})
        receivable = probe.property_account_receivable_id
        values = {
            "receivable_account_id": receivable.id,
            "revenue_account_id": revenue.id,
            "journal_id": journal("S", "IP Invoices", "sale", revenue).id,
            "cash_account_id": cash.id,
            "bank_account_id": bank.id,
            "mobile_money_account_id": mobile.id,
            "patient_advance_liability_account_id": advance.id,
            "patient_credit_liability_account_id": credit.id,
            "advance_receipt_journal_id": journal("R", "IP Advance Receipts", "general", cash).id,
            "advance_application_journal_id": journal("A", "IP Advance Application", "general", advance).id,
            "advance_refund_journal_id": journal("F", "IP Advance Refunds", "general", cash).id,
        }
        values["invoice_journal_id"] = values["journal_id"]
        Config = env["hospital.billing.accounting.config"].with_context(active_test=False)
        for source_type, _label in Config.SOURCE_TYPES:
            config = Config.search(
                [("company_id", "=", company.id), ("source_type", "=", source_type)], limit=1
            )
            if config:
                config.write(dict(values, active=True))
            else:
                Config.create(dict(values, company_id=company.id, source_type=source_type))
        product = env["product.product"].create({
            "name": "IP Hospital Service %s" % tag, "type": "service", "sale_ok": True,
        })
        product.product_tmpl_id.with_company(company).write({"property_account_income_id": revenue.id})
        exempt = env["account.tax"].search([
            ("company_id", "=", company.id), ("type_tax_use", "=", "sale"),
            ("amount", "=", 0.0), ("invoice_label", "=", "tax06"), ("active", "=", True),
        ], limit=1) or env["account.tax"].create({
            "name": "IP 0%% Exempt %s" % tag, "amount": 0.0, "amount_type": "percent",
            "type_tax_use": "sale", "invoice_label": "tax06", "company_id": company.id,
        })
        self._accounting = {
            "cash": cash, "bank": bank, "mobile": mobile, "advance": advance,
            "credit": credit, "revenue": revenue, "receivable": receivable,
            "product": product, "exempt_tax": exempt, "values": values, "company": company,
        }
        return self._accounting

    def _map_services_for_accounting(self):
        """Give every exempt billing service in play the fixture's invoice
        product and exempt tax. Called just before a refund or a close, so
        services a suite created after setUpClass are covered too."""
        fixture = self._accounting_fixture()
        if not fixture:
            return None
        # Each service is mapped ONCE per test: a test that later removes a
        # mapping on purpose keeps it removed.
        mapped = self.__dict__.setdefault("_accounting_mapped_services", set())
        services = self.env["hospital.billing.service"].sudo().with_context(active_test=False).search([
            ("company_id", "in", [False, fixture["company"].id]), ("tax_treatment", "=", "exempt"),
            ("id", "not in", list(mapped)),
        ])
        mapped.update(services.ids)
        services.write({
            "invoice_product_id": fixture["product"].id,
            "invoice_tax_ids": [(6, 0, fixture["exempt_tax"].ids)],
        })
        return fixture


class AdmissionCase(InpatientAccountingFixture, TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company

        cls.department = cls.env["hospital.department"].sudo().create(
            {
                "name": "Admissions Test Department %s" % uuid.uuid4().hex[:6],
                "code": "ADT%s" % uuid.uuid4().hex[:6].upper(),
            }
        )
        cls.other_department = cls.env["hospital.department"].sudo().create(
            {
                "name": "Admissions Other Department %s" % uuid.uuid4().hex[:6],
                "code": "ADO%s" % uuid.uuid4().hex[:6].upper(),
            }
        )

        cls.receptionist = cls._make_user("adm_reception", [G_RECEPTIONIST])
        cls.nurse = cls._make_user("adm_nurse", [G_NURSE])
        cls.other_nurse = cls._make_user("adm_nurse_other", [G_NURSE])
        cls.doctor_user = cls._make_user("adm_doctor", [G_DOCTOR])
        cls.other_doctor_user = cls._make_user("adm_doctor_other", [G_DOCTOR])
        cls.pharmacist = cls._make_user("adm_pharmacist", [G_PHARMACIST])
        cls.lab_tech = cls._make_user("adm_lab", [G_LAB])
        cls.accountant = cls._make_user("adm_accountant", [G_ACCOUNTANT])
        cls.manager = cls._make_user("adm_manager", [G_MANAGER])
        cls.admin = cls._make_user("adm_admin", [G_ADMIN])

        # The nurse roster lives on res.users and is contributed by
        # yoya_clinical_bridge. Rostering the nurse to the test department is
        # what puts the fixture ward inside their record rule.
        if "yoya_permitted_department_ids" in cls.env["res.users"]._fields:
            cls.nurse.sudo().write(
                {"yoya_permitted_department_ids": [(6, 0, [cls.department.id])]}
            )
            cls.other_nurse.sudo().write(
                {"yoya_permitted_department_ids": [(6, 0, [cls.other_department.id])]}
            )

        cls.doctor = cls.env["hospital.doctor"].sudo().create(
            {"name": "Admissions Test Doctor", "user_id": cls.doctor_user.id}
        )
        cls.other_doctor = cls.env["hospital.doctor"].sudo().create(
            {"name": "Admissions Other Doctor", "user_id": cls.other_doctor_user.id}
        )

        cls.ward = cls._make_ward("Slice0 Ward A", cls.department)
        cls.room = cls._make_room(cls.ward, "S0-A-101")
        cls.bed_a = cls._make_bed(cls.room, "S0-A-101-1")
        cls.bed_b = cls._make_bed(cls.room, "S0-A-101-2")

        cls.ward_other = cls._make_ward("Slice0 Ward B", cls.other_department)
        cls.room_other = cls._make_room(cls.ward_other, "S0-B-201")
        cls.bed_other = cls._make_bed(cls.room_other, "S0-B-201-1")

    # ------------------------------------------------------------------
    # Factories
    # ------------------------------------------------------------------
    @classmethod
    def _make_user(cls, login, groups):
        return cls.env["res.users"].sudo().create(
            {
                "name": login,
                "login": "%s_%s" % (login, uuid.uuid4().hex[:8]),
                "company_id": cls.company.id,
                "company_ids": [(6, 0, cls.company.ids)],
                "groups_id": [
                    (6, 0, [cls.env.ref("base.group_user").id]
                     + [cls.env.ref(g).id for g in groups])
                ],
            }
        )

    @classmethod
    def _make_ward(cls, name, department=None):
        return cls.env["hospital.ward"].sudo().create(
            {
                "name": "%s %s" % (name, uuid.uuid4().hex[:6]),
                "code": "W%s" % uuid.uuid4().hex[:6].upper(),
                "ward_type": "medical",
                "department_id": department.id if department else False,
                "company_id": cls.company.id,
                "admission_fee": 500.0,
                "daily_ward_rate": 800.0,
            }
        )

    @classmethod
    def _make_room(cls, ward, name):
        return cls.env["hospital.room"].sudo().create(
            {
                "name": "%s %s" % (name, uuid.uuid4().hex[:4]),
                "code": "R%s" % uuid.uuid4().hex[:6].upper(),
                "ward_id": ward.id,
                "room_type": "shared",
            }
        )

    @classmethod
    def _make_bed(cls, room, name):
        return cls.env["hospital.bed"].sudo().create(
            {
                "name": "%s %s" % (name, uuid.uuid4().hex[:4]),
                "code": "B%s" % uuid.uuid4().hex[:6].upper(),
                "room_id": room.id,
                "bed_type": "standard",
            }
        )

    def _patient(self, name="Slice0 Patient"):
        return self.env["hospital.patient"].sudo().create(
            {"name": "%s %s" % (name, uuid.uuid4().hex[:6])}
        )

    # ADVANCE SLICE. A self-pay request needs the doctor's estimate and the full
    # advance -- or an authorized emergency bypass -- before a bed. Suites that
    # are NOT about admission money clear their visits through that existing,
    # audited emergency route (a Hospital Manager, with a reason); it touches
    # clearance only, never a delivered amount. Gate tests set this False.
    CLEAR_ADMISSION_VISITS = True

    def _clear_visit(self, encounter):
        if self.CLEAR_ADMISSION_VISITS:
            encounter.with_user(self.manager).write({
                "emergency_bypass": True,
                "emergency_bypass_reason": "Test fixture: admission money is not under test here.",
            })

    def _encounter(self, patient, state="active", **overrides):
        """An open episode for the patient.

        Created through sudo() rather than the reception workflow so these
        suites test admission authority rather than registration. The
        single-active-episode guard in yoya_reception_bridge still applies to
        this create, which is why each test uses a fresh patient.
        """
        vals = {
            "patient_id": patient.id,
            "encounter_type": "outpatient",
            "company_id": self.company.id,
            "opened_at": fields.Datetime.now(),
        }
        vals.update(overrides)
        encounter = self.env["hospital.encounter"].sudo().create(vals)
        self._clear_visit(encounter)
        if state and encounter.state != state:
            encounter.sudo().write({"state": state})
        return encounter

    def _draft(self, patient=None, bed=None, encounter=True, **overrides):
        """A draft admission ready to confirm."""
        patient = patient or self._patient()
        if encounter:
            self._encounter(patient)
        bed = self.bed_a if bed is None else bed
        vals = {
            "patient_id": patient.id,
            "physician_id": self.doctor.id,
            "company_id": self.company.id,
            "admission_date": fields.Datetime.now(),
        }
        if bed:
            vals.update(
                {
                    "ward_id": bed.ward_id.id,
                    "room_id": bed.room_id.id,
                    "bed_id": bed.id,
                }
            )
        vals.update(overrides)
        return self.env["hospital.admission"].sudo().create(vals)

    def _admitted(self, patient=None, bed=None, **overrides):
        admission = self._draft(patient=patient, bed=bed, **overrides)
        admission.action_confirm_admission()
        return admission

    def _raw(self, sql, params):
        """Reach past the ORM to build a corrupt shape the guards must catch.

        FLUSH FIRST, AND INVALIDATE WITHOUT FLUSHING AFTER. Odoo defers writes:
        _set_occupancy()'s write lives in the cache until something flushes it.
        A raw UPDATE issued while that write is still pending is silently undone
        by the next flush -- and env.invalidate_all() flushes by default, so the
        obvious spelling of this helper quietly does nothing. Both arguments
        below are load-bearing.
        """
        self.env.flush_all()
        self.cr.execute(sql, params)
        self.env.invalidate_all(flush=False)
