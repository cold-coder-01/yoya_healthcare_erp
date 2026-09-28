import hashlib
import json
import math
import uuid

from psycopg2 import IntegrityError

from odoo import api, fields, models
from odoo.exceptions import UserError, AccessError, ValidationError

from odoo.addons.hospital_billing.models.charge_line import OPERATIONAL_MONEY_READ

from .admission_authority import (
    ADMISSION_ACTIVE_STATES,
    ADMISSION_ATTRIBUTION_FIELDS,
    ADMISSION_IDENTITY_FIELDS,
    ADMISSION_LOCATION_FIELDS,
    ADMISSION_MEDICAL_DISCHARGE_FIELDS,
    ADMISSION_MONEY_READ,
    ADMISSION_RATE_SNAPSHOT_FIELDS,
    DESK_FINAL_DISCHARGE_GROUPS,
    DESK_MEDICAL_DISCHARGE_OVERSIGHT_GROUPS,
    ADMISSION_TIMELINE_FIELDS,
    ENCOUNTER_ADMISSIBLE_STATES,
    DESK_ADMIT_GROUPS,
    DESK_CANCEL_REQUEST_GROUPS,
    DESK_REASON_MAX_LENGTH,
    DESK_REQUEST_OVERSIGHT_GROUPS,
    DESK_TOKEN_MAX_LENGTH,
    DESK_TRANSFER_GROUPS,
    G_DOCTOR,
    RATE_BASIS_SELECTION,
    RATE_SNAPSHOT_ORIGIN_SELECTION,
    AdmissionDeskError,
    AdmissionWorkflowError,
    admission_billing_capability,
    admission_operation_capability,
    admission_revision_capability,
    admission_transfer_history_capability,
    desk_code_for,
    has_admission_rate_snapshot_capability,
    has_medical_discharge_capability,
    medical_discharge_capability,
    has_admission_revision_capability,
    has_admission_transfer_history_capability,
    admission_location_capability,
    admission_workflow_capability,
    changed_fields,
    episode_closed_states,
    has_admission_billing_capability,
    has_admission_location_capability,
    has_admission_workflow_capability,
    resolve_location_rate,
)


