from odoo import fields, models


class ResUsers(models.Model):
    _inherit = "res.users"

    # Neither hospital_management nor hospital_billing links a user to a
    # department: hospital.doctor.department_id covers doctors only, and
    # hospital.department.manager_id names a single supervisor. The nurse
    # record rule needs a department roster, so the bridge carries its own
    # rather than reaching into hospital_hr (not a declared dependency).
    yoya_permitted_department_ids = fields.Many2many(
        "hospital.department",
        "yoya_user_permitted_department_rel",
        "user_id",
        "department_id",
        string="Permitted Clinical Departments",
        help="Departments whose unassigned evaluations this user may triage. "
        "Leave empty to restrict the user to evaluations assigned to them.",
    )
