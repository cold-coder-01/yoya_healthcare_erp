"""Admissions Slice 3: stay segments, rate snapshots and bed/day accrual.

  A. no transfer                 -- billing unchanged from the legacy rule
  B. one transfer                -- old segment at the old rate, new at the new
  C. multiple transfers          -- every period billed once, in one segment
  D. same-day transfer
  E. same-day admission/discharge, and the midnight rule
  F. an active stay keeps accruing
  G. a catalogue rate edited afterwards reprices nothing

Times are set by writing the two timeline columns directly (the model refuses
it, as it should) so a stay of days can be tested in milliseconds. Everything
else goes through the real workflow.
"""
import importlib.util
import os
from datetime import datetime, timedelta

from odoo import fields
from odoo.exceptions import AccessError, UserError
from odoo.tests import tagged

from odoo.addons.hospital_admission.models.admission_authority import (
    AdmissionWorkflowError,
)
from odoo.addons.hospital_admission.models.admission_stay import stay_period_count

from .common import AdmissionCase

H = timedelta(hours=1)


@tagged("post_install", "-at_install", "admission_stay_billing")
class StayCase(AdmissionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.cheap_bed = cls._priced_bed("S3 Cheap", 100.0)
        cls.dear_bed = cls._priced_bed("S3 Dear", 900.0)
        cls.mid_bed = cls._priced_bed("S3 Mid", 300.0)

    @classmethod
    def _priced_bed(cls, name, ward_rate, fee=0.0):
        ward = cls._make_ward(name, cls.department)
        ward.sudo().write({"daily_ward_rate": ward_rate, "admission_fee": fee})
        room = cls._make_room(ward, name + "-R")
        return cls._make_bed(room, name + "-B")

    def _move(self, admission, bed):
        admission.action_transfer(to_ward=bed.ward_id, to_room=bed.room_id, to_bed=bed, reason="test")

    def _set_times(self, admission, admitted_at, moves=(), discharged_at=None):
        """Place the stay on the clock: admission time, each transfer (oldest
        first) and, for a finished stay, the discharge time."""
        self._raw(
            "UPDATE hospital_admission SET admission_date = %s WHERE id = %s",
            (admitted_at, admission.id),
        )
        transfers = self.env["hospital.admission.transfer"].sudo().search(
            [("admission_id", "=", admission.id)], order="id asc"
        )
        self.assertEqual(len(transfers), len(moves))
        for transfer, moved_at in zip(transfers, moves):
            self._raw(
                "UPDATE hospital_admission_transfer SET transfer_date = %s WHERE id = %s",
                (moved_at, transfer.id),
            )
        if discharged_at:
            self._raw(
                "UPDATE hospital_admission SET discharge_date = %s WHERE id = %s",
                (discharged_at, admission.id),
            )
        admission.invalidate_recordset()

    def _breakdown(self, admission, now=None):
        return admission._bed_stay_breakdown(now)


@tagged("post_install", "-at_install", "admission_stay_billing")
class TestStaySegments(StayCase):
    # ------------------------------------------------------------------
    # A. no transfer: identical to the legacy rule
    # ------------------------------------------------------------------
    def test_a_no_transfer_bills_exactly_as_before(self):
        admission = self._admitted(bed=self.bed_a)            # ward: fee 500, 800/day
        now = fields.Datetime.now()
        self._set_times(admission, now - 50 * H)
        breakdown = self._breakdown(admission, now)
        self.assertEqual(len(breakdown["segments"]), 1)
        self.assertEqual(breakdown["total_days"], 3)             # ceil(50/24)
        self.assertEqual(breakdown["total_days"], admission.stay_days)
        self.assertEqual(breakdown["bed_total"], 3 * 800.0)
        self.assertEqual(breakdown["admission_fee"], 500.0)
        # The legacy formula, for the record: current rate x stay days + fee.
        self.assertEqual(breakdown["total"], 800.0 * 3 + 500.0)

        admission.action_discharge()
        self._set_times(admission, now - 50 * H, discharged_at=now)
        admission.action_generate_admission_bill()
        lines = admission.bill_id.line_ids.sorted("sequence")
        self.assertEqual(len(lines), 2)
        self.assertEqual((lines[0].quantity, lines[0].unit_price), (1.0, 500.0))
        self.assertEqual((lines[1].quantity, lines[1].unit_price), (3.0, 800.0))

    # ------------------------------------------------------------------
    # B. one transfer
    # ------------------------------------------------------------------
    def test_b_one_transfer_prices_each_segment_at_its_own_rate(self):
        admission = self._admitted(bed=self.cheap_bed)
        self._move(admission, self.dear_bed)
        now = fields.Datetime.now()
        t0 = now - 100 * H
        self._set_times(admission, t0, moves=[t0 + 30 * H])
        breakdown = self._breakdown(admission, now)
        cheap, dear = breakdown["segments"]
        # Periods start at +0, +24, +48, +72, +96: two before the move.
        self.assertEqual((cheap["bed"], cheap["days"], cheap["daily_rate"]), (self.cheap_bed, 2, 100.0))
        self.assertEqual((dear["bed"], dear["days"], dear["daily_rate"]), (self.dear_bed, 3, 900.0))
        self.assertEqual(cheap["end"], dear["start"])
        self.assertEqual(breakdown["bed_total"], 2 * 100.0 + 3 * 900.0)
        self.assertNotEqual(breakdown["bed_total"], 5 * 900.0, "the whole stay at the new rate is the old defect")

    def test_b_the_admission_fee_stays_the_opening_wards(self):
        opening = self._priced_bed("S3 FeeA", 100.0, fee=250.0)
        destination = self._priced_bed("S3 FeeB", 100.0, fee=9999.0)
        admission = self._admitted(bed=opening)
        self._move(admission, destination)
        self.assertEqual(self._breakdown(admission)["admission_fee"], 250.0)
        self.assertEqual(admission.admission_fee_amount, 250.0)

    # ------------------------------------------------------------------
    # C. multiple transfers
    # ------------------------------------------------------------------
    def test_c_multiple_transfers_bill_every_period_exactly_once(self):
        admission = self._admitted(bed=self.cheap_bed)
        self._move(admission, self.dear_bed)
        self._move(admission, self.mid_bed)
        now = fields.Datetime.now()
        t0 = now - 118 * H
        self._set_times(admission, t0, moves=[t0 + 30 * H, t0 + 80 * H])
        breakdown = self._breakdown(admission, now)
        days = [segment["days"] for segment in breakdown["segments"]]
        self.assertEqual(days, [2, 2, 1])                       # periods +0,+24 | +48,+72 | +96
        self.assertEqual(sum(days), stay_period_count(t0, now))
        self.assertEqual(sum(days), admission.stay_days)
        self.assertEqual(breakdown["bed_total"], 2 * 100.0 + 2 * 900.0 + 1 * 300.0)
        self.assertEqual(
            [segment["source"] for segment in breakdown["segments"]],
            ["admission", "transfer", "transfer"],
        )

    def test_c_a_move_exactly_on_a_period_boundary_opens_that_period(self):
        admission = self._admitted(bed=self.cheap_bed)
        self._move(admission, self.dear_bed)
        now = fields.Datetime.now()
        t0 = now - 47 * H
        self._set_times(admission, t0, moves=[t0 + 24 * H])
        days = [s["days"] for s in self._breakdown(admission, now)["segments"]]
        self.assertEqual(days, [1, 1])

    # ------------------------------------------------------------------
    # D. same-day transfer
    # ------------------------------------------------------------------
    def test_d_a_same_day_transfer_does_not_bill_a_second_day(self):
        admission = self._admitted(bed=self.cheap_bed)
        self._move(admission, self.dear_bed)
        now = fields.Datetime.now()
        t0 = now - 3 * H
        self._set_times(admission, t0, moves=[t0 + 1 * H])
        breakdown = self._breakdown(admission, now)
        self.assertEqual([s["days"] for s in breakdown["segments"]], [1, 0])
        self.assertEqual(breakdown["bed_total"], 100.0)
        self.assertEqual(breakdown["total_days"], 1)

    def test_d_back_and_forth_on_one_day_is_still_one_day(self):
        admission = self._admitted(bed=self.cheap_bed)
        self._move(admission, self.dear_bed)
        self._move(admission, self.cheap_bed)
        now = fields.Datetime.now()
        t0 = now - 5 * H
        self._set_times(admission, t0, moves=[t0 + 1 * H, t0 + 2 * H])
        breakdown = self._breakdown(admission, now)
        self.assertEqual([s["days"] for s in breakdown["segments"]], [1, 0, 0])
        self.assertEqual(breakdown["bed_total"], 100.0)

    # ------------------------------------------------------------------
    # E. same-day admission/discharge; the midnight rule
    # ------------------------------------------------------------------
    def test_e_same_day_admission_and_discharge_is_one_day(self):
        admission = self._admitted(bed=self.cheap_bed)
        admission.action_discharge()
        discharged = fields.Datetime.now()
        self._set_times(admission, discharged - 2 * H, discharged_at=discharged)
        self.assertEqual(admission.stay_days, 1)
        breakdown = self._breakdown(admission)
        self.assertEqual(breakdown["total_days"], 1)
        self.assertFalse(breakdown["segments"][0]["ongoing"])
        admission.action_generate_admission_bill()
        stay_line = admission.bill_id.line_ids.filtered(lambda l: l.unit_price == 100.0)
        self.assertEqual(stay_line.quantity, 1.0)

    def test_e_a_zero_length_stay_is_the_minimum_one_day(self):
        admission = self._admitted(bed=self.cheap_bed)
        admission.action_discharge()
        at = fields.Datetime.now()
        self._set_times(admission, at, discharged_at=at)
        self.assertEqual(self._breakdown(admission)["total_days"], 1)

    def test_e_days_are_24_hour_periods_not_calendar_days(self):
        """Crossing midnight means nothing by itself; a started period is a day."""
        self.assertEqual(stay_period_count(datetime(2026, 1, 1, 23, 30), datetime(2026, 1, 2, 0, 30)), 1)
        self.assertEqual(stay_period_count(datetime(2026, 1, 1, 8, 0), datetime(2026, 1, 2, 8, 0)), 1)
        self.assertEqual(stay_period_count(datetime(2026, 1, 1, 8, 0), datetime(2026, 1, 2, 8, 0, 1)), 2)
        self.assertEqual(stay_period_count(datetime(2026, 1, 1, 8, 0), datetime(2026, 1, 1, 8, 0)), 1)

    def test_e_the_period_rule_matches_the_legacy_stay_days_compute(self):
        admission = self._admitted(bed=self.cheap_bed)
        admission.action_discharge()
        for hours in (1, 23, 24, 25, 47, 48, 49, 240):
            with self.subTest(hours=hours):
                end = fields.Datetime.now()
                self._set_times(admission, end - hours * H, discharged_at=end)
                self.assertEqual(self._breakdown(admission)["total_days"], admission.stay_days)

    # ------------------------------------------------------------------
    # F. current active stay
    # ------------------------------------------------------------------
    def test_f_an_active_stay_accrues_until_now(self):
        admission = self._admitted(bed=self.cheap_bed)
        now = fields.Datetime.now()
        self._set_times(admission, now - 10 * H)
        today = self._breakdown(admission, now)
        self.assertTrue(today["segments"][-1]["ongoing"])
        self.assertEqual(today["total_days"], 1)
        later = self._breakdown(admission, now + 30 * H)
        self.assertEqual(later["total_days"], 2)
        self.assertEqual(later["bed_total"], 200.0)

    def test_f_draft_and_cancelled_have_no_stay(self):
        draft = self._draft(bed=self.cheap_bed)
        self.assertEqual(self._breakdown(draft)["segments"], [])
        self.assertEqual(self._breakdown(draft)["total"], 0.0)
        # The draft keeps the legacy preview (an estimate, never billed).
        self.assertEqual(draft.daily_rate_amount, 100.0)

    # ------------------------------------------------------------------
    # G. catalogue edits never reprice history
    # ------------------------------------------------------------------
    def test_g_a_catalogue_rate_changed_after_transfer_reprices_nothing(self):
        admission = self._admitted(bed=self.cheap_bed)
        self._move(admission, self.dear_bed)
        now = fields.Datetime.now()
        t0 = now - 100 * H
        self._set_times(admission, t0, moves=[t0 + 30 * H])
        before = self._breakdown(admission, now)

        self.cheap_bed.ward_id.sudo().write({"daily_ward_rate": 5000.0, "admission_fee": 777.0})
        self.dear_bed.ward_id.sudo().write({"daily_ward_rate": 7000.0})
        self.dear_bed.sudo().write({"daily_bed_rate": 8000.0})

        after = self._breakdown(admission, now)
        self.assertEqual(after["bed_total"], before["bed_total"])
        self.assertEqual(after["admission_fee"], before["admission_fee"])
        self.assertEqual([s["daily_rate"] for s in after["segments"]], [100.0, 900.0])
        self.assertEqual(after["review_reasons"], [])

    def test_g_the_opening_snapshot_follows_the_legacy_precedence(self):
        bed = self._priced_bed("S3 Prec", 100.0)
        bed.room_id.sudo().write({"daily_room_rate": 200.0})
        bed.sudo().write({"daily_bed_rate": 300.0})
        admission = self._admitted(bed=bed)
        snapshot = admission.sudo()
        self.assertTrue(snapshot.rate_snapshot_taken)
        self.assertEqual(snapshot.rate_snapshot_origin, "workflow")
        self.assertEqual((snapshot.rate_snapshot_daily_rate, snapshot.rate_snapshot_basis), (300.0, "bed"))

    def test_g_a_transfer_records_the_destinations_rate(self):
        admission = self._admitted(bed=self.cheap_bed)
        self._move(admission, self.dear_bed)
        transfer = admission.transfer_ids.sudo()
        self.assertTrue(transfer.rate_snapshot_taken)
        self.assertEqual((transfer.to_daily_rate, transfer.to_rate_basis), (900.0, "ward"))
        self.assertEqual(transfer.rate_snapshot_origin, "workflow")


@tagged("post_install", "-at_install", "admission_stay_billing")
class TestStayAuthority(StayCase):
    def test_the_snapshot_cannot_be_written_outside_the_workflow(self):
        admission = self._admitted(bed=self.cheap_bed)
        for vals in (
            {"rate_snapshot_daily_rate": 1.0},
            {"rate_snapshot_admission_fee": 5.0},
            {"rate_snapshot_taken": False},
            {"rate_snapshot_basis": "none"},
        ):
            with self.subTest(vals=vals), self.assertRaises(AdmissionWorkflowError) as caught:
                admission.sudo().write(vals)
            self.assertEqual(caught.exception.code, "admission_rate_snapshot_write_refused")
        with self.assertRaises(AdmissionWorkflowError):
            self.env["hospital.admission"].sudo().create(
                {"patient_id": self._patient().id, "rate_snapshot_daily_rate": 1.0}
            )
        # A draft cannot carry a hand-written rate into its admission either.
        draft = self._draft(bed=self.cheap_bed)
        with self.assertRaises(AdmissionWorkflowError):
            draft.sudo().write({"rate_snapshot_daily_rate": 1.0, "rate_snapshot_taken": True})

    def test_transfer_history_is_immutable(self):
        admission = self._admitted(bed=self.cheap_bed)
        self._move(admission, self.dear_bed)
        transfer = admission.transfer_ids
        for vals in (
            {"transfer_date": fields.Datetime.now() - 100 * H},
            {"to_bed_id": self.mid_bed.id},
            {"from_ward_id": self.ward.id},
            {"to_daily_rate": 1.0},
            {"reason": "rewritten"},
        ):
            with self.subTest(vals=vals), self.assertRaises(AdmissionWorkflowError):
                transfer.sudo().write(vals)
        with self.assertRaises(AdmissionWorkflowError):
            transfer.sudo().unlink()
        # A nurse, who holds write on the model, is refused the same way.
        nurse_view = transfer.with_user(self.nurse)
        with self.assertRaises(UserError):  # AccessError subclasses UserError
            nurse_view.write({"reason": "nurse edit"})

    def test_the_snapshot_is_invisible_to_clinical_roles(self):
        admission = self._admitted(bed=self.bed_a)
        for user in (self.nurse, self.doctor_user, self.receptionist):
            with self.subTest(user=user.login), self.assertRaises(AccessError):
                admission.with_user(user).read(["rate_snapshot_daily_rate"])
        self.assertTrue(admission.with_user(self.accountant).read(["rate_snapshot_daily_rate"]))

    def test_a_missing_snapshot_falls_back_and_asks_for_review(self):
        admission = self._admitted(bed=self.cheap_bed)
        self._raw(
            "UPDATE hospital_admission SET rate_snapshot_taken = FALSE, rate_snapshot_daily_rate = 0 "
            "WHERE id = %s",
            (admission.id,),
        )
        breakdown = self._breakdown(admission)
        self.assertEqual(breakdown["segments"][0]["daily_rate"], 100.0)
        self.assertFalse(breakdown["segments"][0]["rate_snapshot"])
        self.assertIn("rate_snapshot_missing", breakdown["review_reasons"])

    def test_the_upgrade_backfills_from_the_catalogue_in_force(self):
        admission = self._admitted(bed=self.cheap_bed)
        self._move(admission, self.dear_bed)
        transfer = admission.transfer_ids
        self._raw(
            "UPDATE hospital_admission SET rate_snapshot_taken = FALSE, rate_snapshot_origin = NULL, "
            "rate_snapshot_daily_rate = 0, rate_snapshot_basis = NULL WHERE id = %s",
            (admission.id,),
        )
        self._raw(
            "UPDATE hospital_admission_transfer SET rate_snapshot_taken = FALSE, "
            "rate_snapshot_origin = NULL, to_daily_rate = 0, to_rate_basis = NULL WHERE id = %s",
            (transfer.id,),
        )
        path = os.path.join(
            os.path.dirname(os.path.dirname(__file__)), "migrations", "18.0.1.3.0", "post-migrate.py"
        )
        spec = importlib.util.spec_from_file_location("hospital_admission_s3_backfill", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        module.migrate(self.env.cr, "18.0.1.2.0")
        self.env.invalidate_all()

        opening = admission.sudo()
        self.assertEqual(opening.rate_snapshot_origin, "upgrade_backfill")
        # The OPENING location: the first transfer's origin, not today's bed.
        self.assertEqual(opening.rate_snapshot_daily_rate, 100.0)
        moved = transfer.sudo()
        self.assertEqual((moved.to_daily_rate, moved.rate_snapshot_origin), (900.0, "upgrade_backfill"))
        self.assertEqual(self._breakdown(admission)["review_reasons"], [])

    def test_a_transfer_that_would_run_backwards_is_refused(self):
        admission = self._admitted(bed=self.cheap_bed)
        self._raw(
            "UPDATE hospital_admission SET admission_date = %s WHERE id = %s",
            (fields.Datetime.now() + 5 * H, admission.id),
        )
        with self.assertRaises(AdmissionWorkflowError) as caught:
            self._move(admission, self.dear_bed)
        self.assertEqual(caught.exception.code, "admission_timeline_incoherent")

    def test_the_allocator_lays_periods_from_the_admission_time(self):
        t0 = datetime(2026, 3, 1, 10, 0)
        segments = [
            {"start": t0, "end": t0 + 10 * H},
            {"start": t0 + 10 * H, "end": t0 + 60 * H},
            {"start": t0 + 60 * H, "end": t0 + 73 * H},
        ]
        self.env["hospital.admission"]._allocate_stay_days(segments)
        # Periods +0 | +24, +48 | +72.
        self.assertEqual([s["days"] for s in segments], [1, 2, 1])