class HospitalAdmission(models.Model):
    _name = "hospital.admission"
    _description = "Hospital Admission"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "admission_date desc, name desc"
    _rec_name = "name"

    name = fields.Char(
        readonly=True,
        copy=False,
        default="New",
    )
    patient_id = fields.Many2one(
        "hospital.patient",
        required=True,
        ondelete="restrict",
        index=True,
        tracking=True,
    )
    # ── Encounter bridge ────────────────────────────────────────
    #
    # The episode of care this stay belongs to. NOT required at database level,
    # deliberately: the live UAT database holds one historical admission
    # (ADM00001, discharged) that predates this field, and requiring a column
    # it cannot have would either fail the upgrade or force this module to
    # manufacture a visit that never happened. The requirement is enforced by
    # _check_active_admission_has_encounter() for ACTIVE admissions only, so
    # history stays honest and the future stays linked.
    encounter_id = fields.Many2one(
        "hospital.encounter",
        string="Visit",
        ondelete="restrict",
        copy=False,
        index=True,
        tracking=True,
        help="The episode of care this inpatient stay belongs to. Adopted from "
        "the patient's open visit when the admission is confirmed.",
    )
    company_id = fields.Many2one(
        "res.company",
        required=True,
        index=True,
        default=lambda self: self.env.company,
        tracking=True,
    )
    physician_id = fields.Many2one(
        "hospital.doctor",
        string="Physician",
        tracking=True,
    )
    appointment_id = fields.Many2one(
        "hospital.appointment",
        string="Appointment",
        tracking=True,
    )
    diagnosis_id = fields.Many2one(
        "hospital.patient.diagnosis",
        string="Diagnosis",
        tracking=True,
    )
    admission_date = fields.Datetime(
        default=fields.Datetime.now,
        tracking=True,
    )
    expected_discharge_date = fields.Datetime(tracking=True)
    discharge_date = fields.Datetime(tracking=True)
    ward_id = fields.Many2one(
        "hospital.ward",
        string="Ward",
        tracking=True,
    )
    room_id = fields.Many2one(
        "hospital.room",
        string="Room",
        tracking=True,
    )
    bed_id = fields.Many2one(
        "hospital.bed",
        string="Bed",
        index=True,
        tracking=True,
    )
    admission_reason = fields.Text()
    discharge_summary = fields.Text()
    state = fields.Selection(
        [
            ("draft", "Draft"),
            ("admitted", "Admitted"),
            ("transferred", "Transferred"),
            ("discharged", "Discharged"),
            ("cancelled", "Cancelled"),
        ],
        default="draft",
        required=True,
        index=True,
        tracking=True,
    )
    active = fields.Boolean(default=True)
    transfer_ids = fields.One2many(
        "hospital.admission.transfer",
        "admission_id",
        string="Transfers",
    )
    notes = fields.Text()

    # ── Desk concurrency (Admissions Slice 2) ───────────────────
    #
    # Optimistic concurrency for the Admissions / Doctor Desk mutations. Starts
    # at 0; every successful desk mutation raises it by exactly ONE, and a
    # replayed request (same operation token) does not raise it again. The
    # client echoes the revision it loaded; a stale one is refused with
    # admission_revision_conflict rather than acting on state the operator
    # never saw. Writable only under admission_revision_capability().
    workflow_revision = fields.Integer(default=0, readonly=True, copy=False)

    # ── Medical discharge (Admissions Slice 4) ──────────────────
    #
    # The doctor's CLINICAL decision that the patient may leave. Not a state:
    # the patient is still in the bed, still on the ward census, still the
    # bed's owner, until the administrative discharge succeeds. Written only by
    # _desk_request_medical_discharge() under medical_discharge_capability().
    medical_discharge_ready = fields.Boolean(
        string="Medically Ready for Discharge", readonly=True, copy=False, tracking=True,
    )
    medical_discharge_at = fields.Datetime(
        string="Medical Discharge Requested At", readonly=True, copy=False,
    )
    medical_discharge_by_id = fields.Many2one(
        "res.users", string="Medical Discharge By", readonly=True, copy=False,
    )

    # ── Billing linkage ─────────────────────────────────────────
    bill_id = fields.Many2one(
        "hospital.patient.bill",
        string="Bill",
        readonly=True,
        copy=False,
        tracking=True,
    )
    bill_count = fields.Integer(
        compute="_compute_bill_count",
        string="Bills",
    )
    currency_id = fields.Many2one(
        "res.currency",
        default=lambda self: self.env.company.currency_id,
    )
    billing_state = fields.Selection(
        [
            ("not_billed", "Not Billed"),
            ("billed", "Billed"),
            ("partially_paid", "Partially Paid"),
            ("paid", "Paid"),
        ],
        compute="_compute_billing_state",
        string="Billing Status",
        store=True,
    )

    # ── Billing preview (computed) ───────────────────────────────
    #
    # MONEY, so readable only by the roles hospital_billing already lets see
    # operational money (OPERATIONAL_MONEY_READ: cashier, receptionist,
    # accountant, manager, system administrator). Before Admissions Slice 4
    # every clinical role that could open an admission in the backend saw the
    # stay's price on the Billing tab and the printed summary. stay_days is a
    # count of days, not money, and stays visible.
    stay_days = fields.Float(
        compute="_compute_stay_days",
        string="Stay Days",
        digits=(16, 2),
        store=False,
    )
    admission_fee_amount = fields.Float(
        compute="_compute_admission_billing",
        groups=OPERATIONAL_MONEY_READ,
        string="Admission Fee",
        digits=(16, 2),
        store=False,
    )
    daily_rate_amount = fields.Float(
        compute="_compute_admission_billing",
        groups=OPERATIONAL_MONEY_READ,
        string="Daily Rate",
        digits=(16, 2),
        store=False,
    )
    bed_charge_amount = fields.Float(
        compute="_compute_admission_billing",
        groups=OPERATIONAL_MONEY_READ,
        string="Bed Charge",
        digits=(16, 2),
        store=False,
    )
    total_admission_charge = fields.Float(
        compute="_compute_admission_billing",
        groups=OPERATIONAL_MONEY_READ,
        string="Total Admission Charge",
        digits=(16, 2),
        store=False,
    )

    # ------------------------------------------------------------------
    # DATABASE INVARIANTS
    # ------------------------------------------------------------------
    #
    # Odoo's _sql_constraints cannot express a PARTIAL unique index, and these
    # two invariants are inherently partial: a patient may have any number of
    # discharged admissions, and a bed may have been used by any number of
    # past stays. Only the ACTIVE ones must be unique.
    #
    # WHY THE DATABASE AND NOT ONLY PYTHON. The advisory locks in
    # _lock_for_occupancy() serialize the application path. These indexes cover
    # everything that is NOT the application path: a migration, the Odoo shell,
    # a data import, a direct psql session, and hospital_insurance -- which
    # extends this model from outside this repository. A check that lives only
    # in Python is a check that a future caller can forget to call.
    #
    # Named with the _idx suffix rather than registered as SQL constraints so
    # that Odoo's constraint bookkeeping does not try to drop and recreate them
    # as ordinary uniques on every upgrade.
    def init(self):
        super_init = getattr(super(), "init", None)
        if super_init:
            super_init()
        self.env.cr.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS hospital_admission_one_active_per_patient_idx
            ON hospital_admission (patient_id)
            WHERE state IN ('admitted', 'transferred')
            """
        )
        self.env.cr.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS hospital_admission_one_active_per_bed_idx
            ON hospital_admission (bed_id)
            WHERE bed_id IS NOT NULL AND state IN ('admitted', 'transferred')
            """
        )

    # ── Computed helpers ─────────────────────────────────────────

    # ------------------------------------------------------------------
    # AUDIT PROVENANCE
    # ------------------------------------------------------------------
    @api.model
    def _audit(self, **kwargs):
        """Write an audit row whose ACTOR cannot be chosen by the caller.

        hospital.audit.log.create_log() reads the actor as

            self.env.context.get("audit_user_id") or self.env.user.id

        and env.context is attacker-controlled on every RPC call, so any caller
        could attribute their own act to somebody else simply by sending that
        key. Every workflow in this module would otherwise inherit a forgeable
        signature on exactly the records whose provenance matters most: who
        admitted a patient, who moved them, who sent them home.

        The key is stripped here rather than fixed in hospital_management
        because that model is shared by every clinical module in the system and
        changing its contract belongs in a slice that can regression-test all
        of them. This is the narrow fix: admissions stop offering the channel,
        and the wider one is recorded in the Slice 0 report.
        """
        log = self.env["hospital.audit.log"]
        if "audit_user_id" in log.env.context:
            log = log.with_context(audit_user_id=False)
        return log.create_log(**kwargs)

    def _compute_bill_count(self):
        for rec in self:
            rec.bill_count = 1 if rec.bill_id else 0

    @api.depends("bill_id", "bill_id.state", "bill_id.amount_paid", "bill_id.amount_total")
    def _compute_billing_state(self):
        for rec in self:
            if not rec.bill_id:
                rec.billing_state = "not_billed"
            elif rec.bill_id.state == "paid":
                rec.billing_state = "paid"
            elif rec.bill_id.state == "partially_paid":
                rec.billing_state = "partially_paid"
            else:
                rec.billing_state = "billed"

    @api.depends("admission_date", "discharge_date")
    def _compute_stay_days(self):
        for rec in self:
            start = rec.admission_date
            end = rec.discharge_date or fields.Datetime.now()
            if start and end and end > start:
                hours = (end - start).total_seconds() / 3600.0
                rec.stay_days = max(1, math.ceil(hours / 24))
            else:
                rec.stay_days = 1 if start else 0

    @api.depends(
        "state", "stay_days", "admission_date", "discharge_date",
        "ward_id", "ward_id.admission_fee", "ward_id.daily_ward_rate",
        "room_id", "room_id.daily_room_rate",
        "bed_id", "bed_id.daily_bed_rate",
        "transfer_ids", "transfer_ids.transfer_date",
    )
    def _compute_admission_billing(self):
        """SEGMENTED (Admissions Slice 3). The retroactive-rate defect Slice 0
        pinned is fixed here: once a stay has started, every figure comes from
        _bed_stay_breakdown(), which prices each segment of the stay at the
        rate recorded when that segment opened.

          admission_fee_amount   the OPENING ward's fee, recorded at admission
          daily_rate_amount      the CURRENT segment's recorded daily rate
          bed_charge_amount      the sum of every segment's own charge
          total_admission_charge fee + bed charge

        A DRAFT has no stay yet, so it keeps the legacy live preview: the rate
        of the location it names, times the planned stay. That is an estimate,
        and nothing bills from it.
        """
        for rec in self:
            if rec.state in ("draft", "cancelled"):
                daily_rate, _basis = resolve_location_rate(rec.ward_id, rec.room_id, rec.bed_id)
                rec.admission_fee_amount = rec.ward_id.admission_fee if rec.ward_id else 0.0
                rec.daily_rate_amount = daily_rate
                rec.bed_charge_amount = daily_rate * rec.stay_days
                rec.total_admission_charge = rec.admission_fee_amount + rec.bed_charge_amount
                continue
            breakdown = rec._bed_stay_breakdown()
            rec.admission_fee_amount = breakdown["admission_fee"]
            rec.daily_rate_amount = breakdown["current_daily_rate"]
            rec.bed_charge_amount = breakdown["bed_total"]
            rec.total_admission_charge = breakdown["total"]

    # ------------------------------------------------------------------
    # CONSTRAINTS
    # ------------------------------------------------------------------
    @api.constrains("ward_id", "room_id", "bed_id")
    def _check_location_coherence(self):
        """The ward, the room and the bed must describe ONE place.

        The form view supplies domains that keep a human inside the hierarchy,
        but a view domain is a client-side hint: an ORM call, an RPC payload or
        a future API can set ward_id to Ward A and bed_id to a bed in Ward B
        and nothing objected. Occupancy bookkeeping, the bed board and the
        daily rate all read these three fields as if they agreed.
        """
        for rec in self:
            if rec.room_id and rec.ward_id and rec.room_id.ward_id != rec.ward_id:
                raise AdmissionWorkflowError("admission_location_incoherent")
            if rec.bed_id:
                if rec.room_id and rec.bed_id.room_id != rec.room_id:
                    raise AdmissionWorkflowError("admission_location_incoherent")
                if rec.ward_id and rec.bed_id.ward_id != rec.ward_id:
                    raise AdmissionWorkflowError("admission_location_incoherent")
                # A bed with no room or ward named on the admission still has
                # to resolve: an admission that names a bed and nothing else
                # would bill at no rate and appear on no ward's board.
                if not rec.room_id or not rec.ward_id:
                    raise AdmissionWorkflowError("admission_location_incoherent")

    @api.constrains("company_id", "ward_id", "room_id", "bed_id")
    def _check_location_company(self):
        """A patient may not be admitted into another company's bed."""
        for rec in self:
            for location in (rec.ward_id, rec.room_id, rec.bed_id):
                if location and location.company_id and location.company_id != rec.company_id:
                    raise AdmissionWorkflowError("admission_company_mismatch")

    @api.constrains("state", "encounter_id", "patient_id", "company_id")
    def _check_active_admission_has_encounter(self):
        """An ACTIVE admission is an episode of care, so it must name one.

        Scoped to active states on purpose. A discharged or cancelled row from
        before this field existed is history: it may keep an empty visit, and
        ADM00001 in the UAT database does. What must never happen is a patient
        physically in a bed whose stay belongs to no episode -- that is the
        state in which nothing can be billed, claimed or handed over.
        """
        closed = episode_closed_states(self.env["hospital.encounter"])
        for rec in self:
            if rec.state not in ADMISSION_ACTIVE_STATES:
                continue
            encounter = rec.encounter_id
            if not encounter:
                raise AdmissionWorkflowError("admission_encounter_required")
            # sudo: coherence is a property of the data, not of what the
            # writing user may read. It only ever refuses.
            encounter = encounter.sudo()
            if encounter.patient_id != rec.patient_id:
                raise AdmissionWorkflowError("admission_encounter_patient_mismatch")
            if encounter.company_id != rec.company_id:
                raise AdmissionWorkflowError("admission_encounter_company_mismatch")
            if encounter.state in closed:
                raise AdmissionWorkflowError("admission_encounter_closed")

    @api.constrains("admission_date", "discharge_date")
    def _check_timeline(self):
        for rec in self:
            if (
                rec.discharge_date
                and rec.admission_date
                and rec.discharge_date < rec.admission_date
            ):
                raise ValidationError(
                    "The discharge time cannot precede the admission time."
                )

    # ------------------------------------------------------------------
    # LOCKING
    # ------------------------------------------------------------------
    def _lock_for_occupancy(self, beds):
        """THE deterministic lock order for every occupancy mutation.

          1. one advisory lock per bed involved, ASCENDING BY ID
          2. the admission row, FOR UPDATE
          3. every bed row in one statement, ORDER BY id, FOR UPDATE
          4. every cache invalidated, so each later check reads locked rows

        WHY ASCENDING ID, ALWAYS. A transfer touches two beds. If one
        transaction locked old-then-new while another locked new-then-old, the
        two deadlock. Sorting by id gives every transaction in the system the
        same order, so the second one waits instead of dying. This is the same
        rule hospital_pharmacy's dispense lock and hospital_inventory's FEFO
        lock already follow.

        WHY AN ADVISORY LOCK AS WELL AS FOR UPDATE. FOR UPDATE can only lock a
        row that already exists. Two transactions confirming two DIFFERENT
        admissions into the SAME bed both lock their own admission row happily
        and then race on the bed. The advisory lock is keyed on the BED, so it
        serializes them before either reads the bed's state. It is
        transaction-scoped and releases on commit or rollback with no cleanup,
        which is the same mechanism hospital.billing.account,
        hospital.patient.payer and hospital.encounter's episode guard use.
        """
        self.ensure_one()
        cr = self.env.cr
        self.env.flush_all()

        bed_ids = sorted({bed.id for bed in beds if bed})
        for bed_id in bed_ids:
            cr.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                ["hospital.bed.occupancy:%s:%s" % (self.company_id.id, bed_id)],
            )

        cr.execute(
            "SELECT id FROM hospital_admission WHERE id = %s FOR UPDATE", (self.id,)
        )
        if not cr.fetchone():
            raise AdmissionWorkflowError("admission_state_write_refused")
        # A no-op write of the header, the pattern the Laboratory and Pharmacy
        # desks already use. Odoo runs at REPEATABLE READ with the snapshot
        # taken at the request's first query, so a concurrent waiter that has
        # already read this admission would otherwise act on stale values. The
        # no-op makes its lock fail with a serialization error that Odoo's HTTP
        # layer replays in a fresh snapshot.
        cr.execute(
            "UPDATE hospital_admission SET write_date = write_date WHERE id = %s",
            (self.id,),
        )

        if bed_ids:
            cr.execute(
                "SELECT id FROM hospital_bed WHERE id IN %s ORDER BY id FOR UPDATE",
                (tuple(bed_ids),),
            )

        self.env.invalidate_all()

    # ------------------------------------------------------------------
    # ENCOUNTER ADOPTION
    # ------------------------------------------------------------------
    def _eligible_encounters(self):
        """The patient's open episodes in this company, newest first.

        sudo(): whether a patient already has an open visit is a property of
        the data, not of what the admitting user may read -- a receptionist and
        a doctor have different row visibility on hospital.encounter, and an
        open visit is an open visit either way. It only ever feeds a refusal or
        an adoption the caller then works through their own ACLs.
        """
        self.ensure_one()
        closed = episode_closed_states(self.env["hospital.encounter"])
        return (
            self.env["hospital.encounter"]
            .sudo()
            .search(
                [
                    ("patient_id", "=", self.patient_id.id),
                    ("company_id", "=", self.company_id.id),
                    ("state", "not in", list(closed)),
                ],
                order="id desc",
            )
        )

    def _adopt_inpatient_encounter(self):
        """Bind this admission to the patient's episode of care and retype it.

        WHY ADOPT AND NOT CREATE. yoya_reception_bridge enforces ONE ACTIVE
        EPISODE PER PATIENT in hospital.encounter.create(), under an advisory
        lock, because two live episodes for one person means two consultation
        charges, two cashier liabilities and two draws against the same
        benefit. An inpatient stay is not a second episode of care -- it is
        what happens to the episode the patient already has. Creating a second
        encounter here would either be refused by that guard or, if it somehow
        were not, would be exactly the duplication the guard exists to prevent.

        So this module never calls hospital.encounter.create(). It adopts.

        WHEN THERE IS NOTHING TO ADOPT, IT REFUSES. That is the smallest safe
        behaviour and it is a deliberate choice over the alternative. Opening
        an episode from here would mean inventing a visit with no registration,
        no payer capture, no triage and no front-desk provenance -- a record
        that looks like a normal visit and is not one. The patient is
        registered at the front desk first, which is where an episode is
        supposed to begin.
        """
        self.ensure_one()

        if self.encounter_id:
            encounter = self.encounter_id.sudo()
        else:
            candidates = self._eligible_encounters()
            if not candidates:
                raise AdmissionWorkflowError("admission_encounter_required")
            if len(candidates) > 1:
                raise AdmissionWorkflowError("admission_encounter_ambiguous")
            encounter = candidates

        closed = episode_closed_states(self.env["hospital.encounter"])
        if encounter.patient_id != self.patient_id:
            raise AdmissionWorkflowError("admission_encounter_patient_mismatch")
        if encounter.company_id != self.company_id:
            raise AdmissionWorkflowError("admission_encounter_company_mismatch")
        if encounter.state in closed:
            raise AdmissionWorkflowError("admission_encounter_closed")
        if encounter.state not in ENCOUNTER_ADMISSIBLE_STATES:
            raise AdmissionWorkflowError("admission_encounter_closed")

        if not self.encounter_id:
            # The identity guard freezes encounter_id, so binding it goes
            # through the same workflow capability that owns state. Written via
            # self.write() rather than super().write() so the guard actually
            # runs and grants -- reaching past it would mean this one path was
            # never checked at all.
            with admission_workflow_capability():
                self.write({"encounter_id": encounter.id})

        # RETYPE. The episode is now an inpatient one. Written through the
        # encounter's own write(), so its locked-state guard, its audit trail
        # and yoya_reception_bridge's bypass guard all still apply.
        #
        # sudo() on the retype: a receptionist holds write on hospital.encounter
        # but a nurse's record rule may not reach this row, and the retype is a
        # consequence of an admission the caller was already allowed to confirm,
        # not an independent edit of somebody else's visit.
        if encounter.encounter_type != "inpatient":
            encounter.write({"encounter_type": "inpatient"})
            self._audit(
                patient_id=self.patient_id.id,
                model_name="hospital.encounter",
                record_id=encounter.id,
                action_type="update",
                old_value=encounter.encounter_type,
                new_value="inpatient",
                description=(
                    f"Visit {encounter.name} retyped as inpatient on admission {self.name}"
                ),
            )
        return encounter

    # ------------------------------------------------------------------
    # BILLING ACTIONS
    # ------------------------------------------------------------------

    def _may_read_stay_money(self):
        """For the printed summary: may this user see the stay's prices?"""
        user = self.env.user
        return self.env.su or any(
            user.has_group(group) for group in OPERATIONAL_MONEY_READ.split(",")
        )

    def action_generate_admission_bill(self):
        """LEGACY bill -- for admissions with NO visit only (Admissions Slice 4).

        An admission bound to a visit posts its stay to that visit's billing
        account as one charge per bed-day (_sync_stay_charges), which is where
        it is settled. A legacy hospital.patient.bill for the same stay would
        bill it a second time, so it is refused. Historical visit-less
        admissions keep this path, and every legacy bill stays readable.
        """
        self.ensure_one()
        if self.encounter_id:
            raise AdmissionWorkflowError("admission_legacy_bill_superseded")
        if self.state != "discharged":
            raise UserError("Admission bill can only be generated after discharge.")
        # Serialize bill generation against itself. Two operators pressing
        # Generate Bill at the same moment both read bill_id = False and both
        # created a bill; the lock plus the re-read inside it makes the second
        # one see the first one's work.
        self._lock_for_occupancy(self.env["hospital.bed"])
        if self.bill_id:
            raise UserError(
                f"This admission has already been billed. Bill reference: {self.bill_id.name}"
            )
        if not self.discharge_date:
            raise UserError("Discharge date is not set. Please discharge the patient first.")
        if not self.patient_id:
            raise UserError("Patient is required to generate a bill.")

        stay_days = self.stay_days
        if stay_days <= 0:
            raise UserError(
                "Invalid stay duration. Please check admission and discharge dates."
            )

        # ONE LINE PER BILLED SEGMENT (Admissions Slice 3). The legacy code
        # wrote one line of (whole stay x current rate); every segment now
        # carries its own recorded rate and its own days, so a transfer can no
        # longer reprice the stay before it. Segments that bill zero days (a
        # stay entirely inside a period already started elsewhere) or have no
        # rate produce no line, as a zero rate did before.
        breakdown = self._bed_stay_breakdown()
        bill_lines = []

        if breakdown["admission_fee"] > 0:
            bill_lines.append((0, 0, {
                "description": f"Admission Fee - {self.name}",
                "source_type": "admission",
                "quantity": 1.0,
                "unit_price": breakdown["admission_fee"],
                "source_model": "hospital.admission",
                "source_record_id": self.id,
                "sequence": 10,
            }))

        for segment in breakdown["segments"]:
            if segment["days"] <= 0 or segment["daily_rate"] <= 0:
                continue
            parts = [
                segment["ward"].display_name if segment["ward"] else "",
                segment["room"].name if segment["room"] else "",
                segment["bed"].display_name if segment["bed"] else "",
            ]
            location_desc = " / ".join(p for p in parts if p) or self.name
            bill_lines.append((0, 0, {
                "description": (
                    f"Bed Stay Charge - {location_desc} - {segment['days']} day(s)"
                ),
                "source_type": "admission",
                "quantity": float(segment["days"]),
                "unit_price": segment["daily_rate"],
                "source_model": "hospital.admission",
                "source_record_id": self.id,
                "sequence": 20 + segment["index"],
            }))

        if not bill_lines:
            raise UserError(
                "No charges found. Please configure Admission Fee or Daily Rate on the ward."
            )

        bill = self.env["hospital.patient.bill"].create({
            "patient_id": self.patient_id.id,
            "physician_id": self.physician_id.id if self.physician_id else False,
            "appointment_id": self.appointment_id.id if self.appointment_id else False,
            "bill_date": fields.Date.context_today(self),
            "cashier_id": self.env.user.id,
            "currency_id": (
                self.currency_id.id
                or self.env.company.currency_id.id
            ),
            "notes": f"Generated from admission {self.name}",
            "line_ids": bill_lines,
        })

        with admission_billing_capability():
            self.write({"bill_id": bill.id})

        self._audit(
            patient_id=self.patient_id.id,
            model_name=self._name,
            record_id=self.id,
            action_type="update",
            description=f"Admission bill generated: {bill.name} for admission {self.name}",
        )

        return {
            "type": "ir.actions.act_window",
            "name": "Patient Bill",
            "res_model": "hospital.patient.bill",
            "res_id": bill.id,
            "view_mode": "form",
            "target": "current",
        }

    def action_view_admission_bill(self):
        self.ensure_one()
        if not self.bill_id:
            raise UserError("No bill has been generated for this admission yet.")
        return {
            "type": "ir.actions.act_window",
            "name": "Patient Bill",
            "res_model": "hospital.patient.bill",
            "res_id": self.bill_id.id,
            "view_mode": "form",
            "target": "current",
        }

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            # An admission is BORN a draft. Creating one directly as 'admitted'
            # would skip encounter adoption, the bed availability check, the
            # locks and the occupancy write in a single call -- and would leave
            # a patient in a bed the bed does not know about.
            requested_state = vals.get("state")
            if (
                requested_state
                and requested_state != "draft"
                and not has_admission_workflow_capability()
            ):
                raise AdmissionWorkflowError("admission_state_write_refused")
            if vals.get("workflow_revision") and not has_admission_revision_capability():
                # An admission starts at revision 0; only the desk workflow moves it.
                raise AdmissionWorkflowError("admission_state_write_refused")
            if (
                any(vals.get(name) for name in ADMISSION_MEDICAL_DISCHARGE_FIELDS)
                and not has_medical_discharge_capability()
            ):
                raise AdmissionWorkflowError("admission_medical_discharge_write_refused")
            if (
                any(vals.get(name) for name in ADMISSION_RATE_SNAPSHOT_FIELDS)
                and not has_admission_rate_snapshot_capability()
            ):
                # A stay rate is recorded when the patient enters a bed, never
                # supplied by whoever creates the row.
                raise AdmissionWorkflowError("admission_rate_snapshot_write_refused")
            if vals.get("name", "New") == "New":
                vals["name"] = (
                    self.env["ir.sequence"].next_by_code("hospital.admission.sequence") or "New"
                )
        records = super().create(vals_list)
        for rec in records:
            self._audit(
                patient_id=rec.patient_id.id,
                model_name=self._name,
                record_id=rec.id,
                action_type="create",
                description=f"Admission created: {rec.name}",
            )
        return records

    def write(self, vals):
        self._assert_authoritative_write(vals)
        old_states = {rec.id: rec.state for rec in self}
        result = super().write(vals)
        if "state" in vals:
            # Only REAL transitions are audited. The guard lets a write that
            # echoes the current state through -- the form view posts back
            # every field it loaded -- and logging those as "discharged ->
            # discharged" would make an audit trail read like a transition
            # happened when nothing moved, which is exactly the trail a UAT
            # or an incident review has to be able to trust.
            for rec in self.filtered(lambda r: r.state != old_states.get(r.id)):
                self._audit(
                    patient_id=rec.patient_id.id,
                    model_name=self._name,
                    record_id=rec.id,
                    action_type="state_change",
                    old_value=old_states.get(rec.id),
                    new_value=rec.state,
                    description=f"Admission state changed: {rec.name}",
                )
        return result

    def _assert_authoritative_write(self, vals):
        """STATE, IDENTITY, LOCATION AND TIMELINE AUTHORITY.

        Runs before super() and looks at neither the context nor sudo.

        Three tiers, because an admission is not equally settled at every point
        in its life:

          * draft      -- being prepared. Location, attribution and dates are
                          freely editable; this is the point of a draft.
          * active     -- a patient is physically in a bed. Identity,
                          attribution, location and dates are all settled;
                          location moves only through Transfer.
          * terminal   -- discharged or cancelled. History. Nothing moves.

        Writing the value a record already has is not a change and is allowed,
        so the Odoo form view -- which posts back every field it loaded -- can
        still save an admitted admission.
        """
        if not vals:
            return

        if "workflow_revision" in vals and not has_admission_revision_capability():
            for rec in self:
                if vals["workflow_revision"] != rec.workflow_revision:
                    raise AdmissionWorkflowError("admission_state_write_refused")

        for rec in self:
            # ── state ──────────────────────────────────────────────
            if (
                "state" in vals
                and not has_admission_workflow_capability()
                and vals["state"] != rec.state
            ):
                raise AdmissionWorkflowError("admission_state_write_refused")

            # ── bill link ──────────────────────────────────────────
            if (
                "bill_id" in vals
                and not has_admission_billing_capability()
                and changed_fields(rec, ("bill_id",), vals)
            ):
                raise AdmissionWorkflowError("admission_bill_write_refused")

            # ── stay rate snapshot: recorded by the workflow only ──
            #
            # Checked in EVERY tier, drafts included: a draft carrying a
            # hand-written rate would be admitted at it. Compared on a sudo()
            # view because the fields are readable only by the money roles,
            # and whether a write CHANGES them is not a question of who asks.
            if (
                changed_fields(rec.sudo(), ADMISSION_RATE_SNAPSHOT_FIELDS, vals)
                and not has_admission_rate_snapshot_capability()
            ):
                raise AdmissionWorkflowError("admission_rate_snapshot_write_refused")

            # ── medical discharge: the doctor's workflow only ──────
            if (
                changed_fields(rec, ADMISSION_MEDICAL_DISCHARGE_FIELDS, vals)
                and not has_medical_discharge_capability()
            ):
                raise AdmissionWorkflowError("admission_medical_discharge_write_refused")

            # ── identity: settled from creation, never relinked ────
            #
            # encounter_id is bound once by _adopt_inpatient_encounter() under
            # the workflow capability, so the guard admits that one path and
            # nothing else.
            identity = changed_fields(rec, ADMISSION_IDENTITY_FIELDS, vals)
            if identity and not has_admission_workflow_capability():
                if rec.state != "draft" or identity != ["encounter_id"]:
                    raise AdmissionWorkflowError("admission_identity_write_refused")

            if rec.state == "draft":
                # A draft is a work in progress. Location, attribution and the
                # planned admission date are meant to be edited here.
                continue

            # ── attribution: settled once the patient is in a bed ──
            if changed_fields(rec, ADMISSION_ATTRIBUTION_FIELDS, vals):
                raise AdmissionWorkflowError("admission_attribution_write_refused")

            # ── location: Transfer only ────────────────────────────
            if (
                changed_fields(rec, ADMISSION_LOCATION_FIELDS, vals)
                and not has_admission_location_capability()
            ):
                raise AdmissionWorkflowError("admission_location_write_refused")

            # ── timeline: stamped by workflow only ─────────────────
            if (
                changed_fields(rec, ADMISSION_TIMELINE_FIELDS, vals)
                and not has_admission_workflow_capability()
            ):
                raise AdmissionWorkflowError("admission_timeline_write_refused")

    def _write_state(self, new_state, extra=None):
        """THE ONLY way hospital.admission.state moves.

        Every transition method funnels through here, so the capability is
        raised in exactly one place and released in a `finally` even if the
        write raises.
        """
        values = dict(extra or {})
        values["state"] = new_state
        with admission_workflow_capability():
            return self.write(values)

    def _write_location(self, ward, room, bed):
        """THE ONLY way an active admission's location moves. Transfer only."""
        with admission_location_capability():
            return self.write(
                {
                    "ward_id": ward.id if ward else False,
                    "room_id": room.id if room else False,
                    "bed_id": bed.id if bed else False,
                }
            )

    # ------------------------------------------------------------------
    # BED OWNERSHIP
    # ------------------------------------------------------------------
    def _release_bed(self, reason):
        """Free this admission's bed -- and ONLY if this admission holds it.

        THE BUG THIS FIXES. action_discharge and the transfer wizard used to
        write {'state': 'available', 'current_admission_id': False} on
        self.bed_id unconditionally. After any double-booking, discharging
        admission A therefore freed a bed that admission B was lying in, and
        left B active in a bed marked available -- stale occupancy in the
        direction nobody looks for. action_cancel already had the ownership
        check; the other two did not. Now there is one method and all three
        use it.

        Returns False when there is nothing to release, so cancelling a draft
        that merely names a bed is not an error.
        """
        self.ensure_one()
        bed = self.bed_id
        if not bed:
            return False
        if bed.current_admission_id.id != self.id:
            raise AdmissionWorkflowError("admission_bed_not_owned")
        bed._set_occupancy("available", admission=None)
        self._audit(
            patient_id=self.patient_id.id,
            model_name="hospital.bed",
            record_id=bed.id,
            action_type="update",
            old_value="occupied",
            new_value="available",
            description=f"Bed {bed.display_name} freed on {reason} of admission {self.name}",
        )
        return True

    def _occupy_bed(self, bed):
        """Take a bed for this admission, having already locked it.

        Everything here is re-read INSIDE the caller's lock. The old code read
        bed.state, decided, and then wrote -- with nothing holding the bed in
        between, so two transactions both read 'available' and both wrote
        'occupied'.
        """
        self.ensure_one()
        if bed.state != "available":
            raise AdmissionWorkflowError("admission_bed_not_available")
        if bed.current_admission_id and bed.current_admission_id.id != self.id:
            raise AdmissionWorkflowError("admission_bed_owned_by_other")
        # The pointer can be stale in the other direction too: a bed marked
        # available while an active admission still names it. Reading from the
        # admission side catches that.
        holder = bed._active_admission()
        if holder and holder.id != self.id:
            raise AdmissionWorkflowError("admission_bed_owned_by_other")
        bed._set_occupancy("occupied", admission=self)
        self._audit(
            patient_id=self.patient_id.id,
            model_name="hospital.bed",
            record_id=bed.id,
            action_type="update",
            old_value="available",
            new_value="occupied",
            description=f"Bed {bed.display_name} occupied by admission {self.name}",
        )
        return True

    def _assert_no_other_active_admission(self):
        """One patient, one active stay.

        The partial unique index is the real guarantee; this check exists so
        the caller gets a sentence rather than a database error, and so the
        refusal happens before any bed has been touched.

        sudo(): another admission for this patient may be outside the caller's
        record rules -- a nurse scoped to one ward cannot see a stay on
        another. A duplicate is a duplicate either way, and this only refuses.
        """
        self.ensure_one()
        other = (
            self.sudo()
            .search(
                [
                    ("patient_id", "=", self.patient_id.id),
                    ("state", "in", list(ADMISSION_ACTIVE_STATES)),
                    ("id", "!=", self.id),
                ],
                limit=1,
            )
        )
        if other:
            raise AdmissionWorkflowError("admission_patient_already_admitted")

    # ------------------------------------------------------------------
    # WORKFLOW
    # ------------------------------------------------------------------

    def action_confirm_admission(self):
        """draft -> admitted. Atomic, locked, encounter-bound.

        ORDER MATTERS. The batch de-duplication runs FIRST, before any record
        is touched, because _assert_no_other_active_admission() reads the
        DATABASE: in a single confirm of two drafts naming one bed, neither row
        is active yet when the other is checked, so both would pass and the
        second would only fail at the index -- after the first had already
        written occupancy. hospital.encounter.create() has the same two-pass
        shape for the same reason.
        """
        self._assert_no_duplicate_target_beds()
        for rec in self:
            rec._confirm_one()
        return True

    def _assert_no_duplicate_target_beds(self):
        """Refuse a multi-record confirm that names one bed twice."""
        seen = set()
        for rec in self:
            bed_id = rec.bed_id.id
            if not bed_id:
                continue
            if bed_id in seen:
                raise AdmissionWorkflowError("admission_duplicate_bed_in_batch")
            seen.add(bed_id)

    def _confirm_one(self):
        self.ensure_one()
        if self.state != "draft":
            raise UserError("Only Draft admissions can be confirmed.")

        self._lock_for_occupancy(self.bed_id)

        # Re-read inside the lock. Everything below this line sees locked rows.
        if self.state != "draft":
            raise UserError("Only Draft admissions can be confirmed.")

        self._assert_no_other_active_admission()
        self._adopt_inpatient_encounter()

        if self.bed_id:
            # The coherence and company constraints run on write, but the bed
            # is occupied before the state write, so they are asserted here too
            # rather than letting an incoherent location take a bed first.
            self._check_location_coherence()
            self._check_location_company()
            self._occupy_bed(self.bed_id)

        self._write_state("admitted")
        # The stay's opening rate, frozen in the same locked transaction that
        # put the patient in the bed (Admissions Slice 3). From here on, an
        # edit to the catalogue rate cannot reprice this segment.
        self._take_opening_rate_snapshot()
        return True

    def action_discharge(self):
        """The backend Discharge button, and any ORM caller.

        ADMISSIONS SLICE 4: for a USER this is the full administrative
        discharge -- _finalize_discharge(), with the doctor's medical readiness,
        the settlement gate, the final stay posting, bed release and encounter
        completion -- the same core the Admissions Desk runs. A button in the
        back office must not be a way around the checks the desk enforces.

        Server-side system code (env.su: an upgrade, a data fix, a fixture)
        keeps Slice 0's structural discharge, which locks, releases the bed only
        if owned and stamps the time, and decides no business policy. An RPC
        caller cannot reach sudo(), so no user gets that path.
        """
        for rec in self:
            if self.env.su:
                rec._discharge_one()
            else:
                rec._finalize_discharge()
        return True

    def _discharge_one(self):
        """STRUCTURAL discharge (Slice 0). System code only -- see action_discharge.

        Guarantees: the transition is authoritative, the rows are locked, the
        bed is released only if this admission holds it, and the timestamp is
        stamped by the workflow rather than accepted from a caller. Decides no
        business policy: that is _finalize_discharge().
        """
        self.ensure_one()
        if self.state not in ADMISSION_ACTIVE_STATES:
            raise UserError("Only Admitted or Transferred admissions can be discharged.")

        self._lock_for_occupancy(self.bed_id)

        if self.state not in ADMISSION_ACTIVE_STATES:
            raise UserError("Only Admitted or Transferred admissions can be discharged.")

        self._release_bed("discharge")
        self._write_state("discharged", {"discharge_date": fields.Datetime.now()})
        return True

    def action_cancel(self):
        for rec in self:
            rec._cancel_one()
        return True

    def _cancel_one(self):
        self.ensure_one()
        if self.state not in ("draft",) + ADMISSION_ACTIVE_STATES:
            raise UserError("Cannot cancel a discharged admission.")

        self._lock_for_occupancy(self.bed_id)

        if self.state not in ("draft",) + ADMISSION_ACTIVE_STATES:
            raise UserError("Cannot cancel a discharged admission.")

        # A draft names a bed without holding it, so there is nothing to
        # release. An active admission holds one and must own it to free it.
        if self.state in ADMISSION_ACTIVE_STATES and self.bed_id:
            if self.bed_id.current_admission_id.id == self.id:
                self._release_bed("cancellation")
        self._write_state("cancelled")
        return True

    def action_reset_to_draft(self):
        for rec in self:
            if rec.state != "cancelled":
                raise UserError("Only Cancelled admissions can be reset to Draft.")
            # A cancelled admission holds no bed -- _cancel_one() released it.
            # If it somehow still points at one that it owns, resetting would
            # leave a draft silently holding occupancy no workflow would ever
            # clear, so the occupancy is dropped rather than carried back.
            rec._lock_for_occupancy(rec.bed_id)
            if rec.bed_id and rec.bed_id.current_admission_id.id == rec.id:
                rec._release_bed("reset to draft")
            rec._write_state("draft")
        return True

    # ------------------------------------------------------------------
    # TRANSFER
    # ------------------------------------------------------------------
    def action_open_transfer_wizard(self):
        self.ensure_one()
        if self.state not in ADMISSION_ACTIVE_STATES:
            raise UserError("Transfers are only allowed for Admitted or Transferred admissions.")
        return {
            "type": "ir.actions.act_window",
            "name": "Transfer Patient",
            "res_model": "hospital.admission.transfer.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {
                "default_admission_id": self.id,
                "default_current_ward_id": self.ward_id.id if self.ward_id else False,
                "default_current_room_id": self.room_id.id if self.room_id else False,
                "default_current_bed_id": self.bed_id.id if self.bed_id else False,
            },
        }

    @api.model
    def _may_transfer(self):
        """Role only. action_transfer() checks everything else under its locks."""
        user = self.env.user
        return any(user.has_group(group) for group in DESK_TRANSFER_GROUPS)

    def _assert_may_transfer(self):
        """env.su passes: server-side system code (an upgrade, a test fixture,
        another module's workflow) must still be able to move a patient, the
        same rule charge_line._assert_group() applies. An RPC caller cannot
        reach sudo(), so this admits no ordinary user."""
        if self.env.su or self._may_transfer():
            return
        raise AdmissionWorkflowError("admission_transfer_not_authorized")

    def action_transfer(self, to_ward, to_room, to_bed, reason=None):
        """THE transfer transition. A real model method, not wizard code.

        PROMOTED OUT OF THE WIZARD on purpose. The whole transition used to
        live in hospital.admission.transfer.wizard.action_do_transfer: a
        TransientModel, reachable only from the Odoo backend form, holding
        occupancy logic that no other caller could reuse without copying it.
        The wizard now collects input and calls this; the authority lives on
        the model that owns the facts.

        ADMISSIONS SLICE 3 adds, inside the same locks:

          * WHO. Only DESK_TRANSFER_GROUPS may move a patient, on every channel
            -- the backend wizard, RPC and the desk alike. Server-side system
            code (env.su) still may, as it may everywhere else in billing.
          * WHEN. The move is stamped now, and now must not precede the start of
            the segment it closes; otherwise the history would describe a stay
            that runs backwards.
          * AT WHAT RATE. The destination's daily rate is frozen on the transfer
            row as it is created. The segment that row opens is priced from it
            forever after, whatever the catalogue says later.
          * HOW THE HISTORY IS WRITTEN. Only here, under
            admission_transfer_history_capability(); the row is immutable
            afterwards (HospitalAdmissionTransfer.write / unlink).

        The Admissions Desk calls this from _desk_transfer(), which adds the
        revision, the operation token and the desk error vocabulary.
        """
        self.ensure_one()
        self._assert_may_transfer()
        if self.state not in ADMISSION_ACTIVE_STATES:
            raise UserError(
                "Transfer is only allowed for Admitted or Transferred admissions."
            )

        old_bed = self.bed_id
        # BOTH beds in ONE ordered lock set. Locking old-then-new here and
        # new-then-old in a mirror-image transfer is exactly how two operators
        # swapping two patients deadlock.
        self._lock_for_occupancy(old_bed | to_bed)

        if self.state not in ADMISSION_ACTIVE_STATES:
            raise UserError(
                "Transfer is only allowed for Admitted or Transferred admissions."
            )

        # Destination hierarchy, checked server-side. The wizard's view domains
        # are a client-side convenience and prove nothing about an ORM caller.
        if not to_ward or not to_room or not to_bed:
            raise AdmissionWorkflowError("admission_location_incoherent")
        if to_room.ward_id != to_ward or to_bed.room_id != to_room or to_bed.ward_id != to_ward:
            raise AdmissionWorkflowError("admission_location_incoherent")
        if to_bed.company_id and to_bed.company_id != self.company_id:
            raise AdmissionWorkflowError("admission_company_mismatch")

        if to_bed == old_bed:
            # Nothing moves. Refusing is kinder than writing a transfer row
            # that records a move that did not happen.
            raise AdmissionWorkflowError("admission_bed_not_available")

        # The segment this move closes started at the last transfer, or at
        # admission. A clock that reads earlier than that is refused rather
        # than recorded: a backwards segment cannot be priced.
        moved_at = fields.Datetime.now()
        last_move = self._stay_transfers()[-1:]
        segment_start = last_move.transfer_date if last_move else self.admission_date
        if segment_start and moved_at < segment_start:
            raise AdmissionWorkflowError("admission_timeline_incoherent")

        # Release before occupy: ownership of the old bed is verified first, so
        # a transfer from a bed this admission does not hold cannot take a new
        # bed and strand the old one.
        if old_bed:
            self._release_bed("transfer")
        self._occupy_bed(to_bed)

        # The destination's rate, frozen as the segment opens.
        daily_rate, basis = resolve_location_rate(to_ward, to_room, to_bed)
        # sudo() ON THE CREATE, behind the capability: the admissions clerk
        # holds read-only access to transfer history and must not be given
        # more -- a raw create would bypass every check above. The capability
        # is the authority; transferred_by is still the real user, because
        # sudo() keeps the uid.
        with admission_transfer_history_capability():
            self.env["hospital.admission.transfer"].sudo().create(
                {
                    "admission_id": self.id,
                    "transfer_date": moved_at,
                    "from_ward_id": old_bed.ward_id.id if old_bed and old_bed.ward_id else False,
                    "from_room_id": old_bed.room_id.id if old_bed and old_bed.room_id else False,
                    "from_bed_id": old_bed.id if old_bed else False,
                    "to_ward_id": to_ward.id,
                    "to_room_id": to_room.id,
                    "to_bed_id": to_bed.id,
                    "reason": reason,
                    "rate_snapshot_taken": True,
                    "rate_snapshot_origin": "workflow",
                    "to_rate_basis": basis,
                    "to_daily_rate": daily_rate,
                }
            )

        self._write_location(to_ward, to_room, to_bed)
        self._write_state("transferred")

        self._audit(
            patient_id=self.patient_id.id,
            model_name=self._name,
            record_id=self.id,
            action_type="state_change",
            old_value="admitted",
            new_value="transferred",
            description=(
                f"Admission {self.name} transferred to "
                f"{to_bed.display_name} in {to_room.display_name}"
            ),
        )
        return True

    # ==================================================================
    # ADMISSIONS SLICE 2: DESK MUTATION AUTHORITY
    # ==================================================================
    #
    # Two acts, and only two:
    #
    #   _desk_request_admission  the visit's doctor asks for an inpatient stay.
    #                            Creates a DRAFT bound to the visit. No bed, no
    #                            occupancy, no state transition.
    #   _desk_admit              the admissions clerk puts the draft into a bed.
    #                            Assign-bed and admit are ONE atomic act: a bed is
    #                            never held by a draft, so there is no "assigned
    #                            but not admitted" state for another clerk to trip
    #                            over, and the bed becomes occupied only when the
    #                            admission succeeds.
    #
    # THE SHAPE, both methods: authorize -> clean the payload -> LOCK -> check
    # for a replay (same token) -> check the revision and the state on locked,
    # fresh rows -> act through the Slice 0 authority -> bump the revision once
    # -> record the operation. Every refusal is an AdmissionDeskError with a
    # fixed code; the caller's savepoint rolls back every write before it.
    #
    # sudo() appears in exactly the places Slice 0 already justified it (the
    # duplicate / occupancy reads that only ever refuse) plus ONE new one: the
    # doctor's draft is created under sudo() because the doctor holds no create
    # ACL on hospital.admission and should not be given one -- a raw RPC create
    # would skip every check below. The doctor is authorized explicitly against
    # the appointment first; the elevation covers nothing else.

    @api.model
    def _desk_clean_token(self, token):
        if not isinstance(token, str) or not token.strip():
            raise AdmissionDeskError("admission_invalid_payload")
        if len(token.strip()) > DESK_TOKEN_MAX_LENGTH:
            raise AdmissionDeskError("admission_invalid_payload")
        try:
            # Canonical form, so a retry that only changes letter case is the
            # same request.
            return str(uuid.UUID(token.strip()))
        except ValueError:
            raise AdmissionDeskError("admission_invalid_payload") from None

    @api.model
    def _desk_clean_revision(self, value):
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise AdmissionDeskError("admission_invalid_payload")
        return value

    @api.model
    def _desk_clean_id(self, value, missing_code="admission_invalid_payload"):
        if value is None:
            raise AdmissionDeskError(missing_code)
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise AdmissionDeskError("admission_invalid_payload")
        return value

    @api.model
    def _desk_clean_reason(self, value):
        if not isinstance(value, str) or not value.strip():
            raise AdmissionDeskError("admission_invalid_payload")
        if len(value) > DESK_REASON_MAX_LENGTH:
            raise AdmissionDeskError("admission_invalid_payload")
        return value.strip()

    @api.model
    def _desk_digest(self, operation_type, payload):
        """The canonical request, hashed. The ACTOR is part of it: the same
        token presented by someone else is a different request, never a replay.
        Only the hash is stored -- no reason text, no patient, no bed label."""
        canonical = json.dumps(
            dict(payload, type=operation_type, actor=self.env.uid),
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    @api.model
    def _desk_find_replay(self, operation_type, token, digest):
        """The earlier successful operation this request replays, or False.

        sudo() ON THE LOOKUP: a token is globally unique, so "has this token
        been used" must see every row, not only the caller's. Nothing from the
        row except its admission is used, and only when it is this caller's own
        identical request.
        """
        operation = self.env["hospital.admission.operation"].sudo().search(
            [("operation_token", "=", token)], limit=1
        )
        if not operation:
            return False
        if (
            operation.operation_type != operation_type
            or operation.performed_by_id.id != self.env.uid
            or operation.request_digest != digest
        ):
            raise AdmissionDeskError("admission_operation_conflict")
        return operation

    def _desk_bump_revision(self):
        self.ensure_one()
        with admission_revision_capability():
            self.write({"workflow_revision": self.workflow_revision + 1})

    def _desk_record_operation(self, operation_type, token, digest):
        """Write the replay row, LAST. A unique-token collision -- a token raced
        onto another request -- is an operation conflict, not a crash."""
        self.ensure_one()
        values = {
            "admission_id": self.id,
            "operation_type": operation_type,
            "operation_token": token,
            "request_digest": digest,
            "result_revision": self.workflow_revision,
            "performed_by_id": self.env.uid,
        }
        try:
            with self.env.cr.savepoint(), admission_operation_capability():
                self.env["hospital.admission.operation"].sudo().create(values)
        except IntegrityError:
            raise AdmissionDeskError("admission_operation_conflict") from None

    def _desk_finish(self, operation_type, token, digest):
        self._desk_bump_revision()
        self._audit(
            patient_id=self.patient_id.id,
            model_name=self._name,
            record_id=self.id,
            action_type="update",
            description="Admissions Desk %s completed (revision %s)."
            % (operation_type, self.workflow_revision),
        )
        self.env.flush_all()
        self._desk_record_operation(operation_type, token, digest)
        self.env.flush_all()
        return self, False

    # ------------------------------------------------------------------
    # Doctor: request an admission from a visit
    # ------------------------------------------------------------------
    @api.model
    def _desk_assert_may_request(self, appointment):
        """The visit's OWN doctor, or oversight. Group membership alone is not
        enough for a doctor: they must be the doctor on this appointment."""
        user = self.env.user
        if any(user.has_group(group) for group in DESK_REQUEST_OVERSIGHT_GROUPS):
            return
        if user.has_group(G_DOCTOR) and appointment.sudo().doctor_id.user_id == user:
            return
        raise AdmissionDeskError("admission_not_authorized")

    @api.model
    def _desk_lock_patient_requests(self, patient, company):
        """Serialize admission requests for ONE patient. A SELECT-then-INSERT
        cannot stop two concurrent requests: both read "no open admission"
        before either writes. The same advisory-lock shape as the encounter's
        single-active-episode guard."""
        self.env.flush_all()
        self.env.cr.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
            ["hospital.admission.request:%s:%s" % (company.id, patient.id)],
        )
        self.env.invalidate_all()

    @api.model
    def _desk_open_admission_for(self, patient, company):
        """An open (draft, admitted or transferred) admission for the patient.

        sudo(): the duplicate may sit outside the caller's record rules -- a
        doctor cannot see another doctor's request -- and a duplicate is a
        duplicate either way. It only ever refuses.

        WHY DRAFTS COUNT HERE AND NOT IN THE DATABASE INDEX. The partial unique
        index covers ACTIVE stays only; Slice 0 allows several drafts per
        patient in the back office. The DESK is stricter on purpose: one open
        request per patient, so a double submission, or two doctors on one
        patient, never leaves the admissions clerk choosing between competing
        drafts.
        """
        return self.sudo().search(
            [
                ("patient_id", "=", patient.id),
                ("company_id", "=", company.id),
                ("state", "in", ["draft"] + list(ADMISSION_ACTIVE_STATES)),
            ],
            limit=1,
        )

    @api.model
    def _desk_request_admission(self, appointment, reason, operation_token):
        """Create a DRAFT admission for the visit. Returns (admission, replayed).

        Everything the draft names is DERIVED from the visit -- patient, visit,
        doctor, appointment, the visit's primary diagnosis -- never from the
        client. The doctor supplies the reason and nothing else: not a ward, not
        a bed.
        """
        appointment = appointment.sudo()
        if not appointment.exists():
            raise AdmissionDeskError("admission_not_found")
        self._desk_assert_may_request(appointment)
        token = self._desk_clean_token(operation_token)
        reason = self._desk_clean_reason(reason)

        encounter = appointment.encounter_id
        patient = appointment.patient_id
        if not encounter:
            raise AdmissionDeskError("admission_encounter_required")
        company = encounter.company_id

        self._desk_lock_patient_requests(patient, company)
        digest = self._desk_digest(
            "request", {"appointment": appointment.id, "reason": reason}
        )
        replay = self._desk_find_replay("request", token, digest)
        if replay:
            return replay.admission_id.with_env(self.env), True

        closed = episode_closed_states(self.env["hospital.encounter"])
        if encounter.patient_id != patient:
            raise AdmissionDeskError("admission_encounter_mismatch")
        if encounter.state in closed or encounter.state not in ENCOUNTER_ADMISSIBLE_STATES:
            raise AdmissionDeskError("admission_encounter_required")
        if self._desk_open_admission_for(patient, company):
            raise AdmissionDeskError("admission_active_conflict")

        diagnosis = self.env["hospital.patient.diagnosis"].sudo().search(
            [
                ("appointment_id", "=", appointment.id),
                ("patient_id", "=", patient.id),
                ("diagnosis_type", "=", "primary"),
            ],
            order="id desc",
            limit=1,
        )
        values = {
            "patient_id": patient.id,
            "encounter_id": encounter.id,
            "company_id": company.id,
            "physician_id": appointment.doctor_id.id or False,
            "appointment_id": appointment.id,
            "diagnosis_id": diagnosis.id or False,
            "admission_reason": reason,
        }
        # sudo() ON THE CREATE ONLY -- see the section note above. The record is
        # a draft (the create guard refuses any other state on every channel).
        try:
            admission = self.sudo().create(values).with_env(self.env)
        except AdmissionWorkflowError as error:
            raise AdmissionDeskError(desk_code_for(error.code)) from None
        return admission._desk_finish("request", token, digest)

    # ------------------------------------------------------------------
    # Admissions Desk: admit into a bed (assign + confirm, atomically)
    # ------------------------------------------------------------------
    @api.model
    def _desk_assert_may_admit(self):
        if not self._desk_may_admit():
            raise AdmissionDeskError("admission_not_authorized")

    @api.model
    def _desk_may_admit(self):
        """Role only; _desk_admit() re-checks everything else under its locks."""
        user = self.env.user
        return any(user.has_group(group) for group in DESK_ADMIT_GROUPS)

    def _desk_admit(self, bed_id, operation_token, expected_revision):
        """Assign the bed and confirm the admission, in one act. Returns
        (admission, replayed).

        Uses Slice 0's action_confirm_admission() for the admission itself --
        encounter adoption and retype, the no-active-admission check, the
        locked availability check and the occupancy write all live there -- so
        this method adds only what the desk needs on top: authorization, the
        revision, idempotency, the choice of bed, and one error vocabulary.
        """
        self.ensure_one()
        self._desk_assert_may_admit()
        token = self._desk_clean_token(operation_token)
        revision = self._desk_clean_revision(expected_revision)
        bed_id = self._desk_clean_id(bed_id, missing_code="admission_bed_required")

        # Resolved through the caller's own rights (every admitting role reads
        # the bed catalogue); a bed that does not exist is simply unavailable.
        bed = self.env["hospital.bed"].with_context(active_test=False).search(
            [("id", "=", bed_id)], limit=1
        )
        if not bed:
            raise AdmissionDeskError("admission_bed_unavailable")

        try:
            # Admission row, then the target bed: Slice 0's lock order.
            self._lock_for_occupancy(bed)
        except AdmissionWorkflowError:
            raise AdmissionDeskError("admission_not_found") from None

        digest = self._desk_digest(
            "admit", {"admission": self.id, "bed": bed.id, "expected_revision": revision}
        )
        if self._desk_find_replay("admit", token, digest):
            return self, True

        if self.workflow_revision != revision:
            raise AdmissionDeskError("admission_revision_conflict")
        if self.state != "draft":
            raise AdmissionDeskError("admission_invalid_state")
        if not self.encounter_id:
            raise AdmissionDeskError("admission_encounter_required")

        # The bed, re-read under the lock. Checked here as well as in Slice 0 so
        # the desk can tell "not available" from "someone else holds it".
        held = bool(bed.current_admission_id or bed._active_admission())
        if held:
            raise AdmissionDeskError("admission_bed_conflict")
        if not bed.active or bed.state != "available":
            raise AdmissionDeskError("admission_bed_unavailable")
        if bed.company_id and bed.company_id != self.company_id:
            raise AdmissionDeskError("admission_company_mismatch")

        try:
            # A draft's location is freely editable (Slice 0, tier one). The
            # coherence constraint validates the hierarchy on this write.
            self.write(
                {
                    "ward_id": bed.ward_id.id,
                    "room_id": bed.room_id.id,
                    "bed_id": bed.id,
                    # The stay starts NOW, not when the doctor asked for it.
                    "admission_date": fields.Datetime.now(),
                }
            )
            self.action_confirm_admission()
        except AdmissionWorkflowError as error:
            raise AdmissionDeskError(desk_code_for(error.code)) from None

        self.env.flush_all()
        self.env.invalidate_all()
        if (
            self.state != "admitted"
            or bed.state != "occupied"
            or bed.current_admission_id != self
        ):
            raise AdmissionDeskError("admission_integrity_error")
        return self._desk_finish("admit", token, digest)

    # ------------------------------------------------------------------
    # Admissions Desk: transfer to another bed (Admissions Slice 3)
    # ------------------------------------------------------------------
    def _desk_transfer(self, bed_id, reason, operation_token, expected_revision):
        """Move an admitted patient to another bed. Returns (admission, replayed).

        The destination WARD and ROOM are derived from the bed, never taken from
        the client. The transition itself is Slice 0's action_transfer() --
        release, occupy, immutable history with the destination's rate
        snapshot, location, state -- so this adds only what the desk needs:
        authorization before any row is read, the revision, idempotency, the
        choice of bed, and one error vocabulary.

        THE LOCK SET IS DECIDED BEFORE THE LOCK. The old bed is read, then the
        old and new beds are locked together in ascending id order (the rule
        that keeps a mirror-image swap from deadlocking). If, once locked, the
        admission turns out to be in a DIFFERENT bed from the one read, someone
        moved the patient in between: that is a revision conflict, answered
        before anything is touched -- never a second lock taken out of order.
        """
        self.ensure_one()
        if not self._may_transfer():
            raise AdmissionDeskError("admission_not_authorized")
        token = self._desk_clean_token(operation_token)
        revision = self._desk_clean_revision(expected_revision)
        bed_id = self._desk_clean_id(bed_id, missing_code="admission_bed_required")
        reason = self._desk_clean_reason(reason)

        bed = self.env["hospital.bed"].with_context(active_test=False).search(
            [("id", "=", bed_id)], limit=1
        )
        if not bed:
            raise AdmissionDeskError("admission_bed_unavailable")

        seen_bed = self.bed_id
        try:
            self._lock_for_occupancy(seen_bed | bed)
        except AdmissionWorkflowError:
            raise AdmissionDeskError("admission_not_found") from None

        digest = self._desk_digest(
            "transfer",
            {
                "admission": self.id,
                "bed": bed.id,
                "expected_revision": revision,
                "reason": reason,
            },
        )
        if self._desk_find_replay("transfer", token, digest):
            return self, True

        if self.workflow_revision != revision:
            raise AdmissionDeskError("admission_revision_conflict")
        if self.state not in ADMISSION_ACTIVE_STATES:
            raise AdmissionDeskError("admission_invalid_state")
        if self.bed_id != seen_bed:
            raise AdmissionDeskError("admission_revision_conflict")
        if not self.bed_id:
            # An active admission with no bed is a broken record; there is no
            # "from" to release and no segment to close. Reviewed, not moved.
            raise AdmissionDeskError("admission_integrity_error")
        if bed == self.bed_id:
            raise AdmissionDeskError("admission_bed_unavailable")

        # The destination, re-read under the lock.
        if bed.current_admission_id or bed._active_admission():
            raise AdmissionDeskError("admission_bed_conflict")
        if not bed.active or bed.state != "available":
            raise AdmissionDeskError("admission_bed_unavailable")
        if bed.company_id and bed.company_id != self.company_id:
            raise AdmissionDeskError("admission_company_mismatch")
        if not bed.room_id or not bed.ward_id or bed.room_id.ward_id != bed.ward_id:
            raise AdmissionDeskError("admission_location_mismatch")

        old_bed = self.bed_id
        try:
            self.action_transfer(bed.ward_id, bed.room_id, bed, reason=reason)
        except AdmissionWorkflowError as error:
            raise AdmissionDeskError(desk_code_for(error.code)) from None

        self.env.flush_all()
        self.env.invalidate_all()
        if (
            self.state != "transferred"
            or self.bed_id != bed
            or bed.state != "occupied"
            or bed.current_admission_id != self
            or old_bed.current_admission_id == self
        ):
            raise AdmissionDeskError("admission_integrity_error")
        return self._desk_finish("transfer", token, digest)

    # ------------------------------------------------------------------
    # Admissions Desk / Doctor Desk: cancel a DRAFT request (Slice 3)
    # ------------------------------------------------------------------
    def _desk_may_cancel_request(self):
        """AFFORDANCE AND AUTHORITY, role + ownership. The admissions clerk and
        oversight may cancel any request; a doctor only one whose physician
        they are. sudo() reads the physician's user: the ownership test is a
        property of the data, and it only ever refuses."""
        self.ensure_one()
        user = self.env.user
        if any(user.has_group(group) for group in DESK_CANCEL_REQUEST_GROUPS):
            return True
        return bool(
            user.has_group(G_DOCTOR)
            and self.sudo().physician_id.user_id == user
        )

    def _desk_cancel_request(self, operation_token, expected_revision):
        """Cancel a draft admission request. Returns (admission, replayed).

        DRAFT ONLY. A patient who is in a bed is discharged or transferred,
        never "un-requested" -- that path would free a bed with no clinical act
        behind it. A draft holds no bed (Slice 2 assigns and admits in one act),
        and a draft found holding one is an integrity fault, not something to
        clean up quietly.

        Uses Slice 0's action_cancel(). Afterwards the visit whose completion
        Slice 2 DEFERRED because of this request is re-run through its own
        completion path (hospital.encounter._release_deferred_completion), in
        the same transaction, and only if nothing else still holds it open.
        """
        self.ensure_one()
        if not self._desk_may_cancel_request():
            raise AdmissionDeskError("admission_not_authorized")
        token = self._desk_clean_token(operation_token)
        revision = self._desk_clean_revision(expected_revision)

        try:
            self._lock_for_occupancy(self.bed_id)
        except AdmissionWorkflowError:
            raise AdmissionDeskError("admission_not_found") from None

        digest = self._desk_digest(
            "cancel_request", {"admission": self.id, "expected_revision": revision}
        )
        if self._desk_find_replay("cancel_request", token, digest):
            return self, True

        if self.workflow_revision != revision:
            raise AdmissionDeskError("admission_revision_conflict")
        if self.state != "draft":
            raise AdmissionDeskError("admission_invalid_state")
        if self.bed_id and (
            self.bed_id.current_admission_id == self or self.bed_id._active_admission() == self
        ):
            raise AdmissionDeskError("admission_integrity_error")

        try:
            self.action_cancel()
        except AdmissionWorkflowError as error:
            raise AdmissionDeskError(desk_code_for(error.code)) from None

        self.env.flush_all()
        self.env.invalidate_all()
        if self.state != "cancelled":
            raise AdmissionDeskError("admission_integrity_error")
        result = self._desk_finish("cancel_request", token, digest)

        encounter = self.sudo().encounter_id
        if encounter:
            encounter._release_deferred_completion(self)
        return result

    # ==================================================================
    # ADMISSIONS SLICE 4: MEDICAL DISCHARGE AND FINAL DISCHARGE
    # ==================================================================
    #
    #   _desk_request_medical_discharge  the DOCTOR's clinical decision: the
    #                                    patient may go. Records readiness and
    #                                    the discharge summary, posts the stay
    #                                    to date. Frees nothing, settles nothing.
    #   _desk_finalize_discharge         the ADMISSIONS CLERK's act: re-checks
    #                                    readiness, posts the final stay,
    #                                    applies the settlement gate, then
    #                                    discharges, releases the bed and
    #                                    completes the visit -- atomically.
    #
    # Between the two the patient is still in the bed, on the census and the
    # bed's owner: medical readiness is a fact, not a state.

    # ------------------------------------------------------------------
    # Clinical discharge checks
    # ------------------------------------------------------------------
    DISCHARGE_WARNING_MESSAGES = {
        "pending_laboratory": "Laboratory work on this visit is not finished.",
        "pending_radiology": "Imaging on this visit is not finished.",
        "pending_procedures": "A procedure on this admission is not finished.",
        "pending_medication": "A pharmacy dispense on this visit is not finished.",
    }
    DISCHARGE_BLOCKING_MESSAGES = {
        "not_medically_ready": "The doctor has not declared the patient medically ready.",
        "bed_not_owned": "The admission's bed records a different admission.",
        "encounter_not_completable": "The linked visit is not in a state that can be completed.",
    }

    def _count_open(self, model, domain):
        if model not in self.env:
            return 0
        return self.env[model].sudo().search_count(domain)

    def _discharge_checks(self):
        """(blocking codes, warning codes). Amount-free, fixed vocabulary.

        WHAT BLOCKS, AND WHY ONLY THIS. The codebase states no rule that
        unfinished laboratory, imaging, procedure or pharmacy work must hold a
        patient in a bed -- no workflow in any module refuses on it. Inventing
        one here would be a clinical detention policy nobody decided. So those
        are WARNINGS the doctor and the clerk see before they confirm. What
        BLOCKS is only what this module's own invariants require: the doctor's
        readiness, bed ownership, and a visit that can actually be completed.
        The settlement gate is separate (_discharge_financial_refusal).

        sudo(): whether work is still open is a property of the visit, and the
        admissions clerk's rights on the laboratory or pharmacy do not change
        it. Only counts leave this method.
        """
        self.ensure_one()
        admission = self.sudo()
        blocking, warnings = [], []
        if not admission.medical_discharge_ready:
            blocking.append("not_medically_ready")
        bed = admission.bed_id
        if bed and bed.current_admission_id != admission:
            blocking.append("bed_not_owned")
        encounter = admission.encounter_id
        if encounter and encounter.state != "active":
            blocking.append("encounter_not_completable")

        if encounter:
            open_by_visit = (
                ("hospital.laboratory.request", ("requested", "sample_collected", "in_progress"),
                 "pending_laboratory"),
                ("hospital.radiology.request", ("requested", "scheduled", "in_progress"),
                 "pending_radiology"),
                ("hospital.pharmacy.dispense", ("draft", "ready", "partial"), "pending_medication"),
            )
            for model, states, code in open_by_visit:
                if model in self.env and "encounter_id" in self.env[model]._fields:
                    if self._count_open(
                        model, [("encounter_id", "=", encounter.id), ("state", "in", list(states))]
                    ):
                        warnings.append(code)
        if self._count_open(
            "hospital.procedure.request",
            [("admission_id", "=", admission.id),
             ("state", "in", ["requested", "scheduled", "in_progress"])],
        ):
            warnings.append("pending_procedures")
        return blocking, warnings

    # ------------------------------------------------------------------
    # Doctor: medical discharge
    # ------------------------------------------------------------------
    def _desk_may_request_medical_discharge(self):
        """The admission's OWN physician, or oversight. sudo() reads the
        physician's user: ownership is a property of the data."""
        self.ensure_one()
        user = self.env.user
        if any(user.has_group(group) for group in DESK_MEDICAL_DISCHARGE_OVERSIGHT_GROUPS):
            return True
        return bool(user.has_group(G_DOCTOR) and self.sudo().physician_id.user_id == user)

    def _desk_request_medical_discharge(self, summary, operation_token, expected_revision):
        """Declare the patient medically ready for discharge. Returns
        (admission, replayed).

        Records WHO and WHEN, and the discharge summary the doctor writes. Also
        posts the stay to date to the visit's billing account, so the cashier
        sees the stay as it stands. It does NOT free the bed, change the state,
        complete the visit or touch money: that is the final discharge.
        """
        self.ensure_one()
        if not self._desk_may_request_medical_discharge():
            raise AdmissionDeskError("admission_not_authorized")
        token = self._desk_clean_token(operation_token)
        revision = self._desk_clean_revision(expected_revision)
        summary = self._desk_clean_reason(summary)

        try:
            self._lock_for_occupancy(self.bed_id)
        except AdmissionWorkflowError:
            raise AdmissionDeskError("admission_not_found") from None

        digest = self._desk_digest(
            "medical_discharge",
            {"admission": self.id, "expected_revision": revision, "summary": summary},
        )
        if self._desk_find_replay("medical_discharge", token, digest):
            return self, True

        if self.workflow_revision != revision:
            raise AdmissionDeskError("admission_revision_conflict")
        if self.state not in ADMISSION_ACTIVE_STATES or self.medical_discharge_ready:
            raise AdmissionDeskError("admission_invalid_state")

        now = fields.Datetime.now()
        with medical_discharge_capability():
            self.write({
                "medical_discharge_ready": True,
                "medical_discharge_at": now,
                "medical_discharge_by_id": self.env.uid,
                "discharge_summary": summary,
            })
        try:
            self._sync_stay_charges(now)
        except AdmissionWorkflowError as error:
            raise AdmissionDeskError(desk_code_for(error.code)) from None
        self._audit(
            patient_id=self.patient_id.id,
            model_name=self._name,
            record_id=self.id,
            action_type="update",
            description="Admission %s declared medically ready for discharge." % self.name,
        )
        return self._desk_finish("medical_discharge", token, digest)

    # ------------------------------------------------------------------
    # Admissions Desk: final administrative discharge
    # ------------------------------------------------------------------
    @api.model
    def _may_finalize_discharge(self):
        user = self.env.user
        return any(user.has_group(group) for group in DESK_FINAL_DISCHARGE_GROUPS)

    def _finalize_discharge(self):
        """THE administrative discharge. One transaction; the caller's savepoint
        (or the request's transaction) rolls every step back on any refusal.

          1. authority          DESK_FINAL_DISCHARGE_GROUPS (or system code)
          2. lock               admission row + its bed, Slice 0's order
          3. re-check           active, medically ready, bed owned, visit active
          4. post the stay      every bed-day started by NOW, one time stamp
          5. settlement gate    the SAME summary the desk shows, after posting
          6. discharge          release the bed (only if owned), state + time
          7. complete the visit through its own lifecycle

        Returns the amount-free financial status the discharge was taken on.
        """
        self.ensure_one()
        if not (self.env.su or self._may_finalize_discharge()):
            raise AdmissionWorkflowError("admission_discharge_not_authorized")
        if self.state not in ADMISSION_ACTIVE_STATES:
            raise UserError("Only Admitted or Transferred admissions can be discharged.")

        self._lock_for_occupancy(self.bed_id)

        if self.state not in ADMISSION_ACTIVE_STATES:
            raise UserError("Only Admitted or Transferred admissions can be discharged.")
        if not self.medical_discharge_ready:
            raise AdmissionWorkflowError("admission_not_medically_ready")
        if self.bed_id and self.bed_id.current_admission_id != self:
            raise AdmissionWorkflowError("admission_bed_not_owned")
        encounter = self.sudo().encounter_id
        if encounter and encounter.state != "active":
            raise AdmissionWorkflowError("admission_encounter_not_completable")

        # ONE instant for the whole act: the stay is posted through it, the
        # gate is judged at it, and the discharge is stamped with it -- so the
        # posted stay and the recorded stay cannot differ by a period.
        now = fields.Datetime.now()
        self._sync_stay_charges(now)
        self.env.flush_all()
        summary = self._inpatient_financial_summary(now)
        refusal = self._discharge_financial_refusal(summary)
        if refusal:
            raise AdmissionWorkflowError(refusal)

        if self.bed_id:
            self._release_bed("discharge")
        self._write_state("discharged", {"discharge_date": now})

        if encounter:
            # The visit's OWN completion path (hospital_management + this
            # module's override, which now finds no open admission). sudo():
            # the clerk's rights on hospital.encounter do not cover completing a
            # visit, and the consultation completion path runs it the same way.
            encounter.action_complete()
            encounter.invalidate_recordset(["state"])
            if encounter.state != "completed":
                raise AdmissionWorkflowError("admission_encounter_not_completable")

        status = self._status_from_summary(summary)
        self._audit(
            patient_id=self.patient_id.id,
            model_name=self._name,
            record_id=self.id,
            action_type="state_change",
            old_value="admitted",
            new_value="discharged",
            description="Admission %s discharged (financial state: %s)."
            % (self.name, status["financial_state"]),
        )
        return status

    def _desk_post_stay_to_date(self):
        """Post every bed-day started so far, as its own act. Returns the stay
        charges.

        WHY THIS EXISTS. The final discharge posts the stay and applies the
        settlement gate in ONE transaction, so a refusal rolls its posting back
        too. If a new 24-hour period started after the cashier settled, the
        refused discharge would leave nothing new for the cashier to collect
        against. The desk therefore posts the stay to date on its own after a
        settlement refusal. It is idempotent (every period has one key), moves
        no bed, changes no state and bumps no revision -- it records care that
        has already been delivered, which is what every clinical module does as
        its care happens.
        """
        self.ensure_one()
        if not (self.env.su or self._may_finalize_discharge()):
            raise AdmissionWorkflowError("admission_discharge_not_authorized")
        if self.state not in ADMISSION_ACTIVE_STATES:
            return self.env["hospital.charge.line"].browse()
        self._lock_for_occupancy(self.env["hospital.bed"])
        return self._sync_stay_charges(fields.Datetime.now())

    def _desk_finalize_discharge(self, operation_token, expected_revision):
        """The Admissions Desk's final discharge. Returns (admission, replayed).

        Adds to _finalize_discharge() only what the desk needs: authorization
        before any row is read, the revision, idempotency and one error
        vocabulary. A replay of a completed discharge answers from the ledger
        and changes nothing.
        """
        self.ensure_one()
        if not self._may_finalize_discharge():
            raise AdmissionDeskError("admission_not_authorized")
        token = self._desk_clean_token(operation_token)
        revision = self._desk_clean_revision(expected_revision)

        try:
            self._lock_for_occupancy(self.bed_id)
        except AdmissionWorkflowError:
            raise AdmissionDeskError("admission_not_found") from None

        digest = self._desk_digest(
            "final_discharge", {"admission": self.id, "expected_revision": revision}
        )
        if self._desk_find_replay("final_discharge", token, digest):
            return self, True

        if self.workflow_revision != revision:
            raise AdmissionDeskError("admission_revision_conflict")
        if self.state not in ADMISSION_ACTIVE_STATES:
            raise AdmissionDeskError("admission_invalid_state")

        bed = self.bed_id
        try:
            self._finalize_discharge()
        except AdmissionWorkflowError as error:
            raise AdmissionDeskError(desk_code_for(error.code)) from None

        self.env.flush_all()
        self.env.invalidate_all()
        if self.state != "discharged" or (bed and bed.current_admission_id == self):
            raise AdmissionDeskError("admission_integrity_error")
        return self._desk_finish("final_discharge", token, digest)

    def unlink(self):
        for rec in self:
            if rec.state not in ("draft", "cancelled"):
                self._audit(
                    patient_id=rec.patient_id.id,
                    model_name=self._name,
                    record_id=rec.id,
                    action_type="delete_attempt",
                    description=f"Blocked delete attempt on {rec.state} admission: {rec.name}",
                )
                raise AccessError(
                    f"Cannot delete admission '{rec.name}' in state '{rec.state}'. "
                    "Only Draft or Cancelled admissions can be deleted."
                )
        return super().unlink()


