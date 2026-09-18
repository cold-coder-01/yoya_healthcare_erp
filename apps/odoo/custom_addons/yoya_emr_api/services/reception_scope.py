"""Role, capability and scope resolution for the reception API.

Every reception endpoint decides authorization here, explicitly, before it
touches a record. Nothing relies on the Next.js layer, and nothing elevates:
if the calling user may not do it, it does not happen.
"""
import logging
from datetime import datetime, time

import pytz

from odoo.addons.hospital_billing.models.charge_line import (
    OPERATIONAL_INTAKE_GROUPS,
)
from odoo.osv import expression

_logger = logging.getLogger(__name__)

GROUP_RECEPTIONIST = "hospital_management.group_hospital_receptionist"
GROUP_DOCTOR = "hospital_management.group_hospital_doctor"
GROUP_NURSE = "hospital_management.group_hospital_nurse"
GROUP_LAB_TECHNICIAN = "hospital_management.group_hospital_lab_technician"
GROUP_ACCOUNTANT = "hospital_management.group_hospital_accountant"
GROUP_MANAGER = "hospital_management.group_hospital_manager"
GROUP_SYSADMIN = "hospital_management.group_hospital_system_administrator"
GROUP_CASHIER = "hospital_billing.group_hospital_cashier"
GROUP_EMERGENCY_AUTHORIZER = (
    "yoya_reception_bridge.group_hospital_emergency_authorizer"
)
GROUP_FRONT_DESK_NURSE = (
    "yoya_reception_bridge.group_hospital_front_desk_nurse"
)
GROUP_INSURANCE_OFFICER = "hospital_billing.group_hospital_insurance_officer"
# Owned by hospital_radiology (Radiology Slice 0A). yoya_emr_api reaches that
# module through yoya_clinical_bridge, which depends on it explicitly.
GROUP_RADIOLOGY_TECHNICIAN = "hospital_radiology.group_hospital_radiology_technician"
GROUP_RADIOLOGIST = "hospital_radiology.group_hospital_radiologist"

# Who may run the guided registration workflow at all.
#
# MIRRORS hospital.reception.workflow.REGISTRATION_GROUPS, which enforces this
# again inside every workflow method. This copy is a fail-fast for the HTTP
# layer, never the only control -- and test_front_desk_worklist asserts the two
# tuples agree, so a change in the model cannot silently widen the API.
RECEPTION_GROUPS = (
    GROUP_FRONT_DESK_NURSE,
    GROUP_RECEPTIONIST,
    GROUP_MANAGER,
    GROUP_SYSADMIN,
)

# Who may work the front-desk worklist: the intake roles above, plus a plain
# Hospital Nurse, who does the triage half of the job today. Deliberately
# EXCLUDES Cashier -- the cashier has their own payment endpoint and must gain
# no clinical-edit surface from this read model.
FRONT_DESK_GROUPS = (
    GROUP_FRONT_DESK_NURSE,
    GROUP_NURSE,
    GROUP_RECEPTIONIST,
    GROUP_MANAGER,
    GROUP_SYSADMIN,
)

# Who may authorize an emergency bypass. Mirrors
# yoya_reception_bridge.hospital_encounter.EMERGENCY_AUTHORIZER_GROUPS; the
# encounter enforces it again on write, so this is a fail-fast, not the
# only control.
EMERGENCY_GROUPS = (GROUP_EMERGENCY_AUTHORIZER, GROUP_MANAGER, GROUP_SYSADMIN)

# hospital_billing.billing_engine.AUTHORIZE_GROUPS, redeclared so a change
# there surfaces as a test failure here rather than silently widening access.
PAYER_AUTHORIZATION_GROUPS = (
    GROUP_RECEPTIONIST,
    GROUP_ACCOUNTANT,
    GROUP_MANAGER,
    GROUP_SYSADMIN,
)

# IMPORTED, not restated. This used to be a hand-maintained copy that had
# already drifted from the module it claimed to mirror. It is now the same
# object hospital.charge.payment.wizard.action_confirm enforces, so a change
# there cannot silently disagree with what this API reports.
#
# The standalone Cashier group is now IN this tuple and the receptionist is
# not. The API endpoint nevertheless remains disabled -- see
# record_payment_api_enabled below and the 501 payment endpoint.
RECEIPT_GROUPS = OPERATIONAL_INTAKE_GROUPS

