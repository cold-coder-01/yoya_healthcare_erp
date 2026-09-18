"use client";

import { useEffect, useRef, useState } from "react";

import { formatHospitalDate, formatHospitalDateTime } from "@/lib/clinical-format";
import {
  SCHEDULE_SUPPORT_TEXT,
  START_SUPPORT_TEXT,
  ageSexLabel,
  canScheduleStudy,
  canStartExam,
  clearanceNotice,
  examLabel,
  fileSizeLabel,
  orDash,
  reviewMessage,
  scheduleConfirmText,
  startConfirmText,
  studyCountLabel,
} from "@/lib/rad-desk-format";
import type {
  RadDeskCapabilities,
  RadOperationalResult,
  RadRequestDetail,
  RadTransitionKind,
} from "@/types/rad-desk";

import { RadLanePill, RadPriorityPill, RadReportStatePill } from "./rad-status-pill";

/**
 * The Radiology Desk request panel.
 *
 * TWO ACTIONS, EACH FOR EXACTLY ONE LANE (Slice 2): Schedule study for a clear
 * request in To schedule, Start exam for a clear request in Ready to start. No
 * lane offers both, and every other lane offers nothing -- not even a disabled
 * button. Reporting, validation, release and image upload are later slices, and
 * a greyed-out button is a promise about workflow that has not shipped.
 *
 * THE BUTTON IS AN AFFORDANCE, NOT A PERMISSION. It renders from the server's
 * own lane and flags; the endpoint re-checks the same policy under a row lock.
 *
 * EACH ACTION NEEDS AN EXPLICIT SECOND CLICK. The first opens a compact
 * confirmation naming the request, the patient and the study; only "Confirm"
 * sends anything. Escape cancels, focus lands on Cancel, and no keyboard
 * shortcut performs the final step.
 *
 * NOTHING PRICED APPEARS HERE. A request awaiting clearance says so in one
 * amount-free sentence; `billing_blocked` is the only billing value in the
 * payload and it is a boolean.
 *
 * A REPORT CONFLICT IS STATED, NOT RESOLVED. With more than one active report
 * the server sends no report at all, and this panel shows the server's review
 * sentence instead of guessing which report is real.
 *
 * IMAGES ARE METADATA. There is no image element, no link and no URL: Slice 1
 * serves no bytes to this desk.
 */

function Field({
  label,
  value,
  mono = false,
}: {
  label: string;
  value: string;
  mono?: boolean;
}) {
  return (
    <div className="flex min-w-0 flex-col gap-0.5">
      <dt className="cl-micro font-bold uppercase tracking-[0.06em] text-slate-400">
        {label}
      </dt>
      <dd
        className={`truncate cl-secondary font-semibold text-slate-800 ${
          mono ? "font-mono" : ""
        }`}
      >
        {value}
      </dd>
    </div>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="flex flex-col gap-1.5">
      <h3 className="cl-micro font-bold uppercase tracking-[0.08em] text-slate-500">
        {title}
      </h3>
      {children}
    </section>
  );
}

function Narrative({ label, text }: { label: string; text: string | null }) {
  return (
    <div className="flex flex-col gap-0.5">
      <span className="cl-micro font-bold uppercase tracking-[0.06em] text-slate-400">
        {label}
      </span>
      <p className="whitespace-pre-wrap cl-body text-slate-800">
        {text ?? <span className="text-slate-400">Not recorded</span>}
      </p>
    </div>
  );
}

function Banner({ tone, children }: { tone: "amber" | "red"; children: React.ReactNode }) {
  const style =
    tone === "red"
      ? "border-red-300 bg-red-50 text-red-900"
      : "border-amber-300 bg-amber-50 text-amber-900";
  return (
    <div role="status" className={`flex items-start gap-2 rounded border px-3 py-2 cl-secondary ${style}`}>
      <span aria-hidden className="mt-px font-bold">
        !
      </span>
      <p className="font-semibold">{children}</p>
    </div>
  );
}

/**
 * One transition: its button, then its confirmation.
 *
 * KEYED BY REQUEST AND KIND at the call site, so selecting another request
 * remounts it closed rather than carrying an open confirmation across.
 */
