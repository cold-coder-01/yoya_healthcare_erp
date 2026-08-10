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
    """Raw group membership for the current user."""
    user = env.user
    return {
        "receptionist": user.has_group(GROUP_RECEPTIONIST),
        "cashier": user.has_group(GROUP_CASHIER),
        "accountant": user.has_group(GROUP_ACCOUNTANT),
        "manager": user.has_group(GROUP_MANAGER),
        "system_administrator": user.has_group(GROUP_SYSADMIN),
        "emergency_authorizer": user.has_group(GROUP_EMERGENCY_AUTHORIZER),
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
