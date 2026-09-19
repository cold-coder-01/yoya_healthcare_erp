"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { messageFromPayload } from "@/lib/api-error";
import {
  ACTIVE_LANE_KEY,
  PHARMACY_SESSION_PATH,
  deskRoleLabel,
  detailIsLoading,
  dispensePath,
  laneStatuses,
  matchesSearch,
  resolveSelection,
  visibleDetail,
  worklistPath,
} from "@/lib/pharmacy-desk-format";
import type {
  ApiEnvelope,
  PharmacyDeskRoles,
  PharmacyDispenseDetail,
  PharmacyDispenseResponse,
  PharmacyQueueRow,
  PharmacySessionResponse,
  PharmacyWorklistResponse,
  PharmacyWorklistSummary,
} from "@/types/pharmacy-desk";

import PharmacyDispensePanel from "./pharmacy-dispense-panel";
import PharmacyFilters from "./pharmacy-filters";
import PharmacyQueue from "./pharmacy-queue";

/**
 * The Pharmacy Desk. READ ONLY.
 *
 * QUEUE LEFT, DISPENSE DETAIL RIGHT -- the arrangement the Laboratory,
 * Radiology and Doctor desks use. Selecting a dispense never navigates.
 *
 * EVERY CALL IS A GET TO /api/pharmacy/*. The browser holds no Odoo session and
 * knows no Odoo URL; each BFF route is gated upstream by
 * reception_scope.may_pharmacy_desk before it touches a record. There is no
 * write anywhere in this file, because Pharmacy Slice 1 has none.
 *
 * THE LANE COUNTS COME FROM THE SERVER and are never recounted here.
 */
