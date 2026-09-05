"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { messageFromPayload } from "@/lib/api-error";
import { formatHospitalDate } from "@/lib/clinical-format";
import {
  HISTORY_READ_ONLY_TEXT,
  HISTORY_UNAVAILABLE_TEXT,
  NO_HISTORY_TEXT,
  abnormalNote,
  abnormalNoteClass,
  contentChips,
  historyProgressText,
  primaryDiagnosisText,
  visitProvenance,
} from "@/lib/history-format";
import type { ApiEnvelope } from "@/types/doctor";
import type {
  DoctorHistoryDetailResponse,
  DoctorHistoryResponse,
  HistoryVisit,
} from "@/types/doctor-history";

import ClinicalResultViewerModal from "./clinical-result-viewer-modal";
import HistoryVisitView from "./history-visit-view";

const PAGE_SIZE = 20;

/**
 * The HISTORY section: this patient's PRIOR clinical episodes.
 *
 * A REVIEW SURFACE, AND ONLY THAT. There is no control on this screen that
 * writes anything -- no edit, no save, no correction, no cancel, no re-order.
 * A historical episode belongs to the consultation that created it, and Slice
 * 9A serves every one of these records read-only at the record-rule layer, so
 * a button here could not do anything except fail. The absence is the design.
 *
 * THE CURRENT VISIT IS NOT IN THIS LIST, and this component does not filter it
 * out. The server excludes the open episode by identity; re-deriving that rule
 * in the browser would be a second definition of "current" that could drift
 * from the one the API enforces. Whatever the API returns is the history.
 *
 * TWO LEVELS, AND THE SPLIT IS THE WHOLE PERFORMANCE STORY. The list carries
 * counts, never content: a doctor scanning ten visits costs ONE request. An
 * episode's narrative, results, imaging and prescriptions are fetched only when
 * that episode is opened, and cached for the session so reopening is free.
 * Fetching ten episodes' full contents to render ten one-line rows would move
 * several hundred kilobytes of clinical narrative that nobody reads.
 *
 * NOTHING IS PERSISTED. No localStorage, no sessionStorage, no cookie. Prior
 * clinical history is the most sensitive payload this desk handles and it lives
 * in React state for exactly as long as the tab is open.
 *
 * ACCESS CAN LAPSE WHILE THIS IS OPEN. Longitudinal reading is bounded by an
 * active care relationship, so completing or cancelling the current visit ends
 * it. When a refetch answers 404 the cached list AND any open episode are
 * CLEARED -- see `load` below. A tab left open must not keep a patient's
 * history on screen after the right to see it has gone.
 */