HOSPITAL_TIME_ZONE = "Africa/Addis_Ababa"


def role_flags(env):
    """Raw group membership for the current user.

    NOTE ON front_desk_nurse: this is DIRECT membership of
    group_hospital_front_desk_nurse, and it is deliberately NOT the same
    question as may_front_desk() below.

      may_front_desk()   -> may this user OPEN the worklist (broad: also plain
                            Nurse, Receptionist, Manager, Admin)
      front_desk_nurse   -> is this user actually a Front Desk Nurse (narrow)

    The front-end uses the narrow flag to decide which workspace a user lands
    in, so a plain Receptionist or plain Nurse is never mistaken for front-desk
    staff. Because the group only IMPLIES Nurse and nothing implies IT, the
    check stays exact: a plain Nurse reads False, a Front Desk Nurse reads True
    for both this flag and the inherited "nurse" rights.

    Routing is convenience only -- every front-desk endpoint still enforces its
    own group check in controllers/front_desk.py.
    """
    user = env.user
    return {
        "receptionist": user.has_group(GROUP_RECEPTIONIST),
        "cashier": user.has_group(GROUP_CASHIER),
        "accountant": user.has_group(GROUP_ACCOUNTANT),
        "manager": user.has_group(GROUP_MANAGER),
        "system_administrator": user.has_group(GROUP_SYSADMIN),
        "emergency_authorizer": user.has_group(GROUP_EMERGENCY_AUTHORIZER),
        "front_desk_nurse": user.has_group(GROUP_FRONT_DESK_NURSE),
        # NARROW, like front_desk_nurse: direct membership of the officer group,
        # not "may open the desk" (which also admits manager and admin). The
        # front end routes a PURE officer to their own workspace with it; a
        # manager who also holds it keeps their existing landing page.
        "insurance_officer": user.has_group(GROUP_INSURANCE_OFFICER),
        # Authoritative Odoo group membership, and NOT the same kind of flag as
        # the two above. Read the difference before routing on it.
        #
        # Nothing implies group_hospital_front_desk_nurse or the officer group,
        # so those two are effectively direct membership. group_hospital_doctor
        # IS implied: hospital_management's group_hospital_manager carries
        # implied_ids = receptionist + doctor + nurse, and
        # group_hospital_system_administrator implies manager. has_group()
        # honours the implication chain, so a MANAGER AND AN ADMIN BOTH READ
        # TRUE HERE. That is correct -- they really do hold the Doctor group,
        # and _assert_may_start_consultation lets them start a consultation on
        # that basis -- but it means this flag alone cannot answer "is this
        # person a doctor rather than a manager".
        #
        # What keeps the routing right is PRECEDENCE, not narrowness:
        # landingRouteForRoles tests the reception branch (which claims manager
        # and admin) before it reaches the doctor branch, so only a user whose
        # remaining identity is Doctor lands on /doctor. See the ordering note
        # in apps/web/src/lib/reception-roles.ts.
        #
        # Derived from group membership ONLY: never from a job title, never
        # from the presence of a hospital.doctor record, and never inferred
        # from the ABSENCE of another role -- that last one is what would drag
        # every plain nurse out of /triage.
        "doctor": user.has_group(GROUP_DOCTOR),
        # Added with the Laboratory Desk, and NARROW like front_desk_nurse and
        # insurance_officer: nothing implies group_hospital_lab_technician, so
        # this is effectively direct membership rather than may_lab_desk()
        # (which also admits manager and admin).
        #
        # REPORTING ONLY. It grants nothing. Every /lab/* endpoint still decides
        # for itself through may_lab_desk(), and a client that sets this flag by
        # hand gains exactly nothing -- the desk answers 403 on the server's own
        # check. What it fixes is that a Lab Technician had no landing route at
        # all: holding no reception-side role and not the doctor group, they
        # fell through every branch of landingRouteForRoles and were dropped on
        # /triage, a clinical workspace they hold no ACL for.
        #
        # A manager and an admin read FALSE here, unlike `doctor`: manager
        # implies receptionist + doctor + nurse and NOT lab technician. They are
        # claimed by the reception branch long before it matters either way.
        "lab_technician": user.has_group(GROUP_LAB_TECHNICIAN),
        # Added with the Radiology Desk, and NARROW for the same reason as
        # lab_technician: nothing implies either Radiology group (Slice 0A), so
        # these are direct membership, and a manager or admin reads FALSE.
        #
        # REPORTING ONLY. Every /radiology/* endpoint decides for itself through
        # may_rad_desk(). What these fix is the landing route: a pure Radiology
        # Technician or Radiologist holds no reception-side, doctor or laboratory
        # role, and would otherwise fall through to /triage.
        "radiology_technician": user.has_group(GROUP_RADIOLOGY_TECHNICIAN),
        "radiologist": user.has_group(GROUP_RADIOLOGIST),
    }


