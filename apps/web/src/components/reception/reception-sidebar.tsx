import Link from "next/link";

import {
  canUseCashier,
  canUseInsuranceCredit,
  canUseClinical,
  canUseFrontDesk,
  canUseReception,
  CASHIER_ROUTE,
  INSURANCE_CREDIT_ROUTE,
  FRONT_DESK_ROUTE,
  type ReceptionRoles,
} from "@/lib/reception-roles";

/**
 * Role-aware navigation.
 *
 * The previous version carried a `roles` array on each item but never read
 * it, so every user saw every link -- a pure receptionist was offered the
 * Evaluation Queue. Visibility is now actually derived from the session.
 *
 * This is navigation only. It hides links a user cannot use; it is not an
 * access control. The real enforcement is in Odoo (record rules and the
 * explicit group checks in yoya_emr_api).
 */
export default function ReceptionSidebar({
  roles,
}: {
  roles: ReceptionRoles | null;
}) {
  const showFrontDesk = canUseFrontDesk(roles);
  const showReception = canUseReception(roles);
  const showClinical = canUseClinical(roles);
  // Offered to anyone who may open the desk, INCLUDING a manager or accountant
  // who lands elsewhere. A landing route is a default, not a restriction.
  const showCashier = canUseCashier(roles);
  const showInsuranceCredit = canUseInsuranceCredit(roles);

  // B2.2 RETIRED THE SCAFFOLDING.
  //
  // Reception Queue, New Visit and Evaluation Queue were interim links: until
  // /front-desk could register AND triage, a Front Desk Nurse had to hop to
  // /reception/new to open a visit and to /triage to record one, and without
  // those links the role could not finish the workflow it owns.
  //
  // /front-desk now does both, so for a Front Desk Nurse the daily workspace is
  // exactly one destination and the hops are gone. The pages themselves are NOT
  // deleted -- they stay reachable for migration and admin, and a plain Nurse
  // still gets /triage and a legacy Receptionist still gets /reception, which
  // is why the checks below are role-scoped rather than removed outright.
  const items = [
    { label: "Front Desk Queue", href: FRONT_DESK_ROUTE, visible: showFrontDesk },
    {
      label: "Reception Queue",
      href: "/reception",
      visible: showReception && !showFrontDesk,
    },
    {
      label: "New Visit",
      href: "/reception/new",
      visible: showReception && !showFrontDesk,
    },
    {
      label: "Evaluation Queue",
      href: "/triage",
      visible: showClinical && !showFrontDesk,
    },
    { label: "Cashier Desk", href: CASHIER_ROUTE, visible: showCashier },
    {
      label: "Insurance / Credit",
      href: INSURANCE_CREDIT_ROUTE,
      visible: showInsuranceCredit,
    },
  ].filter((item) => item.visible);

  return (
    <aside className="hidden w-64 shrink-0 bg-emerald-950 text-white lg:flex lg:flex-col">
      <div className="border-b border-white/10 px-5 py-5">
        <div className="text-lg font-semibold tracking-tight">YOYA EMR</div>
        <div className="mt-1 text-xs text-emerald-100">
          {showFrontDesk ? "Front Desk Workspace" : "Reception Workspace"}
        </div>
      </div>
      <nav aria-label="Reception navigation" className="flex-1 space-y-1 px-3 py-4">
        {items.map((item) => (
          <Link
            key={item.label}
            href={item.href}
            className="block rounded-md bg-white/10 px-3 py-2 text-sm font-medium text-white ring-1 ring-white/10 transition hover:bg-white/20"
          >
            {item.label}
          </Link>
        ))}
      </nav>
      <div className="border-t border-white/10 px-5 py-4 text-xs text-emerald-100/75">
        {showFrontDesk
          ? "Front desk intake and arrival triage"
          : "Receptionist operations only"}
      </div>
    </aside>
  );
}