export default function HistoryWorkspace({
  appointmentId,
}: {
  appointmentId: number;
}) {
  const [data, setData] = useState<DoctorHistoryResponse | null>(null);
  const [visits, setVisits] = useState<HistoryVisit[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [loadingMore, setLoadingMore] = useState(false);

  /* The row that opened the viewer. Focus returns to it on close, so a
     keyboard user resumes where they were. */
  const originRef = useRef<HTMLButtonElement | null>(null);
  const [openVisit, setOpenVisit] = useState<HistoryVisit | null>(null);
  const [detail, setDetail] = useState<DoctorHistoryDetailResponse | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState<string | null>(null);
  /*
    Episodes already fetched this session, keyed by historical appointment id.
    A settled episode does not change, so reopening one is a state change
    rather than a request. Held in a ref rather than state: it is a cache, not
    something a render reads directly, and writing it must not schedule one.
  */
  const detailCache = useRef(new Map<number, DoctorHistoryDetailResponse>());

  /*
    THE FIRST PAGE ONLY. "Load more" is a separate, explicit fetch that appends;
    it deliberately does not live in this effect, because a paging request must
    not be re-issued when the effect's dependencies change.
  */
  useEffect(() => {
    const controller = new AbortController();

    async function load() {
      setLoading(true);
      setError(null);
      try {
        const response = await fetch(
          `/api/doctor/visits/${appointmentId}/history?limit=${PAGE_SIZE}&offset=0`,
          { cache: "no-store", signal: controller.signal },
        );
        const payload =
          (await response.json()) as ApiEnvelope<DoctorHistoryResponse>;
        if (controller.signal.aborted) return;
        if (!response.ok || !payload.success) {
          /*
            EVERYTHING IS DROPPED ON A REFUSAL, and 404 is the case that
            matters: it is what the server answers once the care relationship
            has lapsed. Keeping the previous page on screen would leave a
            patient's history visible after the right to read it ended, which
            is precisely the failure the bounded policy exists to prevent.
            An open episode goes with it.
          */
          setData(null);
          setVisits([]);
          setDetail(null);
          setOpenVisit(null);
          detailCache.current.clear();
          setError(
            response.status === 404
              ? HISTORY_UNAVAILABLE_TEXT
              : messageFromPayload(payload, HISTORY_UNAVAILABLE_TEXT),
          );
          return;
        }
        setData(payload.data);
        setVisits(payload.data.visits);
      } catch {
        if (!controller.signal.aborted) {
          setError("Unable to reach the history service.");
        }
      } finally {
        if (!controller.signal.aborted) setLoading(false);
      }
    }

    void load();
    return () => controller.abort();
  }, [appointmentId]);

  /*
    APPEND, NEVER REPLACE, and never re-sort. The server returns newest-first
    and each page continues where the last ended, so concatenation preserves
    the order. Re-sorting in the browser would be a second ordering rule that
    could disagree with the one the server applied.
  */
  const loadMore = useCallback(async () => {
    if (!data || !data.has_more || loadingMore) return;
    setLoadingMore(true);
    try {
      const response = await fetch(
        `/api/doctor/visits/${appointmentId}/history` +
          `?limit=${PAGE_SIZE}&offset=${visits.length}`,
        { cache: "no-store" },
      );
      const payload =
        (await response.json()) as ApiEnvelope<DoctorHistoryResponse>;
      if (!response.ok || !payload.success) {
        setError(
          response.status === 404
            ? HISTORY_UNAVAILABLE_TEXT
            : messageFromPayload(payload, "Unable to load more visits."),
        );
        return;
      }
      setData(payload.data);
      setVisits((current) => [...current, ...payload.data.visits]);
    } catch {
      setError("Unable to reach the history service.");
    } finally {
      setLoadingMore(false);
    }
  }, [appointmentId, data, loadingMore, visits.length]);

  const closeVisit = useCallback(() => {
    setOpenVisit(null);
    setDetail(null);
    setDetailError(null);
    const origin = originRef.current;
    originRef.current = null;
    // Next frame: the row is still behind the modal at this point in the
    // commit, and focusing an element about to re-render loses the ring.
    if (origin) requestAnimationFrame(() => origin.focus());
  }, []);

  /*
    OPENING AN EPISODE IS THE ONLY THING THAT FETCHES DETAIL, and it fetches
    exactly one episode. A cached episode opens with no request at all.
  */
  const openEpisode = useCallback(
    async (visit: HistoryVisit, origin: HTMLButtonElement) => {
      const historicalId = visit.appointment_id;
      originRef.current = origin;
      setOpenVisit(visit);
      setDetailError(null);

      if (historicalId === null) {
        // An episode with no appointment cannot be addressed by the detail
        // route. Slice 9A addresses history by appointment id, so this is
        // stated honestly rather than guessed around.
        setDetail(null);
        setDetailError(HISTORY_UNAVAILABLE_TEXT);
        return;
      }

      const cached = detailCache.current.get(historicalId);
      if (cached) {
        setDetail(cached);
        return;
      }

      setDetail(null);
      setDetailLoading(true);
      try {
        const response = await fetch(
          `/api/doctor/visits/${appointmentId}/history/${historicalId}`,
          { cache: "no-store" },
        );
        const payload =
          (await response.json()) as ApiEnvelope<DoctorHistoryDetailResponse>;
        if (!response.ok || !payload.success) {
          // The same sentence for every refusal. A per-case message would tell
          // a reader which of "gone", "never existed" and "not yours" it was.
          setDetail(null);
          setDetailError(HISTORY_UNAVAILABLE_TEXT);
          return;
        }
        detailCache.current.set(historicalId, payload.data);
        setDetail(payload.data);
      } catch {
        setDetailError("Unable to reach the history service.");
      } finally {
        setDetailLoading(false);
      }
    },
    [appointmentId],
  );

  /* ---------------- states ---------------- */
  if (loading && !visits.length) {
    return (
      <div className="flex flex-col gap-2" aria-busy="true">
        {[0, 1, 2].map((row) => (
          <div
            key={row}
            className="h-16 animate-pulse rounded-lg border border-slate-200 bg-white motion-reduce:animate-none"
          />
        ))}
        <p className="cl-secondary text-slate-500">Loading history…</p>
      </div>
    );
  }

  if (error && !visits.length) {
    return (
      <div
        role="alert"
        className="rounded-md border border-amber-300 bg-amber-50 px-3 py-2.5"
      >
        <p className="cl-secondary font-semibold leading-snug text-amber-900">
          {error}
        </p>
      </div>
    );
  }

  const progress = historyProgressText(visits.length, data?.total ?? 0);

  return (
    <div className="flex flex-col gap-3">
      {/* ---- Bar ---- */}
      <div className="flex flex-wrap items-center gap-2">
        <h3 className="cl-meta font-bold uppercase tracking-[0.07em] text-slate-500">
          Prior visits
        </h3>
        <span aria-hidden className="h-px flex-1 bg-slate-200" />
        {progress ? (
          <span className="cl-meta text-slate-500">{progress}</span>
        ) : null}
      </div>

      {/* A failure while rows are on screen: say so, keep what is there. */}
      {error && visits.length ? (
        <p
          role="alert"
          className="rounded-md border border-amber-300 bg-amber-50 px-3 py-1.5 cl-meta leading-snug text-amber-900"
        >
          {error}
        </p>
      ) : null}

      {!visits.length ? (
        <p className="rounded-lg border border-slate-200 bg-white px-3 py-6 text-center cl-secondary text-slate-500">
          {NO_HISTORY_TEXT}
        </p>
      ) : (
        <ul className="flex flex-col gap-1.5">
          {visits.map((visit) => (
            <li key={visit.encounter_id}>
              <EpisodeRow visit={visit} onOpen={openEpisode} />
            </li>
          ))}
        </ul>
      )}

      {data?.has_more ? (
        <button
          type="button"
          onClick={() => void loadMore()}
          disabled={loadingMore}
          className="self-center rounded border border-slate-300 bg-white px-3 py-1 cl-meta font-bold uppercase tracking-wide text-slate-600 outline-none transition-colors hover:border-slate-400 hover:bg-slate-50 hover:text-slate-900 focus-visible:ring-2 focus-visible:ring-emerald-600 disabled:opacity-60"
        >
          {loadingMore ? "Loading…" : "Load more"}
        </button>
      ) : null}

      {openVisit ? (
        <ClinicalResultViewerModal
          title={openVisit.encounter_code ?? "Previous visit"}
          subtitle={
            [formatHospitalDate(openVisit.date, ""), visitProvenance(openVisit)]
              .filter(Boolean)
              .join(" · ") || null
          }
          badge={HISTORY_READ_ONLY_TEXT}
          onClose={closeVisit}
        >
          <HistoryVisitView
            appointmentId={appointmentId}
            visit={openVisit}
            detail={detail}
            loading={detailLoading}
            error={detailError}
          />
        </ClinicalResultViewerModal>
      ) : null}
    </div>
  );
}

/**
 * One prior episode, as a compact chart line.
 *
 * A BUTTON, not a card with a nested link. The whole row is the target, it is
 * reachable by keyboard, and it announces the episode it opens rather than
 * "View" repeated down the list.
 *
 * WHAT IS DELIBERATELY NOT HERE: note text, result values, report narrative and
 * medication instructions. Those are what opening the visit is for, and a row
 * carrying them would make the list impossible to scan and expensive to build.
 */
function EpisodeRow({
  visit,
  onOpen,
}: {
  visit: HistoryVisit;
  onOpen: (visit: HistoryVisit, origin: HTMLButtonElement) => void;
}) {
  const chips = contentChips(visit.counts);
  const abnormal = abnormalNote(visit.counts);
  const provenance = visitProvenance(visit);
  const primary = primaryDiagnosisText(visit);
  /* An EMPTY fallback, not the default dash: this row needs to distinguish
     "no date recorded" in words, and formatHospitalDate never returns null. */
  const date = formatHospitalDate(visit.date, "");

  return (
    <button
      type="button"
      onClick={(event) => onOpen(visit, event.currentTarget)}
      aria-label={`Open visit ${visit.encounter_code ?? ""}${
        date ? ` on ${date}` : ""
      }`.trim()}
      className="flex w-full flex-col gap-1 rounded-lg border border-slate-200 bg-white px-3 py-2 text-left outline-none transition-colors hover:border-emerald-400 hover:bg-emerald-50/30 focus-visible:border-emerald-600 focus-visible:ring-2 focus-visible:ring-emerald-600/40"
    >
      <span className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5">
        <span className="cl-secondary font-bold text-slate-900">
          {date || "Date not recorded"}
        </span>
        {visit.encounter_code ? (
          <span className="cl-micro font-semibold uppercase tracking-wide text-slate-500">
            {visit.encounter_code}
          </span>
        ) : null}
        <span aria-hidden className="flex-1" />
        {visit.status_label ? (
          <span className="shrink-0 rounded border border-slate-300 bg-slate-50 px-1.5 py-px cl-micro font-bold uppercase tracking-wide text-slate-600">
            {visit.status_label}
          </span>
        ) : null}
      </span>

      {provenance ? (
        <span className="cl-meta text-slate-600">{provenance}</span>
      ) : null}

      {visit.chief_complaint ? (
        <span className="line-clamp-1 cl-meta text-slate-700">
          {visit.chief_complaint}
        </span>
      ) : null}

      {primary ? (
        <span className="flex flex-wrap items-baseline gap-1.5">
          <span className="cl-micro font-bold uppercase tracking-wide text-emerald-700">
            Primary
          </span>
          <span className="cl-meta font-semibold text-slate-900">{primary}</span>
        </span>
      ) : null}

      {chips.length || abnormal ? (
        <span className="flex flex-wrap items-center gap-1">
          {chips.map((chip) => (
            <span
              key={chip.key}
              className="rounded border border-slate-200 bg-slate-50 px-1.5 py-px cl-micro font-semibold text-slate-600"
            >
              {chip.label}
            </span>
          ))}
          {abnormal ? (
            /* THE WORD IS PRESENT, not colour alone: the same rule the Results
               tab follows, so the signal survives a printout and a reader who
               cannot separate amber from red. */
            <span
              className={`rounded border px-1.5 py-px cl-micro font-bold ${abnormalNoteClass(
                abnormal.tone,
              )}`}
            >
              {abnormal.text}
            </span>
          ) : null}
        </span>
      ) : null}
    </button>
  );
}
