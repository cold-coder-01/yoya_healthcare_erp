"""Prescribing from the Doctor Desk.

WHAT THESE TESTS ARE FOR
------------------------
Medication is the third ancillary service a doctor orders the same way, and it
is the one that differs most underneath. Five properties carry this slice:

  1. THE DOCTOR DESK CREATES NOTHING DOWNSTREAM. It writes a prescription and
     calls the EXISTING action_confirm(); hospital_pharmacy's override composes
     exactly one draft dispense. Unlike laboratory and radiology, confirmation
     raises NO charge, moves NO stock and creates NO fiscal transaction --
     medication is billed later, by the pharmacist, at Mark Ready. The tests
     below assert all four side-effect counts are unchanged.

  2. ONE SUBMISSION IS ONE PRESCRIPTION, however many medicines it names, and
     therefore ONE dispense. Writing one prescription per drug would send the
     patient to the counter once per drug.

  3. THE PICKER PREDICTS TWO GATES, NOT ONE. A medicine needs a billing service
     the pharmacist's Mark Ready will accept AND an inventory mapping Validate
     Dispense will accept. A medicine failing the second is the dangerous one:
     it would be prescribed, priced and PAID FOR before anything refused.

  4. STOCK IS NOT A FILTER. A correctly configured medicine that happens to be
     out of stock must still be prescribable -- nothing reserves stock at
     prescribing, partial dispensing is the routine outcome, and a picker that
     hid it would push the doctor toward whatever was on the shelf rather than
     whatever was indicated.

  5. THE STATUS COMES FROM THE DISPENSE, NEVER FROM prescription.state, which
     stays `confirmed` forever however much has been handed over.
"""
import json
import uuid

from odoo import fields
from odoo.tests import tagged
from odoo.tools import mute_logger

from .test_doctor_consultation_complete import CompletionCase
from .test_doctor_desk_api import G_DOCTOR

CATALOGUE = "/yoya-emr/api/v1/doctor/catalogue/medicines"
ORDERS = "/yoya-emr/api/v1/doctor/visits/%s/orders/medications"
CANCEL = "/yoya-emr/api/v1/doctor/visits/%s/orders/medications/%s/cancel"

G_PHARMACIST = "hospital_management.group_hospital_pharmacist"

EXPECTED_PRESCRIPTION_KEYS = {
    "id", "prescription_code", "medicines", "diagnosis", "notes", "status",
    "status_label", "ordered_at", "created_at", "progress_itemised",
    "editable", "cancellable",
}

EXPECTED_MEDICINE_LINE_KEYS = {
    "id", "medicine_id", "name", "code", "dosage_form", "strength", "dosage",
    "route", "frequency", "duration", "quantity", "instructions",
    "dispensed_quantity", "remaining_quantity",
}

EXPECTED_CATALOGUE_KEYS = {
    "id", "name", "code", "generic_name", "brand_name", "dosage_form",
    "strength", "route", "category",
}

# Money, stock, supply chain and pharmacy-internal vocabulary. None of it may
# appear anywhere in a Doctor Desk medication payload.
FORBIDDEN_KEYS = (
    "amount", "balance", "outstanding", "paid", "receipt", "sponsor",
    "payer", "tariff", "price", "invoice", "charge", "coverage",
    "billing_service", "default_price", "sale_price", "unit_price",
    "subtotal", "billing_delivered", "fiscal",
    "inventory_item", "inventory_consumed", "consumption", "batch",
    "expiry", "quantity_on_hand", "available_quantity", "unit_cost",
    "pharmacist", "encounter_id",
)


