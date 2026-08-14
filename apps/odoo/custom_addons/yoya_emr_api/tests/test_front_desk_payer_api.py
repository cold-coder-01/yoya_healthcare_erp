"""The Front Desk payer projection carries identity and nothing else.

Asserted structurally, against ELIGIBILITY_IDENTITY_KEYS, rather than by listing
the forbidden names: a deny-list starts leaking the day a field is added to
hospital.patient.payer, and this is the layer that would leak it.
"""

import uuid
from datetime import timedelta

from odoo import fields
from odoo.tests import TransactionCase, tagged

from odoo.addons.yoya_emr_api.services.front_desk_serializers import (
    ELIGIBILITY_IDENTITY_KEYS,
    serialize_eligibility_identity,
    serialize_front_desk_visit,
)
from odoo.addons.yoya_emr_api.services.reception_scope import (
    front_desk_capability_flags,
)

G_FRONT_DESK = "yoya_reception_bridge.group_hospital_front_desk_nurse"
G_DOCTOR = "hospital_management.group_hospital_doctor"

# Names that must never appear anywhere in a front-desk payer payload, at any
# depth. Belt and braces alongside the allowlist assertion.
FORBIDDEN_KEY_FRAGMENTS = (
    "limit",
    "amount",
    "payment_terms",
    "tariff",
    "currency",
    "notes",
    "utilization",
    "responsibility",
    "coverage",
)


