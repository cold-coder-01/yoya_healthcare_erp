"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { messageFromPayload } from "@/lib/api-error";
import { formatHospitalDate } from "@/lib/clinical-format";
import {
  NO_ORDERS_TEXT,
  OPEN_RESULT_TEXT,
  canOpenResult,
  checkedAtText,
  hasAnyOrder,
  labResultSummary,
  pendingReason,
  reportPreview,
  resultStatusTone,
  sectionSummary,
  serviceSummary,
  supersededText,
  workflowLabel,
} from "@/lib/results-format";
import type { ApiEnvelope } from "@/types/doctor";
import type {
  DoctorResultsResponse,
  LaboratoryReview,
  RadiologyReview,
  ResultStatus,
} from "@/types/doctor-results";

import ClinicalResultViewerModal from "./clinical-result-viewer-modal";
import { LaboratoryResultView, RadiologyResultView } from "./result-views";

/**
 * The result currently open in the viewer.
 *
 * A DISCRIMINATED UNION, so the body that renders it cannot be chosen wrongly:
 * a laboratory selection carries a LaboratoryReview and nothing else. `title`
 * and `subtitle` are resolved by the row that opened it, because the row
 * already knows how to name itself and the viewer should not learn each
 * service's vocabulary a second time.
 */
type Selection =
  | { kind: "laboratory"; row: LaboratoryReview; title: string; subtitle: string }
  | { kind: "radiology"; row: RadiologyReview; title: string; subtitle: string };

/**
 * The RESULTS section: what the laboratory and imaging have HANDED OFF.
 *
 * A REVIEW SURFACE, AND ONLY THAT. There is no control on this screen that
 * writes anything -- no acknowledge, no mark-reviewed, no sign-off -- because
 * no model records any of those, and a button would let the desk assert
 * something no record supports. Reporting, validating and releasing belong to
 * the departments that do the work.
 *
 * RELEASED IS THE ONLY THING SHOWN, and the server decides it. A validated
 * result is deliberately absent: laboratory holds validated results back until
 * release, and a validated radiology report is still editable. This component
 * never sees one, and must never infer availability from a request's state.
 *
 * THE REQUEST IS THE GROUPING UNIT, not the result, because that is how a
 * doctor remembers what they are waiting for: "the CBC I ordered", not
 * "LABRES0094". A pending request is therefore a first-class row rather than
 * an absence.
 *
 * A WORKLIST, NOT A REPORT DOCUMENT. Every request is a compact row whatever
 * its state, and the full panel or report opens in a viewer. Rendering results
 * inline read well with one order and collapsed under four: a doctor scrolling
 * past two screens of tables to find the study they were chasing is worse
 * served than one who scans ten rows and opens the one that matters.
 *
 * OPENING A VIEWER COSTS NO REQUEST. The workspace already holds every result
 * line, interpretation and report the endpoint returned, so a viewer is a
 * state change over data that is already here. There is exactly one fetch in
 * this file and it belongs to the worklist.
 *
 * NO POLLING. The tab refetches when it mounts -- which, because the workspace
 * mounts one section at a time, is every time the doctor opens it -- and there
 * is a Refresh control for the case where they are sitting on the screen
 * waiting. A background poll per open desk would buy a clinician nothing they
 * do not get by clicking, and there is no realtime transport in this
 * application to extend.
 */
