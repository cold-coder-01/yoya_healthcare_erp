"use client";

/**
 * WORKSTATION QUICK PREVIEW -- the hospital-wide read-only summary modal.
 *
 * One large dialog that answers "where does this patient stand?" at a glance,
 * next to (never instead of) a desk's detailed pane. Admissions adopted it
 * first; every desk composes the same few pieces so previews look alike:
 *
 *   <WorkstationPreviewDialog>        the frame: X, Close, Escape, one scroll
 *     <PreviewHeader>                 name, identifiers, status pill
 *     <PreviewSection>                a titled band with a column grid
 *       <PreviewField>                label over value
 *       <PreviewMetric>               a compact counter
 *       <PreviewStatus>               a toned headline (pill + text)
 *       <PreviewWarning>              a toned list of reasons
 *
 * THE CONTRACT EVERY DESK KEEPS:
 *   * READ ONLY. No form, no submit, no fetch in here. A desk builds a view
 *     model from the payload it ALREADY holds and passes plain values in, so a
 *     preview can never show more than the detail view the server authorised.
 *   * Money only where the desk is a financial workstation (Cashier). The
 *     primitives are figure-agnostic; the desk's view model decides.
 *
 * Intended later adopters (not built yet): Front Desk (demographics, encounter,
 * doctor/department, triage/visit state, payer clearance); Doctor (complaint,
 * vitals, diagnosis, orders/results, medication, admission/discharge status);
 * Ward Nurse (location, diagnosis, vitals, nursing status, medication
 * administration, pending clinical work); Pharmacy (medication, prescribed /
 * intended / delivered / remaining quantity, clearance, stock context); Lab
 * (test, specimen, collection, processing, validation/release); Radiology
 * (order, scheduling, examination, report, release); Cashier (billing account,
 * responsibility, advance, applied advance, receivable, refundable balance).
 */
import { useEffect, useRef, type ReactNode } from "react";

export type PreviewTone = "neutral" | "ok" | "warn" | "danger" | "info";

const PILL_TONES: Record<PreviewTone, string> = {
  neutral: "border-slate-300 bg-slate-50 text-slate-700",
  ok: "border-emerald-300 bg-emerald-50 text-emerald-900",
  warn: "border-amber-300 bg-amber-50 text-amber-900",
  danger: "border-red-300 bg-red-50 text-red-800",
  info: "border-violet-300 bg-violet-50 text-violet-900",
};

const TEXT_TONES: Record<PreviewTone, string> = {
  neutral: "text-slate-700",
  ok: "text-emerald-800",
  warn: "text-amber-900",
  danger: "text-red-800",
  info: "text-violet-900",
};

const COLUMNS = {
  2: "grid-cols-2",
  3: "grid-cols-2 min-[900px]:grid-cols-3",
  4: "grid-cols-2 min-[900px]:grid-cols-4",
} as const;

export function PreviewPill({ tone, children }: { tone: PreviewTone; children: ReactNode }) {
  return (
    <span className={`inline-flex shrink-0 items-center rounded border px-1.5 py-0.5 cl-micro font-bold uppercase tracking-wide ${PILL_TONES[tone]}`}>
      {children}
    </span>
  );
}

export function WorkstationPreviewDialog({
  titleId,
  header,
  onClose,
  children,
}: {
  /** id of the heading inside `header`, for aria-labelledby. */
  titleId: string;
  header: ReactNode;
  onClose: () => void;
  children: ReactNode;
}) {
  const closeRef = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    closeRef.current?.focus();
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") onClose();
    }
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [onClose]);

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby={titleId}
      className="fixed inset-0 z-40 flex items-center justify-center bg-slate-950/40 p-3"
      onClick={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <div className="flex max-h-[94vh] w-[min(1080px,100%)] flex-col overflow-hidden rounded-lg border border-slate-300 bg-white shadow-xl">
        <div className="flex shrink-0 items-start gap-2 border-b border-slate-200 px-4 py-2.5">
          <div className="min-w-0 flex-1">{header}</div>
          <button
            type="button"
            aria-label="Close preview"
            onClick={onClose}
            className="flex h-7 w-7 shrink-0 items-center justify-center rounded text-slate-500 hover:bg-slate-100 hover:text-slate-900 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-sky-600"
          >
            <svg aria-hidden viewBox="0 0 20 20" fill="currentColor" className="h-4 w-4">
              <path d="M5.3 5.3a1 1 0 0 1 1.4 0L10 8.6l3.3-3.3a1 1 0 1 1 1.4 1.4L11.4 10l3.3 3.3a1 1 0 0 1-1.4 1.4L10 11.4l-3.3 3.3a1 1 0 0 1-1.4-1.4L8.6 10 5.3 6.7a1 1 0 0 1 0-1.4Z" />
            </svg>
          </button>
        </div>
        {/* The ONE scroll, and only when the content is genuinely larger. */}
        <div className="min-h-0 flex-1 overflow-y-auto">{children}</div>
        <div className="flex shrink-0 items-center justify-between gap-2 border-t border-slate-200 px-4 py-2">
          <span className="cl-micro text-slate-500">Read-only preview. Use the detail pane to act.</span>
          <button
            ref={closeRef}
            type="button"
            onClick={onClose}
            className="h-8 rounded-md border border-slate-300 bg-white px-3 cl-meta font-bold text-slate-700 hover:bg-slate-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-sky-600"
          >
            Close
          </button>
        </div>
      </div>
    </div>
  );
}

