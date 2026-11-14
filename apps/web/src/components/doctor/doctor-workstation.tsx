"use client";

import { useCallback, useEffect, useMemo, useState } from "react";

import { messageFromPayload } from "@/lib/api-error";
import { hospitalToday } from "@/lib/clinical-format";
import {
  isConsultationMode,
  isConsultationVisible,
} from "@/lib/consultation-format";
import type { ConsultationSection } from "@/lib/diagnosis-format";
import { bucketCounts, bucketOf } from "@/lib/doctor-format";
import {
  INPATIENTS_LANE,
  INPATIENTS_PATH,
  emptyQueueText,
  inpatientsByVisit,
  matchesSearch,
  truncatedText,
  visitSource,
  type DoctorLane,
} from "@/lib/doctor-inpatients";
import type {
  ApiEnvelope,
  DoctorInpatientRow,
  DoctorInpatientsResponse,
  DoctorQueueResponse,
  DoctorQueueRow,
  DoctorVisitResponse,
  DoctorVisitSearchResponse,
} from "@/types/doctor";

import ConsultationWorkspace from "./consultation/consultation-workspace";
import DoctorBucketBar from "./doctor-bucket-bar";
import DoctorFilters from "./doctor-filters";
import DoctorOrderRail from "./doctor-order-rail";
import DoctorPatientPanel from "./doctor-patient-panel";
import DoctorQueue from "./doctor-queue";

/**
 * The Doctor Desk.
 *
 * QUEUE LEFT, PATIENT CENTRE-RIGHT, ORDER RAIL FAR RIGHT -- the arrangement
 * doctors already work in on the vendor OPD screen, so the muscle memory
 * transfers. Everything is one screen: selecting a patient never navigates.
 *
 * The browser holds no Odoo session and knows no Odoo URL. Every call below
 * goes to /api/doctor/*, and every one of those is scoped by Odoo record rules
 * plus the clinical scope domain before it returns a row.
 */
