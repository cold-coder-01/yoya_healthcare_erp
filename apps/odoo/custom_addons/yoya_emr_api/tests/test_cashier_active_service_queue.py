"""Cashier discovery for charges raised DURING a consultation (Defect A).

WHY THIS FILE EXISTS
--------------------
Selam Fikadu (HMS11831 / ENC13989) reached UAT with three active laboratory
charges totalling 550.00, a doctor screen correctly reading AWAITING CLEARANCE,
and NO ROW ANYWHERE IN THE CASHIER QUEUE. Opening /cashier/visits/10009 by hand
worked perfectly, which is what proved the payment workspace was never the
problem: discovery was.

THE CAUSE IS ONE EARLY RETURN.

    _resolve_front_desk_stage():
        if self.state == "in_consultation":
            return "in_consultation"          # <- returns BEFORE money

The 'awaiting_cashier' arm sits below that line and is only reachable for a
CONFIRMED visit. So every billable thing ordered after
action_start_consultation() -- laboratory today, radiology, medication and
procedures on the identical charge path -- was invisible to a queue that filters
on front_desk_stage.

THE FIX IS A SECOND LANE, NOT A WIDER STAGE. front_desk_stage keeps its meaning
and its early return; the appointment stays state='in_consultation' from
discovery through payment; no combined 'in_consultation_awaiting_cashier' state
exists. Clinical state and financial state stay separate facts.

The two tests that carry this file are:

  * test_20, which is the UAT scenario end to end through the REAL laboratory
    ordering path; and
  * test_60, which raises a RADIOLOGY charge and asserts the identical row --
    the proof that nothing here special-cases the laboratory, and that the
    lane already works for order paths this slice has not built yet.
"""
import json
import uuid

from odoo import fields
from odoo.tests import HttpCase, tagged

G_CASHIER = "hospital_billing.group_hospital_cashier"
G_MANAGER = "hospital_management.group_hospital_manager"

WORKLIST = "/yoya-emr/api/v1/cashier/worklist"
CONSULTATION_FEE = 1500.0
LAB_FEE = 150.0