export function PreviewHeader({
  titleId,
  title,
  identifiers,
  status,
}: {
  titleId: string;
  title: string;
  /** Short label/value pairs rendered as one mono line. */
  identifiers: { label: string; value: string }[];
  status?: ReactNode;
}) {
  return (
    <div className="flex min-w-0 items-start gap-3">
      <div className="min-w-0 flex-1">
        <h2 id={titleId} className="truncate cl-head-lg font-bold text-slate-950">
          {title}
        </h2>
        <dl className="mt-0.5 flex flex-wrap gap-x-4 gap-y-0.5 cl-meta">
          {identifiers.map((item) => (
            <div key={item.label} className="flex min-w-0 items-baseline gap-1">
              <dt className="cl-micro font-bold uppercase tracking-wide text-slate-500">{item.label}</dt>
              <dd className="truncate font-mono text-slate-800">{item.value}</dd>
            </div>
          ))}
        </dl>
      </div>
      {status ? <div className="flex shrink-0 flex-col items-end gap-1 pt-0.5">{status}</div> : null}
    </div>
  );
}

export function PreviewSection({
  title,
  columns,
  aside,
  children,
}: {
  title: string;
  /** Grid columns for the body; omit for free-form content. */
  columns?: keyof typeof COLUMNS;
  aside?: ReactNode;
  children: ReactNode;
}) {
  return (
    <section className="min-w-0 border-t border-slate-200 px-4 py-2 first:border-t-0">
      <div className="mb-1 flex items-center justify-between gap-2">
        <h3 className="cl-micro font-bold uppercase tracking-wide text-slate-600">{title}</h3>
        {aside}
      </div>
      {columns ? <dl className={`grid gap-x-3 gap-y-1.5 ${COLUMNS[columns]}`}>{children}</dl> : children}
    </section>
  );
}

export function PreviewField({
  label,
  muted = false,
  wide = false,
  children,
}: {
  label: string;
  /** Redacted or absent: rendered quieter so it is not mistaken for data. */
  muted?: boolean;
  /** Span the whole row (free text such as a reason). */
  wide?: boolean;
  children: ReactNode;
}) {
  return (
    <div className={`flex min-w-0 flex-col ${wide ? "col-span-full" : ""}`}>
      <dt className="cl-micro font-bold uppercase tracking-wide text-slate-500">{label}</dt>
      <dd className={`min-w-0 cl-body ${wide ? "line-clamp-3 whitespace-pre-wrap" : "truncate"} ${muted ? "italic text-slate-500" : "text-slate-900"}`}>
        {children}
      </dd>
    </div>
  );
}

export function PreviewMetric({
  label,
  value,
  note,
  muted = false,
}: {
  label: string;
  value: string;
  note?: string | null;
  muted?: boolean;
}) {
  return (
    <div className="flex min-w-0 flex-col rounded border border-slate-200 bg-slate-50 px-2 py-1">
      <dt className="truncate cl-micro font-bold uppercase tracking-wide text-slate-500">{label}</dt>
      <dd className={`font-mono cl-head font-bold leading-tight ${muted ? "text-slate-400" : "text-slate-900"}`}>{value}</dd>
      {note ? <dd className="truncate cl-micro text-slate-500">{note}</dd> : null}
    </div>
  );
}

export function PreviewStatus({
  tone,
  label,
  children,
}: {
  tone: PreviewTone;
  label: string;
  children?: ReactNode;
}) {
  return (
    <div className="flex min-w-0 flex-wrap items-center gap-2">
      <PreviewPill tone={tone}>{label}</PreviewPill>
      {children ? <span className={`min-w-0 cl-body ${TEXT_TONES[tone]}`}>{children}</span> : null}
    </div>
  );
}

export function PreviewWarning({
  tone,
  title,
  items,
}: {
  tone: "warn" | "danger";
  title: string;
  items: { key: string; text: string }[];
}) {
  if (items.length === 0) return null;
  return (
    <div role={tone === "danger" ? "alert" : undefined} className={`mt-1.5 rounded border px-2 py-1 ${PILL_TONES[tone]}`}>
      <p className="cl-micro font-bold uppercase tracking-wide">{title}</p>
      <ul className="list-disc pl-4 cl-meta">
        {items.map((item) => (
          <li key={item.key}>{item.text}</li>
        ))}
      </ul>
    </div>
  );
}
