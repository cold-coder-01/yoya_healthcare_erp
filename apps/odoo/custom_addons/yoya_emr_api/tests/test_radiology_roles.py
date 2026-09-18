"""Radiology Slice 0A: the two Radiology roles, exercised through the real
workflow and against every other desk.

hospital_radiology's own security tests pin the ACL matrix and the menus. These
answer the two questions that matrix cannot answer on its own:

  1. IS THE ACCESS ENOUGH? Lab Technician used to be the whole imaging
     department. After this slice a Radiology Technician and a Radiologist, with
     no Lab Technician anywhere, must still be able to take a Doctor Desk order
     from scheduling to a released report -- through the EXISTING model methods
     and hospital_billing's overrides, unchanged. The radiologist's request write
     in particular is proven here: release completes the request as the
     releasing user.

  2. IS THE ACCESS ONLY THAT? The Radiology roles must open no other desk --
     Laboratory, Doctor, Cashier, Front Desk, Insurance/Credit -- and reach none
     of the laboratory, billing, pharmacy or consultation models.

No workflow is changed by this slice; nothing here asserts a new behaviour of
any transition, only who may perform the transitions that already exist.
"""
import base64

from lxml import etree

from odoo.exceptions import AccessError
from odoo.tests import Form, tagged

from ..services import reception_scope
from .test_doctor_radiology_api import RadiologyCase

# A real PNG signature: hospital.radiology.image sniffs the bytes.
PNG = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"\x00" * 64)

TECHNICIAN = "hospital_radiology.group_hospital_radiology_technician"
RADIOLOGIST = "hospital_radiology.group_hospital_radiologist"
LAB_TECHNICIAN = "hospital_management.group_hospital_lab_technician"

LAB_SESSION = "/yoya-emr/api/v1/lab/session"
LAB_WORKLIST = "/yoya-emr/api/v1/lab/worklist"
DOCTOR_SESSION = "/yoya-emr/api/v1/doctor/session"
DOCTOR_WORKLIST = "/yoya-emr/api/v1/doctor/worklist"
CASHIER_WORKLIST = "/yoya-emr/api/v1/cashier/worklist"
FRONT_DESK_WORKLIST = "/yoya-emr/api/v1/front-desk/worklist"
INSURANCE_WORKLIST = "/yoya-emr/api/v1/insurance-credit/worklist"

DESK_GATES = (
    "may_lab_desk",
    "may_doctor_desk",
    "may_cashier_desk",
    "may_front_desk",
    "may_insurance_credit",
    "may_reception",
    "may_intake",
    "may_triage",
    "may_record_payment",
    "may_emergency_bypass",
    "may_authorize_payer",
)

# Models outside Radiology that a Radiology role must not reach. Checked only
# when installed, so the list can name the whole neighbourhood.
UNRELATED_MODELS = (
    "hospital.laboratory.test",
    "hospital.laboratory.request",
    "hospital.laboratory.request.line",
    "hospital.laboratory.result",
    "hospital.laboratory.result.line",
    "hospital.charge.line",
    "hospital.charge.receipt",
    "hospital.billing.account",
    "hospital.billing.service",
    "hospital.patient.bill",
    "hospital.payer",
    "hospital.patient.payer",
    "hospital.prescription",
    "hospital.prescription.line",
    "hospital.pharmacy.dispense",
    "hospital.pharmacy.medicine",
    "hospital.consultation",
    "hospital.patient.evaluation",
    "hospital.stock.consumption",
    "hospital.reception.workflow",
)