@tagged("post_install", "-at_install", "cashier_active_service")
class TestCashierActiveServiceQueue(HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.uom = cls.env["uom.uom"].sudo().search([], limit=1)

        cls.consultation_service = (
            cls.env["hospital.billing.service"]
            .sudo()
            .get_default_consultation_service(cls.company)
        )
        cls.consultation_service.sudo().write(
            {
                "default_price": CONSULTATION_FEE,
                "fixed_fee": True,
                "prepayment_required": True,
                "coverage_auth_required": False,
                "active": True,
                "is_default_consultation": True,
            }
        )

        cls.cashier_password = "cashier-active-test"
        cls.manager_password = "manager-active-test"
        cls.cashier = cls._make_user(
            "active_cashier", cls.cashier_password, [G_CASHIER]
        )
        cls.manager = cls._make_user(
            "active_manager", cls.manager_password, [G_MANAGER]
        )

        cls.doctor = cls.env["hospital.doctor"].sudo().create(
            {"name": "Active Service Doctor %s" % uuid.uuid4().hex[:6]}
        )
        cls.lab_test = cls._make_lab_test()

    # ------------------------------------------------------------------
    # Fixtures
    # ------------------------------------------------------------------
    @classmethod
    def _make_user(cls, login, password, group_xmlids):
        return cls.env["res.users"].sudo().create(
            {
                "name": login,
                "login": "%s_%s" % (login, uuid.uuid4().hex[:6]),
                "password": password,
                "company_id": cls.company.id,
                "company_ids": [(6, 0, cls.company.ids)],
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
    def _service(cls, service_type, price=LAB_FEE, prepayment_required=True):
        return cls.env["hospital.billing.service"].sudo().create(
            {
                "name": "%s %s" % (service_type.title(), uuid.uuid4().hex[:6]),
                "code": "T-ACT-%s" % uuid.uuid4().hex[:8].upper(),
                "service_type": service_type,
                "default_price": price,
                "prepayment_required": prepayment_required,
                "company_id": cls.company.id,
                "currency_id": cls.company.currency_id.id,
                "uom_id": cls.uom.id,
                "tax_treatment": "exempt",
            }
        )

    @classmethod
    def _make_lab_test(cls):
        tag = uuid.uuid4().hex[:6]
        return cls.env["hospital.laboratory.test"].sudo().create(
            {
                "name": "Active CBC %s" % tag,
                "code": "ACT%s" % tag.upper(),
                "category": "hematology",
                "sample_type": "blood",
                # prepayment_required=True is the point: an unpaid prepaid
                # charge is what must reach the cashier.
                "billing_service_id": cls._service("laboratory").id,
            }
        )

    def _visit(self, name="Active Patient"):
        """A confirmed visit with its consultation charge raised, unpaid."""
        suffix = uuid.uuid4().hex[:8]
        patient = self.env["hospital.patient"].sudo().create(
            {"name": "%s %s" % (name, suffix)}
        )
        appointment = self.env["hospital.appointment"].sudo().create(
            {
                "patient_id": patient.id,
                "doctor_id": self.doctor.id,
                "appointment_date": fields.Datetime.now(),
            }
        )
        appointment.action_confirm()
        appointment.invalidate_recordset()
        return appointment

    def _complete_triage(self, appointment):
        evaluation = self.env["hospital.patient.evaluation"].sudo().create(
            {
                "patient_id": appointment.patient_id.id,
                "appointment_id": appointment.id,
            }
        )
        evaluation.write({"started_at": fields.Datetime.now(), "state": "done"})
        appointment.invalidate_recordset()
        return evaluation

    def _pay_account(self, appointment, amount):
        account = appointment.encounter_id.sudo().billing_account_id
        account.sudo().record_operational_payment(
            amount=amount, payment_method="cash", intake_token=uuid.uuid4().hex
        )
        appointment.invalidate_recordset()
        return account

    def _in_consultation(self, name="Active Patient"):
        """A visit whose consultation has really started and is fully paid.

        Uses the AUTHORITATIVE transition, not a state write: the whole defect
        is about what action_start_consultation() does to queue derivation, and
        a fixture that wrote state='in_consultation' directly would skip the
        clearance persistence and charge-delivery moves that come with it.
        """
        appointment = self._visit(name)
        self._complete_triage(appointment)
        self._pay_account(appointment, CONSULTATION_FEE)
        appointment.with_user(self.manager).action_start_consultation()
        appointment.invalidate_recordset()
        self.assertEqual(appointment.state, "in_consultation")
        return appointment

    def _raise_charge(self, appointment, service, description="Ordered service"):
        """One mid-consultation charge, through the billing engine.

        Deliberately the ENGINE and not a clinical model: it is the single path
        every order type shares, so a charge raised this way is exactly what a
        radiology or pharmacy order will produce.
        """
        engine = self.env["hospital.billing.engine"].sudo()
        charge = engine.create_or_update_charge(
            appointment.encounter_id.sudo(),
            source_model="hospital.appointment",
            source_res_id=appointment.id,
            source_event="active_service_test",
            source_key="ACT-%s" % uuid.uuid4().hex[:12],
            description=description,
            service=service,
        )
        engine.activate_charge(charge)
        appointment.invalidate_recordset()
        return charge

    def _order_laboratory(self, appointment, tests=None):
        """The REAL laboratory path: request -> action_confirm_request().

        hospital_billing's override is what raises the charges. Nothing in this
        helper creates one, which is how the test proves the cashier lane picks
        up charges it never saw created.
        """
        request = self.env["hospital.laboratory.request"].sudo().create(
            {
                "patient_id": appointment.patient_id.id,
                "physician_id": self.doctor.id,
                "appointment_id": appointment.id,
                "encounter_id": appointment.encounter_id.id,
                "line_ids": [
                    (0, 0, {"test_id": test.id})
                    for test in (tests or [self.lab_test])
                ],
            }
        )
        request.action_confirm_request()
        appointment.invalidate_recordset()
        return request

    # ------------------------------------------------------------------
    # HTTP helpers
    # ------------------------------------------------------------------
    def _login_cashier(self):
        self.authenticate(self.cashier.login, self.cashier_password)

    def _worklist(self, query=""):
        return json.loads(self.url_open(WORKLIST + query).text)

    def _initial(self, payload, appointment):
        return [
            row
            for row in payload["data"]["initial_clearance"]
            if row["appointment_id"] == appointment.id
        ]

    def _active(self, payload, appointment):
        return [
            row
            for row in payload["data"]["active_service_clearance"]
            if row["appointment_id"] == appointment.id
        ]

    def _pay_http(self, appointment, amount):
        return self.url_open(
            "/yoya-emr/api/v1/cashier/visits/%s/payment" % appointment.id,
            data=json.dumps(
                {
                    "amount": amount,
                    "payment_method": "cash",
                    "idempotency_key": uuid.uuid4().hex,
                }
            ),
            headers={"Content-Type": "application/json"},
        )

    # ==================================================================
    # LANE SHAPE
    # ==================================================================
    def test_01_the_response_carries_two_named_lanes(self):
        self._login_cashier()
        data = self._worklist()["data"]
        for key in (
            "initial_clearance",
            "active_service_clearance",
            "active_service_lane_counts",
            "active_service_truncated",
        ):
            self.assertIn(key, data)
        self.assertIsInstance(data["initial_clearance"], list)
        self.assertIsInstance(data["active_service_clearance"], list)

    def test_02_rows_remains_an_alias_of_initial_clearance(self):
        """Existing clients read `rows`. It must keep meaning what it meant --
        the initial lane ONLY -- never a silent merge of both."""
        appointment = self._visit()
        self._complete_triage(appointment)
        active = self._in_consultation()
        self._raise_charge(active, self._service("laboratory"))

        self._login_cashier()
        data = self._worklist()["data"]
        self.assertEqual(data["rows"], data["initial_clearance"])

        row_ids = {row["appointment_id"] for row in data["rows"]}
        self.assertIn(appointment.id, row_ids)
        self.assertNotIn(
            active.id, row_ids,
            "An in-consultation payment must not leak into the initial lane.",
        )

    # ==================================================================
    # ELIGIBILITY
    # ==================================================================
    def test_10_confirmed_unpaid_visit_still_appears_in_initial_clearance(self):
        """REGRESSION GUARD. The pre-consultation queue is untouched."""
        appointment = self._visit()
        self._complete_triage(appointment)
        self._login_cashier()

        payload = self._worklist()
        rows = self._initial(payload, appointment)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["stage"], "awaiting_cashier")
        self.assertEqual(rows[0]["lane"], "collect")
        self.assertAlmostEqual(
            rows[0]["patient_outstanding"], CONSULTATION_FEE, places=2
        )
        self.assertFalse(
            self._active(payload, appointment),
            "A visit that never started consultation is not an active-service case.",
        )

    def test_11_in_consultation_with_nothing_owed_does_not_appear(self):
        appointment = self._in_consultation()
        self._login_cashier()

        payload = self._worklist()
        self.assertFalse(self._active(payload, appointment))
        self.assertFalse(self._initial(payload, appointment))

    def test_20_in_consultation_with_ordered_laboratory_appears(self):
        """THE DEFECT, end to end, through the real ordering path.

        No charge is created by this test. The doctor's own transition raises
        them, and the cashier must find the patient without anyone writing a
        stage, a flag or a queue row.
        """
        appointment = self._in_consultation("Selam Scenario")
        request = self._order_laboratory(appointment)

        # The doctor's side of the same fact, asserted so a future change that
        # breaks one and not the other is caught here.
        self.assertEqual(request.state, "requested")
        self.assertTrue(request.sudo().billing_blocked)

        self._login_cashier()
        rows = self._active(self._worklist(), appointment)

        self.assertEqual(
            len(rows), 1,
            "The patient the doctor is holding must be discoverable at the window.",
        )
        row = rows[0]
        self.assertEqual(row["visit_state"], "in_consultation")
        # 'partial', not 'collect', and that is the SERVER'S verdict rather than
        # an oversight: reaching this lane at all means the consultation charge
        # was paid, so the account has genuinely received money. The lane is an
        # ACCOUNT-level fact, resolve_collectability owns it, and both lanes
        # read the same resolver -- restating it here for the active lane would
        # be the second source of truth this desk exists to avoid. The row
        # carries patient_paid alongside, so the window reads "150.00 due,
        # 1,500.00 paid so far", which is exactly the position.
        self.assertEqual(row["lane"], "partial")
        self.assertAlmostEqual(row["patient_outstanding"], LAB_FEE, places=2)
        self.assertAlmostEqual(row["patient_paid"], CONSULTATION_FEE, places=2)
        self.assertEqual(
            [category["key"] for category in row["service_categories"]],
            ["laboratory"],
        )
        self.assertTrue(row["blocking_reason"])
        self.assertTrue(row["requested_at"])

    def test_21_the_appointment_state_is_never_changed_by_discovery(self):
        """No fake combined state, and no write of any kind."""
        appointment = self._in_consultation()
        self._order_laboratory(appointment)
        self._login_cashier()

        self.assertTrue(self._active(self._worklist(), appointment))
        appointment.invalidate_recordset()
        self.assertEqual(appointment.state, "in_consultation")
        self.assertEqual(
            appointment.sudo().front_desk_stage, "in_consultation",
            "front_desk_stage keeps its pre-consultation semantics exactly.",
        )

    def test_22_multiple_blocking_charges_produce_one_row(self):
        appointment = self._in_consultation()
        for index in range(3):
            self._raise_charge(
                appointment, self._service("laboratory"), "Test %s" % index
            )
        self._login_cashier()

        rows = self._active(self._worklist(), appointment)
        self.assertEqual(
            len(rows), 1, "Three unpaid tests are one trip to the window."
        )
        self.assertAlmostEqual(
            rows[0]["patient_outstanding"], LAB_FEE * 3, places=2
        )

    def test_23_payment_removes_the_visit_without_touching_the_state(self):
        """THE UAT EXIT CONDITION.

        Derived from billing truth: nothing clears a queue flag, because there
        is no queue flag to clear.
        """
        appointment = self._in_consultation()
        request = self._order_laboratory(appointment)
        self._login_cashier()
        self.assertTrue(self._active(self._worklist(), appointment))

        response = self._pay_http(appointment, LAB_FEE)
        self.assertEqual(response.status_code, 200)
        appointment.invalidate_recordset()

        self.assertFalse(
            self._active(self._worklist(), appointment),
            "Cleared money means the visit leaves the lane by itself.",
        )
        self.assertEqual(
            appointment.state, "in_consultation",
            "Paying is not a clinical transition.",
        )
        # And the doctor's side moved with it, which is the whole point.
        request.invalidate_recordset()
        self.assertFalse(request.sudo().billing_blocked)

    def test_24_partial_payment_keeps_the_visit_in_the_lane(self):
        appointment = self._in_consultation()
        self._order_laboratory(appointment)
        self._login_cashier()

        self._pay_http(appointment, LAB_FEE / 2)
        appointment.invalidate_recordset()

        rows = self._active(self._worklist(), appointment)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["lane"], "partial")
        self.assertAlmostEqual(
            rows[0]["patient_outstanding"], LAB_FEE / 2, places=2
        )

    def test_25_cancelled_charges_do_not_surface(self):
        appointment = self._in_consultation()
        charge = self._raise_charge(appointment, self._service("laboratory"))
        charge.sudo().write({"charge_state": "cancelled"})
        appointment.invalidate_recordset()
        self._login_cashier()

        self.assertFalse(
            self._active(self._worklist(), appointment),
            "A cancelled charge blocks nothing and owes nothing.",
        )

    def test_26_a_delivery_basis_charge_does_not_surface(self):
        """Pay-after-delivery is a legitimate configuration, not a block.

        It contributes 0.00 to amount_due_for_clearance, so the engine does not
        gate on it -- and neither may this lane, or the cashier would be sent
        after money the hospital has not asked for yet.
        """
        appointment = self._in_consultation()
        self._raise_charge(
            appointment, self._service("laboratory", prepayment_required=False)
        )
        self._login_cashier()
        self.assertFalse(self._active(self._worklist(), appointment))

    def test_27_emergency_bypass_never_enters_the_lane(self):
        appointment = self._in_consultation()
        appointment.encounter_id.with_user(self.manager).write(
            {
                "emergency_bypass": True,
                "emergency_bypass_reason": "Critical presentation",
            }
        )
        self._raise_charge(appointment, self._service("laboratory"))
        appointment.invalidate_recordset()
        self._login_cashier()

        self.assertFalse(
            self._active(self._worklist(), appointment),
            "Bypass is an independent authorized route, in both lanes.",
        )

    def test_28_a_cancelled_visit_never_enters_the_lane(self):
        appointment = self._in_consultation()
        self._raise_charge(appointment, self._service("laboratory"))
        appointment.sudo().action_cancel()
        self._login_cashier()
        self.assertFalse(self._active(self._worklist(), appointment))

    # ==================================================================
    # GENERICITY
    # ==================================================================
    def test_60_a_radiology_charge_produces_the_identical_row(self):
        """THE ANTI-SPECIAL-CASE TEST.

        Nothing in the lane knows what a laboratory is. This raises a RADIOLOGY
        charge through the same engine and asserts the same row -- so the
        radiology, medication and procedure order paths land in this queue with
        no cashier code written for them.
        """
        appointment = self._in_consultation()
        self._raise_charge(
            appointment, self._service("radiology", price=400.0), "Chest X-Ray"
        )
        self._login_cashier()

        rows = self._active(self._worklist(), appointment)
        self.assertEqual(len(rows), 1)
        self.assertAlmostEqual(rows[0]["patient_outstanding"], 400.0, places=2)
        self.assertEqual(
            [category["key"] for category in rows[0]["service_categories"]],
            ["radiology"],
        )

    def test_61_mixed_categories_are_reported_together_once(self):
        appointment = self._in_consultation()
        self._raise_charge(appointment, self._service("laboratory"), "CBC")
        self._raise_charge(
            appointment, self._service("pharmacy", price=60.0), "Amoxicillin"
        )
        self._raise_charge(appointment, self._service("laboratory"), "Glucose")
        self._login_cashier()

        rows = self._active(self._worklist(), appointment)
        self.assertEqual(len(rows), 1)
        keys = [category["key"] for category in rows[0]["service_categories"]]
        self.assertEqual(
            keys, ["laboratory", "pharmacy"],
            "De-duplicated and stably ordered, never one entry per charge.",
        )
        labels = {
            category["key"]: category["label"]
            for category in rows[0]["service_categories"]
        }
        self.assertEqual(labels["pharmacy"], "Medication")

    def test_62_the_lane_names_no_clinical_model(self):
        """A source-level guard against the special case creeping back in.

        The cheapest way to make this lane work for laboratory alone would be
        to reach into hospital.laboratory.request. That would then have to be
        rewritten for radiology, again for pharmacy and again for procedures.
        Asserting the ABSENCE of those names is what keeps the next author
        honest, because a laboratory-only implementation would still pass every
        other test in this file.
        """
        import inspect

        from odoo.addons.yoya_emr_api.controllers import cashier as cashier_controller
        from odoo.addons.yoya_emr_api.services import cashier_serializers
        from odoo.addons.yoya_reception_bridge.models import hospital_appointment

        for module in (
            cashier_controller,
            cashier_serializers,
            hospital_appointment,
        ):
            source = inspect.getsource(module)
            for banned in (
                "hospital.laboratory",
                "hospital.radiology",
                "hospital.pharmacy",
                "laboratory_request",
            ):
                with self.subTest(module=module.__name__, banned=banned):
                    self.assertNotIn(banned, source, banned)

    # ==================================================================
    # CONFIDENTIALITY
    # ==================================================================
    def test_70_no_clinical_content_reaches_the_queue(self):
        """A cashier is told a category and an amount. Never an indication."""
        appointment = self._in_consultation("Confidential Patient")
        request = self._order_laboratory(appointment)
        request.sudo().write(
            {
                "clinical_notes": "SECRETINDICATION suspected sepsis",
                "priority": "stat",
            }
        )
        self._login_cashier()

        blob = json.dumps(self._worklist())
        for banned in (
            "SECRETINDICATION",
            "clinical_notes",
            "clinical_indication",
            "diagnosis",
            "presenting_complaint",
            request.name,
            self.lab_test.name,
        ):
            self.assertNotIn(banned, blob, banned)

    def test_71_the_row_carries_no_commercial_terms(self):
        appointment = self._in_consultation()
        self._raise_charge(appointment, self._service("laboratory"))
        self._login_cashier()

        blob = json.dumps(self._worklist())
        for banned in (
            "limit_amount", "member_limit_amount", "limit_scope",
            "payment_terms_days", "tariff_mode", "coverage_percent",
        ):
            self.assertNotIn(banned, blob, banned)

    def test_72_the_row_keys_are_exactly_what_was_designed(self):
        appointment = self._in_consultation()
        self._raise_charge(appointment, self._service("laboratory"))
        self._login_cashier()

        row = self._active(self._worklist(), appointment)[0]
        self.assertEqual(
            set(row),
            {
                "appointment_id", "appointment_code", "appointment_date",
                "visit_state", "lane", "patient", "encounter_name",
                "patient_outstanding", "patient_paid", "responsibility_state",
                "blocking_reason", "blocking_reason_code",
                "service_categories", "requested_at",
            },
        )
        self.assertEqual(
            set(row["patient"]), {"id", "name", "identification_code"}
        )

    # ==================================================================
    # ACCESS
    # ==================================================================
    def test_80_a_real_cashier_can_read_the_lane(self):
        """The lane must not repeat the front_desk_stage ACL failure.

        A Hospital Cashier holds no rights on hospital.patient.evaluation. The
        active lane deliberately reads neither it nor front_desk_stage, and
        this asserts the endpoint stays 200 for a genuine cashier session.
        """
        appointment = self._in_consultation()
        self._order_laboratory(appointment)
        self._login_cashier()

        response = self.url_open(WORKLIST)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(
            self._active(json.loads(response.text), appointment)
        )

    def test_81_the_visit_detail_workspace_is_unchanged(self):
        """Discovery is the fix. The payment page is reused as-is."""
        appointment = self._in_consultation()
        self._order_laboratory(appointment)
        self._login_cashier()

        response = self.url_open(
            "/yoya-emr/api/v1/cashier/visits/%s" % appointment.id
        )
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.text)["data"]
        self.assertEqual(data["appointment"]["state"], "in_consultation")
        self.assertTrue(data["collectability"]["collectable"])

    def test_82_one_encounters_charges_never_reach_another_visit(self):
        """NO CROSS-ENCOUNTER BLEED, in either direction.

        The lane resolves charges through appointment -> encounter ->
        billing account, never by searching charges globally. A global search
        keyed on anything looser would surface a patient because SOMEBODY ELSE
        owes money, and would inflate this row's figure with another episode's
        obligation. Both halves are asserted.

        SCOPE NOTE, stated rather than implied: hospital.appointment carries no
        company_id and the cashier's own record rule on it is [(1,'=',1)] --
        pre-existing, and identical for the initial lane. This lane therefore
        inherits exactly the visibility the queue already had and widens
        nothing. Company scoping for the whole desk is a separate decision.
        """
        blocked = self._in_consultation("Owes Money")
        self._raise_charge(blocked, self._service("laboratory"))
        settled = self._in_consultation("Owes Nothing")

        self._login_cashier()
        payload = self._worklist()

        self.assertEqual(len(self._active(payload, blocked)), 1)
        self.assertFalse(
            self._active(payload, settled),
            "Another encounter's unpaid charge must not surface this visit.",
        )
        self.assertAlmostEqual(
            self._active(payload, blocked)[0]["patient_outstanding"],
            LAB_FEE,
            places=2,
            msg="The figure is this encounter's alone.",
        )
