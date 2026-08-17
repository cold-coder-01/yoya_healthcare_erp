/**
 * Shared role logic for navigation and post-login routing.
 *
 * IMPORTANT CONTRACT NOTE
 * The reception session payload (yoya_emr_api reception_scope.role_flags)
 * exposes exactly seven flags:
 *
 *     receptionist, cashier, accountant, manager,
 *     system_administrator, emergency_authorizer, front_desk_nurse
 *
 * There is still NO `nurse` flag. Any code reading `roles.nurse` from this
 * payload silently reads `undefined`. Plain-nurse routing is therefore derived
 * by ELIMINATION -- a user with no reception-side role belongs in the clinical
 * workspace -- rather than by testing a field that does not exist.
 *
 * `front_desk_nurse` is the ONE exception and the reason it was added: a Front
 * Desk Nurse could not be told apart from a plain Nurse by elimination, because
 * the Odoo group IMPLIES Hospital Nurse and carries no reception-side flag of
 * its own. It is direct membership of
 * yoya_reception_bridge.group_hospital_front_desk_nurse and nothing else -- not
 * a receptionist, not a nurse, not a title, not a menu.
 */

export type ReceptionRoles = {
  receptionist: boolean;
  cashier: boolean;
  accountant: boolean;
  manager: boolean;
  system_administrator: boolean;
  emergency_authorizer: boolean;
  front_desk_nurse: boolean;
};

export const RECEPTION_ROUTE = "/reception";
export const CLINICAL_ROUTE = "/triage";
export const FRONT_DESK_ROUTE = "/front-desk";
export const CASHIER_ROUTE = "/cashier";

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

/** Parse an untrusted payload into roles. Unknown shape -> null, never a guess. */
export function parseReceptionRoles(value: unknown): ReceptionRoles | null {
  if (!isRecord(value)) {
    return null;
  }
  const flag = (key: string) => value[key] === true;
  const known = [
    "receptionist",
    "cashier",
    "accountant",
    "manager",
    "system_administrator",
    "emergency_authorizer",
    "front_desk_nurse",
  ];
  if (!known.some((key) => key in value)) {
    return null;
  }
  // Strict `=== true`, so a payload from an Odoo instance that has not been
  // upgraded yet (no front_desk_nurse key) reads false and the user keeps their
  // existing landing route. Absence never grants the front desk.
  return {
    receptionist: flag("receptionist"),
    cashier: flag("cashier"),
    accountant: flag("accountant"),
    manager: flag("manager"),
    system_administrator: flag("system_administrator"),
    emergency_authorizer: flag("emergency_authorizer"),
    front_desk_nurse: flag("front_desk_nurse"),
  };
}

/**
 * Registration links (Reception Queue, New Visit).
 *
 * A Front Desk Nurse is included because registration IS part of their job:
 * the front desk nurse performs reception, intake and triage as one role.
 * reception_scope.RECEPTION_GROUPS already lists the Front Desk Nurse group, so
 * the API has always accepted these calls from them -- omitting the flag here
 * only hid links the user was already authorized to use, leaving the target
 * role with no way to register a walk-in at all.
 *
 * These pages are INTERIM. B2 folds registration into /front-desk itself.
 */
export function canUseReception(roles: ReceptionRoles | null): boolean {
  if (!roles) {
    return false;
  }
  return (
    roles.front_desk_nurse ||
    roles.receptionist ||
    roles.manager ||
    roles.system_administrator
  );
}

/**
 * Evaluation Queue visibility.
 *
 * A Front Desk Nurse must see it even when they also carry the legacy
 * Receptionist group, which would otherwise suppress the link and strand them
 * between registration and triage -- the exact workspace-hopping the front desk
 * role exists to remove. Triage mutations live only on /triage until B2.
 *
 * A legacy standalone receptionist still must NOT see it: they are not a nurse.
 * Because the payload carries no nurse flag, a user with no reception-side role
 * at all is treated as clinical, which is the plain-nurse case.
 */
export function canUseClinical(roles: ReceptionRoles | null): boolean {
  if (!roles) {
    // Fail closed for the reception-only link, open for clinical: an
    // unknown-role user still needs somewhere to land.
    return true;
  }
  if (roles.front_desk_nurse || roles.manager || roles.system_administrator) {
    return true;
  }
  return !roles.receptionist;
}

/**
 * Front Desk workstation visibility.
 *
 * Authoritative group membership ONLY. Deliberately narrower than the API's
 * may_front_desk(), which also lets a plain Nurse, Receptionist, Manager or
 * Admin READ the worklist. Reusing that broader check here would drag every
 * receptionist out of the Reception Queue and into the front desk, which is
 * exactly the confusion this flag exists to prevent.
 */
export function canUseFrontDesk(roles: ReceptionRoles | null): boolean {
  return roles?.front_desk_nurse === true;
}

/**
 * Cashier Desk visibility.
 *
 * Mirrors the server's CASHIER_DESK_GROUPS (yoya_emr_api reception_scope),
 * which is OPERATIONAL_INTAKE_GROUPS: cashier, accountant, manager, admin.
 * The server refuses the worklist for anyone else, so this only decides
 * whether the link and the landing route are offered.
 *
 * A Front Desk Nurse is deliberately absent, mirroring the server's exclusion
 * of the Cashier from the front-desk worklist. The two workstations do not
 * read each other's queues.
 */
export function canUseCashier(roles: ReceptionRoles | null): boolean {
  if (!roles) {
    return false;
  }
  return (
    roles.cashier ||
    roles.accountant ||
    roles.manager ||
    roles.system_administrator
  );
}

/**
 * Where a user belongs immediately after login.
 *
 * Front Desk is checked FIRST, ahead of the reception/clinical split. Without
 * that precedence a Front Desk Nurse falls through to the old Evaluation Queue
 * by elimination (no reception-side role), which was the reported bug.
 *
 * This ordering also means a Manager or Admin who has been deliberately granted
 * Front Desk Nurse lands on the front desk rather than the Reception Queue. The
 * group is assigned to nobody by default and only ever by an explicit
 * operational decision, so holding it is a statement of intent -- and the
 * sidebar still offers them every link their other roles allow, so nothing is
 * lost. A manager WITHOUT the group is completely unaffected.
 *
 * CASHIER sits between front desk and reception, and its absence was a real
 * bug: a pure Cashier holds no front-desk and no reception-side role, so they
 * fell through BOTH branches by elimination and landed on the clinical
 * Evaluation Queue -- a workspace they cannot act in. `roles.cashier` was
 * parsed by parseReceptionRoles all along and read by nothing.
 *
 * It is placed BELOW front desk for the reason above (that group is an explicit
 * grant), and ABOVE reception because a reception-side role is broad: a Manager
 * who also holds Cashier is far more likely to want the Reception Queue as
 * their default, and canUseReception already catches them first... which is
 * exactly why the cashier branch tests the NARROW flag. See canUseCashier:
 * manager/admin do get the cashier LINK, but a manager still LANDS on
 * reception, because canUseReception is evaluated on the same pass.
 */
export function landingRouteForRoles(roles: ReceptionRoles | null): string {
  if (canUseFrontDesk(roles)) {
    return FRONT_DESK_ROUTE;
  }
  if (canUseReception(roles)) {
    return RECEPTION_ROUTE;
  }
  // Narrow on purpose: only a user whose reception-side identity is "cashier"
  // (or accountant) lands here. Manager and admin were already routed above.
  if (canUseCashier(roles)) {
    return CASHIER_ROUTE;
  }
  return CLINICAL_ROUTE;
}