function TransitionAction({
  kind,
  detail,
  pending,
  onConfirm,
}: {
  kind: RadTransitionKind;
  detail: RadRequestDetail;
  pending: boolean;
  onConfirm: (requestId: number) => Promise<boolean>;
}) {
  const [confirming, setConfirming] = useState(false);
  const cancelRef = useRef<HTMLButtonElement>(null);

  const label = kind === "schedule" ? "Schedule study" : "Start exam";
  const confirmLabel = kind === "schedule" ? "Confirm schedule" : "Confirm start";
  const question = kind === "schedule" ? scheduleConfirmText(detail) : startConfirmText(detail);
  const support = kind === "schedule" ? SCHEDULE_SUPPORT_TEXT : START_SUPPORT_TEXT;

  // Focus lands on CANCEL, never on the step that changes the record.
  useEffect(() => {
    if (confirming) cancelRef.current?.focus();
  }, [confirming]);

  if (!confirming) {
    return (
      <button
        type="button"
        onClick={() => setConfirming(true)}
        disabled={pending}
        className="rounded border border-teal-700 bg-teal-700 px-3 py-1.5 cl-secondary font-bold text-white hover:bg-teal-800 disabled:opacity-50"
      >
        {label}
      </button>
    );
  }

  return (
    <div
      role="alertdialog"
      aria-label={label}
      aria-describedby={`rad-confirm-${kind}-${detail.id}`}
      onKeyDown={(event) => {
        if (event.key === "Escape" && !pending) {
          event.preventDefault();
          setConfirming(false);
        }
        // No shortcut performs the final step: it takes a deliberate click.
        if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) {
          event.preventDefault();
        }
      }}
      className="flex flex-col gap-2 rounded border border-teal-300 bg-teal-50 px-3 py-2"
    >
      <p id={`rad-confirm-${kind}-${detail.id}`} className="cl-body font-semibold text-slate-900">
        {question}
      </p>
      <p className="cl-secondary text-slate-600">{support}</p>
      <div className="flex items-center gap-2">
        <button
          ref={cancelRef}
          type="button"
          onClick={() => setConfirming(false)}
          disabled={pending}
          className="rounded border border-slate-300 bg-white px-3 py-1.5 cl-secondary font-bold text-slate-700 hover:bg-slate-50 disabled:opacity-50"
        >
          Cancel
        </button>
        <button
          type="button"
          onClick={async () => {
            if (pending) return;
            const done = await onConfirm(detail.id);
            if (done) setConfirming(false);
          }}
          disabled={pending}
          className="rounded border border-teal-700 bg-teal-700 px-3 py-1.5 cl-secondary font-bold text-white hover:bg-teal-800 disabled:opacity-50"
        >
          {pending ? "Working…" : confirmLabel}
        </button>
      </div>
    </div>
  );
}