export default function PharmacyWorkstation() {
  const [lane, setLane] = useState(ACTIVE_LANE_KEY);
  // No date default: yesterday's prescription is still today's work.
  const [date, setDate] = useState("");
  const [search, setSearch] = useState("");
  const [debouncedSearch, setDebouncedSearch] = useState("");

  const [rows, setRows] = useState<PharmacyQueueRow[]>([]);
  const [summary, setSummary] = useState<PharmacyWorklistSummary | null>(null);
  const [truncated, setTruncated] = useState(false);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [detail, setDetail] = useState<PharmacyDispenseDetail | null>(null);
  /*
    A failed RE-READ of the dispense already on screen keeps it, marked stale,
    rather than blanking a panel the pharmacist is reading.
  */
  const [detailStaleFor, setDetailStaleFor] = useState<number | null>(null);
  const detailRef = useRef<PharmacyDispenseDetail | null>(null);
  useEffect(() => {
    detailRef.current = detail;
  }, [detail]);

  const [deskAllowed, setDeskAllowed] = useState<boolean | null>(null);
  const [roles, setRoles] = useState<PharmacyDeskRoles | null>(null);
  const [queueLoading, setQueueLoading] = useState(true);
  const [detailLoading, setDetailLoading] = useState(false);
  const [queueError, setQueueError] = useState<string | null>(null);
  const [detailError, setDetailError] = useState<string | null>(null);
  const [refreshToken, setRefreshToken] = useState(0);

  useEffect(() => {
    const timer = setTimeout(() => setDebouncedSearch(search.trim()), 200);
    return () => clearTimeout(timer);
  }, [search]);

  /* ---------------- session (presentation only) ---------------- */
  useEffect(() => {
    const controller = new AbortController();

    async function loadSession() {
      try {
        const response = await fetch(PHARMACY_SESSION_PATH, {
          cache: "no-store",
          signal: controller.signal,
        });
        const payload = (await response.json()) as ApiEnvelope<PharmacySessionResponse>;
        if (controller.signal.aborted) return;
        if (response.ok && payload.success) {
          setDeskAllowed(payload.data.capabilities.pharmacy_desk);
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
          worklistPath({ status: statuses, date: date || null, q: debouncedSearch || null }),
          { cache: "no-store", signal: controller.signal },
        );
        const payload = (await response.json()) as ApiEnvelope<PharmacyWorklistResponse>;
        if (controller.signal.aborted) return;

        if (!response.ok || !payload.success) {
          setRows([]);
          setSummary(null);
          setQueueError(messageFromPayload(payload, "Unable to load the pharmacy queue."));
          return;
        }

        setRows(payload.data.rows ?? []);
        setSummary(payload.data.summary ?? null);
        setTruncated(payload.data.meta.truncated);
        setQueueError(null);
      } catch {
        if (!controller.signal.aborted) {
          setRows([]);
          setSummary(null);
          setQueueError("Unable to reach the pharmacy service.");
        }
      } finally {
        if (!controller.signal.aborted) setQueueLoading(false);
      }
    }

    void loadQueue();
    return () => controller.abort();
  }, [statuses, date, debouncedSearch, refreshToken]);

  /** Instant local narrowing while typing, ahead of the debounced server search. */
  const visibleRows = useMemo(
    () => rows.filter((row) => matchesSearch(row, search)),
    [rows, search],
  );

  const activeId = useMemo(() => resolveSelection(visibleRows, selectedId), [visibleRows, selectedId]);

  /* ---------------- selected dispense ---------------- */
  useEffect(() => {
    if (activeId === null) {
      return;
    }

    const controller = new AbortController();

    async function loadDispense(dispenseId: number) {
      setDetailLoading(true);
      setDetailError(null);
      try {
        const response = await fetch(dispensePath(dispenseId), {
          cache: "no-store",
          signal: controller.signal,
        });
        const payload = (await response.json()) as ApiEnvelope<PharmacyDispenseResponse>;
        if (controller.signal.aborted) return;

        if (!response.ok || !payload.success) {
          if (detailRef.current?.id === dispenseId) {
            setDetailStaleFor(dispenseId);
            return;
          }
          setDetail(null);
          setDetailError(messageFromPayload(payload, "Unable to load the selected dispense."));
          return;
        }
        setDetail(payload.data.dispense);
        setDetailStaleFor(null);
      } catch {
        if (!controller.signal.aborted) {
          if (detailRef.current?.id === dispenseId) {
            setDetailStaleFor(dispenseId);
            return;
          }
          setDetail(null);
          setDetailError("Unable to reach the pharmacy service.");
        }
      } finally {
        if (!controller.signal.aborted) setDetailLoading(false);
      }
    }

    void loadDispense(activeId);
    return () => controller.abort();
  }, [activeId, refreshToken]);

  const refresh = useCallback(() => setRefreshToken((token) => token + 1), []);

  const detailForSelection = visibleDetail(detail, activeId);
  const panelIsLoading = detailIsLoading(activeId, detailLoading, detailForSelection);

  return (
    <div className="flex min-h-[640px] flex-col gap-2 min-[1100px]:h-full min-[1100px]:min-h-0">
      {deskAllowed === false ? (
        <div className="shrink-0 rounded-lg border border-amber-300 bg-amber-50 px-3 py-2 cl-body text-amber-900">
          <span className="font-bold">This is not your workstation.</span> The Pharmacy Desk is
          open to pharmacists, managers and system administrators. Prescribing is done from the
          Doctor Desk.
        </div>
      ) : null}

      <PharmacyFilters
        lane={lane}
        date={date}
        search={search}
        loading={queueLoading}
        summary={summary}
        roleLabel={deskRoleLabel(roles)}
        onLaneChange={setLane}
        onDateChange={setDate}
        onSearchChange={setSearch}
        onRefresh={refresh}
      />

      <div className="grid min-h-0 flex-1 gap-2 min-[1100px]:grid-cols-[minmax(400px,42%)_minmax(0,1fr)]">
        <PharmacyQueue
          rows={visibleRows}
          selectedId={activeId}
          loading={queueLoading}
          error={queueError}
          truncated={truncated}
          onSelect={setSelectedId}
        />
        <PharmacyDispensePanel
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