class MedicationCase(CompletionCase):
    """CompletionCase plus a pharmacy catalogue with every misconfiguration.

    Built on CompletionCase rather than DiagnosisCase so the completed-
    consultation tests sign off through the REAL Slice 4 path -- assessment,
    primary diagnosis, then the complete endpoint -- instead of writing state
    by hand and testing a shape the desk never produces.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        tag = uuid.uuid4().hex[:6]
        cls.tag = tag
        cls.uom = cls.env["uom.uom"].sudo().search([], limit=1)
        cls.pharmacist_password = "dm-pharm-pw-1"
        cls.pharmacist = cls._make_user(
            "dm_pharm", cls.pharmacist_password, [G_PHARMACIST]
        )
        cls.other_doctor_password = "dm-doc2-pw-1"
        cls.other_doctor_user = cls._make_user(
            "dm_doc2", cls.other_doctor_password, [G_DOCTOR]
        )
        cls.other_doctor = cls.env["hospital.doctor"].sudo().create(
            {"name": "Medication Other Doctor %s" % tag,
             "user_id": cls.other_doctor_user.id}
        )

        cls.inventory_ready = "hospital.inventory.item" in cls.env
        cls.pharmacy_location = None
        cls.category = None
        if cls.inventory_ready:
            cls.pharmacy_location = cls.env[
                "hospital.inventory.location"
            ].sudo().get_default_pharmacy_store()
            cls.category = cls.env.ref(
                "hospital_inventory.category_pharmacy_medicine",
                raise_if_not_found=False,
            )
            cls.inventory_ready = bool(cls.pharmacy_location and cls.category)

        # FULLY CONFIGURED: billing service + inventory item + stock.
        cls.amoxil = cls._make_medicine("Amoxil", "capsule", stock=500.0)
        cls.cetiriz = cls._make_medicine("Cetiriz", "tablet", stock=500.0)
        # FULLY CONFIGURED BUT OUT OF STOCK. Must still be offered: prescribing
        # is a clinical act, and nothing reserves stock at this point anyway.
        cls.dry = cls._make_medicine("DryStock", "tablet", stock=0.0)
        # NO BILLING SERVICE -- the shape three of the five UAT medicines have.
        cls.unbilled = cls._make_medicine(
            "Unbilled", "tablet", stock=500.0, billing=False
        )
        # BILLED BUT NOT STOCKED. THE DANGEROUS ONE: Mark Ready would accept it
        # and take the patient's money, and Validate Dispense would then refuse.
        cls.unstocked = cls._make_medicine(
            "Unstocked", "tablet", stock=None, inventory=False
        )
        # Archived medicine: still a row, never prescribable.
        cls.archived = cls._make_medicine("Archived", "tablet", stock=500.0)
        cls.archived.sudo().write({"active": False})
        # Mapped to an ARCHIVED billing service.
        cls.dead_service = cls._make_medicine("DeadSvc", "tablet", stock=500.0)
        cls.dead_service.sudo().billing_service_id.write({"active": False})
        # Mapped to a service whose effective window has closed.
        cls.expired = cls._make_medicine("Expired", "tablet", stock=500.0)
        cls.expired.sudo().billing_service_id.write(
            {"effective_date_end": "2000-01-01"}
        )

    # ------------------------------------------------------------------
    @classmethod
    def _make_medicine(cls, label, dosage_form, stock=None, billing=True,
                       inventory=True):
        name = "%s %s" % (label, cls.tag)
        vals = {
            "name": name,
            "code": "%s-%s" % (label.upper()[:6], cls.tag.upper()),
            "dosage_form": dosage_form,
            "strength": "500mg",
            "route": "oral",
            "generic_name": "%s generic" % label,
        }
        if billing:
            service = cls.env["hospital.billing.service"].sudo().create(
                {
                    "name": "%s Service" % name,
                    "code": "T-MED-%s-%s" % (label.upper()[:6], cls.tag.upper()),
                    "service_type": "pharmacy",
                    "default_price": 120.0,
                    "company_id": cls.env.company.id,
                    "currency_id": cls.env.company.currency_id.id,
                    "uom_id": cls.uom.id,
                    # PREPAID, which is how this hospital prices medication and
                    # what makes an unpaid ready dispense genuinely blocked.
                    "prepayment_required": True,
                    "tax_treatment": "exempt",
                }
            )
            vals["billing_service_id"] = service.id
        medicine = cls.env["hospital.pharmacy.medicine"].sudo().create(vals)
        if "sale_price" in medicine._fields and billing:
            medicine.sudo().write({"sale_price": 120.0})

        if inventory and cls.inventory_ready:
            item = cls.env["hospital.inventory.item"].sudo().create(
                {
                    "name": "%s Item" % name,
                    "code": "ITM-%s-%s" % (label.upper()[:6], cls.tag.upper()),
                    "item_type": "medicine",
                    "accounting_category": "medicine",
                    "category_id": cls.category.id,
                    "unit_of_measure": dosage_form,
                    "standard_cost": 10.0,
                    "currency_id": cls.env.company.currency_id.id,
                    "company_id": cls.env.company.id,
                }
            )
            medicine.sudo().write({"inventory_item_id": item.id})
            if stock:
                cls.env["hospital.inventory.batch"].sudo().create(
                    {
                        "item_id": item.id,
                        "batch_number": "B-%s-%s" % (
                            label.upper()[:6], cls.tag.upper()
                        ),
                        "location_id": cls.pharmacy_location.id,
                        "received_date": fields.Date.today(),
                        "expiry_date": fields.Date.add(
                            fields.Date.today(), days=365
                        ),
                        "quantity_on_hand": stock,
                        "unit_cost": 10.0,
                        "currency_id": cls.env.company.currency_id.id,
                        "state": "available",
                    }
                )
        return medicine

    # ------------------------------------------------------------------
    def _prescribe(self, appointment, medicines=None, **extra):
        """Write a prescription the way a conforming client does.

        A FRESH request_token IS ALWAYS SENT, because the endpoint requires one.
        `extra` overrides it, so a test needing a SPECIFIC token (idempotency)
        or a malformed one passes request_token= explicitly; a test needing the
        key ABSENT builds the body itself with _post_body.
        """
        entries = medicines
        if entries is None:
            entries = [{"medicine_id": self.amoxil.id, "quantity": 30}]
        body = {"medicines": entries, "request_token": uuid.uuid4().hex}
        body.update(extra)
        return self._post_body(ORDERS % appointment.id, body)

    def _prescriptions(self, payload):
        return payload["data"]["prescriptions"]

    def _prescriptions_of(self, consultation):
        return (
            self.env["hospital.prescription"]
            .sudo()
            .with_context(active_test=False)
            .search([("consultation_id", "=", consultation.id)])
        )

    def _side_effects(self):
        """Everything prescribing must NOT create."""
        counts = {}
        for key, model in (
            ("charges", "hospital.charge.line"),
            ("receipts", "hospital.charge.receipt"),
            ("bills", "hospital.patient.bill"),
            ("consumption", "hospital.stock.consumption"),
            ("stock_moves", "hospital.stock.movement"),
            ("fiscal", "hospital.fiscal.transaction"),
        ):
            if model in self.env:
                counts[key] = self.env[model].sudo().search_count([])
        return counts

    def _dispense_of(self, prescription):
        return (
            self.env["hospital.pharmacy.dispense"]
            .sudo()
            .search([("prescription_id", "=", prescription.id)])
        )

    def _scan(self, node, found):
        if isinstance(node, dict):
            for key, value in node.items():
                found.add(str(key).lower())
                self._scan(value, found)
        elif isinstance(node, list):
            for item in node:
                self._scan(item, found)

    def _assert_clean(self, data, label):
        found = set()
        self._scan(data, found)
        for forbidden in FORBIDDEN_KEYS:
            for key in found:
                self.assertNotIn(
                    forbidden, key,
                    "%s leaked a forbidden key containing %r: %r"
                    % (label, forbidden, key),
                )


@tagged("post_install", "-at_install", "doctor_medication")
class TestMedicineCatalogue(MedicationCase):
    """The medicine picker: bounded, searchable, and priced nowhere."""

    def test_01_only_deterministically_orderable_medicines_are_offered(self):
        response, payload = self._get(CATALOGUE, q=self.tag)
        self.assertEqual(response.status_code, 200)
        names = {m["name"] for m in payload["data"]["medicines"]}
        self.assertIn(self.amoxil.name, names)
        self.assertIn(self.cetiriz.name, names)

    def test_02_a_medicine_with_no_billing_service_is_excluded(self):
        _response, payload = self._get(CATALOGUE, q=self.tag)
        names = {m["name"] for m in payload["data"]["medicines"]}
        self.assertNotIn(self.unbilled.name, names)
        # And the two ways a mapped service can still be unusable.
        self.assertNotIn(self.dead_service.name, names)
        self.assertNotIn(self.expired.name, names)

    def test_03_a_medicine_with_no_inventory_mapping_is_excluded(self):
        """THE DANGEROUS ONE. Billable but unstocked means the patient pays and
        then cannot be given the medicine."""
        if not self.inventory_ready:
            self.skipTest("hospital_inventory is not configured")
        _response, payload = self._get(CATALOGUE, q=self.tag)
        names = {m["name"] for m in payload["data"]["medicines"]}
        self.assertNotIn(self.unstocked.name, names)

    def test_04_a_valid_medicine_with_no_stock_is_still_offered(self):
        """Stock is deliberately NOT a filter."""
        _response, payload = self._get(CATALOGUE, q=self.tag)
        names = {m["name"] for m in payload["data"]["medicines"]}
        self.assertIn(
            self.dry.name,
            names,
            "an out-of-stock but correctly configured medicine must stay "
            "prescribable: nothing reserves stock at prescribing",
        )

    def test_05_an_archived_medicine_is_never_offered(self):
        _response, payload = self._get(CATALOGUE, q=self.tag)
        names = {m["name"] for m in payload["data"]["medicines"]}
        self.assertNotIn(self.archived.name, names)

    def test_06_the_endpoint_matches_the_models_own_domain(self):
        """The controller must not have its own idea of orderable."""
        model = self.env["hospital.pharmacy.medicine"].sudo()
        offered = model.search(
            model.doctor_orderable_domain() + [("name", "ilike", self.tag)]
        )
        _response, payload = self._get(CATALOGUE, q=self.tag, limit=50)
        self.assertEqual(
            {m["id"] for m in payload["data"]["medicines"]},
            set(offered.ids),
        )

    def test_07_search_matches_name_code_and_generic(self):
        for term in (self.amoxil.name, self.amoxil.code, "Amoxil generic"):
            _response, payload = self._get(CATALOGUE, q=term)
            self.assertIn(
                self.amoxil.id,
                {m["id"] for m in payload["data"]["medicines"]},
                "search failed for %r" % term,
            )

    def test_08_the_limit_is_clamped_server_side(self):
        _response, payload = self._get(CATALOGUE, q=self.tag, limit=9999)
        self.assertLessEqual(payload["data"]["limit"], 50)

    def test_09_catalogue_rows_carry_no_money_or_stock(self):
        _response, payload = self._get(CATALOGUE, q=self.tag)
        rows = payload["data"]["medicines"]
        self.assertTrue(rows)
        for row in rows:
            self.assertEqual(set(row), EXPECTED_CATALOGUE_KEYS)
        self._assert_clean(payload["data"], "medicine catalogue")

    def test_10_non_doctor_roles_are_denied_the_catalogue(self):
        for user, password in (
            (self.cashier, self.cashier_password),
            (self.receptionist, self.receptionist_password),
        ):
            response, _payload = self._get(
                CATALOGUE, user=user, password=password, q=self.tag
            )
            self.assertEqual(response.status_code, 403)


@tagged("post_install", "-at_install", "doctor_medication")
class TestPrescriptionWriting(MedicationCase):
    """Writing a prescription, and everything it must not do."""

    def test_20_an_empty_consultation_lists_nothing(self):
        appointment, _encounter = self._in_consultation_visit()
        _response, payload = self._get(ORDERS % appointment.id)
        self.assertEqual(self._prescriptions(payload), [])
        self.assertTrue(payload["data"]["can_order"])

    def test_21_one_medicine_creates_one_prescription(self):
        appointment, encounter = self._in_consultation_visit()
        response, payload = self._prescribe(appointment)
        self.assertEqual(response.status_code, 200)
        rows = self._prescriptions(payload)
        self.assertEqual(len(rows), 1)
        self.assertEqual(len(rows[0]["medicines"]), 1)

        consultation = self._consultation_of(encounter)
        prescriptions = self._prescriptions_of(consultation)
        self.assertEqual(len(prescriptions), 1)
        self.assertEqual(prescriptions.state, "confirmed")
        self.assertEqual(prescriptions.patient_id, appointment.patient_id)
        self.assertEqual(prescriptions.physician_id, self.doctor)
        self.assertEqual(prescriptions.appointment_id, appointment)
        self.assertEqual(prescriptions.consultation_id, consultation)

    def test_22_multiple_medicines_create_ONE_prescription_with_many_lines(self):
        appointment, encounter = self._in_consultation_visit()
        response, payload = self._prescribe(
            appointment,
            medicines=[
                {"medicine_id": self.amoxil.id, "quantity": 30,
                 "frequency": "twice daily", "duration": "5 days"},
                {"medicine_id": self.cetiriz.id, "quantity": 14,
                 "frequency": "once daily"},
                {"medicine_id": self.dry.id, "quantity": 7},
            ],
        )
        self.assertEqual(response.status_code, 200)
        rows = self._prescriptions(payload)
        self.assertEqual(len(rows), 1, "three medicines must be ONE prescription")
        self.assertEqual(len(rows[0]["medicines"]), 3)

        prescriptions = self._prescriptions_of(self._consultation_of(encounter))
        self.assertEqual(len(prescriptions), 1)
        self.assertEqual(len(prescriptions.line_ids), 3)

    def test_23_the_same_medicine_may_be_prescribed_twice(self):
        """A tapering course and a rescue dose are two legitimate lines."""
        appointment, encounter = self._in_consultation_visit()
        response, _payload = self._prescribe(
            appointment,
            medicines=[
                {"medicine_id": self.amoxil.id, "quantity": 30,
                 "instructions": "morning"},
                {"medicine_id": self.amoxil.id, "quantity": 10,
                 "instructions": "as needed"},
            ],
        )
        self.assertEqual(response.status_code, 200)
        prescriptions = self._prescriptions_of(self._consultation_of(encounter))
        self.assertEqual(len(prescriptions.line_ids), 2)

    def test_24_confirmation_creates_exactly_one_draft_dispense(self):
        appointment, encounter = self._in_consultation_visit()
        self._prescribe(
            appointment,
            medicines=[
                {"medicine_id": self.amoxil.id, "quantity": 30},
                {"medicine_id": self.cetiriz.id, "quantity": 14},
            ],
        )
        prescription = self._prescriptions_of(self._consultation_of(encounter))
        dispense = self._dispense_of(prescription)
        self.assertEqual(len(dispense), 1)
        self.assertEqual(dispense.state, "draft")
        self.assertEqual(len(dispense.line_ids), 2)
        self.assertEqual(dispense.patient_id, appointment.patient_id)
        self.assertEqual(dispense.prescription_id, prescription)

    def test_25_the_dispense_carries_the_prescribed_quantities_and_zero_dispensed(self):
        appointment, encounter = self._in_consultation_visit()
        self._prescribe(
            appointment,
            medicines=[{"medicine_id": self.amoxil.id, "quantity": 30}],
        )
        prescription = self._prescriptions_of(self._consultation_of(encounter))
        line = self._dispense_of(prescription).line_ids
        self.assertEqual(line.prescribed_quantity, 30.0)
        self.assertEqual(
            line.dispensed_quantity, 0.0,
            "the pharmacist records what is actually handed over",
        )

    def test_26_prescribing_creates_no_charge_receipt_stock_or_fiscal_record(self):
        """THE property that separates medication from laboratory and radiology."""
        appointment, _encounter = self._in_consultation_visit()
        before = self._side_effects()
        response, _payload = self._prescribe(
            appointment,
            medicines=[
                {"medicine_id": self.amoxil.id, "quantity": 30},
                {"medicine_id": self.cetiriz.id, "quantity": 14},
            ],
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            self._side_effects(), before,
            "prescribing must create no charge, receipt, bill, stock "
            "consumption, stock movement or fiscal transaction",
        )

    def test_27_medicine_name_is_snapshotted_although_the_onchange_never_runs(self):
        """medicine_name is required=True and the onchange does not fire on a
        programmatic create, so the bridge must fill it."""
        appointment, encounter = self._in_consultation_visit()
        self._prescribe(appointment)
        prescription = self._prescriptions_of(self._consultation_of(encounter))
        line = prescription.line_ids
        self.assertEqual(line.medicine_name, self.amoxil.name)
        self.assertTrue(line.medicine_name)

    def test_28_the_dose_and_route_are_seeded_from_the_catalogue(self):
        appointment, encounter = self._in_consultation_visit()
        self._prescribe(appointment)
        line = self._prescriptions_of(
            self._consultation_of(encounter)
        ).line_ids
        self.assertEqual(line.dosage, self.amoxil.strength)
        # The catalogue says `oral`; the LINE's vocabulary also has `oral`.
        self.assertEqual(line.route, "oral")

    def test_29_the_doctors_own_values_are_never_overwritten_by_seeding(self):
        appointment, encounter = self._in_consultation_visit()
        self._prescribe(
            appointment,
            medicines=[{
                "medicine_id": self.amoxil.id,
                "quantity": 30,
                "dosage": "250mg",
                "route": "injection",
                "frequency": "every 8 hours",
                "duration": "3 days",
                "instructions": "after food",
            }],
        )
        line = self._prescriptions_of(
            self._consultation_of(encounter)
        ).line_ids
        self.assertEqual(line.dosage, "250mg")
        self.assertEqual(line.route, "injection")
        self.assertEqual(line.frequency, "every 8 hours")
        self.assertEqual(line.duration, "3 days")
        self.assertEqual(line.instructions, "after food")

    @mute_logger("odoo.http")
    def test_30_a_zero_or_negative_quantity_is_refused(self):
        appointment, encounter = self._in_consultation_visit()
        for quantity in (0, -5, 0.0):
            response, payload = self._prescribe(
                appointment,
                medicines=[{"medicine_id": self.amoxil.id,
                            "quantity": quantity}],
            )
            self.assertEqual(response.status_code, 400, quantity)
            self.assertFalse(payload["success"])
        self.assertFalse(
            self._prescriptions_of(self._consultation_of(encounter))
        )

    @mute_logger("odoo.http")
    def test_31_a_missing_or_non_numeric_quantity_is_refused(self):
        appointment, _encounter = self._in_consultation_visit()
        for entry in (
            {"medicine_id": self.amoxil.id},
            {"medicine_id": self.amoxil.id, "quantity": "thirty"},
            {"medicine_id": self.amoxil.id, "quantity": True},
        ):
            response, _payload = self._prescribe(appointment, medicines=[entry])
            self.assertEqual(response.status_code, 400, entry)

    @mute_logger("odoo.http")
    def test_32_an_empty_medicine_list_is_refused(self):
        appointment, _encounter = self._in_consultation_visit()
        for medicines in ([], "amoxil", {"medicine_id": 1}):
            response, payload = self._prescribe(
                appointment, medicines=medicines
            )
            self.assertEqual(response.status_code, 400, medicines)
            self.assertEqual(payload["error"]["code"], "invalid_field")
        # `null` and a missing key are built directly: the helper reads
        # medicines=None as "use the default", which is a fixture convenience
        # and must not be mistaken for the endpoint accepting it.
        for body in (
            {"request_token": uuid.uuid4().hex, "medicines": None},
            {"request_token": uuid.uuid4().hex},
        ):
            response, payload = self._post_body(ORDERS % appointment.id, body)
            self.assertEqual(response.status_code, 400, body)
            self.assertEqual(payload["error"]["code"], "invalid_field")

    @mute_logger("odoo.http")
    def test_33_an_unorderable_medicine_is_refused_server_side(self):
        """The picker is an affordance. A stale tab or a scripted call must not
        be able to place an order the pharmacist cannot fill."""
        appointment, encounter = self._in_consultation_visit()
        for medicine in (self.unbilled, self.unstocked, self.archived):
            response, payload = self._prescribe(
                appointment,
                medicines=[{"medicine_id": medicine.id, "quantity": 10}],
            )
            self.assertEqual(response.status_code, 400, medicine.name)
            self.assertFalse(payload["success"])
        self.assertFalse(
            self._prescriptions_of(self._consultation_of(encounter)),
            "a refused submission must leave nothing behind",
        )

    @mute_logger("odoo.http")
    def test_34_one_bad_medicine_refuses_the_whole_submission(self):
        appointment, encounter = self._in_consultation_visit()
        before = self._side_effects()
        response, _payload = self._prescribe(
            appointment,
            medicines=[
                {"medicine_id": self.amoxil.id, "quantity": 30},
                {"medicine_id": self.unbilled.id, "quantity": 10},
            ],
        )
        self.assertEqual(response.status_code, 400)
        self.assertFalse(
            self._prescriptions_of(self._consultation_of(encounter))
        )
        self.assertEqual(self._side_effects(), before)

    @mute_logger("odoo.http")
    def test_35_client_supplied_ownership_is_rejected_by_name(self):
        appointment, _encounter = self._in_consultation_visit()
        for field in ("patient_id", "physician_id", "consultation_id", "state"):
            response, payload = self._prescribe(appointment, **{field: 1})
            self.assertEqual(response.status_code, 400, field)
            self.assertEqual(payload["error"]["code"], "protected_field", field)

    @mute_logger("odoo.http")
    def test_36_unknown_fields_are_rejected(self):
        appointment, _encounter = self._in_consultation_visit()
        response, payload = self._prescribe(appointment, refills=3)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(payload["error"]["code"], "unknown_field")

    @mute_logger("odoo.http")
    def test_37_an_unknown_field_on_a_medicine_row_is_rejected(self):
        appointment, _encounter = self._in_consultation_visit()
        response, payload = self._prescribe(
            appointment,
            medicines=[{"medicine_id": self.amoxil.id, "quantity": 5,
                        "prn": True}],
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(payload["error"]["code"], "unknown_field")

    @mute_logger("odoo.http")
    def test_38_an_invalid_route_is_refused(self):
        appointment, _encounter = self._in_consultation_visit()
        # `iv` belongs to the CATALOGUE's vocabulary, not the line's.
        response, _payload = self._prescribe(
            appointment,
            medicines=[{"medicine_id": self.amoxil.id, "quantity": 5,
                        "route": "iv"}],
        )
        self.assertEqual(response.status_code, 400)

    def test_39_a_diagnosis_and_note_round_trip(self):
        appointment, encounter = self._in_consultation_visit()
        self._add(appointment)
        diagnosis = self._diagnoses_of(self._consultation_of(encounter))[:1]
        response, payload = self._prescribe(
            appointment, diagnosis_id=diagnosis.id, notes="review in 5 days"
        )
        self.assertEqual(response.status_code, 200)
        row = self._prescriptions(payload)[0]
        self.assertEqual(row["diagnosis"]["id"], diagnosis.id)
        self.assertEqual(row["notes"], "review in 5 days")


@tagged("post_install", "-at_install", "doctor_medication")
class TestPrescriptionIdempotency(MedicationCase):
    """One token, one prescription, one dispense."""

    def test_50_the_same_token_returns_the_same_prescription(self):
        appointment, encounter = self._in_consultation_visit()
        token = uuid.uuid4().hex
        first_response, first = self._prescribe(appointment, request_token=token)
        second_response, second = self._prescribe(
            appointment, request_token=token
        )
        self.assertEqual(first_response.status_code, 200)
        self.assertEqual(second_response.status_code, 200)

        consultation = self._consultation_of(encounter)
        prescriptions = self._prescriptions_of(consultation)
        self.assertEqual(len(prescriptions), 1, "a retry must not duplicate")
        self.assertEqual(len(self._prescriptions(first)), 1)
        self.assertEqual(len(self._prescriptions(second)), 1)
        self.assertEqual(
            self._prescriptions(first)[0]["id"],
            self._prescriptions(second)[0]["id"],
        )
        self.assertEqual(
            len(self._dispense_of(prescriptions)), 1,
            "and must not compose a second pharmacy dispense",
        )

    @mute_logger("odoo.http")
    def test_51_a_missing_token_is_refused_and_creates_nothing(self):
        appointment, encounter = self._in_consultation_visit()
        for body in (
            {"medicines": [{"medicine_id": self.amoxil.id, "quantity": 5}]},
            {"medicines": [{"medicine_id": self.amoxil.id, "quantity": 5}],
             "request_token": ""},
            {"medicines": [{"medicine_id": self.amoxil.id, "quantity": 5}],
             "request_token": "   "},
            {"medicines": [{"medicine_id": self.amoxil.id, "quantity": 5}],
             "request_token": None},
        ):
            response, payload = self._post_body(ORDERS % appointment.id, body)
            self.assertEqual(response.status_code, 400)
            self.assertEqual(payload["error"]["code"], "missing_request_token")
        self.assertFalse(
            self._prescriptions_of(self._consultation_of(encounter))
        )

    @mute_logger("odoo.http")
    def test_52_a_wrong_token_type_is_invalid_field_not_missing(self):
        appointment, _encounter = self._in_consultation_visit()
        response, payload = self._post_body(
            ORDERS % appointment.id,
            {"medicines": [{"medicine_id": self.amoxil.id, "quantity": 5}],
             "request_token": 42},
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(payload["error"]["code"], "invalid_field")

    def test_53_the_same_token_on_another_consultation_is_independent(self):
        token = uuid.uuid4().hex
        first, first_enc = self._in_consultation_visit()
        second, second_enc = self._in_consultation_visit()
        self._prescribe(first, request_token=token)
        response, _payload = self._prescribe(second, request_token=token)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            len(self._prescriptions_of(self._consultation_of(first_enc))), 1
        )
        self.assertEqual(
            len(self._prescriptions_of(self._consultation_of(second_enc))), 1
        )


@tagged("post_install", "-at_install", "doctor_medication")
class TestPrescriptionScope(MedicationCase):
    """Whose prescriptions a doctor may see, and when."""

    def test_60_a_doctor_reads_their_own_prescription(self):
        appointment, _encounter = self._in_consultation_visit()
        self._prescribe(appointment)
        _response, payload = self._get(ORDERS % appointment.id)
        self.assertEqual(len(self._prescriptions(payload)), 1)

    @mute_logger("odoo.http")
    def test_61_another_doctor_cannot_read_this_visits_prescriptions(self):
        appointment, _encounter = self._in_consultation_visit()
        self._prescribe(appointment)
        response, _payload = self._get(
            ORDERS % appointment.id,
            user=self.other_doctor_user,
            password=self.other_doctor_password,
        )
        # The visit itself is out of the other doctor's scope, so the refusal
        # arrives before any prescription is read.
        self.assertIn(response.status_code, (403, 404))

    def test_62_prescriptions_stay_readable_after_the_consultation_completes(self):
        appointment, _encounter = self._completable_visit()
        self._prescribe(appointment)
        response, _body = self._complete(appointment)
        self.assertEqual(response.status_code, 200)

        _response, payload = self._get(ORDERS % appointment.id)
        self.assertEqual(len(self._prescriptions(payload)), 1)
        self.assertFalse(
            payload["data"]["can_order"],
            "a completed consultation is read-only for the desk",
        )

    @mute_logger("odoo.http")
    def test_63_a_completed_consultation_refuses_new_prescriptions(self):
        appointment, encounter = self._completable_visit()
        response, _body = self._complete(appointment)
        self.assertEqual(response.status_code, 200)
        response, payload = self._prescribe(appointment)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(payload["error"]["code"], "consultation_completed")
        self.assertFalse(
            self._prescriptions_of(self._consultation_of(encounter))
        )

    @mute_logger("odoo.http")
    def test_64_non_doctor_roles_are_denied_the_orders_endpoint(self):
        appointment, _encounter = self._in_consultation_visit()
        for user, password in (
            (self.cashier, self.cashier_password),
            (self.receptionist, self.receptionist_password),
        ):
            response, _payload = self._get(
                ORDERS % appointment.id, user=user, password=password
            )
            self.assertEqual(response.status_code, 403)



@tagged("post_install", "-at_install", "doctor_medication")
class TestPrescriptionCancellation(MedicationCase):
    """Cancellation delegates entirely to Slice 6A's hardened workflow."""

    def test_70_a_confirmed_prescription_with_a_draft_dispense_cancels(self):
        appointment, encounter = self._in_consultation_visit()
        _response, payload = self._prescribe(appointment)
        prescription_id = self._prescriptions(payload)[0]["id"]
        self.assertTrue(self._prescriptions(payload)[0]["cancellable"])

        response, cancelled = self._post(CANCEL % (appointment.id, prescription_id))
        self.assertEqual(response.status_code, 200)
        row = self._prescriptions(cancelled)[0]
        self.assertEqual(row["status"], "cancelled")

        prescription = self._prescriptions_of(self._consultation_of(encounter))
        self.assertEqual(prescription.state, "cancelled")
        self.assertEqual(
            self._dispense_of(prescription).state, "cancelled",
            "6A propagation must have withdrawn the dispense too",
        )

    def test_71_cancelling_a_ready_dispense_cleans_the_charges(self):
        """Slice 6A's cleanup, reached through the Doctor Desk."""
        appointment, encounter = self._in_consultation_visit()
        _response, payload = self._prescribe(appointment)
        prescription_id = self._prescriptions(payload)[0]["id"]
        prescription = self._prescriptions_of(self._consultation_of(encounter))
        dispense = self._dispense_of(prescription)

        # The pharmacist prices it. THIS is where medication charges appear.
        dispense.line_ids.sudo().write({"dispensed_quantity": 30.0})
        dispense.with_user(self.pharmacist).action_mark_ready()
        charges = dispense.sudo().charge_line_ids
        self.assertTrue(charges)
        self.assertEqual(set(charges.mapped("charge_state")), {"active"})

        response, _cancelled = self._post(
            CANCEL % (appointment.id, prescription_id)
        )
        self.assertEqual(response.status_code, 200)
        self.env.invalidate_all()
        self.assertEqual(set(charges.mapped("charge_state")), {"cancelled"})
        self.assertEqual(dispense.unified_amount_due_for_clearance, 0.0)

    @mute_logger("odoo.http")
    def test_72_cancellation_is_refused_once_medication_was_dispensed(self):
        if not self.inventory_ready:
            self.skipTest("hospital_inventory is not configured")
        appointment, encounter = self._in_consultation_visit()
        _response, payload = self._prescribe(appointment)
        prescription_id = self._prescriptions(payload)[0]["id"]
        prescription = self._prescriptions_of(self._consultation_of(encounter))
        dispense = self._dispense_of(prescription)

        dispense.line_ids.sudo().write({"dispensed_quantity": 30.0})
        dispense.with_user(self.pharmacist).action_mark_ready()
        self._pay(encounter)
        dispense.with_user(self.pharmacist).action_mark_dispensed()
        self.assertEqual(dispense.state, "dispensed")

        response, cancel_payload = self._post(
            CANCEL % (appointment.id, prescription_id)
        )
        self.assertEqual(response.status_code, 422)
        self.assertFalse(cancel_payload["success"])
        self.env.invalidate_all()
        self.assertEqual(prescription.state, "confirmed")
        self.assertEqual(dispense.state, "dispensed")

    def test_73_a_dispensed_prescription_is_not_offered_as_cancellable(self):
        if not self.inventory_ready:
            self.skipTest("hospital_inventory is not configured")
        appointment, encounter = self._in_consultation_visit()
        self._prescribe(appointment)
        prescription = self._prescriptions_of(self._consultation_of(encounter))
        dispense = self._dispense_of(prescription)
        dispense.line_ids.sudo().write({"dispensed_quantity": 30.0})
        dispense.with_user(self.pharmacist).action_mark_ready()
        self._pay(encounter)
        dispense.with_user(self.pharmacist).action_mark_dispensed()

        _response, payload = self._get(ORDERS % appointment.id)
        row = self._prescriptions(payload)[0]
        self.assertFalse(
            row["cancellable"],
            "the desk must not offer a control the model will certainly refuse",
        )

    @mute_logger("odoo.http")
    def test_74_cancellation_is_refused_after_the_consultation_completes(self):
        appointment, encounter = self._completable_visit()
        _response, payload = self._prescribe(appointment)
        prescription_id = self._prescriptions(payload)[0]["id"]

        complete_response, _complete_body = self._complete(appointment)
        self.assertEqual(complete_response.status_code, 200)

        response, _cancel_payload = self._post(
            CANCEL % (appointment.id, prescription_id)
        )
        self.assertEqual(response.status_code, 409)
        prescription = self._prescriptions_of(self._consultation_of(encounter))
        self.assertEqual(prescription.state, "confirmed")

    @mute_logger("odoo.http")
    def test_75_a_prescription_from_another_consultation_is_not_found(self):
        first, first_enc = self._in_consultation_visit()
        second, _second_enc = self._in_consultation_visit()
        self._prescribe(first)
        stray = self._prescriptions_of(self._consultation_of(first_enc))

        response, payload = self._post(CANCEL % (second.id, stray.id))
        self.assertEqual(response.status_code, 404)
        self.assertEqual(payload["error"]["code"], "prescription_not_found")


