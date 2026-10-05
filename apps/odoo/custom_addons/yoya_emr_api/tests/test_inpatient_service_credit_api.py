"""Radiology and Laboratory clearance for an ACTIVE SELF-PAY INPATIENT is
decided by the same inpatient-credit authority Pharmacy uses (UAT RADREQ0414 /
ADM00004): hospital.billing.engine.check_service_clearance().

RADIOLOGY
  A. sufficient credit            -> covered; the Doctor Desk no longer reads
                                     "awaiting clearance"; the desk can start it
  B. insufficient credit          -> blocked, "shortfall"; Start refused
  C. cost exactly equals credit   -> allowed
  D. an outpatient is unchanged   -> blocked until paid, then "service"
  E. a sponsored inpatient is unchanged (payer rules; never the advance)
  F. Radiology creates no receipt
  G. released CT -> delivered care +CT, credit -CT, in the settlement
  H. two services cannot spend the same credit: CT + CT, CT + Pharmacy
  I. no amount crosses the Radiology Desk
  +  a covered CT never reaches the Cashier's Service Payments lane

LABORATORY
  A. sufficient credit -> covered, collectable
  B. insufficient credit -> blocked, collection refused
  C. an outpatient is unchanged
  D. no receipt
  E. validated result -> delivered care +fee, credit -fee
  F. no amount crosses the Laboratory Desk
  +  a covered test never reaches the Cashier's Service Payments lane
"""
import json
import uuid

from odoo.exceptions import UserError
from odoo.tests import tagged

from .test_lab_desk_api import DETAIL as LAB_DETAIL, FORBIDDEN_KEYS as LAB_FORBIDDEN_KEYS, PREPAID_LAB_FEE, LabDeskCase
from .test_pharmacy_inpatient_credit_api import PharmacyInpatientCreditCase
from .test_radiology_desk_api import DETAIL as RAD_DETAIL, _walk_keys
from .test_radiology_desk_report import RadReportCase

CT_PRICE = 900.0
SCHEDULE = RAD_DETAIL + "/schedule"
START = RAD_DETAIL + "/start"
DOCTOR_RAD_ORDERS = "/yoya-emr/api/v1/doctor/visits/%s/orders/radiology"
CASHIER_WORKLIST = "/yoya-emr/api/v1/cashier/worklist"
COVERED = "inpatient_credit"


class InpatientCreditMixin:
    """An admission on the visit, on a ward that charges nothing, so the
    patient's credit moves only with what the test delivers."""

    @classmethod
    def _make_ic_ward(cls):
        tag = uuid.uuid4().hex[:5].upper()
        cls.ic_ward = cls.env["hospital.ward"].sudo().create({
            "name": "SC Ward %s" % tag, "code": "SC%s" % tag, "ward_type": "surgical",
            "company_id": cls.env.company.id, "daily_ward_rate": 0.0, "admission_fee": 0.0,
        })
        cls.ic_room = cls.env["hospital.room"].sudo().create({
            "name": "SC Room", "code": "SCR%s" % tag, "ward_id": cls.ic_ward.id,
        })

    def _admit(self, appointment, encounter, advance=None):
        bed = self.env["hospital.bed"].sudo().create({
            "name": "SC Bed %s" % uuid.uuid4().hex[:4],
            "code": "SCB%s" % uuid.uuid4().hex[:5].upper(),
            "room_id": self.ic_room.id,
        })
        admission = self.env["hospital.admission"].sudo().create({
            "patient_id": appointment.patient_id.id,
            "physician_id": self.doctor.id,
            "company_id": self.env.company.id,
            "encounter_id": encounter.id,
            "ward_id": bed.ward_id.id, "room_id": bed.room_id.id, "bed_id": bed.id,
        })
        admission.action_confirm_admission()
        if advance:
            admission.with_user(self.doctor_user)._desk_set_estimate(
                advance, "Planned stay", str(uuid.uuid4()), admission.workflow_revision
            )
            admission.with_user(self.cashier)._cashier_record_advance(
                advance, "cash", idempotency_key=uuid.uuid4().hex,
            )
        self.env.invalidate_all()
        return admission

    def _summary(self, admission):
        self.env.invalidate_all()
        return admission.sudo()._inpatient_financial_summary()

    def _credit_to(self, admission, target):
        """Deliver neutral care until the patient's credit is exactly `target`."""
        burn = self._summary(admission)["patient_credit"] - target
        self.assertGreaterEqual(burn, 0.0)
        if burn > 0:
            engine = self.env["hospital.billing.engine"].sudo()
            service = self.env["hospital.billing.service"].sudo().create({
                "name": "SC burn %s" % uuid.uuid4().hex[:5], "service_type": "procedure",
                "default_price": burn, "prepayment_required": False,
            })
            charge = engine.create_or_update_charge(
                admission.encounter_id, "hospital.procedure.request", admission.id, "sc_burn",
                "SC burn", source_line_id=int(uuid.uuid4().int % 10**8), service=service,
                qty_requested=1, unit_price=burn,
            )
            engine.activate_charge(charge)
            engine.mark_charge_delivered(charge, qty_delivered=1)
        self.assertEqual(self._summary(admission)["patient_credit"], target)

    def _receipts(self):
        return self.env["hospital.charge.receipt"].sudo().search_count([])

    def _make_sponsored(self, encounter):
        sponsor = self.env["res.partner"].sudo().create({"name": "SC Sponsor %s" % uuid.uuid4().hex[:4]})
        encounter.sudo().write({"payer_type": "corporate", "payer_id": sponsor.id})
        self.env.invalidate_all()

    def _in_cashier_service_lane(self, appointment):
        self.authenticate(self.cashier.login, self.cashier_password)
        payload = json.loads(self.url_open(CASHIER_WORKLIST).text)
        return any(
            row["appointment_id"] == appointment.id
            for row in payload["data"]["active_service_clearance"]
        )


