"""Runtime provenance, capability leakage and the direct-write matrix.

WHY THIS FILE EXISTS. Slice 0 human UAT ran

    adm.write({"state": "admitted"})

in an Odoo shell on a clone upgraded with the repo-first addons path, and the
write succeeded. The guard was not defective. The shell was not running it.

`odoo-bin shell --addons-path=...` puts the subcommand FIRST, and
odoo/cli/command.py main() runs subcommand discovery -- initialize_sys_path()
-- before the subcommand parses its own options. At that moment the addons
path comes from the odoo.conf beside odoo-bin, which names C:\\custom_addons
and not this repository. initialize_sys_path() only appends, so when the shell
later reads --addons-path the repository lands LAST, and the pre-Slice-0 copy
of hospital_admission in C:\\custom_addons won. The upgrade and every test run
used `odoo-bin --addons-path=... -u ...` (no subcommand), skipped discovery,
and loaded the repository. Same flag, opposite result.

So this suite was green while UAT executed different code. The tests below
cannot stop a human from starting the wrong copy -- nothing in this module
runs when it is not the copy loaded -- but they make the copy under test
explicit in every run's log, and they pin the invariants the UAT exercised
exactly as the UAT exercised them: ORM, sudo, forged context, multi-record,
and after a workflow has run or failed.
"""
import inspect
import logging
import os

from odoo.modules.module import get_manifest
from odoo.tests import tagged

from odoo.addons.hospital_admission.models import admission_authority as authority
from odoo.addons.hospital_admission.models.admission_authority import (
    AdmissionWorkflowError,
)

from .common import AdmissionCase

_logger = logging.getLogger(__name__)

MODULE_DIR = os.path.normcase(
    os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
)

# Every addon that extends hospital.admission or hospital.bed today, inspected
# by hand for this slice: none of them defines write() or create(). A new
# extension that DOES must be reviewed for a path around the guard before it
# joins this list -- see test_no_unreviewed_extension_overrides_write_or_create.
REVIEWED_WRITE_OVERRIDES = {
    "hospital.admission": {"odoo.addons.hospital_admission.models.admission"},
    "hospital.bed": {"odoo.addons.hospital_admission.models.ward"},
}


@tagged("post_install", "-at_install", "admission_runtime")
class TestAdmissionRuntimeProvenance(AdmissionCase):
    # ==================================================================
    # PART 7: which copy is actually running
    # ==================================================================
    def test_the_code_under_test_is_this_copy(self):
        """Log and assert WHICH hospital_admission is loaded.

        Printed on every run so a test log can be read against a UAT
        transcript: if the two name different directories, they tested
        different code.
        """
        write_file = os.path.normcase(
            os.path.abspath(inspect.getsourcefile(type(self.env["hospital.admission"]).write))
        )
        manifest = get_manifest("hospital_admission")
        installed = (
            self.env["ir.module.module"]
            .sudo()
            .search([("name", "=", "hospital_admission")], limit=1)
            .latest_version
        )
        _logger.info(
            "ADMISSIONS RUNTIME: module dir=%s | hospital.admission.write from %s | "
            "manifest version=%s | installed version=%s",
            MODULE_DIR, write_file, manifest.get("version"), installed,
        )
        self.assertTrue(
            write_file.startswith(MODULE_DIR),
            "hospital.admission.write resolves outside the module under test: "
            "%s. Another copy of hospital_admission shadows this one." % write_file,
        )
        self.assertEqual(
            manifest.get("version"),
            installed,
            "The loaded hospital_admission manifest does not match the version "
            "installed in this database. Code and schema are out of step.",
        )

    def test_the_slice0_schema_is_present(self):
        admission = self.env["hospital.admission"]
        for name in ("encounter_id", "company_id"):
            self.assertIn(name, admission._fields)
        for model in ("hospital.ward", "hospital.room", "hospital.bed"):
            self.assertIn("company_id", self.env[model]._fields)

    def test_the_guard_is_on_the_resolved_write(self):
        """The write() Python actually dispatches to is the guarded one."""
        source = inspect.getsource(type(self.env["hospital.admission"]).write)
        self.assertIn("_assert_authoritative_write", source)
        bed_source = inspect.getsource(type(self.env["hospital.bed"]).write)
        self.assertIn("_assert_authoritative_occupancy_write", bed_source)

    def test_no_unreviewed_extension_overrides_write_or_create(self):
        """TRIPWIRE. Every class in the MRO that defines write() or create()
        sits between the caller and the guard. hospital_insurance,
        hospital_nursing, hospital_procedure, hospital_inventory and
        hospital_operation_theatre all extend hospital.admission and none of
        them does; a future one that does must be read for a path around the
        guard -- a direct models.Model.write(), a super() to the wrong class --
        before it is added to REVIEWED_WRITE_OVERRIDES.
        """
        for model_name, reviewed in REVIEWED_WRITE_OVERRIDES.items():
            for klass in type(self.env[model_name]).mro():
                module = getattr(klass, "__module__", "")
                if not module.startswith("odoo.addons."):
                    continue
                if not module.startswith(("odoo.addons.hospital", "odoo.addons.yoya")):
                    continue
                for method in ("write", "create"):
                    if method in klass.__dict__:
                        self.assertIn(
                            module,
                            reviewed,
                            "%s.%s is overridden by %s, which has not been reviewed "
                            "against the Slice 0 authority guard."
                            % (model_name, method, module),
                        )


