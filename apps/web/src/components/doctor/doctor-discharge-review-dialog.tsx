"use client";

import { useCallback, useEffect, useRef, type KeyboardEvent as ReactKeyboardEvent } from "react";

import {
  PreviewField,
  PreviewHeader,
  PreviewPill,
  PreviewSection,
  PreviewWarning,
} from "@/components/workstation/workstation-preview";
import type { DischargeReview } from "@/lib/doctor-discharge-review";

type Notice = { tone: "red" | "amber" | "green"; text: string };

/**
 * REVIEW DISCHARGE on the Doctor Desk: the wizard step between writing the
 * discharge summary and requesting discharge.
 *
 * A modal, never an inline card: a realistic summary is longer than the
 * consultation strip the card lives in, and an inline review was clipped
 * there. The frame is the Admissions action-wizard shell (as Finalize
 * discharge) built from the shared Preview primitives: header and footer stay
 * put, the BODY is the one scroll, and it opens at the top because the dialog
 * mounts fresh on every open.
 *
 * Presentational only. The card owns the summary text, the operation token
 * and the mutation; "Go back" (also X and Escape) returns to the summary
 * editor with the text intact. The confirm is a pointer click (detail === 0 is
 * ignored) and focus starts on "Go back", the rule every irreversible act
 * follows. While a request is in flight nothing closes the dialog.
 */
export default function DoctorDischargeReviewDialog({
  review,
  busy,
  notice,
  onConfirm,
  onClose,
}: {
  review: DischargeReview;
  busy: boolean;
  notice: Notice | null;
  onConfirm: () => void;
  onClose: () => void;
}) {
  const panelRef = useRef<HTMLDivElement>(null);
  const bodyRef = useRef<HTMLDivElement>(null);
  const backRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (bodyRef.current) bodyRef.current.scrollTop = 0;
    backRef.current?.focus();
  }, []);

  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape" && !busy) onClose();
    }
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [busy, onClose]);

  /* A minimal focus trap: Tab cycles within the panel, never behind it. */
  const onKeyDownPanel = useCallback((event: ReactKeyboardEvent) => {
    if (event.key !== "Tab") return;
    const panel = panelRef.current;
    if (!panel) return;
    const focusable = panel.querySelectorAll<HTMLElement>("button:not([disabled]), [href]");
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

  const titleId = "doctor-discharge-review-title";

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby={titleId}
      className="fixed inset-0 z-40 flex items-center justify-center bg-slate-950/40 p-4"
    >
      <div
        ref={panelRef}
        onKeyDown={onKeyDownPanel}
        className="flex max-h-[88vh] w-full max-w-[760px] flex-col overflow-hidden rounded-lg border border-slate-300 bg-white shadow-xl"
      >
        <div className="flex shrink-0 items-start gap-2 border-b border-slate-200 px-4 py-2.5">
          <div className="min-w-0 flex-1">
            <PreviewHeader
              titleId={titleId}
              title={review.title}
              identifiers={review.identifiers}
              status={<PreviewPill tone="info">Medical discharge</PreviewPill>}
            />
          </div>
          <button
            type="button"
            aria-label="Close discharge review"
            disabled={busy}
            onClick={onClose}
            className="flex h-7 w-7 shrink-0 items-center justify-center rounded text-slate-500 hover:bg-slate-100 hover:text-slate-900 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-sky-600 disabled:opacity-40"
          >
            <svg aria-hidden viewBox="0 0 20 20" fill="currentColor" className="h-4 w-4">
              <path d="M5.3 5.3a1 1 0 0 1 1.4 0L10 8.6l3.3-3.3a1 1 0 1 1 1.4 1.4L11.4 10l3.3 3.3a1 1 0 0 1-1.4 1.4L10 11.4l-3.3 3.3a1 1 0 0 1-1.4-1.4L8.6 10 5.3 6.7a1 1 0 0 1 0-1.4Z" />
            </svg>
          </button>
        </div>

        {/* The ONE scroll: header and footer stay visible. */}
        <div
          ref={bodyRef}
          data-testid="discharge-review-body"
          className="min-h-0 flex-1 overflow-y-auto overflow-x-hidden"
        >
          <PreviewSection title="Admission" columns={4}>
            {review.admissionContext.map((field) => (
              <PreviewField key={field.label} label={field.label}>
                {field.value}
              </PreviewField>
            ))}
          </PreviewSection>

          <PreviewSection
            title="Discharge summary"
            aside={<span className="font-mono cl-micro text-slate-500">rev {review.revision}</span>}
          >
            <p
              aria-label="Discharge summary"
              className="whitespace-pre-wrap break-words rounded border border-slate-200 bg-slate-50 px-3 py-2 cl-body leading-relaxed text-slate-900 [overflow-wrap:anywhere]"
            >
              {review.summary}
            </p>
          </PreviewSection>

          <PreviewSection title="Medical readiness" columns={2}>
            <PreviewField label="Action" wide>
              {review.action}
            </PreviewField>
            <PreviewField label="Revision">{String(review.revision)}</PreviewField>
          </PreviewSection>

          {review.warnings.length ? (
            <PreviewSection title="Warnings">
              <PreviewWarning tone="warn" title="Review before requesting" items={review.warnings} />
              <p className="mt-1 cl-micro text-slate-500">
                Warnings do not block the request. The Admissions Desk sees them too.
              </p>
            </PreviewSection>
          ) : null}
        </div>

        {notice ? (
          <p
            role="status"
            className={`mx-4 my-2 shrink-0 rounded border px-2 py-1 cl-meta ${
              notice.tone === "red"
                ? "border-red-300 bg-red-50 text-red-900"
                : notice.tone === "amber"
                  ? "border-amber-300 bg-amber-50 text-amber-900"
                  : "border-emerald-300 bg-emerald-50 text-emerald-900"
            }`}
          >
            {notice.text}
          </p>
        ) : null}

        <footer className="flex shrink-0 items-center justify-end gap-2 border-t border-slate-200 px-4 py-3">
          <button
            ref={backRef}
            type="button"
            disabled={busy}
            onClick={onClose}
            className="h-8 rounded-md border border-slate-300 bg-white px-3 cl-meta font-bold text-slate-700 hover:bg-slate-50 disabled:opacity-40"
          >
            Go back
          </button>
          <button
            type="button"
            disabled={busy}
            onClick={(event) => {
              // Pointer only: a keyboard-activated click reports detail 0.
              if (event.detail === 0) return;
              onConfirm();
            }}
            className="h-8 rounded-md bg-violet-700 px-3 cl-meta font-bold text-white hover:bg-violet-800 disabled:opacity-40"
          >
            {busy ? "Requesting…" : "Request discharge"}
          </button>
        </footer>
      </div>
    </div>
  );
}