export default function ResultsWorkspace({
  appointmentId,
}: {
  appointmentId: number;
}) {
  const [data, setData] = useState<DoctorResultsResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [checkedAt, setCheckedAt] = useState<string | null>(null);
  /* The button that opened the viewer. Focus returns to it on close, so a
     keyboard user resumes where they were. */
  const originRef = useRef<HTMLButtonElement | null>(null);
  /*
    WHICH RESULT IS OPEN, as one discriminated value rather than one state per
    service. Two independent flags would eventually both be set, and the screen
    would have to decide which document it was showing.
  */
  const [openResult, setOpenResult] = useState<Selection | null>(null);

  /*
    ONE TOKEN DRIVES EVERY READ. The effect owns the fetch and the Refresh
    button bumps the token, which is the pattern doctor-workstation already
    uses for its own refresh. Keeping the request inside the effect means the
    AbortController that cancels it is the same one the cleanup runs, so a
    doctor who leaves the tab mid-flight cannot have a late response land on an
    unmounted panel -- and no state is written synchronously from the effect
    body to cascade a render.
  */
  const [reloadToken, setReloadToken] = useState(0);
  const refresh = useCallback(() => setReloadToken((token) => token + 1), []);

  useEffect(() => {
    const controller = new AbortController();

    async function load() {
      setLoading(true);
      setError(null);
      try {
        const response = await fetch(
          `/api/doctor/visits/${appointmentId}/results`,
          { cache: "no-store", signal: controller.signal },
        );
        const payload =
          (await response.json()) as ApiEnvelope<DoctorResultsResponse>;
        if (controller.signal.aborted) return;
        if (!response.ok || !payload.success) {
          setError(messageFromPayload(payload, "Unable to load results."));
          return;
        }
        setData(payload.data);
        setCheckedAt(new Date().toISOString());
      } catch {
        if (!controller.signal.aborted) {
          setError("Unable to reach the results service.");
        }
      } finally {
        if (!controller.signal.aborted) setLoading(false);
      }
    }

    void load();
    return () => controller.abort();
  }, [appointmentId, reloadToken]);

  const closeResult = useCallback(() => {
    setOpenResult(null);
    const origin = originRef.current;
    originRef.current = null;
    // Next frame: the row is still behind the modal at this point in the
    // commit, and focusing an element about to re-render loses the ring.
    if (origin) requestAnimationFrame(() => origin.focus());
  }, []);

  /*
    THE WHOLE OF "OPEN": remember the button, remember the row. No fetch, no
    reload token, no loading state -- the row handed in here already carries
    every line and paragraph the viewer renders.
  */
  const showResult = useCallback(
    (selection: Selection, origin: HTMLButtonElement) => {
      originRef.current = origin;
      setOpenResult(selection);
    },
    [],
  );

  /* ---------------- states ---------------- */
  if (loading && !data) {
    return (
      <div className="flex flex-col gap-2.5" aria-busy="true">
        {[0, 1].map((row) => (
          <div
            key={row}
            className="h-24 animate-pulse rounded-lg border border-slate-200 bg-white motion-reduce:animate-none"
          />
        ))}
        <p className="cl-secondary text-slate-500">Loading results…</p>
      </div>
    );
  }

  if (error && !data) {
    return (
      <div
        role="alert"
        className="rounded-md border border-amber-300 bg-amber-50 px-3 py-2.5"
      >
        <p className="cl-secondary font-semibold leading-snug text-amber-900">
          {error}
        </p>
        <button
          type="button"
          onClick={refresh}
          className="mt-2 rounded border border-amber-400 bg-white px-2.5 py-1 cl-meta font-bold uppercase tracking-wide text-amber-900 outline-none transition-colors hover:bg-amber-100 focus-visible:ring-2 focus-visible:ring-amber-600"
        >
          Try again
        </button>
      </div>
    );
  }

  const payload = data ?? { laboratory: [], radiology: [] };
  const anyOrder = hasAnyOrder(payload);

  return (
    <div className="flex flex-col gap-3">
      {/* ---- Bar: freshness + refresh ---- */}
      <div className="flex flex-wrap items-center gap-2">
        <h3 className="cl-meta font-bold uppercase tracking-[0.07em] text-slate-500">
          Results
        </h3>
        <span aria-hidden className="h-px flex-1 bg-slate-200" />
        {checkedAt ? (
          <span className="cl-meta text-slate-500">{checkedAtText(checkedAt)}</span>
        ) : null}
        <button
          type="button"
          onClick={refresh}
          disabled={loading}
          aria-label="Refresh results"
          className="shrink-0 rounded border border-slate-300 bg-white px-2 py-0.5 cl-micro font-bold uppercase tracking-wide text-slate-600 outline-none transition-colors hover:border-slate-400 hover:bg-slate-50 hover:text-slate-900 focus-visible:ring-2 focus-visible:ring-emerald-600 disabled:opacity-60"
        >
          {loading ? "Refreshing…" : "Refresh"}
        </button>
      </div>

      {/* A refresh that failed while old data is on screen: say so, and keep
          the results the doctor is reading rather than blanking the panel. */}
      {error && data ? (
        <p
          role="alert"
          className="rounded border border-amber-300 bg-amber-50 px-2.5 py-1.5 cl-secondary leading-snug text-amber-900"
        >
          {error} Showing the last successful read.
        </p>
      ) : null}

      {!anyOrder ? (
        <p className="rounded-md border border-slate-200 bg-white px-3 py-6 text-center cl-secondary text-slate-500">
          {NO_ORDERS_TEXT}
        </p>
      ) : null}

      {payload.laboratory.length ? (
        <section className="flex flex-col gap-1.5">
          <SectionHeading title="Laboratory" rows={payload.laboratory} />
          {payload.laboratory.map((row) => (
            <LaboratoryCard key={row.request_id} row={row} onOpen={showResult} />
          ))}
        </section>
      ) : null}

      {payload.radiology.length ? (
        <section className="flex flex-col gap-1.5">
          <SectionHeading title="Radiology" rows={payload.radiology} />
          {payload.radiology.map((row) => (
            <RadiologyCard key={row.request_id} row={row} onOpen={showResult} />
          ))}
        </section>
      ) : null}

      {openResult ? (
        <ClinicalResultViewerModal
          /* THE SERVICE IS THE TITLE, matching the row the doctor clicked. The
             codes and dates are provenance and belong in the strip below it. */
          title={openResult.title}
          subtitle={openResult.subtitle}
          status={
            <StatusBadge
              status={openResult.row.status}
              label={openResult.row.status_label}
            />
          }
          onClose={closeResult}
        >
          {openResult.kind === "laboratory" ? (
            <LaboratoryResultView row={openResult.row} />
          ) : (
            <RadiologyResultView row={openResult.row} />
          )}
        </ClinicalResultViewerModal>
      ) : null}
    </div>
  );
}

