"""Admission reference integrity.

  1. the first reference comes from the sequence, in its shape (ADM + 5 digits)
  2. every later one differs, strictly increasing
  3. a caller can neither choose nor change a reference, even under sudo()
  4. a missing sequence fails closed instead of writing "New"
  5. a rolled-back draw is never reissued
  6. two real, concurrent connections never draw the same number
  7. THE ROOT CAUSE: reloading the data file (what `-u` does) no longer
     rewinds the counter
  8. the upgrade migration moves the counter past every issued reference,
     never backwards, renames nothing, and survives historical duplicates
  9. legacy duplicate references stay readable and editable
"""
import importlib.util
import os
import re

from odoo.modules.module import get_module_path
from odoo.sql_db import db_connect
from odoo.tests import tagged
from odoo.tools import convert_file

from ..models.admission_authority import ADMISSION_SEQUENCE_CODE, AdmissionWorkflowError
from .common import AdmissionCase

REFERENCE = re.compile(r"^ADM(\d{5,})$")


def _number(reference):
    match = REFERENCE.match(reference or "")
    assert match, reference
    return int(match.group(1))


def _load_migration():
    path = os.path.join(
        get_module_path("hospital_admission"), "migrations", "18.0.1.6.0", "post-migrate.py"
    )
    spec = importlib.util.spec_from_file_location("hospital_admission_ref_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.migrate


@tagged("post_install", "-at_install", "admission_reference")
class TestAdmissionReference(AdmissionCase):
    @property
    def sequence(self):
        return self.env.ref("hospital_admission.sequence_hospital_admission")

    def test_1_2_references_come_from_the_sequence_and_increase(self):
        first = self._draft(bed=False)
        second = self._draft(bed=False)
        third = self._admitted(bed=self.bed_b)
        names = [first.name, second.name, third.name]
        self.assertEqual(len(set(names)), 3, names)
        numbers = [_number(name) for name in names]
        self.assertEqual(numbers, sorted(numbers))
        for name in names:
            self.assertRegex(name, REFERENCE)
            self.assertNotEqual(name, "New")
        self.assertEqual(self.sequence.prefix, "ADM")
        self.assertEqual(self.sequence.padding, 5)
        self.assertEqual(self.sequence.code, ADMISSION_SEQUENCE_CODE)

    def test_3_a_caller_cannot_choose_the_reference(self):
        patient = self._patient()
        self._encounter(patient)
        with self.assertRaises(AdmissionWorkflowError) as caught:
            self.env["hospital.admission"].sudo().create({
                "patient_id": patient.id, "physician_id": self.doctor.id,
                "company_id": self.company.id, "name": "ADM99999",
            })
        self.assertEqual(caught.exception.code, "admission_reference_write_refused")
        # The form's placeholder is not a choice; it is replaced.
        placeholder = self._draft(bed=False, name="New")
        self.assertRegex(placeholder.name, REFERENCE)

    def test_3_nor_change_it_afterwards_even_under_sudo(self):
        admission = self._admitted(bed=self.bed_a)
        original = admission.name
        for record in (admission, admission.with_user(self.manager), admission.with_user(self.admin)):
            with self.assertRaises(AdmissionWorkflowError) as caught:
                record.sudo().write({"name": "ADM00001"})
            self.assertEqual(caught.exception.code, "admission_reference_write_refused")
        # Echoing the current value (what the form view posts back) is fine.
        admission.sudo().write({"name": original})
        self.assertEqual(admission.name, original)

    def test_4_a_missing_sequence_fails_closed(self):
        self.sequence.sudo().write({"active": False})
        with self.assertRaises(AdmissionWorkflowError) as caught:
            self._draft(bed=False)
        self.assertEqual(caught.exception.code, "admission_sequence_missing")

    def test_5_a_rolled_back_draw_is_never_reissued(self):
        before = self._draft(bed=False)
        try:
            with self.env.cr.savepoint():
                lost = self._draft(bed=False)
                lost_number = _number(lost.name)
                raise RuntimeError("roll the admission back")
        except RuntimeError:
            pass
        after = self._draft(bed=False)
        self.assertGreater(_number(after.name), lost_number)
        self.assertGreater(lost_number, _number(before.name))

    def test_6_concurrent_transactions_never_draw_the_same_number(self):
        """Two REAL database connections -- separate transactions, both open
        at once, neither the test's own -- draw alternately from the
        PostgreSQL sequence behind ir.sequence, then both roll back. nextval()
        is atomic and non-transactional, so the draws are disjoint and none is
        handed out again after the rollback."""
        dbname = self.env.cr.dbname
        sequence_table = "ir_sequence_%03d" % self.sequence.id
        draws = {"a": [], "b": []}
        connections = {key: db_connect(dbname).cursor() for key in draws}
        try:
            for cr in connections.values():
                # Anything that would block surfaces as an error, not a hang.
                cr.execute("SET LOCAL lock_timeout = '5s'")
            for _ in range(25):
                for key, cr in connections.items():
                    cr.execute("SELECT nextval(%s)", (sequence_table,))
                    draws[key].append("ADM%05d" % cr.fetchone()[0])
        finally:
            for cr in connections.values():
                cr.rollback()
                cr.close()
        everything = draws["a"] + draws["b"]
        self.assertEqual(len(everything), 50)
        self.assertEqual(len(set(everything)), 50)
        # And the test transaction's next admission clears all of them, even
        # though both connections rolled back.
        self.assertGreater(_number(self._draft(bed=False).name), max(map(_number, everything)))

    def test_7_reloading_the_data_file_does_not_rewind_the_counter(self):
        self._draft(bed=False)
        self._draft(bed=False)
        before = self.sequence.number_next_actual
        self.assertGreater(before, 1)
        convert_file(
            self.env, "hospital_admission", "data/admission_sequence.xml", {},
            mode="update", noupdate=False, kind="data",
        )
        self.env.invalidate_all()
        self.assertEqual(self.sequence.number_next_actual, before)
        admission = self._draft(bed=False)
        self.assertEqual(_number(admission.name), before)

    def test_8_the_migration_moves_the_counter_past_every_issued_reference(self):
        migrate = _load_migration()
        one, two, three = (self._draft(bed=False) for _ in range(3))
        # The UAT shape: duplicates, a high historical number, and a counter
        # rewound to 1 by the old upgrade behaviour.
        self._raw("UPDATE hospital_admission SET name = 'ADM00001' WHERE id IN %s", (tuple([one.id, two.id]),))
        self._raw("UPDATE hospital_admission SET name = 'ADM90041' WHERE id = %s", (three.id,))
        self.sequence.sudo().write({"number_next": 1})
        self.env.cr.execute(
            "UPDATE ir_model_data SET noupdate = FALSE "
            "WHERE module = 'hospital_admission' AND name = 'sequence_hospital_admission'"
        )

        migrate(self.env.cr, "18.0.1.5.0")
        self.env.invalidate_all()

        self.assertEqual(self.sequence.number_next_actual, 90042)
        self.env.cr.execute(
            "SELECT noupdate FROM ir_model_data "
            "WHERE module = 'hospital_admission' AND name = 'sequence_hospital_admission'"
        )
        self.assertTrue(self.env.cr.fetchone()[0])
        # Nothing renamed.
        self.assertEqual((one.name, two.name, three.name), ("ADM00001", "ADM00001", "ADM90041"))
        self.assertEqual(self._draft(bed=False).name, "ADM90042")

        # Never backwards: re-running with the counter already ahead is a no-op.
        migrate(self.env.cr, "18.0.1.5.0")
        self.env.invalidate_all()
        self.assertEqual(self.sequence.number_next_actual, 90043)
        # A fresh install is left alone.
        migrate(self.env.cr, None)

    def test_9_legacy_duplicates_stay_readable_and_editable(self):
        first, second = self._draft(bed=False), self._draft(bed=False)
        self._raw("UPDATE hospital_admission SET name = 'ADM00001' WHERE id IN %s", (tuple([first.id, second.id]),))
        found = self.env["hospital.admission"].sudo().search([("name", "=", "ADM00001"), ("id", "in", [first.id, second.id])])
        self.assertEqual(found, first | second)
        self.assertEqual({record.display_name for record in found}, {"ADM00001"})
        with self.assertRaises(AdmissionWorkflowError):
            first.sudo().write({"name": "ADM00002"})
        # An ordinary edit that posts the (duplicate) name back is untouched.
        first.sudo().write({"name": "ADM00001", "notes": "legacy row still editable"})
        self.assertEqual(first.notes, "legacy row still editable")
