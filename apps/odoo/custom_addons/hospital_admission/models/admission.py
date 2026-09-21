import math

from odoo import api, fields, models
from odoo.exceptions import UserError, AccessError, ValidationError

from .admission_authority import (
    ADMISSION_ACTIVE_STATES,
    ADMISSION_ATTRIBUTION_FIELDS,
    ADMISSION_IDENTITY_FIELDS,
    ADMISSION_LOCATION_FIELDS,
    ADMISSION_TIMELINE_FIELDS,
    ENCOUNTER_ADMISSIBLE_STATES,
    AdmissionWorkflowError,
    admission_billing_capability,
    admission_location_capability,
    admission_workflow_capability,
    changed_fields,
    episode_closed_states,
    has_admission_billing_capability,
    has_admission_location_capability,
    has_admission_workflow_capability,
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
    stay_days = fields.Float(
        compute="_compute_stay_days",
        string="Stay Days",
        digits=(16, 2),
        store=False,
    )
    admission_fee_amount = fields.Float(
        compute="_compute_admission_billing",
        string="Admission Fee",
        digits=(16, 2),
        store=False,
    )
    daily_rate_amount = fields.Float(
        compute="_compute_admission_billing",
        string="Daily Rate",
        digits=(16, 2),
        store=False,
    )
    bed_charge_amount = fields.Float(
        compute="_compute_admission_billing",
        string="Bed Charge",
        digits=(16, 2),
        store=False,
    )
    total_admission_charge = fields.Float(
        compute="_compute_admission_billing",
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
        "stay_days",
        "ward_id", "ward_id.admission_fee", "ward_id.daily_ward_rate",
        "room_id", "room_id.daily_room_rate",
        "bed_id", "bed_id.daily_bed_rate",
    )
    def _compute_admission_billing(self):
        # DELIBERATELY UNCHANGED IN THIS SLICE.
        #
        # This multiplies the CURRENT location's daily rate by the WHOLE stay,
        # so a patient who spends nine days on a general ward and one in ICU is
        # billed ten ICU days. That is a real defect and it is recorded as
        # PART 19 of the slice report.
        #
        # It is not fixed here because the fix is a billing change, not an
        # authority change: it needs per-segment rates derived from the
        # transfer history, a decision about which rate applies to the segment
        # a transfer opens, and agreement with the charge engine that Slice 0
        # deliberately does not touch. Rewriting it inside an authority slice
        # would put a billing change behind a security review.
        #
        # What this slice DOES guarantee is that it is not made worse: the
        # transfer workflow now writes a complete, ordered, server-attributed
        # hospital.admission.transfer row for every move, which is exactly the
        # input a segmentation fix needs and which did not reliably exist
        # before. test_rate_segmentation_is_deferred_not_worsened pins the
        # current behaviour so the later fix is a deliberate change.
        for rec in self:
            ward = rec.ward_id
            room = rec.room_id
            bed = rec.bed_id

            rec.admission_fee_amount = ward.admission_fee if ward else 0.0

            if bed and bed.daily_bed_rate > 0:
                rec.daily_rate_amount = bed.daily_bed_rate
            elif room and room.daily_room_rate > 0:
                rec.daily_rate_amount = room.daily_room_rate
            elif ward:
                rec.daily_rate_amount = ward.daily_ward_rate
            else:
                rec.daily_rate_amount = 0.0

            rec.bed_charge_amount = rec.daily_rate_amount * rec.stay_days
            rec.total_admission_charge = rec.admission_fee_amount + rec.bed_charge_amount

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

    def action_generate_admission_bill(self):
        self.ensure_one()
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

        bill_lines = []

        if self.admission_fee_amount > 0:
            bill_lines.append((0, 0, {
                "description": f"Admission Fee - {self.name}",
                "source_type": "admission",
                "quantity": 1.0,
                "unit_price": self.admission_fee_amount,
                "source_model": "hospital.admission",
                "source_record_id": self.id,
                "sequence": 10,
            }))

        if self.daily_rate_amount > 0:
            parts = [
                self.ward_id.display_name if self.ward_id else "",
                self.room_id.name if self.room_id else "",
                self.bed_id.display_name if self.bed_id else "",
            ]
            location_desc = " / ".join(p for p in parts if p)
            bill_lines.append((0, 0, {
                "description": (
                    f"Bed Stay Charge - {location_desc} - {int(stay_days)} day(s)"
                    if location_desc
                    else f"Bed Stay Charge - {self.name} - {int(stay_days)} day(s)"
                ),
                "source_type": "admission",
                "quantity": float(stay_days),
                "unit_price": self.daily_rate_amount,
                "source_model": "hospital.admission",
                "source_record_id": self.id,
                "sequence": 20,
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
        return True

    def action_discharge(self):
        for rec in self:
            rec._discharge_one()
        return True

    def _discharge_one(self):
        """Structurally safe discharge. Business policy is a LATER slice.

        What this slice guarantees: the transition is authoritative, the rows
        are locked, the bed is released only if this admission holds it, and
        the timestamp is stamped by the workflow rather than accepted from a
        caller.

        What this slice deliberately does NOT add: discharge_pending, doctor
        sign-off, billing clearance and pending lab/imaging checks. Those are
        business policy, they need the encounter bridge this slice is building
        in order to be expressible at all, and they belong to Slice 4.
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

    def action_transfer(self, to_ward, to_room, to_bed, reason=None):
        """THE transfer transition. A real model method, not wizard code.

        PROMOTED OUT OF THE WIZARD on purpose. The whole transition used to
        live in hospital.admission.transfer.wizard.action_do_transfer: a
        TransientModel, reachable only from the Odoo backend form, holding
        occupancy logic that no other caller could reuse without copying it.
        The wizard now collects input and calls this; the authority lives on
        the model that owns the facts.

        No API route is registered for it in this slice. That is Slice 3.
        """
        self.ensure_one()
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

        # Release before occupy: ownership of the old bed is verified first, so
        # a transfer from a bed this admission does not hold cannot take a new
        # bed and strand the old one.
        if old_bed:
            self._release_bed("transfer")
        self._occupy_bed(to_bed)

        self.env["hospital.admission.transfer"].create(
            {
                "admission_id": self.id,
                "transfer_date": fields.Datetime.now(),
                "from_ward_id": old_bed.ward_id.id if old_bed and old_bed.ward_id else False,
                "from_room_id": old_bed.room_id.id if old_bed and old_bed.room_id else False,
                "from_bed_id": old_bed.id if old_bed else False,
                "to_ward_id": to_ward.id,
                "to_room_id": to_room.id,
                "to_bed_id": to_bed.id,
                "reason": reason,
                # NOT taken from the caller. See the field's own comment.
                "transferred_by": self.env.user.id,
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

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            vals["transferred_by"] = self.env.uid
        return super().create(vals_list)

    def write(self, vals):
        """Transfer history is a record of something that happened.

        A nurse holds create and write on this model, so before this guard the
        history of where a patient had been could be rewritten after the fact.
        """
        if "transferred_by" in vals:
            for rec in self:
                if vals["transferred_by"] != rec.transferred_by.id:
                    raise AdmissionWorkflowError("admission_identity_write_refused")
        return super().write(vals)


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
