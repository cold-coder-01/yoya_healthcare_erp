"use client";

import { useCallback, useEffect, useMemo, useState } from "react";

import { messageFromPayload } from "@/lib/api-error";
import {
  matchesSearch,
  requestPath,
  resolveSelection,
  statusCounts,
  worklistPath,
} from "@/lib/lab-desk-format";
import type {
  ApiEnvelope,
  LabQueueRow,
  LabRequestDetail,
  LabRequestResponse,
  LabSessionResponse,
  LabWorklistResponse,
} from "@/types/lab-desk";

import LabFilters, { laneStatuses } from "./lab-filters";
import LabQueue from "./lab-queue";
import LabRequestPanel from "./lab-request-panel";

/**
 * The Laboratory Desk.
 *
 * QUEUE LEFT, REQUEST DETAIL RIGHT -- the two-panel arrangement the Doctor
 * Desk already establishes, so the muscle memory transfers between
 * workstations. Everything is one screen: selecting a request never navigates.
 *
 * The browser holds no Odoo session and knows no Odoo URL. Every call below
 * goes to /api/laboratory/*, and every one of those is gated by
 * services/reception_scope.may_lab_desk before it touches a record and scoped
 * by Odoo record rules after it does.
 *
 * SLICE 1 IS READ-ONLY. There is no mutation anywhere in this tree.
 */