def _in_any(env, groups):
    user = env.user
    return any(user.has_group(group) for group in groups)


def capability_flags(env):
    """What this user may actually do through THIS API."""
    return {
        "create_visit": _in_any(env, RECEPTION_GROUPS),
        "create_patient_through_workflow": _in_any(env, RECEPTION_GROUPS),
        "send_to_triage": _in_any(env, RECEPTION_GROUPS),
        "emergency_bypass": _in_any(env, EMERGENCY_GROUPS),
        "payer_authorization": _in_any(env, PAYER_AUTHORIZATION_GROUPS),
        # Reflects the underlying Odoo permission, which is what decides
        # whether the user could take payment in the Odoo UI. Now True for a
        # standalone cashier and False for a plain receptionist, matching the
        # boundary enforced in action_confirm.
        "record_payment": _in_any(env, RECEIPT_GROUPS),
        # NOT a statement about backend route availability.
        #
        # The backend cashier route DOES exist and does record payment:
        #   POST /yoya-emr/api/v1/cashier/visits/<appointment_id>/payment
        # (yoya_emr_api/controllers/cashier.py). The earlier comment here
        # claimed "this API records no payment for anyone", which stopped being
        # true the moment that controller landed.
        #
        # This flag means "the FRONT-END payment feature is enabled". The
        # Next.js visit detail screen gates its payment affordance on it
        # (apps/web/src/app/reception/visits/[appointmentId]/visit-detail-client.tsx),
        # so flipping it to True here would expose a payment button in an
        # untested UI flow. It therefore stays False until that UI work is
        # deliberately done. The separate reception payment endpoint remains
        # 501 for the same reason.
        #
        # A client wanting to know whether the CALLER may take money should
        # read "record_payment" above, which is the real permission.
        "record_payment_api_enabled": False,
    }


def may_reception(env):
    return _in_any(env, RECEPTION_GROUPS)


def may_emergency_bypass(env):
    return _in_any(env, EMERGENCY_GROUPS)


def may_authorize_payer(env):
    return _in_any(env, PAYER_AUTHORIZATION_GROUPS)


def may_record_payment(env):
    return _in_any(env, RECEIPT_GROUPS)


def may_front_desk(env):
    """May this user open the front-desk worklist at all."""
    return _in_any(env, FRONT_DESK_GROUPS)


def may_intake(env):
    """May this user register patients and open visits."""
    return _in_any(env, RECEPTION_GROUPS)


def may_triage(env):
    """May this user record and complete a nursing evaluation.

    hospital.patient.evaluation grants read/write/create to Nurse, Doctor,
    Manager and Admin in hospital_management's ACL; Front Desk Nurse inherits it
    by implying Nurse. The receptionist holds READ only and is absent here on
    purpose -- triage is a clinical act.
    """
    return _in_any(
        env,
        (GROUP_FRONT_DESK_NURSE, GROUP_NURSE, GROUP_DOCTOR, GROUP_MANAGER, GROUP_SYSADMIN),
    )


# Who may OPEN the Cashier Desk (a read gate), as opposed to who may take money
# (OPERATIONAL_INTAKE_GROUPS, the write gate). They are the same tuple today and
# are still stated separately on purpose: reading the payment queue and moving
# cash are different acts, and a future read-only supervisor role belongs here
# without being handed intake rights.
#
# The Front Desk Nurse is deliberately ABSENT, mirroring FRONT_DESK_GROUPS'
# exclusion of the Cashier. The two workstations do not read each other's
# queues.
CASHIER_DESK_GROUPS = OPERATIONAL_INTAKE_GROUPS


