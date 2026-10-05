from odoo import api, fields, models
from odoo.exceptions import AccessError

from .admission_authority import (
    ADMISSION_ACTIVE_STATES,
    BED_OCCUPANCY_FIELDS,
    AdmissionWorkflowError,
    bed_occupancy_capability,
    changed_fields,
    has_bed_occupancy_capability,
)


class HospitalWard(models.Model):
    _name = "hospital.ward"
    _description = "Hospital Ward"
    _order = "name"

    name = fields.Char(required=True)
    code = fields.Char()
    ward_type = fields.Selection(
        [
            ("general", "General"),
            ("private", "Private"),
            ("emergency", "Emergency"),
            ("maternity", "Maternity"),
            ("pediatric", "Pediatric"),
            ("surgical", "Surgical"),
            ("medical", "Medical"),
            ("icu", "ICU"),
            ("isolation", "Isolation"),
            ("other", "Other"),
        ],
    )
    floor = fields.Char()
    department_id = fields.Many2one("hospital.department", string="Department")
    description = fields.Text()
    active = fields.Boolean(default=True)

    # The facility catalogue gains a company so that occupancy can be checked
    # inside one. Existing rows are filled with the default at upgrade, which
    # is correct for the single-company deployment this runs in today and is
    # what a second company would need in place before it could be added.
    company_id = fields.Many2one(
        "res.company",
        required=True,
        index=True,
        default=lambda self: self.env.company,
    )

    currency_id = fields.Many2one(
        "res.currency",
        default=lambda self: self.env.company.currency_id,
    )
    admission_fee = fields.Float(
        string="Admission Fee",
        digits=(16, 2),
        default=0.0,
    )
    daily_ward_rate = fields.Float(
        string="Daily Ward Rate",
        digits=(16, 2),
        default=0.0,
    )

    @api.depends("name", "code")
    def _compute_display_name(self):
        for ward in self:
            if ward.code:
                ward.display_name = f"[{ward.code}] {ward.name}"
            else:
                ward.display_name = ward.name or ""


class HospitalRoom(models.Model):
    _name = "hospital.room"
    _description = "Hospital Room"
    _order = "ward_id, name"

    name = fields.Char(required=True)
    code = fields.Char()
    ward_id = fields.Many2one(
        "hospital.ward",
        required=True,
        ondelete="restrict",
    )
    company_id = fields.Many2one(
        "res.company",
        related="ward_id.company_id",
        store=True,
        readonly=True,
        index=True,
    )
    room_type = fields.Selection(
        [
            ("shared", "Shared"),
            ("private", "Private"),
            ("isolation", "Isolation"),
            ("icu", "ICU"),
            ("emergency", "Emergency"),
            ("other", "Other"),
        ],
    )
    floor = fields.Char()
    description = fields.Text()
    active = fields.Boolean(default=True)

    currency_id = fields.Many2one(
        "res.currency",
        default=lambda self: self.env.company.currency_id,
    )
    daily_room_rate = fields.Float(
        string="Daily Room Rate",
        digits=(16, 2),
        default=0.0,
        help="If set, overrides ward daily rate for billing.",
    )

    @api.depends("name", "ward_id")
    def _compute_display_name(self):
        for room in self:
            if room.ward_id:
                room.display_name = f"{room.ward_id.name} / {room.name}"
            else:
                room.display_name = room.name or ""


