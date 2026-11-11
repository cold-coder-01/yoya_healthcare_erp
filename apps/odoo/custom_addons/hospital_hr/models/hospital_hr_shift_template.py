from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class HospitalHrShiftTemplate(models.Model):
    _name = "hospital.hr.shift.template"
    _description = "Hospital HR Shift Template"
    _order = "sequence, start_time, name"

    name = fields.Char(required=True, translate=True)
    code = fields.Char(required=True)
    start_time = fields.Float(
        required=True,
        help="Start of the shift as time of day (e.g. 8.0 = 08:00).",
    )
    end_time = fields.Float(
        required=True,
        help="End of the shift as time of day. An end time before the start "
        "time means the shift runs into the next day.",
    )
    duration_hours = fields.Float(
        compute="_compute_duration_hours",
        store=True,
        help="Shift length in hours; overnight shifts wrap past midnight.",
    )
    shift_type = fields.Selection(
        [
            ("regular", "Regular"),
            ("on_call", "On Call"),
            ("emergency", "Emergency"),
            ("ot", "Operation Theatre"),
            ("ward", "Ward"),
        ],
        string="Shift Type",
        required=True,
        default="regular",
    )
    is_night_shift = fields.Boolean(
        compute="_compute_is_night_shift",
        store=True,
        readonly=False,
        help="Suggested automatically for overnight shifts; can be adjusted.",
    )
    color = fields.Integer(string="Color Index")
    active = fields.Boolean(default=True)
    sequence = fields.Integer(default=10)
    notes = fields.Text()

    _sql_constraints = [
        ("code_unique", "unique(code)", "The shift template code must be unique."),
    ]

    @api.depends("start_time", "end_time")
    def _compute_duration_hours(self):
        for shift in self:
            if shift.end_time > shift.start_time:
                shift.duration_hours = shift.end_time - shift.start_time
            else:
                shift.duration_hours = 24.0 - shift.start_time + shift.end_time

    @api.depends("start_time", "end_time")
    def _compute_is_night_shift(self):
        for shift in self:
            shift.is_night_shift = shift.end_time <= shift.start_time

    @api.constrains("start_time", "end_time")
    def _check_times(self):
        for shift in self:
            for label, value in (
                (_("start time"), shift.start_time),
                (_("end time"), shift.end_time),
            ):
                if not 0.0 <= value <= 24.0:
                    raise ValidationError(
                        _("The %(label)s must be between 00:00 and 24:00.", label=label)
                    )
            if shift.start_time == shift.end_time:
                raise ValidationError(
                    _("The start and end time of a shift cannot be identical.")
                )
