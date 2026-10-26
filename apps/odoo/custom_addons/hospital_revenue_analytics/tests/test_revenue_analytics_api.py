"""The revenue analytics HTTP API: the role boundary as the browser meets it.

Real users with real groups, a real session, real routes. Run on a FREE
--http-port (never 8069: the Windows service holds it and Windows lets the test
server co-bind silently).
"""
import json
import uuid

from odoo.tests import HttpCase, tagged

BASE = "/yoya-emr/api/v1/revenue-analytics"
QUERY = "?date_from=2031-03-01&date_to=2031-03-31"
G_FINANCE = "hospital_revenue_analytics.group_hospital_revenue_analytics"
G_EXECUTIVE = "hospital_revenue_analytics.group_hospital_executive"
G_ACCOUNTANT = "hospital_management.group_hospital_accountant"
G_NURSE = "hospital_management.group_hospital_nurse"
G_DOCTOR = "hospital_management.group_hospital_doctor"
G_MANAGER = "hospital_management.group_hospital_manager"
G_CASHIER = "hospital_billing.group_hospital_cashier"

PATIENT_KEYS = ("patient_id", "patient_name", "encounter_id", "admission_id", "charge_id")


@tagged("post_install", "-at_install", "revenue_analytics", "revenue_analytics_api")
class TestRevenueAnalyticsApi(HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.users = {
            name: cls._user(name, groups)
            for name, groups in (
                ("finance", [G_FINANCE]),
                ("accountant", [G_ACCOUNTANT]),
                ("executive", [G_EXECUTIVE]),
                ("nurse", [G_NURSE]),
                ("doctor", [G_DOCTOR]),
                ("manager", [G_MANAGER]),
                ("cashier", [G_CASHIER]),
            )
        }

    @classmethod
    def _user(cls, name, groups):
        login = "ra_api_%s_%s" % (name, uuid.uuid4().hex[:6])
        return cls.env["res.users"].sudo().create({
            "name": login,
            "login": login,
            "password": login,
            "company_id": cls.env.company.id,
            "company_ids": [(6, 0, cls.env.company.ids)],
            "groups_id": [(6, 0, [cls.env.ref("base.group_user").id]
                           + [cls.env.ref(group).id for group in groups])],
        })

    def _get(self, name, path):
        user = self.users[name]
        self.authenticate(user.login, user.login)
        response = self.url_open(BASE + path)
        return response.status_code, json.loads(response.content)

    def test_session_reports_capabilities_without_gating(self):
        status, body = self._get("executive", "/session")
        self.assertEqual(status, 200)
        caps = body["data"]["capabilities"]
        self.assertTrue(caps["executive"])
        self.assertFalse(caps["finance"])
        self.assertFalse(caps["data_quality"])
        self.assertFalse(caps["patient_drilldown"])
        status, body = self._get("nurse", "/session")
        self.assertEqual(status, 200)
        self.assertFalse(body["data"]["capabilities"]["analytics"])
        self.assertEqual(body["data"]["companies"], [])

    def test_finance_and_accountant_reach_every_endpoint(self):
        for name in ("finance", "accountant"):
            for path in ("/summary", "/trend", "/doctors", "/services", "/departments",
                         "/unbilled", "/data-quality"):
                status, body = self._get(name, path + QUERY)
                self.assertEqual(status, 200, (name, path, body))
                self.assertTrue(body["success"])

    def test_executive_reads_aggregates_and_nothing_else(self):
        for path in ("/summary", "/trend", "/doctors", "/services", "/departments"):
            status, body = self._get("executive", path + QUERY)
            self.assertEqual(status, 200, (path, body))
            text = json.dumps(body)
            for key in PATIENT_KEYS:
                self.assertNotIn('"%s"' % key, text, (path, key))
        for path in ("/unbilled", "/data-quality"):
            status, body = self._get("executive", path + QUERY)
            self.assertEqual(status, 403, path)
            self.assertEqual(body["error"]["code"], "revenue_analytics_not_authorized")

    def test_clinical_cashier_and_manager_are_refused(self):
        for name in ("nurse", "doctor", "manager", "cashier"):
            for path in ("/summary", "/doctors", "/data-quality"):
                status, body = self._get(name, path + QUERY)
                self.assertEqual(status, 403, (name, path))
                self.assertEqual(body["error"]["code"], "revenue_analytics_not_authorized")

    def test_bad_filters_are_400_and_foreign_company_403(self):
        status, body = self._get("finance", "/summary?date_from=2031-04-01&date_to=2031-03-01")
        self.assertEqual((status, body["error"]["code"]), (400, "invalid_date_range"))
        other = self.env["res.company"].sudo().create({"name": "RA API Other %s" % uuid.uuid4().hex[:4]})
        status, body = self._get("finance", "/summary%s&company=%s" % (QUERY, other.id))
        self.assertEqual((status, body["error"]["code"]), (403, "company_not_allowed"))

    def test_there_is_no_write_route(self):
        user = self.users["finance"]
        self.authenticate(user.login, user.login)
        response = self.url_open(BASE + "/summary" + QUERY, data=json.dumps({}),
                                 headers={"Content-Type": "application/json"})
        self.assertNotEqual(response.status_code, 200)