class HospitalAdmissionTransfer(models.Model):
    _name = "hospital.admission.transfer"
    _description = "Admission Transfer"
    _order = "transfer_date desc"

    admission_id = fields.Many2one(
        "hospital.admission",
        required=True,
        ondelete="cascade",
        index=True,
    )
    transfer_date = fields.Datetime(
        default=fields.Datetime.now,
        required=True,
    )
    from_ward_id = fields.Many2one("hospital.ward", string="From Ward")
    from_room_id = fields.Many2one("hospital.room", string="From Room")
    from_bed_id = fields.Many2one("hospital.bed", string="From Bed")
    to_ward_id = fields.Many2one("hospital.ward", string="To Ward", required=True)
    to_room_id = fields.Many2one("hospital.room", string="To Room", required=True)
    to_bed_id = fields.Many2one("hospital.bed", string="To Bed", required=True)
    reason = fields.Text()
    # PROVENANCE IS SERVER-DERIVED. The field used to carry a default of
    # env.user, which a caller could simply override in the create payload --
    # so the record of who moved a patient was whatever the caller said it was.
    # It is now stamped in create() from the session and is not writable
    # afterwards.
    transferred_by = fields.Many2one(
        "res.users",
        string="Transferred By",
        readonly=True,
    )

    # ── Destination rate snapshot (Admissions Slice 3) ──────────
    #
    # The segment this row OPENS is priced from these, never from the
    # catalogue. Minimum facts only: the daily rate and which catalogue level
    # it came from. Readable by the money roles only.
    rate_snapshot_taken = fields.Boolean(
        string="Destination Rate Recorded", readonly=True, groups=ADMISSION_MONEY_READ,
    )
    rate_snapshot_origin = fields.Selection(
        RATE_SNAPSHOT_ORIGIN_SELECTION,
        string="Destination Rate Origin", readonly=True, groups=ADMISSION_MONEY_READ,
    )
    to_rate_basis = fields.Selection(
        RATE_BASIS_SELECTION,
        string="Destination Rate Basis", readonly=True, groups=ADMISSION_MONEY_READ,
    )
    to_daily_rate = fields.Float(
        string="Destination Daily Rate", digits=(16, 2), readonly=True,
        groups=ADMISSION_MONEY_READ,
    )

    @api.model_create_multi
    def create(self, vals_list):
        """ONLY action_transfer() creates history (Admissions Slice 3).

        Every row starts a new billed stay segment. A row created anywhere else
        -- a nurse's ACL, an import, a sudo() script -- would reprice the stay
        without a patient having moved, so the capability is required
        whatever the caller's rights.
        """
        if not has_admission_transfer_history_capability():
            raise AdmissionWorkflowError("admission_transfer_history_refused")
        for vals in vals_list:
            vals["transferred_by"] = self.env.uid
        return super().create(vals_list)

    def write(self, vals):
        """Transfer history is a record of something that happened. IMMUTABLE.

        A nurse holds create and write on this model, so before Slice 0's guard
        the history of where a patient had been could be rewritten after the
        fact. Slice 3 closes the rest: the dates, both ends and the rate
        snapshot are what the stay is billed from, so nothing on a transfer row
        changes once written. Echoing the current value is not a change.
        """
        if "transferred_by" in vals:
            for rec in self:
                if vals["transferred_by"] != rec.transferred_by.id:
                    raise AdmissionWorkflowError("admission_identity_write_refused")
        names = [name for name in vals if name in self._fields]
        for rec in self.sudo():
            if changed_fields(rec, names, vals):
                raise AdmissionWorkflowError("admission_transfer_history_refused")
        return super().write(vals)

    def unlink(self):
        """History is not deleted. A draft or cancelled admission that is itself
        deleted takes its rows with it through the database cascade, which does
        not pass through here."""
        raise AdmissionWorkflowError("admission_transfer_history_refused")


