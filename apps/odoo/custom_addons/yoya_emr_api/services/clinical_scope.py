"""Explicit clinical scoping for hospital.appointment.

hospital_management ships NO record rules for hospital.appointment, so the ORM
would happily return every appointment in the hospital to any role holding the
read ACL. This module therefore computes an explicit domain for every request
rather than trusting the ORM to scope the result.

yoya_clinical_bridge does scope hospital.patient.evaluation, and those rules
still apply on top of everything here. The domains below are an ADDITIONAL
restriction, never a replacement.
"""
import logging

from odoo.osv import expression

_logger = logging.getLogger(__name__)

GROUP_RECEPTIONIST = "hospital_management.group_hospital_receptionist"
GROUP_DOCTOR = "hospital_management.group_hospital_doctor"
GROUP_NURSE = "hospital_management.group_hospital_nurse"
GROUP_MANAGER = "hospital_management.group_hospital_manager"
GROUP_SYSADMIN = "hospital_management.group_hospital_system_administrator"

# Checked EXPLICITLY, never inferred from Hospital Nurse.
#
# group_hospital_front_desk_nurse IMPLIES Hospital Nurse, so before this the
# front desk fell into the department-scoped nurse branch below -- which asks a
# question the entrance cannot answer. At the moment a visit is registered there
# is no evaluation yet, and a front desk nurse legitimately has no
# yoya_permitted_department_ids (the desk triages every arrival, whatever
# department the patient is booked into). _nurse_domain therefore returned
# DENY_ALL and the clinical save/complete endpoints answered 403 out_of_scope on
# the first triage of every new visit.
GROUP_FRONT_DESK_NURSE = "yoya_reception_bridge.group_hospital_front_desk_nurse"

# Matches the ORM: a user holding several clinical roles gets the UNION of
# their scopes, exactly as Odoo ORs the record rules of multiple groups.
DENY_ALL = [("id", "=", 0)]


def hospital_groups(env):
    """Group membership booleans for the current user.

    'front_desk_nurse' is DIRECT membership of group_hospital_front_desk_nurse,
    exactly as reception_scope.role_flags reports it. A plain Hospital Nurse
    reads False here and keeps the department-scoped branch; a front desk nurse
    reads True for BOTH this flag and 'nurse', and the front desk branch wins
    because it is consulted first.
    """
    user = env.user
    return {
        "receptionist": user.has_group(GROUP_RECEPTIONIST),
        "doctor": user.has_group(GROUP_DOCTOR),
        "nurse": user.has_group(GROUP_NURSE),
        "front_desk_nurse": user.has_group(GROUP_FRONT_DESK_NURSE),
        "manager": user.has_group(GROUP_MANAGER),
        "system_administrator": user.has_group(GROUP_SYSADMIN),
    }


def _candidate_appointment_ids(env, base_domain):
    """Appointment ids matching the caller's filters, before scoping.

    Used to bound the evaluation lookups below. Without it, the receptionist
    branch would scan every evaluation ever recorded.
    """
    return env["hospital.appointment"].search(base_domain).ids


def _nurse_domain(env, candidate_ids):
    """Appointments in a permitted department, or whose evaluation is mine.

    The second branch is what lets a nurse keep an evaluation that was handed
    to them personally even when it sits outside their departments.
    """
    permitted_ids = env.user.yoya_permitted_department_ids.ids
    assigned = env["hospital.patient.evaluation"].search(
        [
            ("assigned_nurse_id", "=", env.user.id),
            ("appointment_id", "in", candidate_ids),
        ]
    )
    assigned_appointment_ids = assigned.mapped("appointment_id").ids

    branches = []
    if permitted_ids:
        branches.append([("department_id", "in", permitted_ids)])
    if assigned_appointment_ids:
        branches.append([("id", "in", assigned_appointment_ids)])
    if not branches:
        # A nurse with no departments configured and nothing assigned sees
        # nothing. Fail closed rather than falling through to "everything".
        return DENY_ALL
    return expression.OR(branches)


def _doctor_domain(env):
    return [("doctor_id.user_id", "=", env.user.id)]


def _receptionist_domain(env, candidate_ids):
    """Front desk sees appointments that already carry an evaluation.

    This search runs as the caller, so the receptionist record rule on
    hospital.patient.evaluation (appointment_id != False) applies to it.
    """
    linked = env["hospital.patient.evaluation"].search(
        [("appointment_id", "in", candidate_ids)]
    )
    linked_appointment_ids = linked.mapped("appointment_id").ids
    if not linked_appointment_ids:
        return DENY_ALL
    return [("id", "in", linked_appointment_ids)]


def build_appointment_scope_domain(env, base_domain):
    """Return the scope domain to AND with ``base_domain``.

    ``base_domain`` is the caller's own filter (state, date, department...).
    It is passed in so the evaluation lookups stay bounded.
    """
    groups = hospital_groups(env)

    if groups["manager"] or groups["system_administrator"]:
        return []

    # BEFORE the generic nurse branch, and unrestricted for the same reason
    # yoya_reception_bridge gives this group an unrestricted ir.rule on
    # hospital.appointment: the entrance triages EVERY arrival, whatever
    # department the visit is booked into.
    #
    # This grants no appointment the group could not already reach through
    # rule_appointment_front_desk_nurse -- it removes a contradiction between
    # this API-layer domain and the model-layer rule, rather than widening
    # anything. The department-scoped branch below is untouched, so a plain
    # Hospital Nurse (and the future general nursing workspace) keeps exactly
    # the scope it has today.
    if groups["front_desk_nurse"]:
        return []

    needs_candidates = groups["nurse"] or groups["receptionist"]
    candidate_ids = _candidate_appointment_ids(env, base_domain) if needs_candidates else []

    branches = []
    if groups["doctor"]:
        branches.append(_doctor_domain(env))
    if groups["nurse"]:
        branches.append(_nurse_domain(env, candidate_ids))
    if groups["receptionist"]:
        branches.append(_receptionist_domain(env, candidate_ids))

    if not branches:
        # No hospital role at all. Deny rather than defaulting to open.
        return DENY_ALL
    return expression.OR(branches)


def scoped_appointment_domain(env, base_domain):
    return expression.AND([base_domain, build_appointment_scope_domain(env, base_domain)])


def find_appointment_in_scope(env, appointment_id):
    """Return (appointment, reason).

    reason is None when found, 'not_found' when no such appointment exists at
    all, and 'out_of_scope' when it exists but the caller may not reach it.
    """
    Appointment = env["hospital.appointment"]
    base_domain = [("id", "=", appointment_id)]

    if not Appointment.search_count(base_domain):
        return Appointment.browse(), "not_found"

    appointment = Appointment.search(scoped_appointment_domain(env, base_domain), limit=1)
    if not appointment:
        return Appointment.browse(), "out_of_scope"
    return appointment, None


def latest_evaluation_for(env, appointment):
    """Most recent evaluation for an appointment, subject to record rules.

    yoya_clinical_bridge adds unique(appointment_id), but legacy rows may
    predate it, so this deliberately orders and limits rather than assuming
    a single match.
    """
    return env["hospital.patient.evaluation"].search(
        [("appointment_id", "=", appointment.id)],
        order="evaluation_date desc, id desc",
        limit=1,
    )