@tagged("post_install", "-at_install", "doctor_medication")
class TestPrescriptionPayload(MedicationCase):
    """What the desk receives, and everything it must never receive."""

    def test_80_the_prescription_keys_are_exactly_what_was_designed(self):
        appointment, _encounter = self._in_consultation_visit()
        _response, payload = self._prescribe(appointment)
        row = self._prescriptions(payload)[0]
        self.assertEqual(set(row), EXPECTED_PRESCRIPTION_KEYS)
        for medicine in row["medicines"]:
            self.assertEqual(set(medicine), EXPECTED_MEDICINE_LINE_KEYS)

    def test_81_the_payload_carries_no_money_stock_or_pharmacy_internals(self):
        appointment, encounter = self._in_consultation_visit()
        self._prescribe(
            appointment,
            medicines=[
                {"medicine_id": self.amoxil.id, "quantity": 30},
                {"medicine_id": self.cetiriz.id, "quantity": 14},
            ],
        )
        # Take it all the way to a priced, paid, dispensed record so the scan
        # runs against a payload with the most to leak.
        prescription = self._prescriptions_of(self._consultation_of(encounter))
        dispense = self._dispense_of(prescription)
        dispense.line_ids.sudo().write({"dispensed_quantity": 5.0})
        dispense.with_user(self.pharmacist).action_mark_ready()
        self._pay(encounter)

        _response, payload = self._get(ORDERS % appointment.id)
        self._assert_clean(payload["data"], "prescription list")

    def test_82_the_status_is_derived_from_the_dispense_not_the_prescription(self):
        appointment, encounter = self._in_consultation_visit()
        self._prescribe(appointment)
        prescription = self._prescriptions_of(self._consultation_of(encounter))
        dispense = self._dispense_of(prescription)

        _response, payload = self._get(ORDERS % appointment.id)
        self.assertEqual(
            self._prescriptions(payload)[0]["status"], "awaiting_pharmacy"
        )

        dispense.line_ids.sudo().write({"dispensed_quantity": 30.0})
        dispense.with_user(self.pharmacist).action_mark_ready()
        _response, payload = self._get(ORDERS % appointment.id)
        self.assertEqual(
            self._prescriptions(payload)[0]["status"], "ready_at_pharmacy"
        )
        # prescription.state has NOT moved, and must not have been read.
        self.assertEqual(prescription.state, "confirmed")

    def test_83_partial_dispensing_shows_as_partially_dispensed(self):
        if not self.inventory_ready:
            self.skipTest("hospital_inventory is not configured")
        appointment, encounter = self._in_consultation_visit()
        self._prescribe(
            appointment,
            medicines=[{"medicine_id": self.amoxil.id, "quantity": 30}],
        )
        prescription = self._prescriptions_of(self._consultation_of(encounter))
        dispense = self._dispense_of(prescription)
        dispense.line_ids.sudo().write({"dispensed_quantity": 10.0})
        dispense.with_user(self.pharmacist).action_mark_ready()
        self._pay(encounter)
        dispense.with_user(self.pharmacist).action_mark_dispensed()
        self.assertEqual(dispense.state, "partial")

        _response, payload = self._get(ORDERS % appointment.id)
        row = self._prescriptions(payload)[0]
        self.assertEqual(row["status"], "partially_dispensed")
        # And the clinical progress the prescriber needs to manage the course.
        self.assertTrue(row["progress_itemised"])
        line = row["medicines"][0]
        self.assertEqual(line["quantity"], 30.0)
        self.assertEqual(line["dispensed_quantity"], 10.0)
        self.assertEqual(line["remaining_quantity"], 20.0)

    def test_84_progress_is_null_before_the_pharmacy_records_anything(self):
        appointment, _encounter = self._in_consultation_visit()
        _response, payload = self._prescribe(appointment)
        line = self._prescriptions(payload)[0]["medicines"][0]
        self.assertEqual(line["dispensed_quantity"], 0.0)
        self.assertEqual(line["remaining_quantity"], 30.0)


