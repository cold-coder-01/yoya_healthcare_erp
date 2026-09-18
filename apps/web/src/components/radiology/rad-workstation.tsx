"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { codeFromPayload, messageFromPayload } from "@/lib/api-error";
import {
  ACTIVE_LANE_KEY,
  RAD_SESSION_PATH,
  activeSelection,
  deskRoleLabel,
  detailIsLoading,
  laneStatuses,
  matchesSearch,
  requestPath,
  shouldReconcileAfterTransition,
  transitionErrorMessage,
  transitionOutcomeText,
  transitionPath,
  visibleDetail,
  worklistPath,
} from "@/lib/rad-desk-format";
import type {
  ApiEnvelope,
  RadDeskCapabilities,
  RadDeskRoles,
  RadModalityOption,
  RadQueueRow,
  RadRequestDetail,
  RadRequestResponse,
  RadSessionResponse,
  RadTransitionKind,
  RadTransitionResponse,
  RadWorklistResponse,
  RadWorklistSummary,
} from "@/types/rad-desk";

import RadFilters from "./rad-filters";
import RadQueue from "./rad-queue";
import RadRequestPanel from "./rad-request-panel";

/**
 * THE ONE POST SITE ON THIS DESK. Schedule and Start both go through here to
 * the BFF, bodiless, so there is exactly one place a write request is built and
 * none of them can ever address Odoo.
 */
async function postRad<T>(path: string) {
  const response = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: "{}",
    cache: "no-store",
  });
  const payload = (await response.json()) as ApiEnvelope<T>;
  return { response, payload };
}

