"use client";

import { useCallback, useEffect, useMemo, useState } from "react";

import { messageFromPayload } from "@/lib/api-error";
import { hospitalToday } from "@/lib/clinical-format";
import { bucketCounts, bucketOf, type DoctorBucket } from "@/lib/doctor-format";
import type {
  ApiEnvelope,
  DoctorQueueResponse,
  DoctorQueueRow,
  DoctorVisitResponse,
} from "@/types/doctor";

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
  const [date, setDate] = useState(hospitalToday());
  const [bucket, setBucket] = useState<DoctorBucket>("all");
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

  useEffect(() => {
    const timer = setTimeout(() => setDebouncedSearch(search.trim().toLowerCase()), 200);
    return () => clearTimeout(timer);
  }, [search]);

  /* ---------------- queue ---------------- */
  useEffect(() => {
    const controller = new AbortController();

    async function loadQueue() {
      setQueueLoading(true);
      try {
        const response = await fetch(`/api/doctor/queue?date=${date}`, {
          cache: "no-store",
          signal: controller.signal,
        });
        const payload = (await response.json()) as ApiEnvelope<DoctorQueueResponse>;
        if (controller.signal.aborted) return;

        if (!response.ok || !payload.success) {
          setQueueError(messageFromPayload(payload, "Unable to load your queue."));
          return;
        }

        const nextRows = payload.data.rows ?? [];
        setRows(nextRows);
        setTruncated(payload.data.meta.truncated);
        setQueueError(null);
        // Keep the current patient selected across refreshes; fall back to the
        // top of the queue only when they are genuinely gone from it.
        setSelectedId((current) =>
          current && nextRows.some((row) => row.appointment_id === current)
            ? current
            : (nextRows[0]?.appointment_id ?? null),
        );
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
  }, [date, refreshToken]);

  /**
   * Bucket and text filtering happen in the browser, over rows Odoo already
   * scoped and returned. This narrows a visible list; it can never widen it.
   */
  const visibleRows = useMemo(() => {
    return rows.filter((row) => {
      if (bucket !== "all" && bucketOf(row) !== bucket) return false;
      if (!debouncedSearch) return true;
      const haystack = `${row.patient.name} ${row.patient.mrn ?? ""} ${
        row.appointment_code ?? ""
      }`.toLowerCase();
      return haystack.includes(debouncedSearch);
    });
  }, [rows, bucket, debouncedSearch]);

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
  const counts = useMemo(() => bucketCounts(rows), [rows]);

  /*
    Matching the loaded detail against the ACTIVE id is what stops the panel
    showing the previous patient for a frame after the selection moves, and it
    is also why the loader never nulls `detail`: a refresh of the same patient
    keeps their record on screen while "Updating…" carries the feedback.
  */
  const detailForSelection =
    detail && detail.visit.appointment_id === activeId ? detail : null;

  return (
    /* 100vh minus the shell chrome: 44px header + 1px border + 24px padding. */
    <div className="flex min-h-[640px] flex-col gap-2 min-[1100px]:h-[calc(100vh-69px)] min-[1100px]:min-h-[560px]">
      <DoctorBucketBar counts={counts} active={bucket} onChange={setBucket} />
      <DoctorFilters
        date={date}
        search={search}
        loading={queueLoading}
        onDateChange={setDate}
        onSearchChange={setSearch}
        onRefresh={refresh}
      />

      <div className="grid min-h-0 flex-1 gap-2 min-[1100px]:grid-cols-[minmax(340px,30%)_minmax(0,1fr)_minmax(180px,200px)]">
        <DoctorQueue
          rows={visibleRows}
          selectedId={activeId}
          loading={queueLoading}
          error={queueError}
          truncated={truncated}
          onSelect={setSelectedId}
        />
        <DoctorPatientPanel
          detail={detailForSelection}
          loading={detailLoading}
          error={activeId ? detailError : null}
          // One token bump refetches BOTH the queue and the open patient, so
          // the two can never disagree about the visit's state after a start.
          onStarted={refresh}
        />
        <div className="hidden min-h-0 min-[1100px]:flex min-[1100px]:flex-col">
          <DoctorOrderRail
            visitState={detailForSelection?.visit.state ?? null}
            encounterName={detailForSelection?.encounter?.name ?? null}
          />
        </div>
      </div>
    </div>
  );
}
