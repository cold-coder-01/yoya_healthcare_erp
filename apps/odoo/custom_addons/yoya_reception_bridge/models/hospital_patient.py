"""MRN protection and reception-workflow-only creation.

hospital.patient.identification_code carries no database constraint, so two
patients can hold the same MRN if a sequence is reset or a value is written
directly.

A Python constraint is used rather than a SQL unique index on purpose: it
installs safely on a database that ALREADY contains duplicates, blocking new
ones without making the upgrade fail. Promoting it to a real unique index is a
separate, deliberate step -- see README notes and the detection SQL reported
with this module.

This file also guards create(): a receptionist may register a patient only
through hospital.reception.workflow (register_new_patient / create_visit),
never through the generic Odoo form or a direct RPC call to
hospital.patient.create(). See reception_capability.py for why that is
enforced with a capability token rather than a context flag.
"""
from odoo import api, models
from odoo.exceptions import AccessError, ValidationError

from .reception_capability import has_reception_workflow_capability

G_MANAGER = "hospital_management.group_hospital_manager"
G_ADMIN = "hospital_management.group_hospital_system_administrator"
G_RECEPTIONIST = "hospital_management.group_hospital_receptionist"
G_FRONT_DESK_NURSE = "yoya_reception_bridge.group_hospital_front_desk_nurse"

DIRECT_CREATE_GROUPS = (G_MANAGER, G_ADMIN)

# Roles that HOLD perm_create on this model and must nevertheless go through
# hospital.reception.workflow. Front Desk Nurse is here because this module
# grants it create -- without it, the guard below would silently stop applying
# to the one new role that can reach create().
WORKFLOW_ONLY_CREATE_GROUPS = (G_RECEPTIONIST, G_FRONT_DESK_NURSE)


class HospitalPatient(models.Model):
    _inherit = "hospital.patient"

    @api.model_create_multi
    def create(self, vals_list):
        self._assert_patient_creation_allowed()
        return super().create(vals_list)

    @api.model
    def _assert_patient_creation_allowed(self):
        """Receptionists register patients through the workflow, not the ORM.

        Scoped to the Receptionist group specifically, not to "everyone who
        isn't a manager." hospital_management's own ACL already grants
        perm_create=1 on hospital.patient to exactly three groups --
        receptionist, manager, system administrator -- and perm_create=0 to
        every other role (doctor, nurse, pharmacist, lab technician,
        accountant, DPO). This guard therefore changes behaviour for
        receptionists only: every other role was already blocked at the ACL
        layer and remains blocked there, untouched by this method.

        env.su passes deliberately, matching the convention already used
        throughout this module (_assert_emergency_authorizer,
        HospitalEncounter._assert_group) and in hospital_billing's own
        _assert_group: trusted server-side code (migrations, data fixtures)
        running under sudo() is not the threat this guards against: an
        ordinary RPC-authenticated receptionist never reaches env.su.
        """
        if self.env.su:
            return
        if has_reception_workflow_capability():
            return
        user = self.env.user
        if any(user.has_group(group) for group in DIRECT_CREATE_GROUPS):
            return
        if any(user.has_group(group) for group in WORKFLOW_ONLY_CREATE_GROUPS):
            raise AccessError(
                "Patients cannot be registered directly. Use the reception "
                "workflow (hospital.reception.workflow.register_new_patient "
                "or create_visit), which issues the MRN and, for a new "
                "patient, the first patient card as part of one guided visit."
            )

    @api.constrains("identification_code")
    def _check_identification_code_unique(self):
        for patient in self:
            code = (patient.identification_code or "").strip()
            # 'New' is the pre-sequence placeholder and is never an identity.
            if not code or code == "New":
                continue
            clash = self.with_context(active_test=False).search(
                [
                    ("identification_code", "=", code),
                    ("id", "!=", patient.id),
                ],
                limit=1,
            )
            if clash:
                raise ValidationError(
                    "Medical record number %s is already assigned to %s. "
                    "An MRN must identify exactly one patient."
                    % (code, clash.display_name)
                )
