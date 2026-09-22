"""Inpatient stay segments and bed/day accrual (Admissions Slice 3).

THE DEFECT THIS REPLACES
------------------------
The legacy bill multiplied the admission's CURRENT daily rate by the WHOLE
stay. A patient who spent nine days on a general ward and one night in ICU was
billed ten ICU nights, and every transfer silently repriced every day before
it. Worse, the rate was read live off the ward / room / bed catalogue, so an
administrator editing a bed rate today repriced stays that ended last month.

WHAT A STAY IS NOW
------------------
An ordered list of SEGMENTS, reconstructed from two authoritative records that
the workflow already writes and that nothing else may write:

    segment 0   admission_date      -> first transfer    at the opening location
    segment i   transfer i          -> transfer i+1      at transfer i's destination
    last        last transfer       -> discharge_date, or NOW for an active stay

Each segment carries the rate that was IN FORCE WHEN IT OPENED: the admission's
opening snapshot for segment 0, the transfer row's destination snapshot for
every later one. Nothing is stored twice; a segment is a view over the
admission and its immutable transfer history.

DAY-COUNT SEMANTICS -- PRESERVED, THEN APPLIED PER SEGMENT
----------------------------------------------------------
The existing rule (HospitalAdmission._compute_stay_days, unchanged) is:

    * the stay is counted in 24-HOUR PERIODS from the admission time, not in
      calendar days -- crossing midnight means nothing by itself;
    * every STARTED period is a whole day (ceil), so any partial day rounds up;
    * the minimum is ONE day, so a same-day admission and discharge is one day.

Segmentation must not change how many days a stay is billed, only which rate
each day is billed at. So the periods are laid down exactly as before -- period
k starts at admission_date + 24h * k -- and each period is billed ONCE, at the
rate of the segment the patient was in WHEN THAT PERIOD STARTED.

Consequences, all deliberate:

    * the total billed days of any stay equal the legacy stay_days exactly,
      with or without transfers;
    * a transfer never reprices a period that had already started -- the
      earlier stay keeps its own rate, which is the point of the fix;
    * a segment in which no period starts (a short stay between two period
      boundaries) is billed zero days: its time was already inside a period
      paid at the previous location's rate. Nothing is billed twice;
    * the admission fee is charged once, from the OPENING snapshot, so a
      transfer does not swap the admission fee for the destination ward's.

Whether a period should instead follow the more expensive location, or the
location at midnight, is a business policy question, not something to invent
here. This rule is the one that preserves the existing totals.
"""
import math
from datetime import timedelta

from odoo import api, fields, models

from .admission_authority import (
    ADMISSION_ACTIVE_STATES,
    ADMISSION_MONEY_READ,
    RATE_BASIS_SELECTION,
    RATE_SNAPSHOT_ORIGIN_SELECTION,
    admission_rate_snapshot_capability,
    resolve_location_rate,
)

DAY = timedelta(hours=24)

# States in which a stay has started and can be segmented.
STAY_STATES = ADMISSION_ACTIVE_STATES + ("discharged",)

# Why a stay's segmentation cannot be trusted as-is. FIXED CODES; the desk
# shows the sentence and never an amount. Surfaced, never repaired.
STAY_REVIEW_MESSAGES = {
    "rate_snapshot_missing": (
        "A stay segment was not recorded when it opened and falls back to today's "
        "ward, room or bed settings."
    ),
    "admission_time_missing": "The admission has no admission time.",
    "discharge_time_missing": "The admission is discharged but has no discharge time.",
    "transfer_before_admission": "A transfer is recorded before the admission time.",
    "transfer_after_end": "A transfer is recorded after the end of the stay.",
    "transfer_chain_broken": (
        "A transfer does not start from the bed the previous transfer ended in."
    ),
    "segment_location_mismatch": (
        "The last stay segment does not end in the admission's current bed."
    ),
}


def stay_period_count(start, end):
    """The legacy day count: started 24-hour periods, minimum one.

    Byte-for-byte the rule in HospitalAdmission._compute_stay_days.
    """
    if not start:
        return 0
    if not end or end <= start:
        return 1
    hours = (end - start).total_seconds() / 3600.0
    return max(1, math.ceil(hours / 24))


