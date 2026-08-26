"""Slice 6A: a doctor prescribes; the pharmacy dispenses. Enforced, not implied.

WHAT THIS PROTECTS. Confirming a prescription composes the linked dispense
automatically, and that composition happens inside the DOCTOR's transaction. So
the doctor has to be able to cause a dispense to exist while being unable to
operate one. Before this slice the ACL solved the first half by granting write
and create on the dispense and its lines, which handed the doctor the second
half as well: over plain RPC they could set dispensed_quantity, raise and
activate the medication charges through Mark Ready, and move stock through
Validate Dispense.

EVERY TEST HERE GOES THROUGH A REAL res.users WITH REAL GROUPS, and calls the
model directly rather than a controller. That is the point: the boundary has to
hold for anyone who can reach the ORM, not merely for someone clicking a screen
that does not show the button. Nothing below passes a context flag, because a
context flag is client input and could never be the boundary.
"""
import uuid

from odoo import fields
from odoo.exceptions import AccessError
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install", "pharmacy_dispense_authorization")
class TestPharmacyDispenseAuthorization(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.doctor_user = cls._make_user(
            "auth_doctor", "hospital_management.group_hospital_doctor"
        )
        cls.pharmacist_user = cls._make_user(
            "auth_pharmacist", "hospital_management.group_hospital_pharmacist"
        )
        cls.manager_user = cls._make_user(
            "auth_manager", "hospital_management.group_hospital_manager"
        )
        cls.admin_user = cls._make_user(
            "auth_sysadmin", "hospital_management.group_hospital_system_administrator"
        )
        # The doctor RECORD is linked to the doctor USER, so the Slice 6A record
        # rules let that user reach their own prescription. Without the link the
        # rule would hide it and these tests would be measuring the wrong thing.
        cls.doctor = cls.env["hospital.doctor"].sudo().create({
            "name": "Authorization Test Doctor",
            "user_id": cls.doctor_user.id,
        })
        cls.medicine = cls.env["hospital.pharmacy.medicine"].sudo().create({
            "name": "Authorization Test Medicine",
            "code": "ATM-%s" % uuid.uuid4().hex[:6],
            "dosage_form": "capsule",
            "route": "oral",
        })
        # hospital_pharmacy does not depend on hospital_billing, but when it IS
        # installed -- as it is here -- Mark Ready refuses any medicine with no
        # billing service before it ever reaches the authorization guard. Mapping
        # one keeps these tests measuring authorization rather than
        # configuration.
        if "hospital.billing.service" in cls.env:
            service = cls.env["hospital.billing.service"].sudo().create({
                "name": "Authorization Test Medicine",
                "code": "T-AUTH-MED-%s" % uuid.uuid4().hex[:6],
                "service_type": "pharmacy",
                "default_price": 50.0,
                "company_id": cls.company.id,
                "currency_id": cls.company.currency_id.id,
                "tax_treatment": "exempt",
            })
            cls.medicine.sudo().write({"billing_service_id": service.id})
            if "sale_price" in cls.medicine._fields:
                cls.medicine.sudo().write({"sale_price": 50.0})

    @classmethod
    def _make_user(cls, login, *group_xmlids):
        groups = [cls.env.ref("base.group_user").id] + [
            cls.env.ref(x).id for x in group_xmlids
        ]
        suffix = uuid.uuid4().hex[:6]
        return cls.env["res.users"].sudo().create({
            "name": login,
            "login": "%s_%s@example.test" % (login, suffix),
            "company_id": cls.company.id,
            "company_ids": [(6, 0, cls.company.ids)],
            "groups_id": [(6, 0, groups)],
        })

    def _prescription_vals(self, patient, appointment, qty=5.0):
        return {
            "patient_id": patient.id,
            "physician_id": self.doctor.id,
            "appointment_id": appointment.id,
            "line_ids": [(0, 0, {
                "medicine_id": self.medicine.id,
                "medicine_name": self.medicine.name,
                "quantity": qty,
                "dosage": "500mg",
                "route": "oral",
            })],
        }

    def _visit(self):
        suffix = uuid.uuid4().hex[:8]
        partner = self.env["res.partner"].sudo().create(
            {"name": "Authorization Partner %s" % suffix}
        )
        patient = self.env["hospital.patient"].sudo().create({
            "name": "Authorization Patient %s" % suffix,
            "accounting_partner_id": partner.id,
        })
        appointment = self.env["hospital.appointment"].sudo().create({
            "patient_id": patient.id,
            "doctor_id": self.doctor.id,
            "appointment_date": fields.Datetime.now(),
            "state": "confirmed",
        })
        self.env["hospital.encounter"].sudo().create({
            "patient_id": patient.id,
            "appointment_id": appointment.id,
            "encounter_type": "outpatient",
            "primary_doctor_id": self.doctor.id,
            "company_id": self.company.id,
        })
        return patient, appointment

    def _doctor_confirmed_dispense(self):
        """A dispense that exists ONLY because the doctor confirmed a
        prescription, created under the doctor's own uid."""
        patient, appointment = self._visit()
        prescription = self.env["hospital.prescription"].with_user(
            self.doctor_user
        ).create(self._prescription_vals(patient, appointment))
        prescription.with_user(self.doctor_user).action_confirm()
        dispense = self.env["hospital.pharmacy.dispense"].sudo().search(
            [("prescription_id", "=", prescription.id)]
        )
        return prescription, dispense

    # ------------------------------------------------------------------
    # 14 -- the one dispense a doctor is entitled to cause still appears
    # ------------------------------------------------------------------
    def test_doctor_confirmation_still_creates_the_dispense_and_lines(self):
        prescription, dispense = self._doctor_confirmed_dispense()

        self.assertEqual(prescription.state, "confirmed")
        self.assertEqual(len(dispense), 1)
        self.assertEqual(dispense.state, "draft")
        self.assertEqual(len(dispense.line_ids), 1)
        self.assertEqual(dispense.line_ids.medicine_id, self.medicine)
        self.assertEqual(dispense.line_ids.prescribed_quantity, 5.0)
        self.assertEqual(dispense.line_ids.dispensed_quantity, 0.0)
        # sudo() bypasses access rights without changing the user, so the
        # authorship of the record still names the doctor who confirmed it.
        self.assertEqual(dispense.create_uid, self.doctor_user)

    def test_doctor_can_still_read_the_dispense_they_caused(self):
        _prescription, dispense = self._doctor_confirmed_dispense()
        as_doctor = dispense.with_user(self.doctor_user)
        self.assertEqual(as_doctor.read(["state"])[0]["state"], "draft")
        self.assertEqual(
            as_doctor.line_ids.with_user(self.doctor_user).read(
                ["prescribed_quantity"]
            )[0]["prescribed_quantity"],
            5.0,
        )

    # ------------------------------------------------------------------
    # 15-19 -- and nothing beyond that
    # ------------------------------------------------------------------
    def test_doctor_cannot_write_dispense_operational_field(self):
        _prescription, dispense = self._doctor_confirmed_dispense()
        with self.assertRaises(AccessError):
            dispense.with_user(self.doctor_user).write({"priority": "emergency"})

    def test_doctor_cannot_write_dispensed_quantity(self):
        _prescription, dispense = self._doctor_confirmed_dispense()
        with self.assertRaises(AccessError):
            dispense.line_ids.with_user(self.doctor_user).write(
                {"dispensed_quantity": 5.0}
            )
        dispense.line_ids.invalidate_recordset()
        self.assertEqual(dispense.line_ids.dispensed_quantity, 0.0)

    def test_doctor_cannot_create_a_dispense_directly(self):
        """The create ACL is gone, so there is no stray-dispense route either."""
        patient, appointment = self._visit()
        with self.assertRaises(AccessError):
            self.env["hospital.pharmacy.dispense"].with_user(self.doctor_user).create({
                "patient_id": patient.id,
                "physician_id": self.doctor.id,
                "appointment_id": appointment.id,
            })

    def test_doctor_cannot_inject_a_line_into_an_existing_dispense(self):
        """Creating a child does not cascade an ACL check to its parent in Odoo,
        so the line model has to refuse this in its own right."""
        _prescription, dispense = self._doctor_confirmed_dispense()
        with self.assertRaises(AccessError):
            self.env["hospital.pharmacy.dispense.line"].with_user(
                self.doctor_user
            ).create({
                "dispense_id": dispense.id,
                "medicine_id": self.medicine.id,
                "prescribed_quantity": 999.0,
                "dispensed_quantity": 999.0,
            })

    def test_doctor_cannot_mark_ready(self):
        _prescription, dispense = self._doctor_confirmed_dispense()
        with self.assertRaises(AccessError):
            dispense.with_user(self.doctor_user).action_mark_ready()
        dispense.invalidate_recordset()
        self.assertEqual(dispense.state, "draft")
        self.assertFalse(
            dispense.charge_line_ids,
            "a refused Mark Ready must not leave a medication charge behind",
        )

    def test_doctor_cannot_mark_dispensed(self):
        _prescription, dispense = self._doctor_confirmed_dispense()
        dispense.line_ids.sudo().write({"dispensed_quantity": 5.0})
        dispense.sudo().action_mark_ready()
        with self.assertRaises(AccessError):
            dispense.with_user(self.doctor_user).action_mark_dispensed()
        dispense.invalidate_recordset()
        self.assertEqual(dispense.state, "ready")

    def test_doctor_cannot_cancel_the_dispense_directly(self):
        """Cancelling THROUGH the prescription is allowed and covered by
        test_pharmacy_cancellation_integrity; reaching past it is not."""
        _prescription, dispense = self._doctor_confirmed_dispense()
        with self.assertRaises(AccessError):
            dispense.with_user(self.doctor_user).action_cancel()
        dispense.invalidate_recordset()
        self.assertEqual(dispense.state, "draft")

    def test_doctor_cannot_mark_partial_or_reset_to_draft(self):
        _prescription, dispense = self._doctor_confirmed_dispense()
        with self.assertRaises(AccessError):
            dispense.with_user(self.doctor_user).action_mark_partial()
        dispense.sudo().action_cancel()
        with self.assertRaises(AccessError):
            dispense.with_user(self.doctor_user).action_reset_to_draft()

    def test_a_context_flag_does_not_open_the_guard(self):
        """Context is client input. It must not be an authorization channel."""
        _prescription, dispense = self._doctor_confirmed_dispense()
        forged = dispense.with_user(self.doctor_user).with_context(
            pharmacy_dispense_from_prescription=True,
            skip_dispense_write_audit=True,
            su=True,
        )
        with self.assertRaises(AccessError):
            forged.action_mark_ready()

    # ------------------------------------------------------------------
    # 20-21 -- the roles that SHOULD be able to work keep working
    # ------------------------------------------------------------------
    def test_pharmacist_operates_the_full_workflow(self):
        _prescription, dispense = self._doctor_confirmed_dispense()
        as_pharmacist = dispense.with_user(self.pharmacist_user)
        as_pharmacist.line_ids.write({"dispensed_quantity": 5.0})
        self.assertEqual(dispense.line_ids.dispensed_quantity, 5.0)
        as_pharmacist.action_cancel()
        dispense.invalidate_recordset()
        self.assertEqual(dispense.state, "cancelled")

    def test_manager_and_sysadmin_retain_their_override(self):
        for user in (self.manager_user, self.admin_user):
            _prescription, dispense = self._doctor_confirmed_dispense()
            as_user = dispense.with_user(user)
            as_user.line_ids.write({"dispensed_quantity": 5.0})
            as_user.action_mark_partial()  # a no-op from draft, but authorized
            as_user.action_cancel()
            dispense.invalidate_recordset()
            self.assertEqual(
                dispense.state,
                "cancelled",
                "%s should retain the operational override" % user.name,
            )

    def test_reopen_is_refused_while_the_prescription_stays_cancelled(self):
        """The clinical half of the reopen guard, checked as a pharmacist."""
        prescription, dispense = self._doctor_confirmed_dispense()
        prescription.sudo().action_cancel()
        dispense.invalidate_recordset()
        self.assertEqual(dispense.state, "cancelled")
        from odoo.exceptions import UserError

        with self.assertRaises(UserError):
            dispense.with_user(self.pharmacist_user).action_reset_to_draft()

    def test_reopen_is_allowed_after_a_pharmacy_side_cancellation(self):
        """The guard is narrow: an ordinary pharmacy cancellation still reopens,
        so this slice did not quietly remove a working control."""
        prescription, dispense = self._doctor_confirmed_dispense()
        dispense.with_user(self.pharmacist_user).action_cancel()
        dispense.invalidate_recordset()
        self.assertEqual(dispense.state, "cancelled")
        self.assertEqual(prescription.state, "confirmed")

        dispense.with_user(self.pharmacist_user).action_reset_to_draft()

        dispense.invalidate_recordset()
        self.assertEqual(dispense.state, "draft")