export default function LabWorkstation() {
  const [lane, setLane] = useState("active");
  // NO DATE DEFAULT, unlike the Doctor Desk. Bench work does not expire at
  // midnight: a specimen ordered yesterday is still uncollected this morning,
  // and defaulting to today would hide exactly the request that has waited
  // longest. The server treats an absent date as "all outstanding days".
  const [date, setDate] = useState("");
  const [search, setSearch] = useState("");
  const [debouncedSearch, setDebouncedSearch] = useState("");

  const [rows, setRows] = useState<LabQueueRow[]>([]);
  const [truncated, setTruncated] = useState(false);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [detail, setDetail] = useState<LabRequestDetail | null>(null);

  const [deskAllowed, setDeskAllowed] = useState<boolean | null>(null);
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
    PRESENTATION ONLY. A session 403 explains the backend's role refusal.
    The worklist endpoint independently enforces the same role gate.
  */
  useEffect(() => {
    const controller = new AbortController();

    async function loadSession() {
      try {
        const response = await fetch("/api/laboratory/session", {
          cache: "no-store",
          signal: controller.signal,
        });
        const payload = (await response.json()) as ApiEnvelope<LabSessionResponse>;
        if (controller.signal.aborted) return;
        setDeskAllowed(
          response.ok && payload.success
            ? payload.data.capabilities.lab_desk
            : response.status === 403 ? false : null,
        );
      } catch {
        // A session we cannot read is not a refusal. Leave the banner off and
        // let the queue's own error carry whatever went wrong.
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
            // The search goes UPSTREAM as well as filtering locally: the local
            // pass only narrows the page already fetched, so a match beyond the
            // limit would be invisible without it.
            q: debouncedSearch || null,
          }),
          { cache: "no-store", signal: controller.signal },
        );
        const payload = (await response.json()) as ApiEnvelope<LabWorklistResponse>;
        if (controller.signal.aborted) return;

        if (!response.ok || !payload.success) {
          setRows([]);
          setQueueError(
            messageFromPayload(payload, "Unable to load the laboratory queue."),
          );
          return;
        }

        setRows(payload.data.rows ?? []);
        setTruncated(payload.data.meta.truncated);
        setQueueError(null);
      } catch {
        if (!controller.signal.aborted) {
          setRows([]);
          setQueueError("Unable to reach the laboratory queue service.");
        }
      } finally {
        if (!controller.signal.aborted) setQueueLoading(false);
      }
    }

    void loadQueue();
    return () => controller.abort();
  }, [statuses, date, debouncedSearch, refreshToken]);

  /**
   * Local text filtering, over rows Odoo already scoped and returned. This
   * narrows a visible list and can never widen it -- it is the instant
   * feedback while typing, ahead of the debounced server search.
   */
  const visibleRows = useMemo(
    () => rows.filter((row) => matchesSearch(row, search)),
    [rows, search],
  );

  /**
   * The selection actually in force, DERIVED rather than synchronised.
   *
   * `selectedId` is the technician's stated intent; this is what the screen can
   * honour right now. When a lane change or a refresh removes the selected
   * request, the panel falls to the top of the visible list during the same
   * render -- an effect that wrote selectedId back would render one frame
   * showing a request that is no longer in the queue, and would trip
   * react-hooks/set-state-in-effect for exactly that reason.
   */
  const activeId = useMemo(
    () => resolveSelection(visibleRows, selectedId),
    [visibleRows, selectedId],
  );

  /* ---------------- selected request ---------------- */
  useEffect(() => {
    if (activeId === null) {
      // The queue emptied under the selection. NOTHING IS CLEARED HERE: the
      // panel is derived from `activeId` below, so a stale `detail` is already
      // invisible, and writing state from an effect body would cascade a
      // render for no gain (react-hooks/set-state-in-effect).
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
        const payload = (await response.json()) as ApiEnvelope<LabRequestResponse>;
        if (controller.signal.aborted) return;

        if (!response.ok || !payload.success) {
          setDetail(null);
          setDetailError(
            messageFromPayload(payload, "Unable to load the selected request."),
          );
          return;
        }
        setDetail(payload.data.request);
      } catch {
        if (!controller.signal.aborted) {
          setDetail(null);
          setDetailError("Unable to reach the laboratory service.");
        }
      } finally {
        if (!controller.signal.aborted) setDetailLoading(false);
      }
    }

    void loadRequest(activeId);
    return () => controller.abort();
  }, [activeId, refreshToken]);

  const refresh = useCallback(() => setRefreshToken((token) => token + 1), []);

  // Counts describe the rows on screen, so a lane count and the list under it
  // can never disagree.
  const counts = useMemo(() => statusCounts(visibleRows), [visibleRows]);

  /*
    Matching the loaded detail against the ACTIVE id is what stops the panel
    showing the previous request for a frame after the selection moves, and it
    is also why the loader never nulls `detail` on a same-request refresh: the
    record stays on screen while "Updating…" carries the feedback.
  */
  const detailForSelection =
    detail && activeId !== null && detail.id === activeId ? detail : null;

  return (
    /*
      The shell owns the viewport height (h-screen + overflow-hidden on the
      layout), so this fills its parent rather than recomputing 100vh minus a
      chrome offset that would have to be kept in sync by hand.
    */
    <div className="flex min-h-[640px] flex-col gap-2 min-[1100px]:h-full min-[1100px]:min-h-0">
      {deskAllowed === false ? (
        <div className="shrink-0 rounded-lg border border-amber-300 bg-amber-50 px-3 py-2 cl-body text-amber-900">
          <span className="font-bold">This is not your workstation.</span> The
          Laboratory Desk is open to laboratory technicians, managers and system
          administrators. Ordering and reviewing laboratory work as a clinician
          is done from the Doctor Desk.
        </div>
      ) : null}

      <LabFilters
        lane={lane}
        date={date}
        search={search}
        loading={queueLoading}
        counts={counts}
        onLaneChange={setLane}
        onDateChange={setDate}
        onSearchChange={setSearch}
        onRefresh={refresh}
      />

      <div className="grid min-h-0 flex-1 gap-2 min-[1100px]:grid-cols-[minmax(360px,38%)_minmax(0,1fr)]">
        <LabQueue
          rows={visibleRows}
          selectedId={activeId}
          loading={queueLoading}
          error={queueError}
          truncated={truncated}
          onSelect={setSelectedId}
        />
        <LabRequestPanel
          detail={detailForSelection}
          loading={detailLoading}
          error={activeId !== null ? detailError : null}
          empty={visibleRows.length === 0}
        />
      </div>
    </div>
  );
}
