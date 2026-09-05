from odoo import fields, models


class HospitalPatient(models.Model):
    _inherit = "hospital.patient"

    # THE INVERSE THE HISTORY RECORD RULES TRAVERSE.
    #
    # hospital_management defines hospital.appointment.patient_id but no
    # inverse, so before this there was no way to ask a patient "which
    # appointments name you". A One2many is a VIRTUAL field: it adds no column
    # and needs no migration, it only gives the existing foreign key a name
    # that a domain can walk.
    #
    # WHY THE RULES NEED IT RATHER THAN THE COMPUTED USER FIELD.
    # ir.rule._compute_domain is ormcached on (uid, su, model, mode, context).
    # It evaluates domain_force ONCE and caches the RESULT, so a domain written
    # as ('patient_id', 'in', user.yoya_care_relationship_patient_ids.ids)
    # would bake today's patient ids into that cache entry. Nothing invalidates
    # it when an appointment closes, so a doctor's longitudinal access would
    # outlive the care relationship that justified it.
    #
    # Traversing this field instead keeps the relationship inside the SQL the
    # rule generates: the cached domain contains only user.id, which is already
    # part of the cache key, and the care relationship is re-evaluated by the
    # database on every query.
    appointment_ids = fields.One2many(
        "hospital.appointment",
        "patient_id",
        string="Appointments",
    )