def may_cashier_desk(env):
    """May this user open the Cashier Desk and read its worklist?"""
    return _in_any(env, CASHIER_DESK_GROUPS)


# Who may OPEN the Insurance/Credit Desk (a read gate). Mirrors
# hospital_billing.charge_responsibility.RESPONSIBILITY_AUTHORITY, which is the
# WRITE gate the model enforces on every authorize/cancel -- restated here so a
# request is refused before it touches a record, never instead of that check.
#
# The ACCOUNTANT IS DELIBERATELY ABSENT, exactly as they are from
# RESPONSIBILITY_AUTHORITY: they read sponsor responsibility for finance work,
# but the party who books the receivable does not decide it.
INSURANCE_CREDIT_GROUPS = (
    GROUP_INSURANCE_OFFICER,
    GROUP_MANAGER,
    GROUP_SYSADMIN,
)


def may_insurance_credit(env):
    """May this user open the Insurance/Credit Desk and authorize a sponsor?"""
    return _in_any(env, INSURANCE_CREDIT_GROUPS)


# Who may OPEN the Doctor Desk (a read gate).
#
# MIRRORS the authorization half of
# yoya_reception_bridge.hospital_appointment._assert_may_start_consultation,
# which admits the ASSIGNED doctor plus CONSULTATION_OVERRIDE_GROUPS
# (manager, admin). The per-visit assignment half cannot be a group tuple, so
# it is not restated here: scoping resolves it (clinical_scope._doctor_domain
# restricts a pure doctor to doctor_id.user_id = me) and the model method
# enforces it again on the mutation.
#
# Deliberately EXCLUDES Nurse, Front Desk Nurse, Receptionist, Cashier and
# Accountant. The nurse's triage surface is /front-desk and /triage; the Doctor
# Desk is not a second door into it.
#
# It is the same tuple front_desk_capability_flags already reports as
# "start_consultation_role", which is not a coincidence: the desk exists to
# perform that one act.
DOCTOR_DESK_GROUPS = (GROUP_DOCTOR, GROUP_MANAGER, GROUP_SYSADMIN)


def may_doctor_desk(env):
    """May this user open the Doctor Desk and read its worklist?"""
    return _in_any(env, DOCTOR_DESK_GROUPS)


# Who may OPEN the Laboratory Desk (a read gate).
#
# THE BENCH ROLES, AND ONLY THE BENCH ROLES. group_hospital_lab_technician is
# the role hospital_management already ships for laboratory work, and it is the
# one yoya_clinical_bridge's rule_laboratory_request_operations /
# rule_laboratory_result_operations grant the cross-patient worklist to. Manager
# and System Administrator are named for the reason every other desk names
# them: Manager IMPLIES Doctor, and an oversight role that could not open the
# workstation it supervises is a support call, not a security control.
#
# THIS GATE IS DELIBERATELY NARROWER THAN THE ORM. Nurse, Receptionist and the
# DPO all hold a read ACL on hospital.laboratory.request and
# hospital.laboratory.result in hospital_management, with NO record rule
# narrowing it -- so the ORM alone would let all three read the whole bench
# queue. That exposure predates this module and is not ours to widen or to
# inherit: an operational workstation is not the same thing as a read ACL, and
# a desk that opened for every role that can technically SELECT the rows would
# turn a known data-visibility gap into a shipped feature. The gap itself is
# left exactly as it is (B9); this endpoint simply does not participate in it.
#
# The DOCTOR is excluded for the same reason in reverse. A doctor holds
# read/write/create on hospital.laboratory.request -- they order the tests --
# but the laboratory is not a second Doctor Desk, and their laboratory surface
# is /doctor's Orders and Results tabs, which are scoped to their own patients.
#
# Cashier, Pharmacist and Accountant hold no laboratory ACL at all and are
# absent here too, so the gate and the ORM agree about them.
LAB_DESK_GROUPS = (GROUP_LAB_TECHNICIAN, GROUP_MANAGER, GROUP_SYSADMIN)