/**
 * The Radiology Desk.
 *
 * QUEUE LEFT, REQUEST DETAIL RIGHT, one screen. Every read is a GET to
 * /api/radiology/*; the only writes are Schedule study and Start exam (Slice
 * 2), both through `postRad`, both gated upstream by may_rad_desk and re-checked
 * under a row lock.
 *
 * THE AUTHORITATIVE PAYLOAD WINS. After a transition the panel shows the request
 * the server re-serialized AFTER it, never a state guessed from the click; the
 * request is PINNED so it stays on screen though it has left the lane; and the
 * queue refetches so rows, lanes and counts move with it.
 *
 * THE LANE COUNTS COME FROM THE SERVER and are never recounted here.
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
    rather than blanking a panel the user is reading -- in particular a state a
    transition has just CONFIRMED. Only a failed FIRST load shows the error.
  */
  const [detailStaleFor, setDetailStaleFor] = useState<number | null>(null);
  const detailRef = useRef<RadRequestDetail | null>(null);
  useEffect(() => {
    detailRef.current = detail;
  }, [detail]);

  const [deskAllowed, setDeskAllowed] = useState<boolean | null>(null);
  const [roles, setRoles] = useState<RadDeskRoles | null>(null);
  const [capabilities, setCapabilities] = useState<RadDeskCapabilities | null>(null);
  const [queueLoading, setQueueLoading] = useState(true);
  const [detailLoading, setDetailLoading] = useState(false);
  const [queueError, setQueueError] = useState<string | null>(null);
  const [detailError, setDetailError] = useState<string | null>(null);
  const [refreshToken, setRefreshToken] = useState(0);

  /*
    THE POST-ACTION PIN. The request just scheduled or started leaves the lane
    on screen the instant the server answers; while pinned it remains the active
    request. Cleared by a real selection or a lane change.
  */
  const [justActedId, setJustActedId] = useState<number | null>(null);
  const justActedRef = useRef<number | null>(null);
  useEffect(() => {
    justActedRef.current = justActedId;
  }, [justActedId]);

  useEffect(() => {
    const timer = setTimeout(() => setDebouncedSearch(search.trim()), 200);
    return () => clearTimeout(timer);
  }, [search]);

  /* ---------------- session ---------------- */
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
          setCapabilities(payload.data.capabilities);
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
  /*
    A QUEUE REFRESH THAT FAILS AFTER A CONFIRMED ACTION. The confirmed request
    stays pinned exactly as the server returned it; the user is told the queue
    could not be refreshed, and nothing reverts to the old lane.
  */
  const [refreshWarningFor, setRefreshWarningFor] = useState<number | null>(null);

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
          setRefreshWarningFor(justActedRef.current);
          return;
        }

        setRows(payload.data.rows ?? []);
        setSummary(payload.data.summary ?? null);
        setModalities(payload.data.meta.modalities ?? []);
        setTruncated(payload.data.meta.truncated);
        setQueueError(null);
        setRefreshWarningFor(null);
      } catch {
        if (!controller.signal.aborted) {
          setRows([]);
          setSummary(null);
          setQueueError("Unable to reach the radiology service.");
          setRefreshWarningFor(justActedRef.current);
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

  /** The selection in force, DERIVED, with the post-action pin honoured. */
  const activeId = useMemo(
    () => activeSelection(visibleRows, selectedId, justActedId),
    [visibleRows, selectedId, justActedId],
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

  /* ---------------- transitions (Slice 2) ---------------- */
  /*
    `pendingId` is the REQUEST ID, so an action in flight can never disable the
    controls of a request selected since. A second submit is refused here as
    well as by the disabled button.
  */
  const [pendingId, setPendingId] = useState<number | null>(null);
  const [actionErrorFor, setActionErrorFor] = useState<{
    requestId: number;
    message: string;
  } | null>(null);
  const [outcomeFor, setOutcomeFor] = useState<{
    requestId: number;
    text: string;
  } | null>(null);

  /**
   * Run Schedule or Start through the BFF. The server decides everything; this
   * reports what it said.
   *
   * On success the SERVER's re-serialized request is shown and pinned, and the
   * queue refetches so the lane and its counts move. On a refusal the request on
   * screen is NOT replaced -- nothing changed server-side -- and a refusal that
   * means the screen is stale triggers a re-read. A lost response is not
   * reported as "not done": the request is re-read to find out.
   */
  const runTransition = useCallback(
    async (kind: RadTransitionKind, requestId: number): Promise<boolean> => {
      if (pendingId !== null) return false;
      setPendingId(requestId);
      setActionErrorFor(null);
      setOutcomeFor(null);
      try {
        const { response, payload } = await postRad<RadTransitionResponse>(
          transitionPath(kind, requestId),
        );
        if (!response.ok || !payload.success) {
          setActionErrorFor({
            requestId,
            message: transitionErrorMessage(messageFromPayload(payload, ""), kind),
          });
          if (shouldReconcileAfterTransition(codeFromPayload(payload))) refresh();
          return false;
        }
        const confirmed = payload.data.request;
        setDetail(confirmed);
        setDetailStaleFor(null);
        setJustActedId(confirmed.id);
        setOutcomeFor({ requestId: confirmed.id, text: transitionOutcomeText(kind, confirmed) });
        refresh();
        return true;
      } catch {
        setActionErrorFor({
          requestId,
          message:
            "Unable to confirm the action with the radiology service. The request is being re-read; check its lane before trying again.",
        });
        refresh();
        return false;
      } finally {
        setPendingId(null);
      }
    },
    [pendingId, refresh],
  );

  const detailForSelection = visibleDetail(detail, activeId, justActedId);
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
        onLaneChange={(nextLane) => {
          setLane(nextLane);
          // Choosing a lane is choosing new work: release the post-action pin.
          setJustActedId(null);
          setOutcomeFor(null);
        }}
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
          onSelect={(requestId) => {
            setSelectedId(requestId);
            // A real selection always wins over the post-action pin.
            setJustActedId(null);
            setOutcomeFor(null);
          }}
        />
        <RadRequestPanel
          detail={detailForSelection}
          loading={panelIsLoading}
          error={activeId !== null ? detailError : null}
          empty={visibleRows.length === 0}
          stale={detailForSelection !== null && detailStaleFor === detailForSelection.id}
          capabilities={capabilities}
          pending={pendingId !== null && detailForSelection !== null && pendingId === detailForSelection.id}
          actionError={
            actionErrorFor && detailForSelection && actionErrorFor.requestId === detailForSelection.id
              ? actionErrorFor.message
              : null
          }
          outcome={
            outcomeFor && detailForSelection && outcomeFor.requestId === detailForSelection.id
              ? outcomeFor.text
              : null
          }
          refreshWarning={
            refreshWarningFor !== null &&
            detailForSelection !== null &&
            refreshWarningFor === detailForSelection.id
          }
          onSchedule={(requestId) => runTransition("schedule", requestId)}
          onStart={(requestId) => runTransition("start", requestId)}
        />
      </div>
    </div>
  );
}