@tagged("post_install", "-at_install", "admission_runtime")
class TestCapabilityLeakage(AdmissionCase):
    # ==================================================================
    # PART 5: the ContextVar never outlives its window
    # ==================================================================
    def test_capabilities_default_to_closed(self):
        """Also catches a previous test that leaked one open globally."""
        self.assertFalse(authority.has_admission_workflow_capability())
        self.assertFalse(authority.has_bed_occupancy_capability())
        self.assertFalse(authority.has_admission_location_capability())
        self.assertFalse(authority.has_admission_billing_capability())

    def test_an_authorized_write_does_not_leave_the_door_open(self):
        admission = self._admitted()
        with authority.admission_workflow_capability():
            admission.write({"notes": "inside the window"})
        self.assertFalse(authority.has_admission_workflow_capability())
        with self.assertRaises(AdmissionWorkflowError):
            admission.write({"state": "discharged"})

    def test_an_exception_inside_the_window_does_not_leave_it_open(self):
        admission = self._admitted()
        with self.assertRaises(RuntimeError):
            with authority.admission_workflow_capability():
                raise RuntimeError("boom")
        self.assertFalse(authority.has_admission_workflow_capability())
        with self.assertRaises(AdmissionWorkflowError):
            admission.write({"state": "discharged"})

    def test_a_bed_exception_inside_the_window_does_not_leave_it_open(self):
        with self.assertRaises(RuntimeError):
            with authority.bed_occupancy_capability():
                raise RuntimeError("boom")
        self.assertFalse(authority.has_bed_occupancy_capability())
        with self.assertRaises(AdmissionWorkflowError):
            self.bed_a.sudo().write({"state": "occupied"})

    def test_nesting_restores_the_outer_window_not_false(self):
        """Token reset, not a blind set(False): an inner window closing must
        leave an enclosing one open, and closing the outer one closes it."""
        with authority.admission_workflow_capability():
            with authority.admission_workflow_capability():
                self.assertTrue(authority.has_admission_workflow_capability())
            self.assertTrue(authority.has_admission_workflow_capability())
        self.assertFalse(authority.has_admission_workflow_capability())

    def test_direct_write_after_a_completed_workflow_is_refused(self):
        admission = self._admitted()
        admission.action_discharge()
        with self.assertRaises(AdmissionWorkflowError):
            admission.write({"state": "admitted"})
        admission.invalidate_recordset()
        self.assertEqual(admission.state, "discharged")

    def test_direct_write_after_a_failed_workflow_is_refused(self):
        """A refusal is the path that would actually risk leaking a window."""
        blocker = self._admitted(bed=self.bed_a)
        contender = self._draft(bed=self.bed_a)
        with self.assertRaises(AdmissionWorkflowError):
            contender.action_confirm_admission()
        self.assertFalse(authority.has_admission_workflow_capability())
        self.assertFalse(authority.has_bed_occupancy_capability())
        with self.assertRaises(AdmissionWorkflowError):
            contender.write({"state": "admitted"})
        with self.assertRaises(AdmissionWorkflowError):
            blocker.write({"state": "discharged"})


