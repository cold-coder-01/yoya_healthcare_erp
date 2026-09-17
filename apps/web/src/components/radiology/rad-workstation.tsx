"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { messageFromPayload } from "@/lib/api-error";
import {
  ACTIVE_LANE_KEY,
  RAD_SESSION_PATH,
  deskRoleLabel,
  detailIsLoading,
  laneStatuses,
  matchesSearch,
  requestPath,
  resolveSelection,
  visibleDetail,
  worklistPath,
} from "@/lib/rad-desk-format";
import type {
  ApiEnvelope,
  RadDeskRoles,
  RadModalityOption,
  RadQueueRow,
  RadRequestDetail,
  RadRequestResponse,
  RadSessionResponse,
  RadWorklistResponse,
  RadWorklistSummary,
} from "@/types/rad-desk";

import RadFilters from "./rad-filters";
import RadQueue from "./rad-queue";
import RadRequestPanel from "./rad-request-panel";

/**
 * The Radiology Desk. READ ONLY.
 *
 * QUEUE LEFT, REQUEST DETAIL RIGHT -- the arrangement the Laboratory and Doctor
 * desks already use, so the muscle memory transfers. Everything is one screen:
 * selecting a request never navigates.
 *
 * EVERY CALL IS A GET TO /api/radiology/*. The browser holds no Odoo session
 * and knows no Odoo URL; each BFF route is gated upstream by
 * reception_scope.may_rad_desk before it touches a record. There is no write
 * anywhere in this file, because Slice 1 has none.
 *
 * THE LANE COUNTS COME FROM THE SERVER and are never recounted here: they
 * describe the whole date + search + modality scope, not the rows on screen.
 */