class HospitalBed(models.Model):
    _name = "hospital.bed"
    _description = "Hospital Bed"
    _order = "room_id, name"

    name = fields.Char(required=True)
    code = fields.Char()
    room_id = fields.Many2one(
        "hospital.room",
        required=True,
        ondelete="restrict",
    )
    ward_id = fields.Many2one(
        "hospital.ward",
        related="room_id.ward_id",
        store=True,
        readonly=True,
        string="Ward",
    )
    company_id = fields.Many2one(
        "res.company",
        related="room_id.company_id",
        store=True,
        readonly=True,
        index=True,
    )
    bed_type = fields.Selection(
        [
            ("standard", "Standard"),
            ("pediatric", "Pediatric"),
            ("icu", "ICU"),
            ("maternity", "Maternity"),
            ("emergency", "Emergency"),
            ("isolation", "Isolation"),
            ("other", "Other"),
        ],
    )
    state = fields.Selection(
        [
            ("available", "Available"),
            ("occupied", "Occupied"),
            ("cleaning", "Cleaning"),
            ("maintenance", "Maintenance"),
            ("blocked", "Blocked"),
        ],
        default="available",
        required=True,
    )
    active = fields.Boolean(default=True)
    current_admission_id = fields.Many2one(
        "hospital.admission",
        string="Current Admission",
        readonly=True,
        index=True,
    )

    currency_id = fields.Many2one(
        "res.currency",
        default=lambda self: self.env.company.currency_id,
    )
    daily_bed_rate = fields.Float(
        string="Daily Bed Rate",
        digits=(16, 2),
        default=0.0,
        help="If set, overrides room and ward daily rates for billing.",
    )

    @api.depends("name", "code", "room_id")
    def _compute_display_name(self):
        for bed in self:
            ref = bed.code or bed.name
            room_name = bed.room_id.name if bed.room_id else ""
            bed.display_name = f"{ref} - {room_name}" if room_name else ref

    # ------------------------------------------------------------------
    # OCCUPANCY AUTHORITY
    # ------------------------------------------------------------------
    #
    # bed.state and bed.current_admission_id together are the hospital's answer
    # to "is there a patient in this bed". Before this slice they were ordinary
    # writable fields: a manager could mark an occupied bed available while the
    # patient was still in it, and any caller could point current_admission_id
    # at somebody else's admission.
    #
    # They are now movable only from _set_occupancy(), which is called only
    # from the admission workflow methods that have already taken the row locks
    # and checked ownership. The guard ignores env.su, so sudo() does not help:
    # a caller needs the capability, and the only way to hold it is to be
    # running inside _set_occupancy().
    #
    def write(self, vals):
        self._assert_authoritative_occupancy_write(vals)
        return super().write(vals)

    @api.model_create_multi
    def create(self, vals_list):
        # A bed is BORN free. Creating one already pointing at an admission, or
        # already 'occupied', would manufacture occupancy that no admission
        # workflow produced and that no discharge would ever clear.
        for vals in vals_list:
            if has_bed_occupancy_capability():
                continue
            if vals.get("current_admission_id"):
                raise AdmissionWorkflowError("admission_occupancy_write_refused")
            if vals.get("state") == "occupied":
                raise AdmissionWorkflowError("admission_occupancy_write_refused")
        return super().create(vals_list)

    def _assert_authoritative_occupancy_write(self, vals):
        """Runs before super() and looks at neither the context nor sudo.

        Writing the value a bed already has is not a change and is allowed, so
        a form or an import that echoes the current values does not break.
        """
        if has_bed_occupancy_capability():
            return
        for bed in self:
            if changed_fields(bed, BED_OCCUPANCY_FIELDS, vals):
                raise AdmissionWorkflowError("admission_occupancy_write_refused")

    def _set_occupancy(self, state, admission=None):
        """THE ONLY way bed occupancy moves. Called from admission workflow only.

        sudo() ON THE BED WRITE, and this is PART 16's whole answer. A
        receptionist may create and work an admission but holds no write ACL on
        hospital.bed, so confirming an admission would fail on the bed write.
        Granting receptionists generic bed editing would let them mark any bed
        in the hospital blocked or free; sudo() in a controller would put the
        decision outside the model. Instead the elevation is here, inside the
        method that owns the fact, behind a capability an RPC payload cannot
        raise, reached only from a workflow method that has already locked the
        rows and verified ownership.

        The caller is responsible for having taken the locks. This method does
        not decide whether the move is allowed -- it is the mechanism, not the
        policy.
        """
        self.ensure_one()
        values = {
            "state": state,
            "current_admission_id": admission.id if admission else False,
        }
        with bed_occupancy_capability():
            self.sudo().write(values)
        return True

    def _active_admission(self):
        """The admission that really holds this bed, read from the admission
        side rather than from current_admission_id.

        current_admission_id is a pointer the bed carries; the authoritative
        fact is which admission is in an active state naming this bed. Reading
        it this way is what lets the integrity check find a bed whose pointer
        and whose admissions disagree.

        sudo(): occupancy is a property of the data, not of what the reading
        user may see, and this only ever feeds a refusal.
        """
        self.ensure_one()
        return (
            self.env["hospital.admission"]
            .sudo()
            .search(
                [
                    ("bed_id", "=", self.id),
                    ("state", "in", list(ADMISSION_ACTIVE_STATES)),
                ],
                limit=1,
            )
        )

    def unlink(self):
        for bed in self:
            if bed.state == "occupied" or bed._active_admission():
                self.env["hospital.admission"]._audit(
                    model_name=self._name,
                    record_id=bed.id,
                    action_type="delete_attempt",
                    description=f"Blocked delete attempt on occupied bed: {bed.display_name}",
                )
                raise AccessError(
                    f"Cannot delete bed '{bed.display_name}' because it is currently occupied."
                )
        return super().unlink()