@tagged("post_install", "-at_install", "admission_runtime")
class TestDirectWriteMatrix(AdmissionCase):
    # ==================================================================
    # PART 8: the UAT's own writes, as the UAT made them
    # ==================================================================
    def _legacy_discharged(self):
        """An ADM00001 twin: discharged, no encounter."""
        patient = self._patient("Legacy Twin")
        self._raw(
            """
            INSERT INTO hospital_admission
                (name, patient_id, state, ward_id, room_id, bed_id, company_id,
                 admission_date, discharge_date, active,
                 create_uid, write_uid, create_date, write_date)
            VALUES (%s, %s, 'discharged', %s, %s, %s, %s,
                    now() - interval '2 days', now() - interval '1 day', true,
                    %s, %s, now(), now())
            """,
            (
                "ADMTWIN-%s" % patient.id, patient.id, self.ward.id, self.room.id,
                self.bed_a.id, self.company.id, self.env.uid, self.env.uid,
            ),
        )
        return self.env["hospital.admission"].sudo().search(
            [("name", "=", "ADMTWIN-%s" % patient.id)]
        )

    def test_the_exact_uat_write_discharged_to_admitted_is_refused(self):
        """1. The write that went through in UAT."""
        legacy = self._legacy_discharged()
        with self.assertRaises(AdmissionWorkflowError) as caught:
            legacy.write({"state": "admitted"})
        self.assertEqual(caught.exception.code, "admission_state_write_refused")
        legacy.invalidate_recordset()
        self.assertEqual(legacy.state, "discharged")

    def test_every_target_state_is_refused_from_every_source(self):
        """2, 3. draft -> admitted, admitted -> discharged, and the rest."""
        sources = {
            "draft": self._draft(bed=self.bed_a),
            "admitted": self._admitted(bed=self.bed_b),
            "discharged": self._legacy_discharged(),
        }
        for source_state, record in sources.items():
            for target in ("draft", "admitted", "transferred", "discharged", "cancelled"):
                if target == source_state:
                    continue
                with self.subTest(source=source_state, target=target):
                    with self.assertRaises(AdmissionWorkflowError):
                        record.write({"state": target})
                    record.invalidate_recordset()
                    self.assertEqual(record.state, source_state)

    def test_sudo_and_forged_context_are_refused(self):
        """4, 5."""
        admission = self._admitted()
        with self.assertRaises(AdmissionWorkflowError):
            admission.sudo().write({"state": "discharged"})
        with self.assertRaises(AdmissionWorkflowError):
            admission.sudo().with_context(
                admission_workflow_capability=True,
                has_admission_workflow_capability=True,
                allow_state_write=True,
                install_mode=True,
                tracking_disable=True,
            ).write({"state": "discharged"})

    def test_a_multi_record_state_write_is_refused_whole(self):
        """6. One refusal refuses the batch; nothing half-applies."""
        first = self._admitted(bed=self.bed_a)
        second = self._admitted(bed=self.bed_b)
        legacy = self._legacy_discharged()
        with self.assertRaises(AdmissionWorkflowError):
            (first | second | legacy).write({"state": "cancelled"})
        self.env.invalidate_all()
        self.assertEqual(first.state, "admitted")
        self.assertEqual(second.state, "admitted")
        self.assertEqual(legacy.state, "discharged")

    def test_location_on_an_active_admission_is_refused(self):
        """9."""
        admission = self._admitted(bed=self.bed_a)
        with self.assertRaises(AdmissionWorkflowError):
            admission.sudo().write({"bed_id": self.bed_b.id})

    def test_bed_occupancy_is_refused_including_the_uat_bed_write(self):
        """10. bed.write({'state': 'occupied'}) is the UAT's second write."""
        with self.assertRaises(AdmissionWorkflowError):
            self.bed_a.sudo().write({"state": "occupied"})
        admission = self._admitted(bed=self.bed_b)
        with self.assertRaises(AdmissionWorkflowError):
            self.bed_b.sudo().write({"current_admission_id": False})
        with self.assertRaises(AdmissionWorkflowError):
            self.bed_a.sudo().write({"current_admission_id": admission.id})

    def test_the_insurance_extension_path_does_not_carry_a_state_change(self):
        """11. hospital_insurance extends hospital.admission (outside this repo)
        and defines no write(). Its fields riding along with a state change must
        not smuggle the state through."""
        if "coverage_id" not in self.env["hospital.admission"]._fields:
            self.skipTest("hospital_insurance is not installed")
        admission = self._admitted()
        with self.assertRaises(AdmissionWorkflowError):
            admission.sudo().write(
                {"coverage_id": False, "guarantee_id": False, "state": "discharged"}
            )
        # ...while the extension's own fields, alone, remain writable.
        admission.sudo().write({"coverage_id": False, "guarantee_id": False})
        self.assertEqual(admission.state, "admitted")

    def test_the_workflow_still_moves_every_transition(self):
        """12. Refusing ordinary writes must not break the real path."""
        admission = self._admitted(bed=self.bed_a)
        self.assertEqual(admission.state, "admitted")
        admission.action_transfer(self.ward, self.room, self.bed_b, reason="runtime")
        self.assertEqual(admission.state, "transferred")
        admission.action_discharge()
        self.assertEqual(admission.state, "discharged")

        cancelled = self._admitted(bed=self.bed_a)
        cancelled.action_cancel()
        self.assertEqual(cancelled.state, "cancelled")
        cancelled.action_reset_to_draft()
        self.assertEqual(cancelled.state, "draft")


@tagged("post_install", "-at_install", "admission_runtime")
class TestAuditOnlyRealTransitions(AdmissionCase):
    def _state_logs(self, admission):
        return self.env["hospital.audit.log"].sudo().search_count(
            [
                ("model_name", "=", "hospital.admission"),
                ("record_id", "=", admission.id),
                ("action_type", "=", "state_change"),
            ]
        )

    def test_an_echoed_state_is_not_audited_as_a_transition(self):
        admission = self._admitted()
        before = self._state_logs(admission)
        admission.write({"state": "admitted", "notes": "form save"})
        self.assertEqual(self._state_logs(admission), before)

    def test_a_real_transition_is_still_audited(self):
        admission = self._admitted()
        before = self._state_logs(admission)
        admission.action_discharge()
        self.assertEqual(self._state_logs(admission), before + 1)
