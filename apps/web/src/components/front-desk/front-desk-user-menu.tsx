"use client";

/**
 * Signed-in user control for the Front Desk header.
 *
 * Replaces the standalone Logout button that used to float in a three-line
 * header. Built on <details>/<summary> rather than a stateful popover: it needs
 * no open/close state, no outside-click listener and no focus trap, and it is
 * keyboard operable natively (Enter/Space toggles, Tab reaches Logout).
 *
 * Logout behaviour is unchanged -- the same POST /api/auth/logout followed by a
 * push to /login and a refresh that TopHeader performed.
 */
import { useRouter } from "next/navigation";

export default function FrontDeskUserMenu({
  userName,
  roleLabel,
}: {
  userName: string | null;
  roleLabel: string;
}) {
  const router = useRouter();

  async function logout() {
    await fetch("/api/auth/logout", { method: "POST" });
    router.push("/login");
    router.refresh();
  }

  return (
    <details className="group relative">
      <summary className="flex h-8 cursor-pointer list-none items-center gap-1.5 rounded px-2 text-xs font-semibold text-slate-700 outline-none hover:bg-slate-100 focus-visible:ring-2 focus-visible:ring-emerald-600 [&::-webkit-details-marker]:hidden">
        <svg
          aria-hidden
          viewBox="0 0 20 20"
          fill="currentColor"
          className="h-4 w-4 shrink-0 text-slate-400"
        >
          <path d="M10 10a3.5 3.5 0 1 0 0-7 3.5 3.5 0 0 0 0 7Zm0 1.5c-3 0-6 1.5-6 3.5v1a1 1 0 0 0 1 1h10a1 1 0 0 0 1-1v-1c0-2-3-3.5-6-3.5Z" />
        </svg>
        <span className="max-w-[160px] truncate">{userName ?? roleLabel}</span>
        <span aria-hidden className="text-[10px] text-slate-400 group-open:rotate-180">
          &#9662;
        </span>
      </summary>

      <div className="absolute right-0 z-30 mt-1 w-52 rounded border border-slate-200 bg-white p-1 shadow-lg">
        <div className="border-b border-slate-100 px-2 py-1.5">
          <div className="truncate text-xs font-bold text-slate-900">
            {userName ?? "Signed in"}
          </div>
          <div className="truncate text-[10px] text-slate-500">{roleLabel}</div>
        </div>
        <button
          type="button"
          onClick={logout}
          className="mt-1 w-full rounded px-2 py-1.5 text-left text-xs font-semibold text-slate-700 outline-none hover:bg-slate-100 focus-visible:ring-2 focus-visible:ring-emerald-600"
        >
          Log out
        </button>
      </div>
    </details>
  );
}
