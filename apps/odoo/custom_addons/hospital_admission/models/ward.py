from odoo import api, fields, models
from odoo.exceptions import AccessError


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

    def unlink(self):
        for bed in self:
            if bed.state == "occupied":
                self.env["hospital.audit.log"].create_log(
                    model_name=self._name,
                    record_id=bed.id,
                    action_type="delete_attempt",
                    description=f"Blocked delete attempt on occupied bed: {bed.display_name}",
                )
                raise AccessError(
                    f"Cannot delete bed '{bed.display_name}' because it is currently occupied."
                )
        return super().unlink()