class RadiologyRolesCase(RadiologyCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.tech_password = "rad0a-tech-pw-1"
        cls.rad_tech = cls._make_user("rad0a_tech", cls.tech_password, [TECHNICIAN])
        cls.radiologist_password = "rad0a-radiologist-pw-1"
        cls.radiologist = cls._make_user(
            "rad0a_radiologist", cls.radiologist_password, [RADIOLOGIST]
        )
        cls.lab_only_password = "rad0a-lab-pw-1"
        cls.lab_only = cls._make_user(
            "rad0a_lab_only", cls.lab_only_password, [LAB_TECHNICIAN]
        )

    def _scheduled_and_started(self):
        """A real Doctor Desk order, paid, and started by the technician."""
        appointment, encounter = self._in_consultation_visit(doctor=self.doctor)
        self._order(appointment)
        self._settle(encounter)
        request = self._requests_of(self._consultation_for(appointment))
        request.with_user(self.rad_tech).action_schedule()
        request.with_user(self.rad_tech).action_mark_in_progress()
        self.assertEqual(request.state, "in_progress")
        return request


@tagged("post_install", "-at_install", "radiology_roles")
class TestRadiologyRolesDriveTheExistingWorkflow(RadiologyRolesCase):
    def test_technician_and_radiologist_complete_a_study_without_lab_technician(self):
        request = self._scheduled_and_started()
        for user in (self.rad_tech, self.radiologist):
            self.assertFalse(user.has_group(LAB_TECHNICIAN))

        Result = self.env["hospital.radiology.result"].with_user(self.radiologist)
        result = Result.create({"request_id": request.id})
        result.write({
            "findings": "No acute intracranial abnormality.",
            "impression": "Normal study.",
        })
        result.action_mark_entered()
        result.action_validate()
        result.action_release()

        self.assertEqual(result.state, "released")
        self.assertEqual(request.state, "completed")
        charges = request.sudo().charge_line_ids
        self.assertTrue(charges)
        self.assertEqual(set(charges.mapped("delivery_state")), {"delivered"})

    def test_technician_attaches_and_removes_imaging_on_an_open_result(self):
        request = self._scheduled_and_started()
        result = self.env["hospital.radiology.result"].with_user(self.rad_tech).create(
            {"request_id": request.id}
        )
        Image = self.env["hospital.radiology.image"].with_user(self.rad_tech)
        image = Image.create({
            "result_id": result.id,
            "name": "Axial",
            "filename": "axial.png",
            "file": PNG,
        })
        self.assertEqual(image.uploaded_by_id, self.rad_tech)
        image.unlink()
        self.assertFalse(image.exists())

    def test_radiologist_attaches_and_removes_imaging_on_an_open_result(self):
        """Radiology Slice 4 role policy: the radiologist may attach imaging
        and remove a wrong upload while the report is open, as the technician
        may. (Slice 0A had withheld it; the image freeze is unchanged.)"""
        request = self._scheduled_and_started()
        result = self.env["hospital.radiology.result"].with_user(self.radiologist).create(
            {"request_id": request.id}
        )
        image = self.env["hospital.radiology.image"].with_user(self.radiologist).create({
            "result_id": result.id,
            "name": "Axial",
            "filename": "axial.png",
            "file": PNG,
        })
        self.assertEqual(image.uploaded_by_id, self.radiologist)
        image.unlink()
        self.assertFalse(image.exists())

    def test_lab_technician_alone_can_no_longer_schedule_or_start(self):
        appointment, encounter = self._in_consultation_visit(doctor=self.doctor)
        self._order(appointment)
        self._settle(encounter)
        request = self._requests_of(self._consultation_for(appointment))
        with self.assertRaises(AccessError):
            request.with_user(self.lab_only).action_schedule()
        self.assertEqual(request.state, "requested")


@tagged("post_install", "-at_install", "radiology_roles")
class TestRadiologyRolesUseTheExistingOdooForms(RadiologyRolesCase):
    """The shipped Radiology screens must still open for the roles that own them.

    THE REGRESSION THIS PINS. hospital_inventory adds a Consumption stat button
    to the request form whose count searches hospital.stock.consumption AS THE
    VIEWING USER. Lab Technician could read that model, so the imaging bench
    never noticed; a Radiology-only user cannot, and the whole request form
    failed with AccessError. The button is now limited to the groups that can
    read consumptions, and this test keeps both halves true: the form opens for
    the Radiology roles, and the button is still there for everyone who had it.
    """

    def _request_with_result_and_image(self):
        request = self._scheduled_and_started()
        result = self.env["hospital.radiology.result"].sudo().create({"request_id": request.id})
        self.env["hospital.radiology.image"].sudo().create({
            "result_id": result.id,
            "name": "Axial",
            "filename": "axial.png",
            "file": PNG,
        })
        return request, result

    def test_request_result_and_exam_forms_open_for_the_radiology_roles(self):
        request, result = self._request_with_result_and_image()
        both = self._make_user("rad0a_forms_both", "rad0a-both-pw-2", [TECHNICIAN, RADIOLOGIST])
        for user in (self.rad_tech, self.radiologist, both):
            Form(request.with_user(user))
            Form(result.with_user(user))
            Form(request.line_ids.exam_id.with_user(user))

    def _form_arch_has_consumption_count(self, user):
        views = self.env["hospital.radiology.request"].with_user(user).get_views(
            [(False, "form")]
        )
        return "inventory_consumption_count" in views["views"]["form"]["arch"]

    def test_consumption_button_groups_are_exactly_the_consumption_readers(self):
        """The button's audience is DERIVED from the ACL, not a second opinion.

        If a group gains or loses read on hospital.stock.consumption, this fails
        until the view follows, so the button can neither reappear for a user
        whose form it would break nor vanish for one who can use it.
        """
        arch = self.env.ref(
            "hospital_inventory.view_hospital_radiology_request_inventory_inherit"
        ).arch_db
        node = etree.fromstring(arch).xpath(
            "//button[@name='action_view_inventory_consumptions']"
        )[0]
        view_groups = {
            self.env.ref(xmlid.strip()) for xmlid in node.get("groups").split(",")
        }
        readers = {
            access.group_id
            for access in self.env["ir.model.access"].sudo().search([
                ("model_id.model", "=", "hospital.stock.consumption"),
                ("perm_read", "=", True),
            ])
        }
        self.assertNotIn(self.env["res.groups"], readers, "a global read ACL exists")
        self.assertEqual(view_groups, readers)

    def test_consumption_button_is_hidden_only_from_non_readers(self):
        for user in (self.rad_tech, self.radiologist):
            self.assertFalse(self._form_arch_has_consumption_count(user), user.login)
        # Every role that can open a radiology request AND read consumptions
        # still sees the button, exactly as before.
        for xmlid in (
            "hospital_management.group_hospital_doctor",
            "hospital_management.group_hospital_nurse",
            "hospital_management.group_hospital_manager",
            "hospital_management.group_hospital_data_protection_officer",
            "hospital_management.group_hospital_system_administrator",
        ):
            reader = self._make_user("rad0a_reader", "rad0a-reader-pw", [xmlid])
            self.assertTrue(self._form_arch_has_consumption_count(reader), xmlid)


@tagged("post_install", "-at_install", "radiology_roles")
class TestRadiologyRolesOpenNoOtherDesk(RadiologyRolesCase):
    def test_no_desk_gate_admits_a_radiology_role(self):
        both = self._make_user("rad0a_both", "rad0a-both-pw-1", [TECHNICIAN, RADIOLOGIST])
        for user in (self.rad_tech, self.radiologist, both):
            env = self.env(user=user)
            for gate in DESK_GATES:
                self.assertFalse(getattr(reception_scope, gate)(env), "%s %s" % (user.login, gate))
            roles = reception_scope.role_flags(env)
            self.assertFalse(roles["lab_technician"], user.login)
            self.assertFalse(roles["doctor"], user.login)

    def test_every_desk_answers_403_to_a_radiology_role(self):
        for user, password in (
            (self.rad_tech, self.tech_password),
            (self.radiologist, self.radiologist_password),
        ):
            for url in (
                LAB_SESSION, LAB_WORKLIST, DOCTOR_WORKLIST,
                CASHIER_WORKLIST, FRONT_DESK_WORKLIST, INSURANCE_WORKLIST,
            ):
                response, _payload = self._get(url, user=user, password=password)
                self.assertEqual(response.status_code, 403, "%s %s" % (user.login, url))

    def test_doctor_session_tells_a_radiology_role_it_has_no_doctor_desk(self):
        """/doctor/session is deliberately UNGATED (see its docstring): the shell
        calls it to explain a refusal, and it returns only the caller's own
        identity and capability flags. For a Radiology role it must say no."""
        for user, password in (
            (self.rad_tech, self.tech_password),
            (self.radiologist, self.radiologist_password),
        ):
            response, payload = self._get(DOCTOR_SESSION, user=user, password=password)
            self.assertEqual(response.status_code, 200, user.login)
            self.assertFalse(payload["data"]["capabilities"]["doctor_desk"], user.login)
            self.assertFalse(payload["data"]["capabilities"]["start_consultation_role"], user.login)

    def test_radiology_roles_reach_no_unrelated_hospital_model(self):
        for user in (self.rad_tech, self.radiologist):
            access = self.env["ir.model.access"].with_user(user)
            for model in UNRELATED_MODELS:
                if model not in self.env:
                    continue
                for mode in ("read", "write", "create", "unlink"):
                    self.assertFalse(
                        access.check(model, mode, False),
                        "%s may %s %s" % (user.login, mode, model),
                    )

    def test_lab_technician_keeps_the_laboratory_desk(self):
        """Taking Radiology away must not take the laboratory with it."""
        env = self.env(user=self.lab_only)
        self.assertTrue(reception_scope.may_lab_desk(env))
        response, _payload = self._get(
            LAB_SESSION, user=self.lab_only, password=self.lab_only_password
        )
        self.assertEqual(response.status_code, 200)
