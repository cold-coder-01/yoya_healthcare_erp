"""Pharmacy Desk Slice 1: the READ-ONLY counter workstation API.

WHAT THESE TESTS ARE FOR
------------------------
  1. THE GATE. Exactly Pharmacist, Manager and System Administrator open the
     desk. Every other hospital role -- including the Doctor, who prescribes,
     and the Cashier, who takes the money -- gets 403 from every route.

  2. THE LANES are derived from facts, not from `state`, are mutually
     exclusive, and legacy contradictions land in `anomaly`, never `completed`.

  3. THE COUNTS describe the whole filter scope and always match the rows.

  4. CONFIDENTIALITY. Billing crosses only as booleans. No amount, currency,
     charge, receipt, invoice or clearance message -- for Manager and Admin
     exactly as for a pharmacist -- and no pharmacist provenance claim.

  5. READ ONLY. No route accepts a write.

HOW THE FIXTURES ARE BUILT
--------------------------
Healthy records go through the REAL workflow: a doctor confirms the
prescription, the pharmacist sets the intended quantity and marks ready, the
cashier pays, the pharmacist validates. Legacy contradictions are reproduced
through the models' own in-process capabilities (the only way left to create
them), exactly as the UAT database's older rows came to exist.

ISOLATION. Every worklist call is scoped by the fixture patient's unique name,
so these tests read only their own rows on a database with real legacy data.
"""
import json
from unittest.mock import patch
from urllib.parse import urlencode

from odoo.tests import tagged

from odoo.addons.hospital_pharmacy.models.pharmacy_authority import (
    prescription_workflow_capability,
)

from ..services.pharmacy_desk_serializers import (
    ANOMALY_MESSAGES,
    PHARMACY_DESK_ACTIVE_LANES,
    PHARMACY_DESK_LANES,
)
from ..services.reception_scope import PHARMACY_DESK_GROUPS, may_pharmacy_desk
from .test_doctor_medication_api import MedicationCase

SESSION = "/yoya-emr/api/v1/pharmacy/session"
WORKLIST = "/yoya-emr/api/v1/pharmacy/worklist"
DETAIL = "/yoya-emr/api/v1/pharmacy/dispenses/%s"

G_SYSADMIN = "hospital_management.group_hospital_system_administrator"
G_LAB = "hospital_management.group_hospital_lab_technician"
G_DPO = "hospital_management.group_hospital_data_protection_officer"
G_RAD_TECH = "hospital_radiology.group_hospital_radiology_technician"
G_INSURANCE = "hospital_billing.group_hospital_insurance_officer"

FORBIDDEN_KEY_FRAGMENTS = (
    "amount", "price", "invoice", "receipt", "balance", "payment", "payer",
    "tariff", "currency", "subtotal", "clearance_message", "allocation",
    "account", "fiscal", "charge_line", "pharmacist_id", "dispensed_by",
    "batch", "expiry", "on_hand", "available_quantity", "unit_cost",
)
FORBIDDEN_TEXT = ("ETB", "Birr", "Patient-payable", "120.0", "remaining due")


