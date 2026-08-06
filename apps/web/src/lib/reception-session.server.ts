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
  try {
    const sessionId = (await cookies()).get(YOYA_ODOO_SESSION_COOKIE)?.value;
    if (!sessionId) {
      return null;
    }

    const result = await fetchReceptionSession(sessionId);

    if (!result.body.success) {
      return null;
    }
    return parseReceptionRoles(result.body.data?.roles);
  } catch {
    return null;
  }
}
