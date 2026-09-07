"use client";

import type { KeyboardEvent as ReactKeyboardEvent, ReactNode } from "react";
import { useCallback, useEffect, useRef } from "react";

/**
 * The centered, READ-ONLY canvas a released clinical result is read on.
 *
 * ONE SHELL, BOTH SERVICES. A laboratory panel and a radiology report are
 * different documents, but reading one is the same act: open it, read it, close
 * it, change nothing. So the chrome lives here once and the two bodies plug in,
 * rather than the Results tab growing a second modal with its own focus trap
 * and its own idea of what a close button looks like. The file is deliberately
 * not called `report-viewer`: a report is a radiology word, and forcing it onto
 * a laboratory panel is how a shared component starts leaking one service's
 * vocabulary into the other's screen.
 *
 * WHY THIS IS NOT NoteEditorModal WITH A FLAG. That modal owns a draft buffer,
 * a dirty comparison, a save path that can be REFUSED by a version check, and a
 * discard confirmation built around all three. A released result has none of
 * those: there is nothing to type, nothing to lose and nothing to save, and
 * every one of those mechanisms would be dead weight that a future edit could
 * accidentally reanimate on a surface that must never write. The chrome the two
 * genuinely share -- the fixed backdrop, the panel, the focus trap, the Escape
 * handler, the focus return -- is small, and duplicating it is cheaper than a
 * `readOnly` branch through save logic.
 *
 * IT RECEIVES DATA, IT NEVER FETCHES. Everything it renders was already loaded
 * by the Results workspace, so opening it is a state change and nothing else --
 * no request, no spinner, no second code path that could disagree with the
 * worklist about what a result says.
 *
 * CLOSING IS UNCONDITIONAL, and only because there is nothing to protect.
 * Cancel, the X, Escape and the backdrop all close immediately. That would be
 * wrong in an editor -- it is exactly the confirmation the note modal exists to
 * show -- and it is right here.
 *
 * THE READ-ONLY TREATMENT IS NEUTRAL SLATE, NOT RED. A released result is an
 * ordinary clinical fact, not a warning. Red on this screen is reserved for a
 * `critical` abnormal flag, and spending it on chrome would drain it from the
 * one place it has to mean "act now".
 */
export default function ClinicalResultViewerModal({
  title,
  subtitle,
  status,
  badge = "Read only",
  onClose,
  children,
}: {
  /** The clinical service being read. Becomes the dialog's accessible name. */
  title: string;
  /**
   * The metadata strip: codes, dates, reporter. IDENTITY AND PROVENANCE ONLY --
   * never a finding, so the header can never quietly become a second, shorter
   * version of the result.
   */
  subtitle?: string | null;
  /** The request's status badge, rendered exactly as the worklist shows it. */
  status?: ReactNode;
  badge?: string | null;
  onClose: () => void;
  children: ReactNode;
}) {
  const panelRef = useRef<HTMLDivElement>(null);
  const closeRef = useRef<HTMLButtonElement>(null);

  /*
    Focus moves INTO the dialog on open, and back to the opener on close. The
    opener is captured by the caller, which knows which card was clicked; this
    component only has to not strand the keyboard user inside a closed dialog.
  */
  useEffect(() => {
    closeRef.current?.focus();
  }, []);

  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      if (event.key !== "Escape") return;
      // Stopped here so Escape closes THIS dialog and does not also reach a
      // surface behind it.
      event.stopPropagation();
      onClose();
    }
    document.addEventListener("keydown", onKeyDown, true);
    return () => document.removeEventListener("keydown", onKeyDown, true);
  }, [onClose]);

  /*
    A minimal focus trap: Tab cycling is left to the browser, but focus is
    prevented from escaping the panel entirely. Deliberately not a full
    focus-management library -- this dialog has one control.
  */
  const onKeyDownPanel = useCallback((event: ReactKeyboardEvent) => {
    if (event.key !== "Tab") return;
    const panel = panelRef.current;
    if (!panel) return;
    const focusable = panel.querySelectorAll<HTMLElement>(
      'button:not([disabled]), [href], input:not([disabled]), [tabindex]:not([tabindex="-1"])',
    );
    if (!focusable.length) return;
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  }, []);

  const titleId = "report-viewer-title";
  const subtitleId = "report-viewer-subtitle";

  return (
    /*
      `fixed` so it escapes the workspace's own overflow-hidden without a
      portal, and a z-index that clears the Doctor Desk chrome: while a report
      is open it IS the task.
    */
    <div className="fixed inset-0 z-[100] flex items-center justify-center p-4">
      <div
        aria-hidden
        onClick={onClose}
        className="absolute inset-0 bg-slate-900/45"
      />

      <div
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        aria-describedby={subtitle ? subtitleId : undefined}
        onKeyDown={onKeyDownPanel}
        /* Sized for reading prose: a comfortable measure on a wide monitor,
           with vw/vh caps so it never overflows a narrow window -- the body
           scrolls internally instead. */
        className="relative flex max-h-[85vh] w-[860px] max-w-[92vw] flex-col overflow-hidden rounded-xl border border-slate-300 bg-white shadow-[0_20px_50px_rgba(15,23,42,0.28)]"
      >
        <header className="flex shrink-0 items-start gap-3 border-b border-slate-200 bg-slate-50 px-4 py-3">
          <div className="min-w-0 flex-1">
            <div className="flex min-w-0 items-center gap-2">
              <h2
                id={titleId}
                className="min-w-0 truncate cl-head font-semibold text-slate-900"
              >
                {title}
              </h2>
              {status}
              {badge ? (
                /* Neutral slate. See the note at the top of this file. */
                <span className="shrink-0 rounded border border-slate-400 bg-white px-1.5 py-px cl-micro font-bold uppercase tracking-wide text-slate-700">
                  {badge}
                </span>
              ) : null}
            </div>
            {subtitle ? (
              <p id={subtitleId} className="mt-0.5 truncate cl-meta text-slate-600">
                {subtitle}
              </p>
            ) : null}
          </div>

          <button
            ref={closeRef}
            type="button"
            onClick={onClose}
            aria-label="Close result"
            className="shrink-0 rounded-md border border-slate-300 bg-white px-2 py-1 cl-body font-semibold leading-none text-slate-600 outline-none transition-colors hover:border-slate-400 hover:text-slate-900 focus-visible:ring-2 focus-visible:ring-emerald-600"
          >
            ✕
          </button>
        </header>

        <div className="min-h-0 flex-1 overflow-y-auto px-4 py-3">{children}</div>

        <footer className="flex shrink-0 items-center justify-end gap-2 border-t border-slate-200 bg-slate-50 px-4 py-2.5">
          <span className="mr-auto hidden cl-meta text-slate-500 sm:inline">
            Escape closes
          </span>
          <button
            type="button"
            onClick={onClose}
            className="rounded-md border border-slate-300 bg-white px-3.5 py-1.5 cl-body font-semibold text-slate-700 outline-none transition-colors hover:bg-slate-100 focus-visible:ring-2 focus-visible:ring-emerald-600"
          >
            Close
          </button>
        </footer>
      </div>
    </div>
  );
}