export default function DoctorWorkstation() {
  // Starts on today and is never blank while a day's queue is shown. Clearing
  // it switches the desk to "search all my visits".
  const [date, setDate] = useState(hospitalToday());
  const [lane, setLane] = useState<DoctorLane>("all");
  const [search, setSearch] = useState("");
  const [debouncedSearch, setDebouncedSearch] = useState("");

  const [rows, setRows] = useState<DoctorQueueRow[]>([]);
  const [truncated, setTruncated] = useState(false);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [detail, setDetail] = useState<DoctorVisitResponse | null>(null);

  const [queueLoading, setQueueLoading] = useState(true);
  const [detailLoading, setDetailLoading] = useState(false);
  const [queueError, setQueueError] = useState<string | null>(null);
  const [detailError, setDetailError] = useState<string | null>(null);
  const [refreshToken, setRefreshToken] = useState(0);

  // The Inpatients lane: its own source, independent of the date.
  const [inpatientRows, setInpatientRows] = useState<DoctorInpatientRow[]>([]);
  const [inpatientsLoaded, setInpatientsLoaded] = useState(false);
  const [inpatientTruncated, setInpatientTruncated] = useState(false);
  const [inpatientLoading, setInpatientLoading] = useState(true);
  const [inpatientError, setInpatientError] = useState<string | null>(null);
  const [inpatientToken, setInpatientToken] = useState(0);

  useEffect(() => {
    const timer = setTimeout(() => setDebouncedSearch(search.trim().toLowerCase()), 200);
    return () => clearTimeout(timer);
  }, [search]);

  /*
    WHERE THE VISIT LIST COMES FROM. A date set: that day's queue, exactly as
    before. Date cleared: the server searches every date once the term is two
    letters long; shorter, nothing is fetched and the prompt shows. The
    browser never sends an empty date.
  */
  const source = useMemo(() => visitSource(date, debouncedSearch), [date, debouncedSearch]);
  const sourcePath = source.kind === "prompt" ? null : source.path;

  /* ---------------- queue ---------------- */
  useEffect(() => {
    if (!sourcePath) {
      return;
    }
    const path = sourcePath;
    const controller = new AbortController();

    async function loadQueue() {
      setQueueLoading(true);
      try {
        const response = await fetch(path, {
          cache: "no-store",
          signal: controller.signal,
        });
        const payload = (await response.json()) as ApiEnvelope<
          DoctorQueueResponse | DoctorVisitSearchResponse
        >;
        if (controller.signal.aborted) return;

        if (!response.ok || !payload.success) {
          setQueueError(messageFromPayload(payload, "Unable to load your queue."));
          return;
        }

        const nextRows = payload.data.rows ?? [];
        setRows(nextRows);
        setTruncated(payload.data.meta.truncated);
        setQueueError(null);
        // Keep the doctor's chosen patient across refreshes. The selection in
        // force is DERIVED below from the visible rows, so a patient who is gone
        // from this list already falls back to its top -- and one chosen in the
        // Inpatients lane is not discarded by a reload of the day's queue.
        setSelectedId((current) => current ?? nextRows[0]?.appointment_id ?? null);
      } catch {
        if (!controller.signal.aborted) {
          setQueueError("Unable to reach the doctor queue service.");
        }
      } finally {
        if (!controller.signal.aborted) setQueueLoading(false);
      }
    }

    void loadQueue();
    return () => controller.abort();
  }, [sourcePath, refreshToken]);

  /* ---------------- inpatients (independent of the date) ---------------- */
  useEffect(() => {
    const controller = new AbortController();

    async function loadInpatients() {
      setInpatientLoading(true);
      try {
        const response = await fetch(INPATIENTS_PATH, {
          cache: "no-store",
          signal: controller.signal,
        });
        const payload = (await response.json()) as ApiEnvelope<DoctorInpatientsResponse>;
        if (controller.signal.aborted) return;

        if (!response.ok || !payload.success) {
          setInpatientError(messageFromPayload(payload, "Unable to load your inpatients."));
          return;
        }
        setInpatientRows(payload.data.rows ?? []);
        setInpatientTruncated(payload.data.meta.truncated);
        setInpatientsLoaded(true);
        setInpatientError(null);
      } catch {
        if (!controller.signal.aborted) {
          setInpatientError("Unable to reach the inpatient service.");
        }
      } finally {
        if (!controller.signal.aborted) setInpatientLoading(false);
      }
    }

    void loadInpatients();
    return () => controller.abort();
  }, [refreshToken, inpatientToken]);

  const inpatientLane = lane === INPATIENTS_LANE;
  const inpatientMap = useMemo(() => inpatientsByVisit(inpatientRows), [inpatientRows]);
  // In prompt mode nothing was fetched for this search: show no stale day.
  const sourceRows = useMemo(() => (sourcePath ? rows : []), [sourcePath, rows]);
  const listSource = inpatientLane ? INPATIENTS_LANE : source.kind;

  /**
   * Bucket and text filtering happen in the browser, over rows Odoo already
   * scoped and returned. This narrows a visible list; it can never widen it.
   */
  const visibleRows = useMemo(() => {
    if (inpatientLane) {
      return inpatientRows
        .map((row) => row.visit)
        .filter((row) =>
          matchesSearch(row, debouncedSearch, INPATIENTS_LANE, inpatientMap.get(row.appointment_id)),
        );
    }
    return sourceRows.filter((row) => {
      if (lane !== "all" && bucketOf(row) !== lane) return false;
      return matchesSearch(row, debouncedSearch, source.kind);
    });
  }, [inpatientLane, inpatientRows, inpatientMap, sourceRows, lane, debouncedSearch, source.kind]);

  /**
   * The selection actually in force, DERIVED rather than synchronised.
   *
   * `selectedId` is the doctor's stated intent; this is what the screen can
   * honour right now. When a filter change hides the selected patient the
   * panel falls to the top of the visible list immediately, during the same
   * render -- an effect that wrote selectedId back would render one frame
   * showing a patient who is no longer in the queue, and would trip
   * react-hooks/set-state-in-effect for exactly that reason.
   */
  const activeId = useMemo(() => {
    if (selectedId && visibleRows.some((row) => row.appointment_id === selectedId)) {
      return selectedId;
    }
    return visibleRows[0]?.appointment_id ?? null;
  }, [visibleRows, selectedId]);

  /* ---------------- selected visit ---------------- */
  useEffect(() => {
    if (!activeId) {
      return;
    }

    const controller = new AbortController();

    async function loadVisit() {
      setDetailLoading(true);
      setDetailError(null);
      try {
        const response = await fetch(`/api/doctor/visits/${activeId}`, {
          cache: "no-store",
          signal: controller.signal,
        });
        const payload = (await response.json()) as ApiEnvelope<DoctorVisitResponse>;
        if (controller.signal.aborted) return;

        if (!response.ok || !payload.success) {
          setDetail(null);
          setDetailError(
            messageFromPayload(payload, "Unable to load the selected patient."),
          );
          return;
        }
        setDetail(payload.data);
      } catch {
        if (!controller.signal.aborted) {
          setDetail(null);
          setDetailError("Unable to reach the patient service.");
        }
      } finally {
        if (!controller.signal.aborted) setDetailLoading(false);
      }
    }

    void loadVisit();
    return () => controller.abort();
  }, [activeId, refreshToken]);

  const refresh = useCallback(() => setRefreshToken((token) => token + 1), []);

  // Counts describe the whole day, not the filtered slice: a doctor filtering
  // to "Wait" still needs to see how many are ready for review.
  const counts = useMemo(() => bucketCounts(sourceRows), [sourceRows]);
  const reloadInpatients = useCallback(() => setInpatientToken((token) => token + 1), []);
  const empty = emptyQueueText(listSource);

  /*
    Matching the loaded detail against the ACTIVE id is what stops the panel
    showing the previous patient for a frame after the selection moves, and it
    is also why the loader never nulls `detail`: a refresh of the same patient
    keeps their record on screen while "Updating…" carries the feedback.
  */
  const detailForSelection =
    detail && detail.visit.appointment_id === activeId ? detail : null;

  /*
    THE MODE, DERIVED FROM THE AUTHORITATIVE VISIT STATE.

    `in_consultation` is written by hospital.appointment.action_start_consultation
    after four independent model-layer gates, and it arrives here on the visit
    payload. Deriving the mode from it rather than from a local "I clicked
    Start" flag is what stops the two disagreeing: a visit started in another
    tab, in Odoo, or by a manager opens the workspace here on the next refresh,
    and a consultation this browser thinks it started but the model refused
    never does.
  */
  /*
    TWO QUESTIONS, ANSWERED SEPARATELY.

    `consultationVisible` decides whether the clinical workspace renders at all.
    It covers a COMPLETED visit too, because the note, diagnoses and orders of a
    finished consultation must stay readable from the desk -- keying the
    workspace on in_consultation alone put the doctor back on the
    Start Consultation panel the instant they signed off.

    `consultationOpen` still means "the doctor may act". It gates the Clinical
    Actions rail, which offers WRITES; the workspace itself takes editability
    from the server's own consultation.editable rather than from either flag.
  */
  const consultationVisible = isConsultationVisible(
    detailForSelection?.visit.state,
  );
  const consultationOpen = isConsultationMode(detailForSelection?.visit.state);

  /*
    THE OPEN SECTION, tagged with the visit it belongs to.

    Storing the id alongside the section is what resets it to Note when the
    doctor selects a different patient, WITHOUT an effect that writes state
    during render. Same pattern doctor-patient-panel.tsx uses for its start
    error, and for the same reason: a value that belongs to one visit must
    never be read while a different one is on screen.

    It lives here rather than inside the workspace so the Clinical Actions rail
    can focus a section as well as the section bar.
  */
  const [sectionState, setSectionState] = useState<{
    appointmentId: number | null;
    section: ConsultationSection;
  }>({ appointmentId: null, section: "note" });

  const section =
    sectionState.appointmentId === activeId ? sectionState.section : "note";
  const openSection = useCallback(
    (next: ConsultationSection) =>
      setSectionState({ appointmentId: activeId, section: next }),
    [activeId],
  );

  return (
    /*
      The shell now owns the viewport height (h-screen + overflow-hidden on the
      layout), so this fills its parent rather than recomputing 100vh minus a
      chrome offset that had to be kept in sync with the header by hand.
    */
    <div className="flex min-h-[640px] flex-col gap-2 min-[1100px]:h-full min-[1100px]:min-h-0">
      <DoctorBucketBar
        counts={counts}
        active={lane}
        onChange={setLane}
        inpatientCount={inpatientsLoaded ? inpatientRows.length : null}
      />
      <DoctorFilters
        date={date}
        search={search}
        loading={queueLoading}
        onDateChange={setDate}
        onSearchChange={setSearch}
        onRefresh={refresh}
      />

      <div className="grid min-h-0 flex-1 gap-2 min-[1100px]:grid-cols-[minmax(320px,26%)_minmax(0,1fr)_minmax(196px,216px)]">
        <DoctorQueue
          rows={visibleRows}
          selectedId={activeId}
          loading={inpatientLane ? inpatientLoading : Boolean(sourcePath) && queueLoading}
          error={inpatientLane ? inpatientError : queueError}
          truncated={inpatientLane ? inpatientTruncated : Boolean(sourcePath) && truncated}
          onSelect={setSelectedId}
          title={
            inpatientLane ? "Inpatients" : source.kind === "search" ? "All My Visits" : "Patient Queue"
          }
          inpatients={inpatientLane ? inpatientMap : null}
          showDate={inpatientLane || source.kind === "search"}
          emptyTitle={empty.title}
          emptyBody={empty.body}
          truncatedMessage={truncatedText(listSource)}
        />
        {consultationVisible && detailForSelection ? (
          /*
            key={appointment_id} is LOAD-BEARING, not a lint appeasement. It
            forces a fresh mount per patient, so the previous patient's draft,
            version token and status can never survive a selection change into
            the next patient's editor -- the single most dangerous thing this
            screen could do. It also means a late in-flight save resolves onto
            an unmounted component and is discarded.
          */
          <ConsultationWorkspace
            key={detailForSelection.visit.appointment_id}
            detail={detailForSelection}
            loading={detailLoading}
            section={section}
            onSectionChange={openSection}
            /*
              One token bump refetches BOTH the queue and the open patient, so
              the visit's new state, its new bucket and the read-only workspace
              can never disagree after a completion. The detail the server
              returned is discarded here deliberately: the refetch is the same
              path every other state change already uses, and having one
              recovery path is worth more than saving a round trip.
            */
            onCompleted={refresh}
            onAdmissionChanged={reloadInpatients}
          />
        ) : (
          <DoctorPatientPanel
            detail={detailForSelection}
            loading={detailLoading}
            error={activeId ? detailError : null}
            // One token bump refetches BOTH the queue and the open patient, so
            // the two can never disagree about the visit's state after a start.
            // That refetch is also what swaps this panel for the workspace.
            onStarted={refresh}
            onAdmissionChanged={reloadInpatients}
          />
        )}
        <div className="hidden min-h-0 min-[1100px]:flex min-[1100px]:flex-col">
          <DoctorOrderRail
            visitState={detailForSelection?.visit.state ?? null}
            encounterName={detailForSelection?.encounter?.name ?? null}
            /*
              The rail offers WRITES, so it stays keyed on consultationOpen. A
              completed visit renders the workspace but must not be invited to
              record a diagnosis or place an order.
            */
            diagnosisActive={consultationOpen && section === "diagnosis"}
            onOpenDiagnosis={
              consultationOpen ? () => openSection("diagnosis") : null
            }
            laboratoryActive={consultationOpen && section === "orders"}
            onOpenLaboratory={
              consultationOpen ? () => openSection("orders") : null
            }
          />
        </div>
      </div>
    </div>
  );
}