export default function RadWorkstation() {
  const [lane, setLane] = useState(ACTIVE_LANE_KEY);
  // No date default: yesterday's order is still today's work.
  const [date, setDate] = useState("");
  const [modality, setModality] = useState("");
  const [search, setSearch] = useState("");
  const [debouncedSearch, setDebouncedSearch] = useState("");

  const [rows, setRows] = useState<RadQueueRow[]>([]);
  const [summary, setSummary] = useState<RadWorklistSummary | null>(null);
  const [modalities, setModalities] = useState<RadModalityOption[]>([]);
  const [truncated, setTruncated] = useState(false);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [detail, setDetail] = useState<RadRequestDetail | null>(null);
  /*
    A failed RE-READ of the request already on screen keeps it, marked stale,
    rather than blanking a panel the user is reading. Only a failed FIRST load
    of a request shows the error.
  */
  const [detailStaleFor, setDetailStaleFor] = useState<number | null>(null);
  const detailRef = useRef<RadRequestDetail | null>(null);
  useEffect(() => {
    detailRef.current = detail;
  }, [detail]);

  const [deskAllowed, setDeskAllowed] = useState<boolean | null>(null);
  const [roles, setRoles] = useState<RadDeskRoles | null>(null);
  const [queueLoading, setQueueLoading] = useState(true);
  const [detailLoading, setDetailLoading] = useState(false);
  const [queueError, setQueueError] = useState<string | null>(null);
  const [detailError, setDetailError] = useState<string | null>(null);
  const [refreshToken, setRefreshToken] = useState(0);

  useEffect(() => {
    const timer = setTimeout(() => setDebouncedSearch(search.trim()), 200);
    return () => clearTimeout(timer);
  }, [search]);

  /* ---------------- session ---------------- */
  /*
    PRESENTATION ONLY. A session 403 explains the backend's role refusal; the
    worklist and detail endpoints enforce the same gate independently.
  */
  useEffect(() => {
    const controller = new AbortController();

    async function loadSession() {
      try {
        const response = await fetch(RAD_SESSION_PATH, {
          cache: "no-store",
          signal: controller.signal,
        });
        const payload = (await response.json()) as ApiEnvelope<RadSessionResponse>;
        if (controller.signal.aborted) return;
        if (response.ok && payload.success) {
          setDeskAllowed(payload.data.capabilities.radiology_desk);
          setRoles(payload.data.roles);
        } else {
          setDeskAllowed(response.status === 403 ? false : null);
        }
      } catch {
        // A session we cannot read is not a refusal; the queue carries the error.
      }
    }

    void loadSession();
    return () => controller.abort();
  }, []);

  /* ---------------- queue ---------------- */
  const statuses = useMemo(() => laneStatuses(lane), [lane]);

  useEffect(() => {
    const controller = new AbortController();

    async function loadQueue() {
      setQueueLoading(true);
      try {
        const response = await fetch(
          worklistPath({
            status: statuses,
            date: date || null,
            q: debouncedSearch || null,
            modality: modality || null,
          }),
          { cache: "no-store", signal: controller.signal },
        );
        const payload = (await response.json()) as ApiEnvelope<RadWorklistResponse>;
        if (controller.signal.aborted) return;

        if (!response.ok || !payload.success) {
          setRows([]);
          setSummary(null);
          setQueueError(messageFromPayload(payload, "Unable to load the radiology queue."));
          return;
        }

        setRows(payload.data.rows ?? []);
        setSummary(payload.data.summary ?? null);
        setModalities(payload.data.meta.modalities ?? []);
        setTruncated(payload.data.meta.truncated);
        setQueueError(null);
      } catch {
        if (!controller.signal.aborted) {
          setRows([]);
          setSummary(null);
          setQueueError("Unable to reach the radiology service.");
        }
      } finally {
        if (!controller.signal.aborted) setQueueLoading(false);
      }
    }

    void loadQueue();
    return () => controller.abort();
  }, [statuses, date, modality, debouncedSearch, refreshToken]);

  /** Instant local narrowing while typing, ahead of the debounced server search. */
  const visibleRows = useMemo(
    () => rows.filter((row) => matchesSearch(row, search)),
    [rows, search],
  );

  /** The selection in force, DERIVED rather than synchronised. */
  const activeId = useMemo(
    () => resolveSelection(visibleRows, selectedId),
    [visibleRows, selectedId],
  );

  /* ---------------- selected request ---------------- */
  useEffect(() => {
    if (activeId === null) {
      // Nothing is written here: an effect that sets state cascades a render.
      // The panel's loading state is DERIVED below instead.
      return;
    }

    const controller = new AbortController();

    async function loadRequest(requestId: number) {
      setDetailLoading(true);
      setDetailError(null);
      try {
        const response = await fetch(requestPath(requestId), {
          cache: "no-store",
          signal: controller.signal,
        });
        const payload = (await response.json()) as ApiEnvelope<RadRequestResponse>;
        if (controller.signal.aborted) return;

        if (!response.ok || !payload.success) {
          if (detailRef.current?.id === requestId) {
            setDetailStaleFor(requestId);
            return;
          }
          setDetail(null);
          setDetailError(messageFromPayload(payload, "Unable to load the selected request."));
          return;
        }
        setDetail(payload.data.request);
        setDetailStaleFor(null);
      } catch {
        if (!controller.signal.aborted) {
          if (detailRef.current?.id === requestId) {
            setDetailStaleFor(requestId);
            return;
          }
          setDetail(null);
          setDetailError("Unable to reach the radiology service.");
        }
      } finally {
        if (!controller.signal.aborted) setDetailLoading(false);
      }
    }

    void loadRequest(activeId);
    return () => controller.abort();
  }, [activeId, refreshToken]);

  const refresh = useCallback(() => setRefreshToken((token) => token + 1), []);

  const detailForSelection = visibleDetail(detail, activeId);
  const panelIsLoading = detailIsLoading(activeId, detailLoading, detailForSelection);

  return (
    <div className="flex min-h-[640px] flex-col gap-2 min-[1100px]:h-full min-[1100px]:min-h-0">
      {deskAllowed === false ? (
        <div className="shrink-0 rounded-lg border border-amber-300 bg-amber-50 px-3 py-2 cl-body text-amber-900">
          <span className="font-bold">This is not your workstation.</span> The
          Radiology Desk is open to radiology technicians, radiologists, managers
          and system administrators. Ordering and reviewing imaging as a clinician
          is done from the Doctor Desk.
        </div>
      ) : null}

      <RadFilters
        lane={lane}
        date={date}
        modality={modality}
        search={search}
        modalities={modalities}
        loading={queueLoading}
        summary={summary}
        roleLabel={deskRoleLabel(roles)}
        onLaneChange={setLane}
        onDateChange={setDate}
        onModalityChange={setModality}
        onSearchChange={setSearch}
        onRefresh={refresh}
      />

      <div className="grid min-h-0 flex-1 gap-2 min-[1100px]:grid-cols-[minmax(380px,40%)_minmax(0,1fr)]">
        <RadQueue
          rows={visibleRows}
          selectedId={activeId}
          loading={queueLoading}
          error={queueError}
          truncated={truncated}
          onSelect={setSelectedId}
        />
        <RadRequestPanel
          detail={detailForSelection}
          loading={panelIsLoading}
          error={activeId !== null ? detailError : null}
          empty={visibleRows.length === 0}
          stale={detailForSelection !== null && detailStaleFor === detailForSelection.id}
        />
      </div>
    </div>
  );
}
