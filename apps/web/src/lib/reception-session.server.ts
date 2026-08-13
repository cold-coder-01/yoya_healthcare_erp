import "server-only";

import { cookies } from "next/headers";

import {
  fetchReceptionSession,
  YOYA_ODOO_SESSION_COOKIE,
} from "@/lib/odoo-client";
import { parseReceptionRoles, type ReceptionRoles } from "@/lib/reception-roles";

/**
 * Server-side role lookup for shell navigation.
 *
 * Runs on the server so the sidebar renders with the correct items on first
 * paint -- no client round-trip, no flash of a menu the user may not use.
 *
 * `import "server-only"` makes importing this from a client component a BUILD
 * error, which is what keeps the Odoo cookie and base URL off the browser.
 *
 * Never throws: navigation must not be able to take a page down. A failed
 * lookup returns null and the callers fail closed on reception-only links.
 */
export async function loadReceptionRoles(): Promise<ReceptionRoles | null> {
  return (await loadReceptionSession())?.roles ?? null;
}

export type ReceptionSessionSummary = {
  roles: ReceptionRoles | null;
  userName: string | null;
  companyName: string | null;
};

/**
 * The same session lookup, but keeping the identity fields the Odoo endpoint
 * already returns (`user.name`, `company.name`) instead of discarding them.
 *
 * The Front Desk shell needs them for its header: the hospital name as the
 * brand and the signed-in nurse in the user menu. Nothing new is fetched --
 * /yoya-emr/api/v1/reception/session has always sent both.
 *
 * Never throws, for the same reason loadReceptionRoles never did: a shell must
 * not be able to take a page down. A failed lookup yields nulls and the header
 * falls back to static text.
 */
export async function loadReceptionSession(): Promise<ReceptionSessionSummary | null> {
  try {
    const sessionId = (await cookies()).get(YOYA_ODOO_SESSION_COOKIE)?.value;
    if (!sessionId) {
      return null;
    }

    const result = await fetchReceptionSession(sessionId);

    if (!result.body.success) {
      return null;
    }

    const data = result.body.data;
    const text = (value: unknown) =>
      typeof value === "string" && value.trim() ? value.trim() : null;

    return {
      roles: parseReceptionRoles(data?.roles),
      userName: text(data?.user?.name),
      companyName: text(data?.company?.name),
    };
  } catch {
    return null;
  }
}