def may_lab_desk(env):
    """May this user open the Laboratory Desk and read its worklist?"""
    return _in_any(env, LAB_DESK_GROUPS)


def lab_desk_capability_flags(env):
    """What the Laboratory Desk may do. Every flag mirrors a server-side guard.

    SLICE 1 IS READ-ONLY, and this reports exactly that. `collect_sample`,
    `enter_result`, `validate_result` and `release_result` are deliberately
    ABSENT rather than present-and-False: no endpoint implements them yet, so a
    flag for them would invite a control that has nothing to call. They arrive
    with the slices that implement the transitions.
    """
    return {
        "lab_desk": may_lab_desk(env),
    }


# Who may OPEN the Radiology Desk (a read gate).
#
# THE IMAGING ROLES FROM RADIOLOGY SLICE 0A, AND OVERSIGHT. Radiology Technician
# and Radiologist are the two groups hospital_radiology now owns, and the ones
# yoya_clinical_bridge's rule_radiology_*_operations grant the hospital-wide
# queue to. Manager and System Administrator are named for the reason every
# other desk names them.
#
# THIS GATE IS NARROWER THAN THE ORM, deliberately and in two directions:
#
#   * Receptionist, Nurse and the DPO hold a read ACL on every Radiology model
#     with no record rule narrowing it. That is known debt; this desk does not
#     inherit it, so they get 403 here, never a populated queue.
#   * The DOCTOR holds read/write/create on their own radiology requests. Their
#     radiology surface is the Doctor Desk's Orders and Results tabs, scoped to
#     their own patients; the imaging department's queue is not a second one.
#
# Lab Technician is ABSENT. Slice 0A took Radiology away from that group, and
# this gate must not quietly hand it back.
#
# Authorization is group membership, never "can read the model": a role that
# can SELECT hospital.radiology.request is not thereby an imaging workstation.
RAD_DESK_GROUPS = (
    GROUP_RADIOLOGY_TECHNICIAN,
    GROUP_RADIOLOGIST,
    GROUP_MANAGER,
    GROUP_SYSADMIN,
)


def may_rad_desk(env):
    """May this user open the Radiology Desk and read its worklist?"""
    return _in_any(env, RAD_DESK_GROUPS)


def rad_desk_role_flags(env):
    """The Radiology Desk's own role header: which of ITS roles the user holds.

    Only the four groups the gate knows about. No other group, no group id, and
    no statement about any other workstation: a header that says "Radiologist"
    has no business telling the browser whether the same person is a cashier.

    `manager` and `system_admin` are direct membership as has_group reports it,
    so an admin reads TRUE for both (admin implies manager).
    """
    user = env.user
    return {
        "radiology_technician": user.has_group(GROUP_RADIOLOGY_TECHNICIAN),
        "radiologist": user.has_group(GROUP_RADIOLOGIST),
        "manager": user.has_group(GROUP_MANAGER),
        "system_admin": user.has_group(GROUP_SYSADMIN),
    }


def rad_desk_capability_flags(env):
    """What the Radiology Desk may do. Every flag mirrors a server-side guard.

    SLICE 2 adds exactly the two transitions it implements: `schedule_study`
    and `start_exam`, open to every desk role (no separation of duties yet).
    No report, validate, release or image flag exists -- not even as False --
    for the reason lab_desk_capability_flags gives: a flag with no endpoint
    behind it is an invitation to build a button that has nothing to call.

    These say which ACTS the role may attempt. Whether ONE request may be
    scheduled or started is the request's own lane, re-checked under a row lock
    by the endpoint on every call.
    """
    allowed = may_rad_desk(env)
    return {
        "radiology_desk": allowed,
        "schedule_study": allowed,
        "start_exam": allowed,
    }


def doctor_capability_flags(env):
    """What the Doctor Desk may do. Every flag mirrors a server-side guard.

    Deliberately NARROW. There is no payment flag, no sponsor-authorization
    flag and no intake flag, because a doctor holds none of them and a flag the
    client could not act on is an invitation to build a button that 403s.

    'start_consultation_role' is the GROUP half only. Whether THIS doctor may
    start THIS visit also depends on assignment, which is per-visit and is
    decided by _assert_may_start_consultation; the serializer resolves it per
    row rather than pretending a group tuple could answer it.
    """
    return {
        "doctor_desk": may_doctor_desk(env),
        "start_consultation_role": may_doctor_desk(env),
    }