/* ------------------------------------------------------------------ *
 * Shared card chrome
 * ------------------------------------------------------------------ */

/** A section title with a quiet tally of what is in it. */
function SectionHeading({
  title,
  rows,
}: {
  title: string;
  rows: ReadonlyArray<{ status: ResultStatus }>;
}) {
  const summary = sectionSummary(rows);
  return (
    <div className="flex items-baseline gap-2">
      <h4 className="cl-meta font-bold uppercase tracking-[0.07em] text-slate-500">
        {title}
      </h4>
      <span aria-hidden className="h-px flex-1 bg-slate-200" />
      {summary ? (
        <span className="shrink-0 cl-meta text-slate-500">{summary}</span>
      ) : null}
    </div>
  );
}

/**
 * The status badge.
 *
 * DOT PLUS WORD, ALWAYS. The dot is what lets a doctor spot a card that has
 * changed while scanning a column; the word is what makes the status survive
 * greyscale, a printed chart and a screen reader. Neither is ever shown alone.
 *
 * Shaped and sized like doctor-badges.StageBadge so a status here and a stage
 * in the header read as the same kind of object.
 */
function StatusBadge({
  status,
  label,
}: {
  status: ResultStatus;
  label: string;
}) {
  const tone = resultStatusTone(status);
  return (
    <span
      className={`inline-flex h-[20px] shrink-0 items-center gap-1 rounded-md border px-1.5 cl-micro font-bold uppercase tracking-[0.06em] ${tone.chip}`}
    >
      <span aria-hidden className={`h-1.5 w-1.5 rounded-full ${tone.dot}`} />
      {label}
    </span>
  );
}

/**
 * The action that opens a released result.
 *
 * A REAL BUTTON, not a clickable card. The card carries identifiers, a status
 * and a summary, and making all of that one giant target would mean a screen
 * reader announcing a paragraph as the label of a control -- and would give a
 * doctor no way to select the request code as text. One labelled button, one
 * job.
 *
 * The accessible name names the REQUEST, so a list of ten of these does not
 * announce as ten identical "Open result" controls.
 */
function OpenResultButton({
  label,
  onOpen,
}: {
  label: string;
  onOpen: (origin: HTMLButtonElement) => void;
}) {
  return (
    <button
      type="button"
      onClick={(event) => onOpen(event.currentTarget)}
      aria-label={`${OPEN_RESULT_TEXT}: ${label}`}
      className="shrink-0 rounded border border-emerald-600 bg-white px-2 py-0.5 cl-meta font-bold uppercase tracking-wide text-emerald-800 outline-none transition-colors hover:bg-emerald-50 focus-visible:ring-2 focus-visible:ring-emerald-600 focus-visible:ring-offset-1"
    >
      {OPEN_RESULT_TEXT} <span aria-hidden>→</span>
    </button>
  );
}

