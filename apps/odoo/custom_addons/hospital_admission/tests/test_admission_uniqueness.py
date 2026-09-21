"""Groups D and E: uniqueness invariants and the concurrency that needs them.

TWO LAYERS, TESTED SEPARATELY AND ON PURPOSE.

The Python checks give the operator a sentence and refuse before any bed is
touched. The PARTIAL UNIQUE INDEXES are the actual guarantee: they cover every
path that is not the application path -- a migration, the Odoo shell, an
import, a psql session, and hospital_insurance, which extends this model from
outside this repository. A check that lives only in Python is a check a future
caller can forget to call, so the tests below reach past the Python and assert
against the database itself.
"""
import psycopg2
from odoo.tests import tagged
from odoo.tools import mute_logger

from odoo.addons.hospital_admission.models.admission_authority import (
    AdmissionWorkflowError,
)

from .common import AdmissionCase


@tagged("post_install", "-at_install", "admission_uniqueness")
class TestAdmissionUniqueness(AdmissionCase):
    # ==================================================================
    # D. UNIQUENESS
    # ==================================================================
    def test_one_active_admission_per_patient(self):
        patient = self._patient()
        self._encounter(patient)
        first = self._draft(patient=patient, bed=self.bed_a, encounter=False)
        first.action_confirm_admission()

        second = self._draft(patient=patient, bed=self.bed_b, encounter=False)
        with self.assertRaises(AdmissionWorkflowError) as caught:
            second.action_confirm_admission()
        self.assertEqual(caught.exception.code, "admission_patient_already_admitted")
        self.assertEqual(second.state, "draft")

    def test_one_active_admission_per_bed(self):
        first = self._admitted(bed=self.bed_a)
        second = self._draft(bed=self.bed_a)
        with self.assertRaises(AdmissionWorkflowError) as caught:
            second.action_confirm_admission()
        # The state check runs before the ownership check, and deliberately so:
        # "that bed is not available" tells the operator what they need without
        # disclosing that another named patient is in it.
        self.assertEqual(caught.exception.code, "admission_bed_not_available")
        self.bed_a.invalidate_recordset()
        self.assertEqual(self.bed_a.current_admission_id, first)

    @mute_logger("odoo.sql_db")
    def test_the_database_enforces_one_active_admission_per_patient(self):
        """Past the Python, straight at the index.

        The workflow capability is raised so the model guard lets the write
        through -- this test is not about the model guard, it is about what
        happens when something bypasses it.
        """
        patient = self._patient()
        self._encounter(patient)
        first = self._draft(patient=patient, bed=self.bed_a, encounter=False)
        first.action_confirm_admission()
        second = self._draft(patient=patient, bed=self.bed_b, encounter=False)

        with self.assertRaises(psycopg2.errors.UniqueViolation):
            with self.cr.savepoint():
                self.cr.execute(
                    "UPDATE hospital_admission SET state = 'admitted' WHERE id = %s",
                    (second.id,),
                )

    @mute_logger("odoo.sql_db")
    def test_the_database_enforces_one_active_admission_per_bed(self):
        first = self._admitted(bed=self.bed_a)
        second = self._draft(bed=self.bed_b)
        second.action_confirm_admission()

        with self.assertRaises(psycopg2.errors.UniqueViolation):
            with self.cr.savepoint():
                self.cr.execute(
                    "UPDATE hospital_admission SET bed_id = %s WHERE id = %s",
                    (self.bed_a.id, second.id),
                )

    def test_the_indexes_exist_and_are_partial(self):
        """The invariant is worthless if the index quietly stopped being made."""
        self.cr.execute(
            """
            SELECT indexname, indexdef FROM pg_indexes
            WHERE tablename = 'hospital_admission'
              AND indexname IN (
                'hospital_admission_one_active_per_patient_idx',
                'hospital_admission_one_active_per_bed_idx')
            """
        )
        found = dict(self.cr.fetchall())
        self.assertEqual(len(found), 2, "Both partial unique indexes must exist")
        for definition in found.values():
            self.assertIn("UNIQUE", definition)
            self.assertIn("WHERE", definition, "The index must be PARTIAL")
            self.assertIn("admitted", definition)
            self.assertIn("transferred", definition)

    def test_discharged_admissions_do_not_block_readmission(self):
        """The whole reason the indexes are partial."""
        patient = self._patient()
        encounter = self._encounter(patient)
        first = self._draft(patient=patient, bed=self.bed_a, encounter=False)
        first.action_confirm_admission()
        first.action_discharge()

        # A new episode for the same patient, then a second stay.
        encounter.sudo().write({"state": "completed"})
        self._encounter(patient)
        second = self._draft(patient=patient, bed=self.bed_a, encounter=False)
        second.action_confirm_admission()
        self.assertEqual(second.state, "admitted")

    def test_many_discharged_stays_may_share_one_bed(self):
        for _ in range(3):
            admission = self._admitted(bed=self.bed_a)
            admission.action_discharge()
        self.cr.execute(
            "SELECT count(*) FROM hospital_admission WHERE bed_id = %s", (self.bed_a.id,)
        )
        self.assertGreaterEqual(self.cr.fetchone()[0], 3)

    # ==================================================================
    # E. CONCURRENCY
    # ==================================================================
    def test_multi_record_confirm_naming_one_bed_is_refused_before_anything_moves(self):
        """THE TWO-PASS SHAPE.

        _assert_no_other_active_admission() reads the DATABASE, so in a single
        confirm of two drafts naming one bed neither row is active yet when the
        other is checked and BOTH would pass -- the second would only fail at
        the index, after the first had already written occupancy. The batch
        must therefore be de-duplicated separately, before any record is
        touched. hospital.encounter.create() has the same two passes for the
        same reason.
        """
        first = self._draft(bed=self.bed_a)
        second = self._draft(bed=self.bed_a)
        batch = first | second

        with self.assertRaises(AdmissionWorkflowError) as caught:
            batch.action_confirm_admission()
        self.assertEqual(caught.exception.code, "admission_duplicate_bed_in_batch")

        # NOTHING moved. Not even the first one.
        self.bed_a.invalidate_recordset()
        self.assertEqual(self.bed_a.state, "available")
        self.assertFalse(self.bed_a.current_admission_id)
        self.assertEqual(first.state, "draft")
        self.assertEqual(second.state, "draft")

    def test_multi_record_confirm_into_different_beds_works(self):
        first = self._draft(bed=self.bed_a)
        second = self._draft(bed=self.bed_b)
        (first | second).action_confirm_admission()
        self.assertEqual(first.state, "admitted")
        self.assertEqual(second.state, "admitted")
        self.assertEqual(self.bed_a.current_admission_id, first)
        self.assertEqual(self.bed_b.current_admission_id, second)

    def test_stale_occupancy_pointing_the_other_way_is_caught(self):
        """A bed marked available while an active admission still names it.

        _occupy_bed() reads occupancy from the ADMISSION side as well as from
        the bed's pointer, because the pointer is the field that goes stale.
        """
        self._admitted(bed=self.bed_a)
        # Force the pointer stale behind the model's back, which is what a
        # pre-Slice-0 direct write or a bad migration leaves behind: the bed
        # says free, an active admission says otherwise.
        self._raw(
            "UPDATE hospital_bed SET state='available', current_admission_id=NULL "
            "WHERE id = %s",
            (self.bed_a.id,),
        )

        contender = self._draft(bed=self.bed_a)
        with self.assertRaises(AdmissionWorkflowError) as caught:
            contender.action_confirm_admission()
        self.assertEqual(caught.exception.code, "admission_bed_owned_by_other")

    def test_lock_order_is_ascending_by_bed_id(self):
        """Deterministic order is what stops two mirror transfers deadlocking.

        Asserted by capturing the SQL the lock helper issues rather than by
        racing two real transactions, which a TransactionCase cannot do: every
        test here shares one cursor.
        """
        admission = self._admitted(bed=self.bed_a)
        statements = []
        original = self.cr.execute

        def recording(query, params=None, *args, **kwargs):
            statements.append((str(query), params))
            return original(query, params, *args, **kwargs)

        self.patch(self.cr, "execute", recording)
        admission._lock_for_occupancy(self.bed_b | self.bed_a)

        advisory = [
            params[0]
            for query, params in statements
            if "pg_advisory_xact_lock" in query and params
        ]
        self.assertEqual(len(advisory), 2, "One advisory lock per bed")
        ids = [int(key.rsplit(":", 1)[1]) for key in advisory]
        self.assertEqual(ids, sorted(ids), "Advisory locks must ascend by bed id")

        for_update = [q for q, _ in statements if "FOR UPDATE" in q]
        self.assertTrue(
            any("hospital_admission" in q for q in for_update),
            "The admission row must be locked",
        )
        bed_locks = [q for q in for_update if "hospital_bed" in q]
        self.assertTrue(bed_locks, "The bed rows must be locked")
        self.assertIn("ORDER BY id", bed_locks[0])

    def test_a_failing_batch_leaves_no_occupancy_behind(self):
        """ATOMIC ROLLBACK, tested where it actually matters.

        The first admission in the batch really does take its bed before the
        second one fails, so this is the case where a partial write would
        survive: a patient recorded as admitted to a bed nobody ever put them
        in. The savepoint stands in for the transaction Odoo's HTTP layer rolls
        back around a failed request.
        """
        good = self._draft(bed=self.bed_a)
        stranger = self._patient()
        bad = self._draft(patient=stranger, bed=self.bed_b, encounter=False)

        with self.assertRaises(AdmissionWorkflowError):
            with self.cr.savepoint():
                (good | bad).action_confirm_admission()

        self.env.invalidate_all()
        self.assertEqual(good.state, "draft")
        self.assertEqual(bad.state, "draft")
        self.assertEqual(self.bed_a.state, "available")
        self.assertFalse(self.bed_a.current_admission_id)

    def test_confirm_refuses_cleanly_before_touching_a_bed(self):
        """A refusal during encounter adoption must not have taken the bed."""
        patient = self._patient()
        encounter = self._encounter(patient)
        admission = self._draft(patient=patient, bed=self.bed_a, encounter=False)
        encounter.sudo().write({"state": "completed"})

        with self.assertRaises(AdmissionWorkflowError) as caught:
            admission.action_confirm_admission()
        self.assertEqual(caught.exception.code, "admission_encounter_required")

        self.env.invalidate_all()
        self.assertEqual(self.bed_a.state, "available")
        self.assertFalse(self.bed_a.current_admission_id)
        self.assertEqual(admission.state, "draft")
