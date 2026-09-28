"""Admissions Slice 1: the READ-ONLY Admissions Desk and Bed Board API.

WHAT THESE TESTS ARE FOR
------------------------
  1. THE GATE. Receptionist, Nurse (incl. Front Desk Nurse), Doctor, Manager
     and System Administrator open the desk. Pharmacist, Lab Technician, DPO,
     Cashier, Radiology Technician and Accountant get 403 from EVERY route --
     before any record is read, so no count and no existence leaks.
  2. SCOPE comes from the record rules, never from the desk: a doctor sees
     their own patients, a nurse their permitted departments.
  3. THE LANES are derived, mutually exclusive, and the counts always match
     the rows.
  4. THE BED BOARD never names a patient whose admission the caller cannot
     read, and cannot be probed for one through its search box.
  5. LEGACY. A discharged admission with no visit is an ordinary discharge,
     with an honest `not_applicable` clearance -- never a fabricated False.
  6. READ ONLY. No route writes; viewing writes no audit row; every workflow
     capability is False, for administrators too.

Every HTTP call goes through a real res.users with a real password. Fixture
patients carry a unique token so these tests read only their own rows on a
clone that already holds real data.
"""
import inspect
import json
import logging
import os
import uuid
from urllib.parse import urlencode

from odoo import fields
from odoo.tests import HttpCase, tagged

from ..services.admissions_desk_serializers import LANE_ORDER, classify_batch
from ..services.reception_scope import ADMISSIONS_DESK_GROUPS, may_admissions_desk

_logger = logging.getLogger(__name__)

API = "/yoya-emr/api/v1/admissions"
SESSION = API + "/session"
WORKLIST = API + "/worklist"
WARDS = API + "/wards"
BEDS = API + "/beds"
DETAIL = API + "/%s"

G_RECEPTIONIST = "hospital_management.group_hospital_receptionist"
G_NURSE = "hospital_management.group_hospital_nurse"
G_DOCTOR = "hospital_management.group_hospital_doctor"
G_MANAGER = "hospital_management.group_hospital_manager"
G_SYSADMIN = "hospital_management.group_hospital_system_administrator"
G_PHARMACIST = "hospital_management.group_hospital_pharmacist"
G_LAB = "hospital_management.group_hospital_lab_technician"
G_DPO = "hospital_management.group_hospital_data_protection_officer"
G_ACCOUNTANT = "hospital_management.group_hospital_accountant"
G_CASHIER = "hospital_billing.group_hospital_cashier"
G_RAD_TECH = "hospital_radiology.group_hospital_radiology_technician"
G_FRONT_DESK_NURSE = "yoya_reception_bridge.group_hospital_front_desk_nurse"

# Nothing money-shaped may appear anywhere in any payload, for any role.
# Matched against WHOLE underscore-separated words of every key, so a word
# like "discharge" is not mistaken for "charge".
FORBIDDEN_KEY_WORDS = frozenset({
    "amount", "amounts", "price", "rate", "rates", "fee", "fees", "invoice",
    "receipt", "balance", "payment", "payer", "tariff", "currency", "subtotal",
    "charge", "charges", "bill", "outstanding", "insurance", "coverage",
    "guarantee", "paid", "due", "cost",
})

# THE ONE EXCEPTION, and why (Admissions Slice 3). The inpatient financial
# STATE crosses as a key, booleans and fixed sentences; its contract names one
# boolean `refund_due`, whose whole word "due" the scan above forbids. It is
# allowed BY NAME, and every test that allows it also asserts its value is a
# boolean -- so a number can never ride in under it.
AMOUNT_FREE_FLAG_KEYS = frozenset({"refund_due"})