/**
 * One request, as a card.
 *
 * THE HIERARCHY, TOP TO BOTTOM: a reference row (code + status + where the
 * request is), then the CLINICAL SERVICE in the strongest type on the card,
 * then quiet metadata, then the body. The ordered test or exam is what a
 * doctor is actually looking for -- "the CBC I ordered" -- so the request code
 * recedes to a mono reference rather than competing with it.
 *
 * THE ACCENT RAIL IS THE ONLY TINT. A 4px left border carries the state; the
 * card body stays white, because a wash of colour across the whole card would
 * fight the abnormality colours inside the results table, and a clinical
 * finding must always outrank a card's administrative state.
 */
function RequestCard({
  row,
  identity,
  metadata,
  context,
  children,
}: {
  row: LaboratoryReview | RadiologyReview;
  identity: string;
  metadata: string;
  context: string | null;
  children: React.ReactNode;
}) {
  const tone = resultStatusTone(row.status);
  return (
    <article
      className={`rounded-lg border border-l-4 border-slate-200 bg-white px-3 py-2 shadow-[0_1px_2px_rgba(15,23,42,0.04)] ${tone.accent}`}
    >
      {/* Reference row: identifiers and state, never clinical content. */}
      <div className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1">
        <span className="shrink-0 font-mono cl-meta font-semibold text-slate-500">
          {row.request_code}
        </span>
        <StatusBadge status={row.status} label={row.status_label} />
        {row.priority && row.priority !== "routine" ? (
          <span className="shrink-0 rounded border border-orange-400 bg-orange-50 px-1.5 py-px cl-micro font-bold uppercase tracking-wide text-orange-900">
            {row.priority}
          </span>
        ) : null}
        <span aria-hidden className="h-px flex-1" />
        {context ? (
          <span className="shrink-0 cl-meta text-slate-500">{context}</span>
        ) : null}
      </div>

      {/* THE CARD'S IDENTITY: what was actually ordered. */}
      <h5 className="mt-0.5 min-w-0 cl-body font-semibold leading-snug text-slate-900">
        {identity}
      </h5>
      <p className="cl-meta text-slate-500">{metadata}</p>

      {children}
    </article>
  );
}

/* ------------------------------------------------------------------ *
 * Laboratory
 * ------------------------------------------------------------------ */

function LaboratoryCard({
  row,
  onOpen,
}: {
  row: LaboratoryReview;
  onOpen: (selection: Selection, origin: HTMLButtonElement) => void;
}) {
  const result = row.result;
  /*
    The identity is the ordered work, whichever side of the release it is on:
    the reported tests once a result exists, the ordered ones until then. Both
    answer the same question -- what is this card about.
  */
  const names = result
    ? result.lines.map((line) => line.name)
    : row.pending_tests.map((test) => test.name);
  const identity = serviceSummary(names);

  return (
    <RequestCard
      row={row}
      identity={identity}
      metadata={[
        "Laboratory",
        row.priority ?? "routine",
        result ? result.result_code : workflowLabel(row.workflow_status),
      ]
        .filter(Boolean)
        .join(" · ")}
      /* RESULT DATE, not the request's workflow state: once a result exists,
         when it was reported is the thing a doctor is placing in time. */
      context={
        result ? `Reported ${formatHospitalDate(result.result_date)}` : pendingReason(row)
      }
    >
      <div className="mt-1 flex flex-wrap items-center justify-between gap-x-3 gap-y-1">
        {result ? (
          /* ONE LINE, FROM THE FLAGS THE LABORATORY SENT. The panel itself is
             one click away; what belongs here is whether it needs opening
             now. */
          <p className="min-w-0 cl-secondary leading-snug text-slate-700">
            {labResultSummary(result.lines)}
          </p>
        ) : (
          <PendingBody cancelled={row.status === "cancelled"} />
        )}
        {canOpenResult(row) ? (
          <OpenResultButton
            label={`${identity} · ${row.request_code}`}
            onOpen={(origin) =>
              onOpen(
                {
                  kind: "laboratory",
                  row,
                  title: identity,
                  subtitle: [
                    row.request_code,
                    result?.result_code,
                    result ? `Reported ${formatHospitalDate(result.result_date)}` : null,
                  ]
                    .filter(Boolean)
                    .join(" · "),
                },
                origin,
              )
            }
          />
        ) : null}
      </div>

      <CardFooter row={row} />
    </RequestCard>
  );
}

