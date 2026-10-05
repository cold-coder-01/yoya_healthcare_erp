"use client";

/**
 * Signed-in user control for the Admissions Desk header.
 *
 * The desk is STANDALONE: a dedicated ward nurse lands here and never sees the
 * legacy clinical shell, so this is where they sign out. Same behaviour as the
 * Front Desk menu -- POST /api/auth/logout, then /login and a refresh -- built
 * on <details>/<summary> so it needs no open/close state and is keyboard
 * operable natively.
 */
import { useRouter } from "next/navigation";

export default function AdmissionsUserMenu({
  userName,
  initial,
}: {
  userName: string;
  initial: string;
}) {
  const router = useRouter();

  async function logout() {
    await fetch("/api/auth/logout", { method: "POST" });
    router.push("/login");
    router.refresh();
  }

  return (
    <details className="group relative">
      <summary className="flex h-8 cursor-pointer list-none items-center gap-2 rounded px-1.5 outline-none hover:bg-slate-100 focus-visible:ring-2 focus-visible:ring-sky-600 [&::-webkit-details-marker]:hidden">
        <span className="max-w-[200px] truncate text-[12px] font-semibold text-slate-700">
          {userName || "Signed in"}
        </span>
        <span
          aria-hidden
          className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-sky-700 text-[11px] font-bold text-white"
        >
          {initial}
        </span>
      </summary>
      <div className="absolute right-0 z-30 mt-1 w-48 rounded border border-slate-200 bg-white p-1 shadow-lg">
        <button
          type="button"
          onClick={logout}
          className="w-full rounded px-2 py-1.5 text-left text-xs font-semibold text-slate-700 outline-none hover:bg-slate-100 focus-visible:ring-2 focus-visible:ring-sky-600"
        >
          Log out
        </button>
      </div>
    </details>
  );
}