def insurance_credit_capability_flags(env):
    """What the desk may do. Every flag mirrors a server-side guard."""
    return {
        "insurance_credit_desk": may_insurance_credit(env),
        "authorize_sponsor": may_insurance_credit(env),
        # An officer is not a cashier. Reported so the desk can say who takes
        # the residual without offering to take it.
        "record_payment": may_record_payment(env),
    }


def cashier_capability_flags(env):
    """What the Cashier Desk may do. Every flag mirrors a server-side guard.

    Deliberately NARROW. Sponsor authorization, eligibility editing and
    consultation start are absent because the Cashier holds none of them, and a
    flag the client could not act on anyway is an invitation to build a button
    that 403s.
    """
    return {
        "cashier_desk": may_cashier_desk(env),
        "record_payment": may_record_payment(env),
        # An accounting act. A cashier sees False and the server enforces it
        # again in hospital_billing.
        "post_receipt_accounting": _in_any(
            env, (GROUP_ACCOUNTANT, GROUP_MANAGER, GROUP_SYSADMIN)
        ),
        # Reported so the desk can name the role that must unblock a visit whose
        # sponsor share is unauthorized, WITHOUT offering the action.
        "authorize_sponsor": may_authorize_payer(env),
    }


def front_desk_capability_flags(env):
    """Capabilities the front-desk workstation needs, all authoritative.

    Every flag mirrors a guard that is enforced again in the model layer, so a
    client cannot act on a flag it was not really granted.
    """
    return {
        "front_desk": may_front_desk(env),
        "intake": may_intake(env),
        "triage": may_triage(env),
        "emergency_bypass": may_emergency_bypass(env),
        "payer_authorization": may_authorize_payer(env),
        # False for a front desk nurse. The cashier records payment through
        # /cashier/visits/<id>/payment, enforced by OPERATIONAL_INTAKE_GROUPS.
        "record_payment": may_record_payment(env),
        # Consultation start is the assigned doctor, manager or admin only, and
        # hospital_appointment._assert_may_start_consultation decides it. This
        # flag reports the group half; assignment is per-visit, so the row-level
        # permitted_actions resolve it properly.
        "start_consultation_role": _in_any(
            env, (GROUP_DOCTOR, GROUP_MANAGER, GROUP_SYSADMIN)
        ),
    }


def hospital_day_bounds_utc(env, day):
    """Naive UTC bounds for a calendar day in hospital-local time.

    Odoo stores datetimes as naive UTC. Comparing them against a day computed
    in the server's or caller's timezone silently shifts the queue by the UTC
    offset, which is +03:00 here.
    """
    tz = pytz.timezone(HOSPITAL_TIME_ZONE)
    start = tz.localize(datetime.combine(day, time.min)).astimezone(pytz.UTC)
    end = tz.localize(datetime.combine(day, time.max)).astimezone(pytz.UTC)
    return start.replace(tzinfo=None), end.replace(tzinfo=None)


def hospital_today(env):
    return datetime.now(pytz.timezone(HOSPITAL_TIME_ZONE)).date()


def reception_queue_domain(
    base_domain=None,
    department_id=None,
    doctor_id=None,
    visit_type=None,
):
    """Only visits the reception workflow actually created.

    A legacy appointment carries no reception metadata, no card issuance and
    no clearance persistence, so showing it in the reception queue would
    invite actions that cannot complete.
    """
    domain = list(base_domain or [])
    domain.append(("reception_workflow_managed", "=", True))
    if department_id:
        domain.append(("department_id", "=", department_id))
    if doctor_id:
        domain.append(("doctor_id", "=", doctor_id))
    if visit_type:
        domain.append(("visit_type", "=", visit_type))
    return domain


def search_text_domain(term):
    """Reception patient lookup. Scalar identity fields only."""
    term = (term or "").strip()
    if not term:
        return expression.FALSE_DOMAIN
    return expression.OR(
        [
            [("identification_code", "ilike", term)],
            [("name", "ilike", term)],
            [("phone", "ilike", term)],
            [("mobile", "ilike", term)],
        ]
    )
