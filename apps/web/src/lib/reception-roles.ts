/**
 * Shared role logic for navigation and post-login routing.
 *
 * IMPORTANT CONTRACT NOTE
 * The reception session payload (yoya_emr_api reception_scope.role_flags)
 * exposes exactly six flags:
 *
 *     receptionist, cashier, accountant, manager,
 *     system_administrator, emergency_authorizer
 *
 * There is NO `nurse` flag. Any code reading `roles.nurse` from this payload
 * silently reads `undefined`. Nurse routing is therefore derived by
 * ELIMINATION -- a user with no reception-side role belongs in the clinical
 * workspace -- rather than by testing a flag that does not exist.
 */

export type ReceptionRoles = {
  receptionist: boolean;
  cashier: boolean;
  accountant: boolean;
  manager: boolean;
  system_administrator: boolean;
  emergency_authorizer: boolean;
};

export const RECEPTION_ROUTE = "/reception";
export const CLINICAL_ROUTE = "/triage";

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
  ];
  if (!known.some((key) => key in value)) {
    return null;
  }
  return {
    receptionist: flag("receptionist"),
    cashier: flag("cashier"),
    accountant: flag("accountant"),
    manager: flag("manager"),
    system_administrator: flag("system_administrator"),
    emergency_authorizer: flag("emergency_authorizer"),
  };
}

/** Reception queue and New Visit. Managers and admins included. */
export function canUseReception(roles: ReceptionRoles | null): boolean {
  if (!roles) {
    return false;
  }
  return roles.receptionist || roles.manager || roles.system_administrator;
}

/**
 * Clinical (Evaluation Queue) visibility.
 *
 * A pure receptionist must NOT see it. Managers and admins must. Because the
 * payload carries no nurse flag, a user with no reception-side role at all is
 * treated as clinical -- which is exactly the nurse case.
 */
export function canUseClinical(roles: ReceptionRoles | null): boolean {
  if (!roles) {
    // Fail closed for the reception-only link, open for clinical: an
    // unknown-role user still needs somewhere to land.
    return true;
  }
  if (roles.manager || roles.system_administrator) {
    return true;
  }
  return !roles.receptionist;
}

/** Where a user belongs immediately after login. */
export function landingRouteForRoles(roles: ReceptionRoles | null): string {
  return canUseReception(roles) ? RECEPTION_ROUTE : CLINICAL_ROUTE;
}
