from odoo import api, fields, models


class HospitalSurgeryTeamMember(models.Model):
    _name = "hospital.surgery.team.member"
    _description = "Surgical Team Member"
    _order = "surgery_id, id"

    surgery_id = fields.Many2one(
        "hospital.surgery.request",
        string="Surgery",
        required=True,
        ondelete="cascade",
    )
    physician_id = fields.Many2one(
        "hospital.doctor",
        string="Physician",
        ondelete="set null",
    )
    user_id = fields.Many2one("res.users", string="User")
    role = fields.Selection(
        [
            ("lead_surgeon", "Lead Surgeon"),
            ("assistant_surgeon", "Assistant Surgeon"),
            ("anesthetist", "Anesthetist"),
            ("scrub_nurse", "Scrub Nurse"),
            ("circulating_nurse", "Circulating Nurse"),
            ("technician", "Technician"),
            ("observer", "Observer"),
            ("other", "Other"),
        ],
        string="Role",
        default="assistant_surgeon",
    )
    name = fields.Char(string="Name / Label")
    notes = fields.Text()

    @api.onchange("physician_id")
    def _onchange_physician_id(self):
        if self.physician_id and not self.name:
            self.name = self.physician_id.display_name