def _walk_keys(value):
    if isinstance(value, dict):
        for key, child in value.items():
            yield key
            yield from _walk_keys(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_keys(child)


class PharmacyDeskCase(MedicationCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.sysadmin_password = "pd-admin-pw-1"
        cls.sysadmin = cls._make_user("pd_admin", cls.sysadmin_password, [G_SYSADMIN])
        cls.lab_password = "pd-lab-pw-1"
        cls.lab = cls._make_user("pd_lab", cls.lab_password, [G_LAB])
        cls.dpo_password = "pd-dpo-pw-1"
        cls.dpo = cls._make_user("pd_dpo", cls.dpo_password, [G_DPO])
        cls.rad_password = "pd-rad-pw-1"
        cls.rad = cls._make_user("pd_rad", cls.rad_password, [G_RAD_TECH])
        cls.officer_password = "pd-ins-pw-1"
        cls.officer = cls._make_user("pd_ins", cls.officer_password, [G_INSURANCE])

    # ------------------------------------------------------------------
    def _desk_get(self, url, user=None, password=None, **params):
        """URL-encoded (patient names have spaces), re-authenticated per call
        because HttpCase shares one session across a class."""
        self._auth(user or self.pharmacist, password or self.pharmacist_password)
        query = urlencode({k: v for k, v in params.items() if v is not None})
        response = self.url_open("%s%s" % (url, "?" + query if query else ""))
        return response, json.loads(response.text)

    def _worklist(self, q, user=None, password=None, **params):
        response, payload = self._desk_get(WORKLIST, user, password, q=q, **params)
        self.assertEqual(response.status_code, 200, payload)
        return payload["data"]

    def _detail(self, dispense, user=None, password=None):
        response, payload = self._desk_get(DETAIL % dispense.id, user, password)
        self.assertEqual(response.status_code, 200, payload)
        return payload["data"]["dispense"]

    def _visit(self):
        appointment, encounter = self._register()
        self._pay(encounter)
        return appointment.sudo(), encounter.sudo()

    def _rx(self, appointment, medicine, qty=10.0):
        """A prescription confirmed BY THE DOCTOR, and the dispense it composed."""
        prescription = self.env["hospital.prescription"].with_user(self.doctor_user).create({
            "patient_id": appointment.patient_id.id,
            "physician_id": self.doctor.id,
            "appointment_id": appointment.id,
            "line_ids": [(0, 0, {
                "medicine_id": medicine.id,
                "medicine_name": medicine.name,
                "quantity": qty,
                "dosage": "500mg",
                "route": "oral",
            })],
        })
        prescription.with_user(self.doctor_user).action_confirm()
        return prescription.sudo(), prescription.sudo().pharmacy_dispense_ids[:1]

    def _ready(self, dispense, intended):
        dispense.line_ids.with_user(self.pharmacist).write({"dispensed_quantity": intended})
        dispense.with_user(self.pharmacist).action_mark_ready()
        self.env.invalidate_all()

    def _pay_and_validate(self, dispense, encounter):
        dispense.sudo()._ensure_pharmacy_billing()
        self._pay(encounter)
        dispense.with_user(self.pharmacist).action_mark_dispensed()
        self.env.invalidate_all()

    def _assert_no_money(self, payload, label):
        for key in _walk_keys(payload):
            for fragment in FORBIDDEN_KEY_FRAGMENTS:
                self.assertNotIn(fragment, str(key).lower(), "%s leaked key %r" % (label, key))
        text = json.dumps(payload)
        for marker in FORBIDDEN_TEXT:
            self.assertNotIn(marker, text, "%s leaked %r" % (label, marker))

    def _lane_fixtures(self):
        """One visit, one patient, one record per lane. Returns (patient name,
        {expected lane: dispense})."""
        appointment, encounter = self._visit()
        name = appointment.patient_id.name
        out = {}

        # Everything that PAYS comes first: a payment clears the whole
        # encounter, so an unpaid record made earlier would be cleared too.
        _rx, d = self._rx(appointment, self.amoxil, qty=6.0)
        self._ready(d, 6.0)
        self._pay(encounter)
        out["ready_to_validate"] = d

        _rx, d = self._rx(appointment, self.cetiriz, qty=8.0)
        self._ready(d, 3.0)
        self._pay_and_validate(d, encounter)
        out["partially_supplied"] = d

        _rx, d = self._rx(appointment, self.amoxil, qty=2.0)
        self._ready(d, 2.0)
        self._pay_and_validate(d, encounter)
        out["completed"] = d

        _rx, d = self._rx(appointment, self.amoxil)
        out["awaiting_preparation"] = d

        _rx, d = self._rx(appointment, self.cetiriz)
        self._ready(d, 5.0)
        out["awaiting_clearance"] = d

        _rx, d = self._rx(appointment, self.dry, qty=4.0)
        self._ready(d, 4.0)
        out["blocked"] = d

        # LEGACY: marked dispensed with no delivery evidence at all.
        _rx, d = self._rx(appointment, self.cetiriz, qty=5.0)
        d.line_ids.with_user(self.pharmacist).write({"dispensed_quantity": 5.0})
        d._write_state("dispensed")
        out["anomaly"] = d

        _rx, d = self._rx(appointment, self.amoxil, qty=3.0)
        d.with_user(self.pharmacist).action_cancel()
        out["cancelled"] = d
        return name, out


# ===========================================================================
# 1. Authorization
# ===========================================================================
@tagged("post_install", "-at_install", "pharmacy_desk")
class TestPharmacyDeskAuthorization(PharmacyDeskCase):

    def _probe(self):
        appointment, _encounter = self._visit()
        _rx, dispense = self._rx(appointment, self.amoxil)
        return dispense

    def _allowed(self, user, password):
        dispense = self._probe()
        for url in (SESSION, WORKLIST, DETAIL % dispense.id):
            response, payload = self._desk_get(url, user, password)
            self.assertEqual(response.status_code, 200, (user.login, url, payload))
            self.assertTrue(payload["success"])

    def _denied(self, user, password):
        dispense = self._probe()
        for url in (SESSION, WORKLIST, DETAIL % dispense.id):
            response, payload = self._desk_get(url, user, password)
            self.assertEqual(response.status_code, 403, (user.login, url))
            self.assertEqual(payload["error"]["code"], "pharmacy_desk_not_authorized")
            self.assertNotIn(dispense.name, response.text)

    def test_01_pharmacist_allowed(self):
        self._allowed(self.pharmacist, self.pharmacist_password)

    def test_02_manager_allowed(self):
        self._allowed(self.manager, self.manager_password)

    def test_03_sysadmin_allowed(self):
        self._allowed(self.sysadmin, self.sysadmin_password)

    def test_04_doctor_denied(self):
        self._denied(self.doctor_user, self.doctor_password)

    def test_05_nurse_denied(self):
        self._denied(self.nurse, self.nurse_password)

    def test_06_cashier_denied(self):
        self._denied(self.cashier, self.cashier_password)

    def test_07_accountant_denied(self):
        self._denied(self.accountant, self.accountant_password)

    def test_08_other_roles_denied(self):
        for user, password in (
            (self.receptionist, self.receptionist_password),
            (self.front_desk, self.fd_password),
            (self.lab, self.lab_password),
            (self.dpo, self.dpo_password),
            (self.rad, self.rad_password),
            (self.officer, self.officer_password),
        ):
            self._denied(user, password)

    def test_09_gate_matches_the_model_operator_groups(self):
        from odoo.addons.hospital_pharmacy.models.pharmacy_dispense import (
            PHARMACY_OPERATOR_GROUPS,
        )
        self.assertEqual(tuple(PHARMACY_DESK_GROUPS), tuple(PHARMACY_OPERATOR_GROUPS))
        self.assertTrue(may_pharmacy_desk(self.env(user=self.pharmacist)))
        self.assertFalse(may_pharmacy_desk(self.env(user=self.doctor_user)))

    def test_10_session_contract_is_read_only(self):
        response, payload = self._desk_get(SESSION)
        data = payload["data"]
        self.assertEqual(set(data), {"user", "company", "roles", "capabilities", "read_only"})
        self.assertEqual(data["capabilities"], {"pharmacy_desk": True})
        self.assertTrue(data["read_only"])
        self.assertEqual(
            data["roles"], {"pharmacist": True, "manager": False, "system_admin": False}
        )

    def test_11_no_write_route_exists(self):
        dispense = self._probe()
        self._auth(self.pharmacist, self.pharmacist_password)
        for url in (WORKLIST, DETAIL % dispense.id, DETAIL % dispense.id + "/validate",
                    DETAIL % dispense.id + "/ready"):
            response = self.url_open(url, data="{}", headers={"Content-Type": "application/json"})
            self.assertIn(response.status_code, (404, 405), url)
        dispense.invalidate_recordset()
        self.assertEqual(dispense.state, "draft")

    def test_12_missing_record_is_404(self):
        response, payload = self._desk_get(DETAIL % 999999999)
        self.assertEqual(response.status_code, 404)
        self.assertEqual(payload["error"]["code"], "pharmacy_dispense_not_found")


# ===========================================================================
# 2. Lanes, counts and confidentiality
# ===========================================================================
@tagged("post_install", "-at_install", "pharmacy_desk")
class TestPharmacyDeskLanes(PharmacyDeskCase):

    def test_20_every_lane_is_derived_and_exclusive(self):
        name, expected = self._lane_fixtures()
        data = self._worklist(name, status=",".join(PHARMACY_DESK_LANES))
        rows = {row["id"]: row for row in data["rows"]}
        for lane, dispense in expected.items():
            self.assertIn(dispense.id, rows, lane)
            self.assertEqual(rows[dispense.id]["lane"], lane, (lane, rows[dispense.id]))
        # Each record appears once, in exactly one lane.
        self.assertEqual(len(rows), len(data["rows"]))
        self.assertEqual(set(expected), set(PHARMACY_DESK_LANES))

        blocked = rows[expected["blocked"].id]
        self.assertEqual(blocked["reason"], "stock_insufficient")
        self.assertTrue(blocked["stock_short"])
        self.assertTrue(rows[expected["awaiting_clearance"].id]["billing_blocked"])
        self.assertFalse(rows[expected["ready_to_validate"].id]["billing_blocked"])
        anomaly = rows[expected["anomaly"].id]
        self.assertEqual(anomaly["reason"], "dispensed_unreconciled")
        self.assertIn(ANOMALY_MESSAGES["dispensed_unreconciled"], anomaly["reason_message"])
        self.assertEqual(anomaly["state"], "dispensed", "the authoritative state still travels")

    def test_21_counts_match_rows_for_every_lane(self):
        name, _expected = self._lane_fixtures()
        everything = self._worklist(name, status=",".join(PHARMACY_DESK_LANES))
        summary = everything["summary"]
        self.assertTrue(everything["meta"]["summary_exact"])
        for lane in PHARMACY_DESK_LANES:
            data = self._worklist(name, status=lane)
            self.assertEqual(len(data["rows"]), summary[lane], lane)
            self.assertTrue(all(row["lane"] == lane for row in data["rows"]), lane)
            # Selecting a lane never changes the counts.
            self.assertEqual(data["summary"], summary)
        self.assertEqual(summary["total"], len(everything["rows"]))
        self.assertEqual(
            summary["active"], sum(summary[lane] for lane in PHARMACY_DESK_ACTIVE_LANES)
        )
        default = self._worklist(name)
        self.assertEqual(len(default["rows"]), summary["active"])
        self.assertNotIn("completed", {row["lane"] for row in default["rows"]})

    def test_22_no_money_for_any_role(self):
        name, expected = self._lane_fixtures()
        for user, password in (
            (self.pharmacist, self.pharmacist_password),
            (self.manager, self.manager_password),
            (self.sysadmin, self.sysadmin_password),
        ):
            data = self._worklist(name, user, password, status=",".join(PHARMACY_DESK_LANES))
            self._assert_no_money(data, "worklist/%s" % user.login)
            for dispense in expected.values():
                self._assert_no_money(self._detail(dispense, user, password), "detail")

    def test_23_detail_line_contract(self):
        _name, expected = self._lane_fixtures()
        detail = self._detail(expected["partially_supplied"])
        self.assertEqual(detail["lane"], "partially_supplied")
        self.assertEqual(detail["prescriber"]["name"], self.doctor.name)
        self.assertTrue(detail["patient"]["name"])
        self.assertIn("mrn", detail["patient"])
        self.assertTrue(detail["prescription"]["code"])
        self.assertNotIn("pharmacist", detail)
        (line,) = detail["lines"]
        self.assertEqual(line["prescribed_quantity"], 8.0)
        self.assertEqual(line["intended_quantity"], 3.0)
        self.assertEqual(line["delivered_quantity"], 3.0)
        self.assertEqual(line["consumed_quantity"], 3.0)
        self.assertEqual(line["remaining_quantity"], 5.0)
        self.assertEqual(line["pending_increment"], 0.0)
        self.assertTrue(line["billing_mapped"])
        self.assertTrue(line["inventory_mapped"])
        self.assertEqual(line["stock_basis"], "remaining")
        self.assertTrue(line["stock_sufficient"])

    def test_24_reading_changes_nothing(self):
        name, expected = self._lane_fixtures()
        before = {d.id: (d.state, tuple(d.line_ids.mapped("dispensed_quantity"))) for d in expected.values()}
        charges = self.env["hospital.charge.line"].sudo().search_count([])
        self._worklist(name, status=",".join(PHARMACY_DESK_LANES))
        for dispense in expected.values():
            self._detail(dispense)
        self.env.invalidate_all()
        after = {d.id: (d.state, tuple(d.line_ids.mapped("dispensed_quantity"))) for d in expected.values()}
        self.assertEqual(before, after)
        self.assertEqual(self.env["hospital.charge.line"].sudo().search_count([]), charges)


# ===========================================================================
# 3. Legacy defensiveness
# ===========================================================================
@tagged("post_install", "-at_install", "pharmacy_desk")
class TestPharmacyDeskLegacy(PharmacyDeskCase):

    def _lane(self, name, dispense):
        data = self._worklist(name, status=",".join(PHARMACY_DESK_LANES))
        rows = {row["id"]: row for row in data["rows"]}
        return rows[dispense.id]

    def test_30_prescription_header_dispensed_over_partial_dispense(self):
        appointment, encounter = self._visit()
        prescription, dispense = self._rx(appointment, self.cetiriz, qty=8.0)
        self._ready(dispense, 3.0)
        self._pay_and_validate(dispense, encounter)
        with prescription_workflow_capability():
            prescription.write({"state": "dispensed"})
        row = self._lane(appointment.patient_id.name, dispense)
        self.assertEqual((row["lane"], row["reason"]), ("anomaly", "prescription_header_conflict"))

    def test_31_confirmed_header_over_completed_dispense_is_normal(self):
        appointment, encounter = self._visit()
        prescription, dispense = self._rx(appointment, self.amoxil, qty=2.0)
        self._ready(dispense, 2.0)
        self._pay_and_validate(dispense, encounter)
        self.assertEqual(prescription.state, "confirmed")
        row = self._lane(appointment.patient_id.name, dispense)
        self.assertEqual(row["lane"], "completed")

    def test_32_flagged_partial_without_delivery(self):
        appointment, _encounter = self._visit()
        _rx, dispense = self._rx(appointment, self.amoxil)
        self._ready(dispense, 5.0)
        dispense._write_state("partial")
        row = self._lane(appointment.patient_id.name, dispense)
        self.assertEqual((row["lane"], row["reason"]), ("anomaly", "partial_without_delivery"))

    def test_33_missing_billing_mapping_goes_blocked(self):
        appointment, _encounter = self._visit()
        _rx, dispense = self._rx(appointment, self.unbilled)
        row = self._lane(appointment.patient_id.name, dispense)
        self.assertEqual((row["lane"], row["reason"]), ("blocked", "billing_mapping_missing"))
        detail = self._detail(dispense)
        self.assertFalse(detail["lines"][0]["billing_mapped"])

    def test_34_missing_inventory_mapping_goes_blocked(self):
        appointment, _encounter = self._visit()
        _rx, dispense = self._rx(appointment, self.unstocked)
        row = self._lane(appointment.patient_id.name, dispense)
        self.assertEqual((row["lane"], row["reason"]), ("blocked", "inventory_mapping_missing"))

    def test_35_ready_without_charge_or_context_does_not_crash(self):
        """The UAT DISP00045 shape: ready with an intent, an unbillable
        medicine and no charge behind it."""
        appointment, _encounter = self._visit()
        _rx, dispense = self._rx(appointment, self.unbilled, qty=2.0)
        dispense.line_ids.with_user(self.pharmacist).write({"dispensed_quantity": 2.0})
        dispense._write_state("ready")
        row = self._lane(appointment.patient_id.name, dispense)
        self.assertEqual(row["lane"], "blocked")
        self.assertTrue(row["billing_blocked"])
        self._detail(dispense)

    def test_36_unreadable_record_is_contained(self):
        appointment, _encounter = self._visit()
        _rx, dispense = self._rx(appointment, self.amoxil)
        def boom(self):
            raise ValueError("corrupt")

        with patch.object(
            type(self.env["hospital.pharmacy.dispense"]), "_pharmacy_desk_stock_facts", boom
        ):
            row = self._lane(appointment.patient_id.name, dispense)
            detail = self._detail(dispense)
        self.assertEqual((row["lane"], row["reason"]), ("anomaly", "unreadable_record"))
        self.assertEqual(detail["lines"], [])
