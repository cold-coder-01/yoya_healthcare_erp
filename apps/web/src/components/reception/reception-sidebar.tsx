import Link from "next/link";

import {
  canUseClinical,
  canUseReception,
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
  const showReception = canUseReception(roles);
  const showClinical = canUseClinical(roles);

  const items = [
    { label: "Reception Queue", href: "/reception", visible: showReception },
    { label: "New Visit", href: "/reception/new", visible: showReception },
    { label: "Evaluation Queue", href: "/triage", visible: showClinical },
  ].filter((item) => item.visible);

  return (
    <aside className="hidden w-64 shrink-0 bg-emerald-950 text-white lg:flex lg:flex-col">
      <div className="border-b border-white/10 px-5 py-5">
        <div className="text-lg font-semibold tracking-tight">YOYA EMR</div>
        <div className="mt-1 text-xs text-emerald-100">Reception Workspace</div>
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
        Receptionist operations only
      </div>
    </aside>
  );
}
