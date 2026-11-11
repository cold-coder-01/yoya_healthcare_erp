from odoo import fields, models


class HospitalDoctor(models.Model):
    _inherit = "hospital.doctor"

    employee_id = fields.Many2one(
        "hr.employee",
        string="Employee Profile",
        ondelete="set null",
        help="HR master record for this doctor. hospital.doctor remains the "
        "clinical directory; hr.employee is the HR source of truth.",
    )
    # Source of truth for professional licenses is hospital.hr.credential
    # (via the employee's computed license summary). The legacy manually
    # entered license_number field stays in the database untouched, but is
    # replaced in the views by this readonly display.
    credential_license_number = fields.Char(
        string="License Number",
        related="employee_id.license_number",
        readonly=True,
        help="License number is managed from Employee Credentials. Link an "
        "Employee Profile and register the license under Hospital HR ▸ "
        "Credentials / Licenses.",
    )
    credential_license_status = fields.Selection(
        related="employee_id.license_status",
        string="License Status",
        readonly=True,
    )