class HospitalAdmissionStay(models.Model):
    _inherit = "hospital.admission"

    # ── Opening rate snapshot (segment 0) ───────────────────────
    #
    # Written ONCE, when the admission is confirmed into a bed, under
    # admission_rate_snapshot_capability(); refused on every other path by
    # _assert_authoritative_write(). Readable only by the money roles: the desk
    # never serializes it and clinical roles never need it.
    rate_snapshot_taken = fields.Boolean(
        string="Stay Rate Recorded",
        readonly=True,
        copy=False,
        groups=ADMISSION_MONEY_READ,
    )
    rate_snapshot_origin = fields.Selection(
        RATE_SNAPSHOT_ORIGIN_SELECTION,
        string="Stay Rate Origin",
        readonly=True,
        copy=False,
        groups=ADMISSION_MONEY_READ,
    )
    rate_snapshot_basis = fields.Selection(
        RATE_BASIS_SELECTION,
        string="Opening Rate Basis",
        readonly=True,
        copy=False,
        groups=ADMISSION_MONEY_READ,
    )
    rate_snapshot_daily_rate = fields.Float(
        string="Opening Daily Rate",
        digits=(16, 2),
        readonly=True,
        copy=False,
        groups=ADMISSION_MONEY_READ,
    )
    rate_snapshot_admission_fee = fields.Float(
        string="Admission Fee (recorded)",
        digits=(16, 2),
        readonly=True,
        copy=False,
        groups=ADMISSION_MONEY_READ,
    )

    # ------------------------------------------------------------------
    # SNAPSHOT
    # ------------------------------------------------------------------
    def _opening_rate_snapshot_values(self):
        """The minimum rate facts of the location the stay opens in: the daily
        rate, which catalogue level it came from, and the admission fee. No
        currency, no description, nothing else -- everything else is on the
        ward / room / bed rows the admission already names."""
        self.ensure_one()
        daily_rate, basis = resolve_location_rate(self.ward_id, self.room_id, self.bed_id)
        return {
            "rate_snapshot_taken": True,
            "rate_snapshot_origin": "workflow",
            "rate_snapshot_basis": basis,
            "rate_snapshot_daily_rate": daily_rate,
            "rate_snapshot_admission_fee": self.ward_id.admission_fee if self.ward_id else 0.0,
        }

    def _take_opening_rate_snapshot(self):
        """Freeze the opening rate. Called ONLY from _confirm_one(), inside the
        occupancy locks, after the bed has been taken.

        sudo() ON THE WRITE, behind the capability: the fields are readable
        only by the money roles, and the admitting clerk or nurse is not one of
        them. The capability, not the elevation, is the authority -- a sudo()
        write without it is refused by _assert_authoritative_write().
        """
        self.ensure_one()
        values = self._opening_rate_snapshot_values()
        with admission_rate_snapshot_capability():
            self.sudo().write(values)

    # ------------------------------------------------------------------
    # SEGMENTS
    # ------------------------------------------------------------------
    def _stay_transfers(self):
        """This admission's transfer history, oldest first.

        sudo(): the history is what the stay is priced from, and the transfer
        model's record rules are narrower than the admission's (a doctor reads
        the admission but only some of its transfers). It is a property of the
        data, and nothing from it is returned to a caller except through the
        amount-free financial status.
        """
        self.ensure_one()
        return (
            self.env["hospital.admission.transfer"]
            .sudo()
            .search([("admission_id", "=", self.id)], order="transfer_date asc, id asc")
        )

    def _stay_end(self, now=None):
        """(end, review_codes). NOW for an active stay; the discharge time for a
        finished one."""
        self.ensure_one()
        if self.state in ADMISSION_ACTIVE_STATES:
            return (now or fields.Datetime.now()), []
        if self.discharge_date:
            return self.discharge_date, []
        return self.admission_date, ["discharge_time_missing"]

    def _stay_segments(self, now=None):
        """The stay as ordered segments. [] for a draft or cancelled admission.

        Each segment: {index, start, end, ongoing, ward, room, bed,
        daily_rate, rate_basis, rate_snapshot, source, transfer}.
        Returns (segments, review_codes). Never raises on inconsistent data --
        it reports it, because a billing read must not become the reason a
        broken record cannot even be looked at.
        """
        self.ensure_one()
        admission = self.sudo()
        if admission.state not in STAY_STATES:
            return [], []
        start = admission.admission_date
        if not start:
            return [], ["admission_time_missing"]
        end, reasons = admission._stay_end(now)
        if end < start:
            end = start
        transfers = admission._stay_transfers()

        # Segment 0 opens where the FIRST transfer left from; with no transfer,
        # the patient never moved and the admission's own location is it.
        if transfers:
            first = transfers[0]
            opening = (first.from_ward_id, first.from_room_id, first.from_bed_id)
        else:
            opening = (admission.ward_id, admission.room_id, admission.bed_id)

        if admission.rate_snapshot_taken:
            opening_rate = admission.rate_snapshot_daily_rate
            opening_basis = admission.rate_snapshot_basis or "none"
            opening_snapshot = True
        else:
            opening_rate, opening_basis = resolve_location_rate(*opening)
            opening_snapshot = False
            reasons.append("rate_snapshot_missing")

        segments = [{
            "index": 0,
            "start": start,
            "ward": opening[0],
            "room": opening[1],
            "bed": opening[2],
            "daily_rate": opening_rate,
            "rate_basis": opening_basis,
            "rate_snapshot": opening_snapshot,
            "source": "admission",
            "transfer": None,
        }]

        previous_bed = opening[2]
        for transfer in transfers:
            moved_at = transfer.transfer_date
            if moved_at < start:
                reasons.append("transfer_before_admission")
                moved_at = start
            if moved_at > end:
                reasons.append("transfer_after_end")
                moved_at = end
            if previous_bed and transfer.from_bed_id and transfer.from_bed_id != previous_bed:
                reasons.append("transfer_chain_broken")
            if transfer.rate_snapshot_taken:
                rate, basis, snapshot = transfer.to_daily_rate, transfer.to_rate_basis or "none", True
            else:
                rate, basis = resolve_location_rate(
                    transfer.to_ward_id, transfer.to_room_id, transfer.to_bed_id
                )
                snapshot = False
                reasons.append("rate_snapshot_missing")
            segments.append({
                "index": len(segments),
                "start": moved_at,
                "ward": transfer.to_ward_id,
                "room": transfer.to_room_id,
                "bed": transfer.to_bed_id,
                "daily_rate": rate,
                "rate_basis": basis,
                "rate_snapshot": snapshot,
                "source": "transfer",
                "transfer": transfer,
            })
            previous_bed = transfer.to_bed_id

        for current, following in zip(segments, segments[1:]):
            current["end"] = following["start"]
            current["ongoing"] = False
        segments[-1]["end"] = end
        segments[-1]["ongoing"] = admission.state in ADMISSION_ACTIVE_STATES

        if (
            admission.state in ADMISSION_ACTIVE_STATES
            and admission.bed_id
            and segments[-1]["bed"] != admission.bed_id
        ):
            reasons.append("segment_location_mismatch")

        # Stable, de-duplicated, in first-seen order.
        return segments, list(dict.fromkeys(reasons))

    @api.model
    def _allocate_stay_days(self, segments):
        """Bill each 24-hour period ONCE, at the segment it STARTS in.

        Mutates and returns `segments`, adding `days` to each. The total equals
        stay_period_count(first start, last end) -- the legacy day count.
        """
        if not segments:
            return segments
        start = segments[0]["start"]
        total = stay_period_count(start, segments[-1]["end"])
        for segment in segments:
            segment["days"] = 0
        cursor = 0
        for k in range(total):
            period_start = start + DAY * k
            # Advance to the LAST segment that has started by this period's
            # start. Segment 0 starts at `start`, so one always has.
            while (
                cursor + 1 < len(segments)
                and segments[cursor + 1]["start"] <= period_start
            ):
                cursor += 1
            segments[cursor]["days"] += 1
        return segments

    def _bed_stay_breakdown(self, now=None):
        """THE bed/stay accrual. Every consumer -- the legacy bill, the preview
        fields on the form, the inpatient financial summary -- reads this.

        Returns {segments, admission_fee, bed_total, total, total_days,
        current_daily_rate, review_reasons}. Money is in the company currency,
        rounded by it.
        """
        self.ensure_one()
        admission = self.sudo()
        currency = admission.company_id.currency_id or self.env.company.currency_id
        segments, reasons = admission._stay_segments(now)
        self._allocate_stay_days(segments)
        for segment in segments:
            segment["amount"] = currency.round(segment["days"] * segment["daily_rate"])

        if not segments:
            fee = 0.0
        elif admission.rate_snapshot_taken:
            fee = admission.rate_snapshot_admission_fee
        else:
            opening_ward = segments[0]["ward"]
            fee = opening_ward.admission_fee if opening_ward else 0.0

        bed_total = currency.round(sum(segment["amount"] for segment in segments))
        fee = currency.round(fee)
        return {
            "segments": segments,
            "admission_fee": fee,
            "bed_total": bed_total,
            "total": currency.round(fee + bed_total),
            "total_days": sum(segment["days"] for segment in segments),
            "current_daily_rate": segments[-1]["daily_rate"] if segments else 0.0,
            "review_reasons": reasons,
        }
