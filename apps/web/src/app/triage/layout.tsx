import { redirect } from "next/navigation";
import type { ReactNode } from "react";

import { ADMISSIONS_ROUTE, isWardOnlyNurse } from "@/lib/reception-roles";
import { loadReceptionRoles } from "@/lib/reception-session.server";

/**
 * The Evaluation Queue's entry guard, for /triage AND /triage/[appointmentId].
 *
 * A DEDICATED WARD NURSE is sent to the Admissions Desk before anything under
 * /triage renders -- so the legacy clinical shell and its sidebar are never
 * rendered for them, not merely hidden. A bookmark, the browser history or the
 * public page's "Evaluation Queue" link all end on /admissions.
 *
 * NO LOOP. The redirect goes one way: /admissions has its own layout and never
 * redirects back. Every other user -- including a nurse with any second role,
 * and an unknown session (null roles) -- renders /triage exactly as before.
 *
 * NOT AN ACCESS CONTROL. It chooses the workspace; Odoo's record rules and
 * group checks still decide what any user may read on either desk.
 */
export default async function TriageLayout({ children }: { children: ReactNode }) {
  if (isWardOnlyNurse(await loadReceptionRoles())) {
    redirect(ADMISSIONS_ROUTE);
  }
  return children;
}