/* ------------------------------------------------------------------ *
 * Radiology
 * ------------------------------------------------------------------ */

function RadiologyCard({
  row,
  onOpen,
}: {
  row: RadiologyReview;
  onOpen: (selection: Selection, origin: HTMLButtonElement) => void;
}) {
  const result = row.result;
  const exams = result ? result.exams : row.pending_exams;
  const modality = exams.find((exam) => exam.modality_label)?.modality_label;
  const identity = serviceSummary(exams.map((exam) => exam.name));

  return (
    <RequestCard
      row={row}
      identity={identity}
      metadata={[
        "Radiology",
        modality,
        row.priority ?? "routine",
        result ? result.result_code : workflowLabel(row.workflow_status),
      ]
        .filter(Boolean)
        .join(" · ")}
      context={
        result ? `Reported ${formatHospitalDate(result.result_date)}` : pendingReason(row)
      }
    >
      <div className="mt-1 flex flex-wrap items-center justify-between gap-x-3 gap-y-1">
        {result ? (
          /*
            ONE LINE OF IMPRESSION. It is the conclusion a clinician acts on, so
            a glance is often enough to know whether to open the report -- and
            clamping it to a single line is what keeps ten studies scannable.
            An empty released report says so here in the same one line.
          */
          <p
            className={`min-w-0 flex-1 truncate cl-secondary leading-snug ${
              result.has_report ? "text-slate-700" : "italic text-slate-500"
            }`}
          >
            {reportPreview(result)}
          </p>
        ) : (
          <PendingBody cancelled={row.status === "cancelled"} />
        )}
        {canOpenResult(row) ? (
          <OpenResultButton
            label={`${identity} · ${row.request_code}`}
            onOpen={(origin) =>
              onOpen(
                {
                  kind: "radiology",
                  row,
                  title: identity,
                  subtitle: [
                    row.request_code,
                    result?.result_code,
                    result?.radiologist,
                    result ? `Reported ${formatHospitalDate(result.result_date)}` : null,
                  ]
                    .filter(Boolean)
                    .join(" · "),
                },
                origin,
              )
            }
          />
        ) : null}
      </div>

      <CardFooter row={row} />
    </RequestCard>
  );
}

/* ------------------------------------------------------------------ *
 * Pending / cancelled body, and the shared footer
 * ------------------------------------------------------------------ */

/**
 * What a card says when there is no released result.
 *
 * PENDING READS AS WAITING, NOT AS BROKEN. The ordered services are already
 * the card identity above, so this is one short sentence about the state of
 * the work rather than a second list. Cancelled states the administrative
 * fact plainly and does not dress it up as a system error.
 */
function PendingBody({ cancelled }: { cancelled: boolean }) {
  return (
    <p className="mt-1 cl-secondary leading-snug text-slate-600">
      {cancelled
        ? "This order was cancelled. No result was reported."
        : "No released result yet."}
    </p>
  );
}

function CardFooter({ row }: { row: LaboratoryReview | RadiologyReview }) {
  const superseded = supersededText(row.superseded_count);
  // The result code now rides in the metadata line, next to the service it
  // identifies; this row is only for what is left over.
  if (!superseded && !row.diagnosis) return null;
  return (
    <div className="mt-1.5 flex flex-wrap items-center gap-x-2 gap-y-1 border-t border-slate-100 pt-1.5 cl-meta text-slate-500">
      {row.diagnosis ? (
        <span>
          Indication: {row.diagnosis.name}
          {row.diagnosis.code ? ` (${row.diagnosis.code})` : ""}
        </span>
      ) : null}
      {superseded ? (
        <span className="font-semibold text-amber-700">{superseded}</span>
      ) : null}
    </div>
  );
}