@tagged("post_install", "-at_install", "front_desk_payer_api")
class TestFrontDeskPayerApi(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.today = fields.Date.context_today(cls.env["hospital.patient.payer"])
        cls.company = cls.env.company

        cls.service = (
            cls.env["hospital.billing.service"]
            .sudo()
            .get_default_consultation_service(cls.company)
        )
        cls.service.sudo().write(
            {
                "default_price": 250.0,
                "fixed_fee": True,
                "prepayment_required": True,
                "coverage_auth_required": False,
                "active": True,
                "is_default_consultation": True,
            }
        )

        suffix = uuid.uuid4().hex[:6]
        cls.department = cls.env["hospital.department"].sudo().create(
            {"name": "Payer API Dept %s" % suffix, "code": "PAD%s" % suffix.upper()}
        )
        cls.front_desk = cls._make_user("api_front_desk", [G_FRONT_DESK])
        cls.doctor_user = cls._make_user("api_payer_doctor", [G_DOCTOR])
        cls.doctor = cls.env["hospital.doctor"].sudo().create(
            {
                "name": "Payer API Doctor",
                "user_id": cls.doctor_user.id,
                "department_id": cls.department.id,
            }
        )
        cls.agreement = cls._make_agreement()

    @classmethod
    def _make_user(cls, login, group_xmlids):
        return cls.env["res.users"].sudo().create(
            {
                "name": login,
                "login": "%s_%s" % (login, uuid.uuid4().hex[:6]),
                "company_id": cls.env.company.id,
                "company_ids": [(6, 0, cls.env.company.ids)],
                "groups_id": [
                    (
                        6,
                        0,
                        [cls.env.ref("base.group_user").id]
                        + [cls.env.ref(x).id for x in group_xmlids],
                    )
                ],
            }
        )

    @classmethod
    def _make_agreement(cls):
        partner = cls.env["res.partner"].sudo().create(
            {"name": "Payer API Partner %s" % uuid.uuid4().hex[:6]}
        )
        payer = cls.env["hospital.payer"].sudo().create(
            {
                "name": "Payer API Insurer %s" % uuid.uuid4().hex[:6],
                "payer_type": "insurance",
                "partner_id": partner.id,
                "company_id": cls.env.company.id,
            }
        )
        agreement = cls.env["hospital.payer.agreement"].sudo().create(
            {
                "payer_id": payer.id,
                "agreement_number": "PAPI-%s" % uuid.uuid4().hex[:8].upper(),
                "company_id": cls.env.company.id,
                "effective_from": cls.today - timedelta(days=20),
                "limit_scope": "unlimited",
            }
        )
        agreement.sudo().action_activate()
        return agreement

    def _eligibility(self, patient, activate=True):
        record = self.env["hospital.patient.payer"].sudo().create(
            {
                "patient_id": patient.id,
                "agreement_id": self.agreement.id,
                "effective_from": self.today - timedelta(days=2),
                "member_reference": "MBR-001",
                "policy_number": "POL-001",
            }
        )
        if activate:
            record.action_activate()
        return record

    def _register(self, patient_payer=None):
        result = self.env["hospital.reception.workflow"].with_user(
            self.front_desk
        ).create_visit(
            patient_values={"name": "Payer API Patient %s" % uuid.uuid4().hex[:6]},
            department=self.department,
            doctor=self.doctor,
            patient_payer=patient_payer,
        )
        return result["appointment"], result["encounter"]

    def _assert_identity_only(self, payload):
        self.assertIsNotNone(payload)
        extra = set(payload) - set(ELIGIBILITY_IDENTITY_KEYS)
        self.assertFalse(
            extra, "front-desk payer payload leaked non-identity keys: %s" % extra
        )
        for key in payload:
            for fragment in FORBIDDEN_KEY_FRAGMENTS:
                self.assertNotIn(
                    fragment,
                    key,
                    "key %r looks monetary/commercial for a front-desk payload" % key,
                )

    # ==================================================================
    # Projection
    # ==================================================================
    def test_eligibility_projection_is_identity_only(self):
        patient = self.env["hospital.patient"].sudo().create({"name": "API Proj"})
        eligibility = self._eligibility(patient)
        payload = serialize_eligibility_identity(
            eligibility.with_user(self.front_desk)
        )
        self._assert_identity_only(payload)
        self.assertEqual(payload["member_reference"], "MBR-001")
        self.assertEqual(payload["policy_number"], "POL-001")
        self.assertTrue(payload["is_valid_today"])

    def test_projection_is_serializable_by_a_front_desk_nurse(self):
        """It must read only ungrouped columns; a protected one would raise."""
        patient = self.env["hospital.patient"].sudo().create({"name": "API Serial"})
        eligibility = self._eligibility(patient)
        serialize_eligibility_identity(eligibility.with_user(self.front_desk))

    def test_visit_detail_exposes_only_safe_eligibility_identity(self):
        appointment, encounter = self._register()
        eligibility = self._eligibility(appointment.patient_id)
        self.env["hospital.reception.workflow"].with_user(
            self.front_desk
        ).set_visit_payer(appointment, patient_payer=eligibility)
        self.env.invalidate_all()

        env = self.env(user=self.front_desk)
        visit = serialize_front_desk_visit(
            appointment.with_user(self.front_desk), front_desk_capability_flags(env)
        )

        self._assert_identity_only(visit["encounter"]["patient_payer"])
        # The phase boundary, visible in the API contract itself.
        self.assertEqual(visit["encounter"]["payer_type"], "self_pay")
        self.assertEqual(encounter.sudo().payer_type, "self_pay")

    def test_visit_detail_reports_no_payer_when_none_selected(self):
        appointment, _encounter = self._register()
        env = self.env(user=self.front_desk)
        visit = serialize_front_desk_visit(
            appointment.with_user(self.front_desk), front_desk_capability_flags(env)
        )
        self.assertIsNone(visit["encounter"]["patient_payer"])

    def test_visit_detail_reports_the_payer_change_state(self):
        appointment, _encounter = self._register()
        env = self.env(user=self.front_desk)
        visit = serialize_front_desk_visit(
            appointment.with_user(self.front_desk), front_desk_capability_flags(env)
        )
        self.assertTrue(visit["payer_change"]["allowed"])
        self.assertFalse(visit["payer_change"]["frozen"])

    def test_payer_change_state_reports_the_freeze(self):
        appointment, encounter = self._register()
        encounter.sudo().action_start()
        self.env.invalidate_all()

        env = self.env(user=self.front_desk)
        visit = serialize_front_desk_visit(
            appointment.with_user(self.front_desk), front_desk_capability_flags(env)
        )
        self.assertFalse(visit["payer_change"]["allowed"])
        self.assertTrue(visit["payer_change"]["frozen"])
        self.assertTrue(visit["payer_change"]["reason"])

    # ==================================================================
    # Lookup scope
    # ==================================================================
    def test_lookup_is_scoped_to_the_requested_patient(self):
        patient = self.env["hospital.patient"].sudo().create({"name": "API Scope A"})
        other = self.env["hospital.patient"].sudo().create({"name": "API Scope B"})
        mine = self._eligibility(patient)
        theirs = self._eligibility(other)

        found = self.env["hospital.reception.workflow"].with_user(
            self.front_desk
        ).selectable_eligibilities(patient.with_user(self.front_desk))

        self.assertIn(mine, found)
        self.assertNotIn(theirs, found)

    def test_lookup_payload_carries_no_monetary_keys(self):
        patient = self.env["hospital.patient"].sudo().create({"name": "API Payload"})
        self._eligibility(patient)
        found = self.env["hospital.reception.workflow"].with_user(
            self.front_desk
        ).selectable_eligibilities(patient.with_user(self.front_desk))

        self.assertTrue(found)
        for record in found:
            self._assert_identity_only(serialize_eligibility_identity(record))