class HospitalAdmissionTransferWizard(models.TransientModel):
    _name = "hospital.admission.transfer.wizard"
    _description = "Admission Transfer Wizard"

    admission_id = fields.Many2one(
        "hospital.admission",
        required=True,
        readonly=True,
    )
    current_ward_id = fields.Many2one(
        "hospital.ward",
        string="Current Ward",
        readonly=True,
    )
    current_room_id = fields.Many2one(
        "hospital.room",
        string="Current Room",
        readonly=True,
    )
    current_bed_id = fields.Many2one(
        "hospital.bed",
        string="Current Bed",
        readonly=True,
    )
    to_ward_id = fields.Many2one(
        "hospital.ward",
        string="New Ward",
        required=True,
    )
    to_room_id = fields.Many2one(
        "hospital.room",
        string="New Room",
        required=True,
    )
    to_bed_id = fields.Many2one(
        "hospital.bed",
        string="New Bed",
        required=True,
    )
    reason = fields.Text()

    def action_do_transfer(self):
        """Collect input, then hand the whole transition to the model.

        Every check, every lock and every write now lives in
        hospital.admission.action_transfer(). This method decides nothing.
        """
        self.ensure_one()
        self.admission_id.action_transfer(
            to_ward=self.to_ward_id,
            to_room=self.to_room_id,
            to_bed=self.to_bed_id,
            reason=self.reason,
        )
        return {"type": "ir.actions.act_window_close"}