# ===========================================================================
# RADIOLOGY
# ===========================================================================
class RadCreditCase(InpatientCreditMixin, RadReportCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._make_ic_ward()

    def _inpatient_ct(self, advance=50000.0, credit=None):
        appointment, encounter, _patient = self._visit()
        admission = self._admit(appointment, encounter, advance=advance)
        if credit is not None:
            self._credit_to(admission, credit)
        record = self._new_order(appointment, exams=[self.ct_brain])
        record.invalidate_recordset()
        return record, appointment, encounter, admission

    def _cover(self, record):
        record.invalidate_recordset()
        return record.sudo().billing_financial_cover

    def _schedule_and_start(self, record):
        response, payload = self._act(SCHEDULE, record)
        self.assertEqual(response.status_code, 200, payload)
        return self._act(START, record)

    def _release(self, record):
        report, _data = self._opened(record)
        response, payload = self._enter(report, {"findings": "No bleed.", "impression": "Normal study."})
        self.assertEqual(response.status_code, 200, payload)
        report.with_user(self.radiologist).action_validate()
        report.with_user(self.radiologist).action_release()
        record.invalidate_recordset()
        return report


@tagged("post_install", "-at_install", "inpatient_service_credit")
class TestRadiologyInpatientCredit(RadCreditCase):

    def test_a_f_g_covered_ct_is_started_released_and_consumes_the_credit(self):
        record, appointment, _encounter, admission = self._inpatient_ct()
        receipts = self._receipts()     # after the visit payment and the advance
        before = self._summary(admission)
        # A: covered, on every surface that used to read "awaiting clearance".
        self.assertFalse(record.sudo().billing_blocked)
        self.assertEqual(self._cover(record), COVERED)
        detail = self._detail(record)
        self.assertEqual((detail["lane"], detail["financial_cover"]), ("to_schedule", COVERED))
        _r, payload = self._get(DOCTOR_RAD_ORDERS % appointment.id)
        self.assertEqual(payload["data"]["orders"][0]["status"], "awaiting_scheduling")

        response, payload = self._schedule_and_start(record)
        self.assertEqual(response.status_code, 200, payload)
        self.assertEqual(record.state, "in_progress")
        # Started, not delivered: the settlement has not moved yet.
        self.assertEqual(self._summary(admission)["actual_delivered"], before["actual_delivered"])

        self._release(record)                                                  # G
        self.assertEqual(record.state, "completed")
        after = self._summary(admission)
        self.assertEqual(after["actual_delivered"] - before["actual_delivered"], CT_PRICE)
        self.assertEqual(before["patient_credit"] - after["patient_credit"], CT_PRICE)
        self.assertEqual(
            after["delivered_by_category"]["radiology"]
            - before["delivered_by_category"].get("radiology", 0.0),
            CT_PRICE,
        )
        self.assertEqual(after["financial_state"], "credit")
        self.assertEqual(self._receipts(), receipts)                           # F

    def test_b_insufficient_credit_blocks(self):
        record, *_rest, admission = self._inpatient_ct(credit=CT_PRICE - 100.0)
        self.assertTrue(record.sudo().billing_blocked)
        self.assertEqual(self._cover(record), "shortfall")
        detail = self._detail(record)
        self.assertEqual((detail["lane"], detail["financial_cover"]), ("awaiting_clearance", "shortfall"))
        record.sudo().action_schedule()      # the MODEL permits booking; Start is the gate
        response, payload = self._act(START, record)
        self.assertEqual(response.status_code, 422, payload)
        self.assertEqual(payload["error"]["code"], "radiology_request_start_blocked")
        self.assertEqual(record.state, "scheduled")
        self.assertEqual(self._summary(admission)["patient_credit"], CT_PRICE - 100.0)

    def test_c_cost_exactly_equal_to_the_credit_is_allowed(self):
        record, *_rest = self._inpatient_ct(credit=CT_PRICE)
        self.assertEqual(self._cover(record), COVERED)
        response, payload = self._schedule_and_start(record)
        self.assertEqual(response.status_code, 200, payload)

    def test_d_an_outpatient_is_unchanged(self):
        record, _patient, _appointment, encounter = self._blocked_requested()
        self.assertIsNone(self._detail(record)["financial_cover"])
        self.assertFalse(self._cover(record))
        self._settle(encounter)
        self.assertEqual(self._cover(record), "service")
        self.assertFalse(record.sudo().billing_blocked)

    def test_e_a_sponsored_inpatient_keeps_the_payer_rules(self):
        appointment, encounter, _patient = self._visit()
        self._admit(appointment, encounter, advance=50000.0)
        self._make_sponsored(encounter)
        record = self._new_order(appointment, exams=[self.ct_brain])
        record.invalidate_recordset()
        payer_answer = self.env["hospital.billing.engine"].sudo().check_financial_clearance(
            encounter, charges=record.sudo().charge_line_ids
        )
        # Exactly the payer rules' answer; the self-pay advance is never used.
        self.assertEqual(record.sudo().billing_blocked, not payer_answer["cleared"])
        self.assertNotEqual(self._cover(record), COVERED)
        self.assertNotEqual(self._cover(record), "shortfall")

    def test_h_two_scans_cannot_spend_the_same_credit(self):
        appointment, encounter, _patient = self._visit()
        admission = self._admit(appointment, encounter, advance=50000.0)
        self._credit_to(admission, 1000.0)
        first = self._new_order(appointment, exams=[self.ct_brain])
        second = self._new_order(appointment, exams=[self.ct_brain])
        # Each alone fits; together they would spend 1,800 of 1,000.
        self.assertEqual(self._cover(first), COVERED)
        self.assertEqual(self._cover(second), COVERED)
        response, payload = self._schedule_and_start(first)
        self.assertEqual(response.status_code, 200, payload)
        # The started scan is committed, although nothing is delivered yet.
        self.assertEqual(self._cover(second), "shortfall")
        second.sudo().action_schedule()
        response, payload = self._act(START, second)
        self.assertEqual(response.status_code, 422, payload)
        self.assertEqual(second.state, "scheduled")

    def test_i_no_amount_crosses_the_radiology_desk(self):
        covered, *_rest = self._inpatient_ct()
        short, *_rest = self._inpatient_ct(credit=100.0)
        for record in (covered, short):
            response, payload = self._desk_get(RAD_DETAIL % record.id)
            self._assert_money_free(payload, response.text)
            for figure in ("50000", "50,000", "900.0", "800.0"):
                self.assertNotIn(figure, response.text)
        rows = self._rows(self._worklist())
        self._assert_money_free({"rows": [rows[covered.id], rows[short.id]]}, json.dumps(rows[covered.id]))

    def test_cashier_lane_never_asks_for_a_covered_ct(self):
        record, appointment, encounter, _admission = self._inpatient_ct()
        encounter.invalidate_recordset()
        self.assertTrue(encounter.sudo().reception_clearance_ok)
        self.assertEqual(encounter.sudo().reception_clearance_state, COVERED)
        self.assertFalse(appointment.sudo()._is_active_service_clearance_pending())
        self.assertFalse(self._in_cashier_service_lane(appointment))
        # Still absent once the scan has started, and once it is delivered.
        self._schedule_and_start(record)
        self.assertFalse(self._in_cashier_service_lane(appointment))
        self._release(record)
        self.assertFalse(self._in_cashier_service_lane(appointment))

    def test_cashier_lane_still_shows_a_shortfall(self):
        _record, appointment, *_rest = self._inpatient_ct(credit=100.0)
        self.assertTrue(appointment.sudo()._is_active_service_clearance_pending())


# ===========================================================================
# RADIOLOGY + PHARMACY: one credit, two departments
# ===========================================================================
@tagged("post_install", "-at_install", "inpatient_service_credit")
class TestRadiologyAndPharmacyShareTheCredit(PharmacyInpatientCreditCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        tag = uuid.uuid4().hex[:6].upper()
        service = cls.env["hospital.billing.service"].sudo().create({
            "name": "SC CT %s" % tag, "code": "SC-CT-%s" % tag, "service_type": "radiology",
            "default_price": CT_PRICE, "prepayment_required": True, "tax_treatment": "exempt",
            "company_id": cls.env.company.id, "currency_id": cls.env.company.currency_id.id,
            "uom_id": cls.env["uom.uom"].sudo().search([], limit=1).id,
        })
        cls.sc_ct = cls.env["hospital.radiology.exam"].sudo().create({
            "name": "SC CT Brain %s" % tag, "code": "SCCT%s" % tag, "modality": "ct",
            "body_part": "Head", "billing_service_id": service.id,
        })

    def _ct(self, appointment, encounter):
        record = self.env["hospital.radiology.request"].sudo().create({
            "patient_id": appointment.patient_id.id, "physician_id": self.doctor.id,
            "appointment_id": appointment.id, "encounter_id": encounter.id,
            "line_ids": [(0, 0, {"exam_id": self.sc_ct.id})],
        })
        record.action_confirm_request()
        record.action_schedule()
        record.invalidate_recordset()
        return record

    def test_h_a_started_ct_holds_its_value_against_a_dispense(self):
        appointment, encounter, admission = self._inpatient(estimate=50000.0)
        self._credit_to(admission, 1000.0)
        ct = self._ct(appointment, encounter)
        self.assertEqual(ct.billing_financial_cover, "inpatient_credit")
        ct.action_mark_in_progress()                                  # 900 of 1,000 committed
        _rx, dispense = self._rx(appointment, self.amoxil, qty=10.0)
        self._prepare(dispense, 4.0)                                  # 480 > the 100 left
        self.assertEqual(self._cover(dispense), "shortfall")
        response, payload = self._validate_http(dispense)
        self._error(response, payload, 422, "pharmacy_billing_blocked")

    def test_h_a_dispense_first_leaves_too_little_for_the_ct(self):
        appointment, encounter, admission = self._inpatient(estimate=50000.0)
        self._credit_to(admission, 1000.0)
        ct = self._ct(appointment, encounter)
        _rx, dispense = self._rx(appointment, self.amoxil, qty=10.0)
        self._prepare(dispense, 4.0)
        response, payload = self._validate_http(dispense)             # 480 delivered
        self.assertEqual(response.status_code, 200, payload)
        ct.invalidate_recordset()
        self.assertEqual(ct.billing_financial_cover, "shortfall")     # 900 > 520
        with self.assertRaises(UserError):
            ct.action_mark_in_progress()
        self.assertEqual(ct.state, "scheduled")


# ===========================================================================
# LABORATORY
# ===========================================================================
class LabCreditCase(InpatientCreditMixin, LabDeskCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._make_ic_ward()

    def _inpatient_lab(self, advance=50000.0, credit=None, admit=True):
        appointment, encounter = self._ready_visit()
        admission = self._admit(appointment, encounter, advance=advance) if admit else None
        if credit is not None:
            self._credit_to(admission, credit)
        record = self.env["hospital.laboratory.request"].sudo().create({
            "patient_id": appointment.patient_id.id, "physician_id": self.doctor.id,
            "appointment_id": appointment.id, "encounter_id": encounter.id,
            "line_ids": [(0, 0, {"test_id": self.prepaid_test.id})],
        })
        record.action_confirm_request()
        record.invalidate_recordset()
        return record, appointment, encounter, admission

    def _cover(self, record):
        record.invalidate_recordset()
        return record.sudo().billing_financial_cover

    def _row_for(self, record, **params):
        """The desk's own row for this request (the detail carries every row key)."""
        response, payload = self._lab_get(LAB_DETAIL % record.id)
        self.assertEqual(response.status_code, 200, payload)
        return payload["data"]["request"]

    def _assert_lab_money_free(self, payload, text):
        for key in _walk_keys(payload):
            for fragment in LAB_FORBIDDEN_KEYS:
                self.assertNotIn(fragment, str(key).lower(), key)
        for figure in ("ETB", "Birr", "50000", "640.0", "540.0"):
            self.assertNotIn(figure, text)


@tagged("post_install", "-at_install", "inpatient_service_credit")
class TestLaboratoryInpatientCredit(LabCreditCase):

    def test_a_d_e_covered_test_is_collected_validated_and_consumes_the_credit(self):
        record, _appointment, _encounter, admission = self._inpatient_lab()
        receipts = self._receipts()     # after the visit payment and the advance
        before = self._summary(admission)
        self.assertFalse(record.sudo().billing_blocked)                      # A
        self.assertEqual(self._cover(record), COVERED)
        row = self._row_for(record)
        self.assertEqual((row["status"], row["financial_cover"]), ("ready_for_collection", COVERED))
        response, payload = self._collect(record)
        self.assertEqual(response.status_code, 200, payload)
        record.invalidate_recordset()
        self.assertEqual(record.state, "sample_collected")

        record.sudo().action_mark_in_progress()                              # E
        result = self.env["hospital.laboratory.result"].sudo().create({"request_id": record.id})
        for line in result.line_ids:
            line.sudo().write({"result_value": "7.4", "unit": "g/dL"})
        result.action_mark_entered()
        result.action_validate()
        result.action_release()
        after = self._summary(admission)
        self.assertEqual(after["actual_delivered"] - before["actual_delivered"], PREPAID_LAB_FEE)
        self.assertEqual(before["patient_credit"] - after["patient_credit"], PREPAID_LAB_FEE)
        self.assertEqual(
            after["delivered_by_category"]["laboratory"]
            - before["delivered_by_category"].get("laboratory", 0.0),
            PREPAID_LAB_FEE,
        )
        self.assertEqual(self._receipts(), receipts)                         # D

    def test_b_insufficient_credit_blocks_collection(self):
        record, *_rest = self._inpatient_lab(credit=PREPAID_LAB_FEE - 100.0)
        self.assertTrue(record.sudo().billing_blocked)
        self.assertEqual(self._cover(record), "shortfall")
        row = self._row_for(record)
        self.assertEqual((row["status"], row["financial_cover"]), ("awaiting_clearance", "shortfall"))
        response, payload = self._collect(record)
        self.assertEqual(response.status_code, 422, payload)
        self.assertEqual(payload["error"]["code"], "lab_not_financially_cleared")
        record.invalidate_recordset()
        self.assertEqual(record.state, "requested")

    def test_c_an_outpatient_is_unchanged(self):
        record, _appointment, encounter, _admission = self._inpatient_lab(admit=False)
        self.assertTrue(record.sudo().billing_blocked)
        self.assertFalse(self._cover(record))
        self.assertIsNone(self._row_for(record)["financial_cover"])
        self._pay(encounter)
        self.assertEqual(self._cover(record), "service")

    def test_f_no_amount_crosses_the_laboratory_desk(self):
        covered, *_rest = self._inpatient_lab()
        short, *_rest = self._inpatient_lab(credit=100.0)
        for record in (covered, short):
            response, payload = self._lab_get(LAB_DETAIL % record.id)
            self.assertEqual(response.status_code, 200, payload)
            self._assert_lab_money_free(payload, response.text)
        row = self._row_for(covered)
        self._assert_lab_money_free(row, json.dumps(row))

    def test_cashier_lane_never_asks_for_a_covered_test(self):
        record, appointment, encounter, _admission = self._inpatient_lab()
        encounter.invalidate_recordset()
        self.assertTrue(encounter.sudo().reception_clearance_ok)
        self.assertFalse(appointment.sudo()._is_active_service_clearance_pending())
        self.assertFalse(self._in_cashier_service_lane(appointment))
        self._collect(record)
        self.assertFalse(self._in_cashier_service_lane(appointment))