def _walk_keys(value):
    if isinstance(value, dict):
        for key, child in value.items():
            yield key
            yield from _walk_keys(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_keys(child)


class AdmissionsDeskCase(HttpCase):
    PASSWORD = "adm-desk-pw-1"

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.token = "ADMDESK%s" % uuid.uuid4().hex[:8].upper()
        cls.company = cls.env.company
        Dept = cls.env["hospital.department"].sudo()
        cls.dept_a = Dept.create({"name": "%s Dept A" % cls.token, "code": "%sA" % cls.token})
        cls.dept_b = Dept.create({"name": "%s Dept B" % cls.token, "code": "%sB" % cls.token})

        mk = cls._make_user
        cls.manager = mk("mgr", [G_MANAGER])
        cls.sysadmin = mk("sys", [G_SYSADMIN])
        cls.receptionist = mk("rcp", [G_RECEPTIONIST])
        cls.doctor_user = mk("doc", [G_DOCTOR])
        cls.other_doctor_user = mk("doc2", [G_DOCTOR])
        cls.nurse_a = mk("nra", [G_NURSE], [cls.dept_a])
        cls.nurse_b = mk("nrb", [G_NURSE], [cls.dept_b])
        cls.front_desk = mk("fdn", [G_FRONT_DESK_NURSE], [cls.dept_a])
        cls.denied = {
            "pharmacist": mk("pha", [G_PHARMACIST]),
            "lab": mk("lab", [G_LAB]),
            "dpo": mk("dpo", [G_DPO]),
            "cashier": mk("csh", [G_CASHIER]),
            "radiology": mk("rad", [G_RAD_TECH]),
            "accountant": mk("acc", [G_ACCOUNTANT]),
        }

        Doctor = cls.env["hospital.doctor"].sudo()
        cls.doctor = Doctor.create({"name": "%s Dr Mine" % cls.token, "user_id": cls.doctor_user.id})
        cls.other_doctor = Doctor.create(
            {"name": "%s Dr Other" % cls.token, "user_id": cls.other_doctor_user.id}
        )

        cls.ward_a = cls._ward("Ward A", cls.dept_a)
        cls.room_a = cls._room(cls.ward_a, "A1")
        cls.bed_a1 = cls._bed(cls.room_a, "A1-1")
        cls.bed_a2 = cls._bed(cls.room_a, "A1-2")
        cls.bed_a3 = cls._bed(cls.room_a, "A1-3")
        cls.bed_clean = cls._bed(cls.room_a, "A1-CLN")
        cls.bed_maint = cls._bed(cls.room_a, "A1-MNT")
        cls.bed_block = cls._bed(cls.room_a, "A1-BLK")
        cls.bed_off = cls._bed(cls.room_a, "A1-OFF")
        cls.bed_clean._set_occupancy("cleaning")
        cls.bed_maint._set_occupancy("maintenance")
        cls.bed_block._set_occupancy("blocked")
        cls.bed_off.sudo().write({"active": False})

        cls.ward_b = cls._ward("Ward B", cls.dept_b)
        cls.room_b = cls._room(cls.ward_b, "B1")
        cls.bed_b1 = cls._bed(cls.room_b, "B1-1")

        # The census: one of each kind, all through the real workflow.
        cls.adm_mine = cls._admit(cls.bed_a1, cls.doctor)                  # ward A, my doctor
        cls.adm_other_ward = cls._admit(cls.bed_b1, cls.other_doctor)      # ward B, other doctor
        cls.adm_draft_bed = cls._draft(cls.bed_a2, cls.doctor)             # draft, bed named
        cls.adm_draft_nobed = cls._draft(None, cls.doctor, ward=cls.ward_a)  # awaiting bed
        cls.adm_transferred = cls._admit(cls.bed_a3, cls.doctor)
        cls.adm_transferred.action_transfer(
            cls.ward_a, cls.room_a, cls._bed(cls.room_a, "A1-4"), reason="closer to station"
        )
        cls.adm_discharged = cls._admit(cls._bed(cls.room_a, "A1-5"), cls.doctor)
        cls.adm_discharged.action_discharge()
        cls.adm_cancelled = cls._draft(None, cls.doctor, ward=cls.ward_a)
        cls.adm_cancelled.action_cancel()

    # ------------------------------------------------------------------
    # Fixtures
    # ------------------------------------------------------------------
    @classmethod
    def _make_user(cls, key, groups, departments=None):
        vals = {
            "name": "%s %s" % (cls.token, key),
            "login": "%s_%s" % (cls.token.lower(), key),
            "password": cls.PASSWORD,
            "company_id": cls.company.id,
            "company_ids": [(6, 0, cls.company.ids)],
            "groups_id": [(6, 0, [cls.env.ref("base.group_user").id] + [cls.env.ref(g).id for g in groups])],
        }
        if departments is not None:
            vals["yoya_permitted_department_ids"] = [(6, 0, [d.id for d in departments])]
        return cls.env["res.users"].sudo().create(vals)

    @classmethod
    def _ward(cls, label, dept):
        return cls.env["hospital.ward"].sudo().create({
            "name": "%s %s" % (cls.token, label), "code": "%s-%s" % (cls.token, label[-1]),
            "ward_type": "medical", "department_id": dept.id, "company_id": cls.company.id,
        })

    @classmethod
    def _room(cls, ward, code):
        return cls.env["hospital.room"].sudo().create(
            {"name": "%s Room %s" % (cls.token, code), "code": "%s-R%s" % (cls.token, code), "ward_id": ward.id}
        )

    @classmethod
    def _bed(cls, room, code):
        return cls.env["hospital.bed"].sudo().create(
            {"name": "%s Bed %s" % (cls.token, code), "code": "%s-%s" % (cls.token, code), "room_id": room.id}
        )

    @classmethod
    def _patient(cls):
        return cls.env["hospital.patient"].sudo().create(
            {"name": "%s Patient %s" % (cls.token, uuid.uuid4().hex[:5])}
        )

    @classmethod
    def _draft(cls, bed, doctor, ward=None):
        patient = cls._patient()
        encounter = cls.env["hospital.encounter"].sudo().create({
            "patient_id": patient.id, "company_id": cls.company.id,
            "primary_doctor_id": doctor.id, "opened_at": fields.Datetime.now(),
        })
        encounter.write({"state": "active"})
        vals = {"patient_id": patient.id, "physician_id": doctor.id, "company_id": cls.company.id}
        if bed:
            vals.update(ward_id=bed.ward_id.id, room_id=bed.room_id.id, bed_id=bed.id)
        elif ward:
            vals.update(ward_id=ward.id)
        return cls.env["hospital.admission"].sudo().create(vals)

    @classmethod
    def _admit(cls, bed, doctor):
        admission = cls._draft(bed, doctor)
        admission.action_confirm_admission()
        return admission

    def _raw(self, sql, params):
        """Build a corrupt shape past the ORM (flush first; see Slice 0)."""
        self.env.flush_all()
        self.cr.execute(sql, params)
        self.env.invalidate_all(flush=False)

    # ------------------------------------------------------------------
    # HTTP
    # ------------------------------------------------------------------
    def _get(self, url, user=None, **params):
        self.env.flush_all()
        self.authenticate((user or self.manager).login, self.PASSWORD)
        query = urlencode({k: v for k, v in params.items() if v is not None})
        response = self.url_open("%s%s" % (url, "?" + query if query else ""))
        return response, json.loads(response.text)

    def _ok(self, url, user=None, **params):
        response, payload = self._get(url, user, **params)
        self.assertEqual(response.status_code, 200, payload)
        self.assertTrue(payload["success"], payload)
        return payload["data"]

    def _worklist(self, user=None, **params):
        params.setdefault("q", self.token)
        params.setdefault("lane", "all")
        return self._ok(WORKLIST, user, **params)

    def _ids(self, data):
        return {row["id"] for row in data["rows"]}

    def _beds(self, user=None, **params):
        return self._ok(BEDS, user, **params)["beds"]

    def _bed_row(self, rows, bed):
        [row] = [r for r in rows if r["id"] == bed.id]
        return row


@tagged("post_install", "-at_install", "admissions_desk")
class TestAdmissionsDeskGate(AdmissionsDeskCase):
    # ==================================================================
    # 1. THE GATE
    # ==================================================================
    def test_the_runtime_is_the_repository_copy(self):
        """Do not trust a green run that tested another copy (Slice 0 lesson)."""
        import odoo.addons.hospital_admission as admission_pkg
        import odoo.addons.yoya_emr_api as api_pkg

        api_root = os.path.dirname(os.path.dirname(api_pkg.__file__))
        admission_root = os.path.dirname(os.path.dirname(admission_pkg.__file__))
        write_file = inspect.getsourcefile(type(self.env["hospital.admission"]).write)
        _logger.info(
            "ADMISSIONS DESK RUNTIME: yoya_emr_api=%s | hospital_admission=%s | write()=%s",
            api_pkg.__file__, admission_pkg.__file__, write_file,
        )
        self.assertEqual(
            os.path.normcase(api_root), os.path.normcase(admission_root),
            "yoya_emr_api and hospital_admission were loaded from different addons trees",
        )

    def test_desk_roles_open_the_session(self):
        for user in (self.manager, self.sysadmin, self.receptionist, self.doctor_user,
                     self.nurse_a, self.front_desk):
            with self.subTest(user=user.login):
                data = self._ok(SESSION, user)
                self.assertTrue(data["capabilities"]["admissions_desk"])
                self.assertTrue(data["read_only"])

    def test_every_route_refuses_every_excluded_role_before_reading(self):
        """403 with the fixed code -- and no count, row, bed or ward leaks."""
        routes = (SESSION, WORKLIST, WARDS, BEDS, DETAIL % self.adm_mine.id, DETAIL % 999999999)
        for role, user in self.denied.items():
            for route in routes:
                with self.subTest(role=role, route=route):
                    response, payload = self._get(route, user)
                    self.assertEqual(response.status_code, 403, payload)
                    self.assertEqual(payload["error"]["code"], "admissions_desk_not_authorized")
                    self.assertNotIn("data", payload)

    def test_a_denied_caller_cannot_tell_an_existing_id_from_a_missing_one(self):
        user = self.denied["pharmacist"]
        _r1, existing = self._get(DETAIL % self.adm_mine.id, user)
        _r2, missing = self._get(DETAIL % 999999999, user)
        self.assertEqual(existing, missing)

    def test_the_gate_agrees_with_the_group_tuple(self):
        for user in (self.manager, self.receptionist, self.doctor_user, self.nurse_a, self.front_desk):
            self.assertTrue(may_admissions_desk(self.env(user=user)))
        for user in self.denied.values():
            self.assertFalse(may_admissions_desk(self.env(user=user)))
        self.assertEqual(len(ADMISSIONS_DESK_GROUPS), 5)

    def test_workflow_capabilities_match_the_slice(self):
        """Slice 2 opened admit (assign_bed is the same act) for the admitting
        roles. Slice 3 opens transfer for the same roles, and cancel_request
        for them and for doctors (their OWN requests, decided per record).
        Discharge stays FALSE for everyone, administrators included -- no
        route exists behind it until Slice 4."""
        for user in (self.sysadmin, self.manager, self.receptionist):
            caps = self._ok(SESSION, user)["capabilities"]
            # Slice 4: discharge is the ADMINISTRATIVE final discharge.
            for flag in ("admit", "assign_bed", "transfer", "cancel_request", "discharge"):
                self.assertIs(caps[flag], True, (user.login, flag))
            self.assertTrue(caps["view_worklist"])
            self.assertTrue(caps["view_bed_board"])
        caps = self._ok(SESSION, self.doctor_user)["capabilities"]
        self.assertIs(caps["cancel_request"], True)
        for flag in ("admit", "assign_bed", "transfer", "discharge"):
            self.assertIs(caps[flag], False, flag)
        for user in (self.nurse_a, self.front_desk):
            caps = self._ok(SESSION, user)["capabilities"]
            for flag in ("admit", "assign_bed", "transfer", "cancel_request", "discharge"):
                self.assertIs(caps[flag], False, (user.login, flag))

    def test_the_session_carries_only_operational_metadata(self):
        data = self._ok(SESSION, self.nurse_a)
        self.assertEqual(set(data), {
            "user", "roles", "role_labels", "desk_role", "scope",
            "permitted_department_ids", "permitted_ward_ids", "capabilities", "read_only",
        })
        self.assertEqual(data["scope"], "permitted_departments")
        self.assertEqual(data["permitted_department_ids"], [self.dept_a.id])
        self.assertIn(self.ward_a.id, data["permitted_ward_ids"])
        self.assertNotIn(self.ward_b.id, data["permitted_ward_ids"])
        self.assertEqual(self._ok(SESSION, self.manager)["scope"], "all_wards")


@tagged("post_install", "-at_install", "admissions_desk")
class TestAdmissionsDeskScope(AdmissionsDeskCase):
    # ==================================================================
    # 2. SCOPE comes from the record rules
    # ==================================================================
    def _everything(self):
        return {a.id for a in (
            self.adm_mine, self.adm_other_ward, self.adm_draft_bed, self.adm_draft_nobed,
            self.adm_transferred, self.adm_discharged, self.adm_cancelled,
        )}

    def test_manager_sysadmin_and_receptionist_see_the_whole_census(self):
        for user in (self.manager, self.sysadmin, self.receptionist):
            with self.subTest(user=user.login):
                self.assertEqual(self._ids(self._worklist(user)), self._everything())

    def test_doctor_sees_only_their_own_patients(self):
        ids = self._ids(self._worklist(self.doctor_user))
        self.assertIn(self.adm_mine.id, ids)
        self.assertNotIn(self.adm_other_ward.id, ids)
        other = self._ids(self._worklist(self.other_doctor_user))
        self.assertEqual(other, {self.adm_other_ward.id})

    def test_nurse_sees_only_permitted_departments(self):
        ids_a = self._ids(self._worklist(self.nurse_a))
        self.assertIn(self.adm_mine.id, ids_a)
        self.assertNotIn(self.adm_other_ward.id, ids_a)
        self.assertEqual(self._ids(self._worklist(self.nurse_b)), {self.adm_other_ward.id})
        self.assertIn(self.adm_mine.id, self._ids(self._worklist(self.front_desk)))

    def test_hidden_admission_detail_is_the_same_404_as_a_missing_one(self):
        r1, hidden = self._get(DETAIL % self.adm_other_ward.id, self.nurse_a)
        r2, missing = self._get(DETAIL % 999999999, self.nurse_a)
        self.assertEqual((r1.status_code, r2.status_code), (404, 404))
        self.assertEqual(hidden["error"], missing["error"])

    def test_an_unrostered_nurse_opens_the_desk_to_an_empty_census(self):
        nobody = self._make_user("nr0", [G_NURSE], [])
        data = self._worklist(nobody)
        self.assertEqual(data["rows"], [])
        self.assertEqual(data["summary"]["total"], 0)


@tagged("post_install", "-at_install", "admissions_desk")
class TestAdmissionsDeskLanes(AdmissionsDeskCase):
    # ==================================================================
    # 3. LANES
    # ==================================================================
    def _lane_of(self, admission, user=None):
        [row] = [r for r in self._worklist(user)["rows"] if r["id"] == admission.id]
        return row

    def test_each_healthy_shape_lands_in_its_lane(self):
        expected = {
            self.adm_mine: "admitted",
            self.adm_transferred: "transferred",
            self.adm_draft_bed: "draft",
            self.adm_draft_nobed: "awaiting_bed",
            self.adm_discharged: "discharged",
            self.adm_cancelled: "cancelled",
        }
        rows = {r["id"]: r for r in self._worklist()["rows"]}
        for admission, lane in expected.items():
            with self.subTest(admission=admission.name):
                self.assertEqual(rows[admission.id]["lane"], lane)
                self.assertEqual(rows[admission.id]["review_reasons"], [])

    def test_bed_pointer_mismatch_is_needs_review(self):
        self._raw("UPDATE hospital_bed SET current_admission_id = %s WHERE id = %s",
                  (self.adm_other_ward.id, self.bed_a1.id))
        row = self._lane_of(self.adm_mine)
        self.assertEqual(row["lane"], "needs_review")
        self.assertIn("bed_pointer_mismatch", [r["code"] for r in row["review_reasons"]])

    def test_active_admission_on_an_available_bed_is_needs_review(self):
        self._raw("UPDATE hospital_bed SET state = 'available' WHERE id = %s", (self.bed_a1.id,))
        row = self._lane_of(self.adm_mine)
        self.assertEqual(row["lane"], "needs_review")
        self.assertIn("bed_not_occupied", [r["code"] for r in row["review_reasons"]])

    def test_active_admission_without_a_visit_is_needs_review(self):
        self._raw("UPDATE hospital_admission SET encounter_id = NULL WHERE id = %s", (self.adm_mine.id,))
        row = self._lane_of(self.adm_mine)
        self.assertEqual(row["lane"], "needs_review")
        self.assertIn("encounter_missing", [r["code"] for r in row["review_reasons"]])

    def test_visit_of_another_patient_is_needs_review(self):
        other_visit = self.adm_other_ward.sudo().encounter_id
        self._raw("UPDATE hospital_admission SET encounter_id = %s WHERE id = %s",
                  (other_visit.id, self.adm_mine.id))
        row = self._lane_of(self.adm_mine)
        self.assertIn("encounter_patient_mismatch", [r["code"] for r in row["review_reasons"]])

    def test_a_nurse_who_cannot_read_the_visit_still_sees_the_fault(self):
        """The integrity facts are not hidden by the encounter's own rules."""
        self._raw("UPDATE hospital_admission SET encounter_id = NULL WHERE id = %s", (self.adm_mine.id,))
        self.assertEqual(self._lane_of(self.adm_mine, self.nurse_a)["lane"], "needs_review")

    def test_legacy_discharged_without_a_visit_is_discharged_not_needs_review(self):
        self._raw("UPDATE hospital_admission SET encounter_id = NULL WHERE id = %s",
                  (self.adm_discharged.id,))
        row = self._lane_of(self.adm_discharged)
        self.assertEqual(row["lane"], "discharged")
        self.assertIsNone(row["billing_blocked"])
        self.assertTrue(row["encounter"]["legacy"])

    def test_every_admission_lands_in_exactly_one_lane_and_counts_match_rows(self):
        data = self._worklist()
        summary = data["summary"]
        self.assertTrue(data["meta"]["summary_exact"])
        self.assertEqual(sum(summary[lane] for lane in LANE_ORDER), summary["total"])
        self.assertEqual(summary["total"], len(data["rows"]))
        for lane in LANE_ORDER:
            with self.subTest(lane=lane):
                rows = self._worklist(lane=lane)["rows"]
                self.assertEqual(len(rows), summary[lane])
                self.assertTrue(all(r["lane"] == lane for r in rows))

    def test_the_default_queue_is_the_active_lanes_and_counts_ignore_the_lane(self):
        data = self._ok(WORKLIST, q=self.token)
        lanes = {row["lane"] for row in data["rows"]}
        self.assertFalse(lanes & {"discharged", "cancelled"})
        self.assertEqual(data["summary"]["discharged"], 1)
        self.assertEqual(data["summary"]["cancelled"], 1)

    def test_classify_batch_is_the_function_the_worklist_uses(self):
        admissions = self.adm_mine | self.adm_draft_nobed | self.adm_cancelled
        lanes = {a.id: lane for a, lane, _r, _e in classify_batch(admissions.sudo())}
        self.assertEqual(lanes[self.adm_mine.id], "admitted")
        self.assertEqual(lanes[self.adm_draft_nobed.id], "awaiting_bed")
        self.assertEqual(lanes[self.adm_cancelled.id], "cancelled")

    def test_filters_ward_and_search(self):
        ward_b = self._worklist(ward_id=self.ward_b.id)
        self.assertEqual(self._ids(ward_b), {self.adm_other_ward.id})
        by_ref = self._worklist(q=self.adm_mine.name)
        self.assertIn(self.adm_mine.id, self._ids(by_ref))
        by_bed = self._worklist(q=self.bed_b1.code)
        self.assertEqual(self._ids(by_bed), {self.adm_other_ward.id})

    def test_bad_parameters_are_refused(self):
        for params in ({"lane": "nonsense"}, {"limit": "0"}, {"limit": "5000"}, {"ward_id": "-1"}):
            with self.subTest(params=params):
                response, payload = self._get(WORKLIST, **params)
                self.assertEqual(response.status_code, 400, payload)


@tagged("post_install", "-at_install", "admissions_desk")
class TestAdmissionsDeskDetail(AdmissionsDeskCase):
    # ==================================================================
    # Detail, clearance, legacy, confidentiality
    # ==================================================================
    def test_detail_sections(self):
        data = self._ok(DETAIL % self.adm_transferred.id)["admission"]
        self.assertEqual(data["lane"], "transferred")
        self.assertEqual(data["transfer_count"], 1)
        [transfer] = data["transfers"]
        self.assertEqual(transfer["from"]["bed"]["id"], self.bed_a3.id)
        self.assertEqual(transfer["reason"], "closer to station")
        self.assertTrue(transfer["transferred_by"])
        self.assertTrue(data["bed_ownership"]["consistent"])
        self.assertEqual(data["encounter"]["type"], "inpatient")
        for key in ("rounds", "notes", "care_plans", "medication_administrations"):
            self.assertIn("count", data["nursing"][key])
        self.assertIsInstance(data["clearance"]["billing_blocked"], bool)

    def test_legacy_admission_opens_with_an_honest_clearance(self):
        self._raw("UPDATE hospital_admission SET encounter_id = NULL WHERE id = %s",
                  (self.adm_discharged.id,))
        data = self._ok(DETAIL % self.adm_discharged.id)["admission"]
        self.assertEqual(data["lane"], "discharged")
        self.assertEqual(data["encounter"], {
            "available": False, "restricted": False, "legacy": True, "reference": None,
            "state": None, "state_label": None, "type": None, "type_label": None,
        })
        self.assertIsNone(data["clearance"]["billing_blocked"])
        self.assertEqual(data["clearance"]["clearance_state"], "not_applicable")

    def test_the_real_ADM00001_opens_if_present(self):
        legacy = self.env["hospital.admission"].sudo().with_context(active_test=False).search(
            [("name", "=", "ADM00001")], limit=1
        )
        if not legacy:
            self.skipTest("ADM00001 is not in this database")
        data = self._ok(DETAIL % legacy.id)["admission"]
        self.assertEqual(data["lane"], "discharged")
        if not legacy.encounter_id:
            self.assertIsNone(data["clearance"]["billing_blocked"])
            self.assertTrue(data["encounter"]["legacy"])

    def test_a_nurse_sees_the_admission_but_not_a_visit_they_cannot_read(self):
        data = self._ok(DETAIL % self.adm_mine.id, self.nurse_a)["admission"]
        if data["encounter"]["restricted"]:
            self.assertIsNone(data["encounter"]["reference"])
        self.assertEqual(data["lane"], "admitted")

    def test_no_payload_contains_anything_money_shaped(self):
        payloads = [
            self._ok(SESSION),
            self._worklist(),
            self._ok(DETAIL % self.adm_mine.id),
            self._ok(WARDS),
            self._ok(BEDS, ward_id=self.ward_a.id),
        ]
        for payload in payloads:
            for key in _walk_keys(payload):
                if key in AMOUNT_FREE_FLAG_KEYS:
                    continue
                words = set(key.lower().split("_"))
                self.assertFalse(words & FORBIDDEN_KEY_WORDS, key)
        financial = payloads[2]["admission"]["financial"]
        self.assertIsInstance(financial["refund_due"], bool)
        self.assertEqual(
            set(financial),
            {"financial_state", "billing_blocked", "settlement_required", "refund_due", "review_reasons"},
        )

    def test_no_note_bodies_are_serialized(self):
        data = self._ok(DETAIL % self.adm_mine.id)["admission"]
        for section in data["nursing"].values():
            if section is not None:
                self.assertEqual(set(section), {"count", "latest_at"})


@tagged("post_install", "-at_install", "admissions_desk")
class TestAdmissionsBedBoard(AdmissionsDeskCase):
    # ==================================================================
    # 4. BED BOARD
    # ==================================================================
    def test_every_bed_state_is_reported(self):
        rows = self._beds(ward_id=self.ward_a.id)
        expect = {
            self.bed_a1: ("occupied", True), self.bed_a2: ("available", True),
            self.bed_clean: ("cleaning", True), self.bed_maint: ("maintenance", True),
            self.bed_block: ("blocked", True), self.bed_off: ("available", False),
        }
        for bed, (state, active) in expect.items():
            row = self._bed_row(rows, bed)
            with self.subTest(bed=bed.code):
                self.assertEqual((row["state"], row["active"]), (state, active))
                self.assertEqual(row["occupied"], state == "occupied")

    def test_occupied_bed_names_its_patient_for_a_caller_who_may_read_it(self):
        row = self._bed_row(self._beds(ward_id=self.ward_a.id), self.bed_a1)
        self.assertTrue(row["can_view_admission"])
        self.assertEqual(row["admission"]["id"], self.adm_mine.id)
        self.assertEqual(row["admission"]["patient"]["name"], self.adm_mine.patient_id.name)
        self.assertFalse(row["needs_review"])

    def test_a_hidden_admission_is_redacted_from_the_bed_board(self):
        """THE CRITICAL ONE: nurse A sees ward B's bed as occupied, and nothing else."""
        patient = self.adm_other_ward.patient_id
        response, payload = self._get(BEDS, self.nurse_a)
        self.assertEqual(response.status_code, 200)
        row = self._bed_row(payload["data"]["beds"], self.bed_b1)
        self.assertTrue(row["occupied"])
        self.assertFalse(row["can_view_admission"])
        self.assertIsNone(row["admission"])
        text = response.text
        for secret in (patient.name, patient.identification_code, self.adm_other_ward.name):
            if secret:
                self.assertNotIn(secret, text)

    def test_the_search_box_cannot_probe_for_a_hidden_patient(self):
        name = self.adm_other_ward.patient_id.name
        self.assertEqual(self._beds(self.nurse_a, q=name), [])
        self.assertEqual([r["id"] for r in self._beds(self.manager, q=name)], [self.bed_b1.id])

    def test_pointer_mismatch_and_admission_side_mismatch_are_flagged(self):
        self._raw("UPDATE hospital_bed SET current_admission_id = NULL WHERE id = %s", (self.bed_a1.id,))
        row = self._bed_row(self._beds(ward_id=self.ward_a.id), self.bed_a1)
        self.assertIn("occupied_without_admission", [f["code"] for f in row["flags"]])
        self.assertTrue(row["needs_review"])

        self._raw("UPDATE hospital_bed SET state = 'available', current_admission_id = NULL WHERE id = %s",
                  (self.bed_a1.id,))
        row = self._bed_row(self._beds(ward_id=self.ward_a.id), self.bed_a1)
        codes = [f["code"] for f in row["flags"]]
        self.assertIn("admission_without_occupied_bed", codes)
        self.assertTrue(row["can_view_admission"])

    def test_active_admission_on_an_unavailable_bed_is_flagged(self):
        self._raw("UPDATE hospital_bed SET state = 'maintenance' WHERE id = %s", (self.bed_a1.id,))
        row = self._bed_row(self._beds(ward_id=self.ward_a.id), self.bed_a1)
        self.assertIn("active_admission_on_unavailable_bed", [f["code"] for f in row["flags"]])

    def test_filters(self):
        ward_b = self._beds(ward_id=self.ward_b.id)
        self.assertEqual([r["id"] for r in ward_b], [self.bed_b1.id])
        room = self._beds(room_id=self.room_a.id)
        self.assertTrue(all(r["room"]["id"] == self.room_a.id for r in room))
        cleaning = self._beds(ward_id=self.ward_a.id, state="cleaning")
        self.assertEqual([r["id"] for r in cleaning], [self.bed_clean.id])
        by_code = self._beds(q=self.bed_a2.code)
        self.assertEqual([r["id"] for r in by_code], [self.bed_a2.id])
        response, _payload = self._get(BEDS, state="nonsense")
        self.assertEqual(response.status_code, 400)

    def test_ward_rollups_agree_with_the_bed_rows(self):
        wards = {w["id"]: w for w in self._ok(WARDS)["wards"]}
        rows = self._beds(ward_id=self.ward_a.id)
        ward = wards[self.ward_a.id]
        active = [r for r in rows if r["active"]]
        self.assertEqual(ward["bed_count"], len(active))
        self.assertEqual(ward["inactive_bed_count"], len(rows) - len(active))
        for state in ("available", "occupied", "cleaning", "maintenance", "blocked"):
            self.assertEqual(ward["%s_count" % state], sum(1 for r in active if r["state"] == state), state)
        self.assertEqual(ward["needs_review_count"], sum(1 for r in rows if r["needs_review"]))
        self.assertEqual(ward["department"]["id"], self.dept_a.id)
        self.assertEqual(sum(r["bed_count"] for r in ward["rooms"]), ward["bed_count"])


@tagged("post_install", "-at_install", "admissions_desk")
class TestAdmissionsDeskReadOnly(AdmissionsDeskCase):
    # ==================================================================
    # 6. READ ONLY
    # ==================================================================
    def test_no_route_accepts_a_write(self):
        """The read routes refuse a POST, and discharge / bare cancel /
        assign-bed do not exist. /admit (Slice 2), /transfer and
        /cancel-request (Slice 3) exist but refuse a malformed body before
        they read a row -- covered fully in the mutation suites."""
        self.authenticate(self.manager.login, self.PASSWORD)
        base = DETAIL % self.adm_mine.id
        for route in (SESSION, WORKLIST, WARDS, BEDS, base,
                      base + "/discharge", base + "/cancel", base + "/assign-bed"):
            with self.subTest(route=route):
                response = self.url_open(route, data=json.dumps({}), headers={"Content-Type": "application/json"})
                self.assertIn(response.status_code, (404, 405), route)
        for route in (base + "/admit", base + "/transfer", base + "/cancel-request",
                      base + "/finalize-discharge"):
            with self.subTest(route=route):
                response = self.url_open(route, data=json.dumps({}), headers={"Content-Type": "application/json"})
                self.assertEqual(response.status_code, 400)
                self.assertEqual(json.loads(response.text)["error"]["code"], "admission_invalid_payload")

    def test_viewing_writes_nothing(self):
        self.env.flush_all()
        self.cr.execute("SELECT count(*) FROM hospital_audit_log")
        audit_before = self.cr.fetchone()[0]
        self.cr.execute("SELECT max(write_date) FROM hospital_admission")
        admission_before = self.cr.fetchone()[0]
        self.cr.execute("SELECT max(write_date) FROM hospital_bed")
        bed_before = self.cr.fetchone()[0]

        self._ok(SESSION)
        self._worklist()
        self._ok(DETAIL % self.adm_mine.id)
        self._ok(WARDS)
        self._ok(BEDS)

        self.cr.execute("SELECT count(*) FROM hospital_audit_log")
        self.assertEqual(self.cr.fetchone()[0], audit_before)
        self.cr.execute("SELECT max(write_date) FROM hospital_admission")
        self.assertEqual(self.cr.fetchone()[0], admission_before)
        self.cr.execute("SELECT max(write_date) FROM hospital_bed")
        self.assertEqual(self.cr.fetchone()[0], bed_before)