function ReportSection({ result }: { result: RadOperationalResult }) {
  return (
    <Section title="Report">
      <div className="flex flex-wrap items-center gap-2 rounded border border-slate-200 bg-slate-50 px-3 py-2">
        <span className="cl-secondary text-slate-600">
          Report{" "}
          <span className="font-mono font-semibold text-slate-800">{result.name}</span>
        </span>
        <RadReportStatePill state={result.state} label={result.state_label} />
        <span className="cl-meta text-slate-500">
          {formatHospitalDate(result.result_date, "—")} · {orDash(result.radiologist)}
        </span>
      </div>

      {result.has_report ? null : (
        <p className="cl-secondary text-slate-500">
          No report text has been recorded on this report.
        </p>
      )}
      <Narrative label="Findings" text={result.findings} />
      <Narrative label="Impression" text={result.impression} />
      <Narrative label="Recommendations" text={result.recommendations} />

      {result.lines.length > 0 ? (
        <table className="w-full table-fixed border-collapse cl-secondary">
          <thead>
            <tr className="border-b border-slate-200 text-left cl-micro uppercase tracking-[0.06em] text-slate-400">
              <th className="w-[34%] py-1 font-bold">Exam</th>
              <th className="w-[18%] py-1 font-bold">Body part</th>
              <th className="w-[12%] py-1 font-bold">Contrast</th>
              <th className="py-1 font-bold">Summary · Notes</th>
            </tr>
          </thead>
          <tbody>
            {result.lines.map((line) => (
              <tr key={line.id} className="border-b border-slate-100 align-top">
                <td className="py-1 pr-2 font-semibold text-slate-800">{examLabel(line.exam)}</td>
                <td className="py-1 pr-2 text-slate-700">{orDash(line.body_part)}</td>
                <td className="py-1 pr-2 text-slate-700">{line.contrast_used ? "Used" : "No"}</td>
                <td className="py-1 text-slate-700">
                  {orDash(line.result_summary)}
                  {line.notes ? (
                    <span className="block text-slate-500">{line.notes}</span>
                  ) : null}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : null}

      <div className="flex flex-col gap-1">
        <span className="cl-micro font-bold uppercase tracking-[0.06em] text-slate-400">
          Images ({result.image_count})
        </span>
        {result.images.length === 0 ? (
          <p className="cl-secondary text-slate-500">No images attached.</p>
        ) : (
          <ul className="flex flex-col divide-y divide-slate-100 rounded border border-slate-200">
            {result.images.map((image) => (
              <li key={image.id} className="flex flex-col gap-0.5 px-2 py-1.5">
                <span className="flex min-w-0 items-center gap-2">
                  <span className="truncate cl-secondary font-semibold text-slate-800">
                    {image.name}
                  </span>
                  <span className="shrink-0 rounded bg-slate-100 px-1 cl-micro font-bold text-slate-600">
                    {orDash(image.image_type_label ?? image.image_type)}
                  </span>
                </span>
                {image.caption ? (
                  <span className="cl-meta text-slate-600">{image.caption}</span>
                ) : null}
                <span className="cl-meta text-slate-400">
                  {image.filename} · {orDash(image.mimetype)} · {fileSizeLabel(image.file_size)} ·{" "}
                  {orDash(image.uploaded_by)} · {formatHospitalDateTime(image.uploaded_at, "—")}
                </span>
              </li>
            ))}
          </ul>
        )}
      </div>
    </Section>
  );
}

export default function RadRequestPanel({
  detail,
  loading,
  error,
  empty,
  stale,
  capabilities,
  pending,
  actionError,
  outcome,
  refreshWarning,
  onSchedule,
  onStart,
}: {
  detail: RadRequestDetail | null;
  loading: boolean;
  error: string | null;
  empty: boolean;
  stale: boolean;
  /** From the session: which acts this ROLE may attempt. */
  capabilities: RadDeskCapabilities | null;
  /** A transition for THIS request is in flight. */
  pending: boolean;
  actionError: string | null;
  /** The server-confirmed outcome for the request just acted on. */
  outcome: string | null;
  /** The queue could not be refreshed after a confirmed action. */
  refreshWarning: boolean;
  onSchedule: (requestId: number) => Promise<boolean>;
  onStart: (requestId: number) => Promise<boolean>;
}) {
  if (error) {
    return (
      <section className="flex min-h-[340px] items-center justify-center rounded-lg border border-slate-200 bg-white p-6 shadow-sm">
        <p role="alert" className="cl-body text-red-800">
          {error}
        </p>
      </section>
    );
  }

  if (loading) {
    return (
      <section
        aria-label="Loading request"
        className="flex min-h-[340px] flex-col gap-3 rounded-lg border border-slate-200 bg-white p-4 shadow-sm"
      >
        <div className="h-4 w-1/3 animate-pulse rounded bg-slate-200" />
        <div className="h-3 w-2/3 animate-pulse rounded bg-slate-100" />
        <div className="h-24 w-full animate-pulse rounded bg-slate-100" />
      </section>
    );
  }

  if (!detail) {
    return (
      <section className="flex min-h-[340px] flex-col items-center justify-center gap-1 rounded-lg border border-slate-200 bg-white p-6 text-center shadow-sm">
        <p className="cl-body font-semibold text-slate-600">
          {empty ? "No request selected" : "Select a request"}
        </p>
        <p className="cl-secondary text-slate-500">
          {empty
            ? "The queue is empty for the current lane and filters."
            : "Choose a request from the queue to see its details."}
        </p>
      </section>
    );
  }

  const clearance = clearanceNotice(detail.lane);
  const review = reviewMessage(detail);
  const offerSchedule = capabilities?.schedule_study === true && canScheduleStudy(detail);
  const offerStart = capabilities?.start_exam === true && canStartExam(detail);

  return (
    <section className="flex min-h-[340px] min-w-0 flex-col overflow-hidden rounded-lg border border-slate-200 bg-white shadow-sm min-[1100px]:min-h-0">
      <header className="flex shrink-0 flex-wrap items-center justify-between gap-2 border-b border-slate-200 bg-slate-50 px-3 py-2">
        <div className="flex min-w-0 flex-col">
          <span className="truncate cl-strong font-bold text-slate-900">
            {detail.patient?.name ?? "—"}
          </span>
          <span className="cl-meta text-slate-500">
            <span className="font-mono font-semibold text-slate-700">
              {detail.request_code}
            </span>{" "}
            · MRN <span className="font-mono">{orDash(detail.patient?.mrn)}</span> ·{" "}
            {ageSexLabel(detail.patient)}
          </span>
        </div>
        <div className="flex items-center gap-1.5">
          {stale ? (
            <span className="cl-meta text-amber-700">Showing last loaded data</span>
          ) : null}
          <RadPriorityPill priority={detail.priority} label={detail.priority_label} />
          <RadLanePill lane={detail.lane} label={detail.lane_label} />
        </div>
      </header>

      <div className="flex min-h-0 flex-1 flex-col gap-4 overflow-y-auto px-3 py-3">
        {review ? <Banner tone="red">{review}</Banner> : null}
        {clearance ? <Banner tone="amber">{clearance}</Banner> : null}

        {outcome ? (
          <div role="status" className="rounded border border-emerald-300 bg-emerald-50 px-3 py-2 cl-secondary font-semibold text-emerald-900">
            {outcome}
          </div>
        ) : null}
        {refreshWarning ? (
          <Banner tone="amber">
            The action was confirmed, but the queue could not be refreshed. This
            request is shown as the server confirmed it. Use Refresh to reload the
            queue.
          </Banner>
        ) : null}
        {actionError ? (
          <p role="alert" className="rounded border border-red-300 bg-red-50 px-3 py-2 cl-secondary font-semibold text-red-900">
            {actionError}
          </p>
        ) : null}
        {offerSchedule ? (
          <TransitionAction
            key={`schedule-${detail.id}`}
            kind="schedule"
            detail={detail}
            pending={pending}
            onConfirm={onSchedule}
          />
        ) : null}
        {offerStart ? (
          <TransitionAction
            key={`start-${detail.id}`}
            kind="start"
            detail={detail}
            pending={pending}
            onConfirm={onStart}
          />
        ) : null}

        <dl className="grid grid-cols-2 gap-x-4 gap-y-2 min-[700px]:grid-cols-4">
          <Field label="Ordering doctor" value={orDash(detail.ordering_physician?.name)} />
          <Field label="Ordered" value={formatHospitalDate(detail.request_date, "—")} />
          <Field label="Record state" value={detail.state} mono />
          <Field
            label="Source"
            value={detail.ordered_from_consultation ? "Doctor Desk order" : "Department / legacy"}
          />
          {detail.completed_at ? (
            <Field label="Completed" value={formatHospitalDateTime(detail.completed_at, "—")} />
          ) : null}
        </dl>

        <Section title="Clinical indication">
          <p className="whitespace-pre-wrap cl-body text-slate-800">
            {detail.clinical_indication ?? (
              <span className="text-slate-400">No clinical indication recorded</span>
            )}
          </p>
        </Section>

        <Section title="Instructions">
          <p className="whitespace-pre-wrap cl-body text-slate-800">
            {detail.instructions ?? <span className="text-slate-400">No instructions</span>}
          </p>
        </Section>

        <Section title={`Ordered studies · ${studyCountLabel(detail.exams.length)}`}>
          {detail.exams.length === 0 ? (
            <p className="cl-secondary text-slate-500">
              No active study is recorded on this request.
            </p>
          ) : (
            <table className="w-full table-fixed border-collapse cl-secondary">
              <thead>
                <tr className="border-b border-slate-200 text-left cl-micro uppercase tracking-[0.06em] text-slate-400">
                  <th className="w-[36%] py-1 font-bold">Exam</th>
                  <th className="w-[16%] py-1 font-bold">Modality</th>
                  <th className="w-[18%] py-1 font-bold">Body part</th>
                  <th className="w-[12%] py-1 font-bold">Contrast</th>
                  <th className="py-1 font-bold">Instruction</th>
                </tr>
              </thead>
              <tbody>
                {detail.exams.map((exam) => (
                  <tr key={exam.request_line_id} className="border-b border-slate-100 align-top">
                    <td className="py-1 pr-2 font-semibold text-slate-800">{examLabel(exam)}</td>
                    <td className="py-1 pr-2 text-slate-700">{orDash(exam.modality_label)}</td>
                    <td className="py-1 pr-2 text-slate-700">{orDash(exam.body_part)}</td>
                    <td className="py-1 pr-2 text-slate-700">
                      {exam.contrast_required ? "Required" : "No"}
                    </td>
                    <td className="py-1 text-slate-700">{orDash(exam.special_instruction)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          {detail.cancelled_exam_count > 0 ? (
            <p className="cl-meta text-slate-500">
              {detail.cancelled_exam_count} cancelled{" "}
              {detail.cancelled_exam_count === 1 ? "study is" : "studies are"} not shown.
            </p>
          ) : null}
        </Section>

        {detail.result ? (
          <ReportSection result={detail.result} />
        ) : detail.result_conflict ? null : (
          <Section title="Report">
            <p className="cl-secondary text-slate-500">No report has been started.</p>
          </Section>
        )}
      </div>

      <footer className="flex h-7 shrink-0 items-center border-t border-slate-200 bg-slate-50 px-3 cl-meta text-slate-500">
        Schedule and start only. Reporting, images, validation and release are not available on this desk yet.
      </footer>
    </section>
  );
}