@tagged("post_install", "-at_install", "doctor_medication")
class TestMedicationDownstreamContinuity(MedicationCase):
    """The doctor signs off; pharmacy and the cashier carry on."""

    def test_90_pharmacy_can_complete_the_workflow_after_completion(self):
        if not self.inventory_ready:
            self.skipTest("hospital_inventory is not configured")
        appointment, encounter = self._completable_visit()
        self._prescribe(
            appointment,
            medicines=[{"medicine_id": self.amoxil.id, "quantity": 30}],
        )
        prescription = self._prescriptions_of(self._consultation_of(encounter))
        dispense = self._dispense_of(prescription)

        # The doctor signs the consultation off with the dispense untouched.
        complete_response, _complete_body = self._complete(appointment)
        self.assertEqual(complete_response.status_code, 200)
        appointment.invalidate_recordset()
        self.assertEqual(appointment.state, "done")

        # 41. The pharmacist may still price it.
        dispense.line_ids.sudo().write({"dispensed_quantity": 30.0})
        dispense.with_user(self.pharmacist).action_mark_ready()
        self.assertEqual(dispense.state, "ready")

        # 42. And the cashier's SERVICE PAYMENTS lane discovers it on a DONE
        #     visit, with the generic 'Medication' category, no code added.
        appointment.invalidate_recordset()
        self.assertTrue(
            appointment.sudo()._is_active_service_clearance_pending(),
            "a done visit with an unpaid medication charge must reach the "
            "cashier's service-payment lane",
        )
        categories = {
            entry["key"]
            for entry in self._service_categories(appointment)
        }
        self.assertIn("pharmacy", categories)

        # 43. And the pharmacist may dispense once it is paid.
        self._pay(encounter)
        dispense.with_user(self.pharmacist).action_mark_dispensed()
        self.assertEqual(dispense.state, "dispensed")

        # The doctor can still read the outcome.
        _response, payload = self._get(ORDERS % appointment.id)
        self.assertEqual(
            self._prescriptions(payload)[0]["status"], "dispensed"
        )

    def _service_categories(self, appointment):
        from ..services.cashier_serializers import (
            serialize_cashier_active_service_row,
        )

        row = serialize_cashier_active_service_row(
            appointment.sudo(),
            appointment.sudo()._active_service_blocking_charges(),
        )
        return row["service_categories"]
