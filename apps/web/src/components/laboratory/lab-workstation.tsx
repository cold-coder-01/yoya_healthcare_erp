"use client";

import { useCallback, useEffect, useMemo, useState } from "react";

import { codeFromPayload, messageFromPayload } from "@/lib/api-error";
import {
  collectErrorMessage,
  collectPath,
  detailIsLoading,
  matchesSearch,
  requestPath,
  resolveSelection,
  shouldReconcileAfter,
  visibleDetail,
  worklistPath,
} from "@/lib/lab-desk-format";
import type {
  ApiEnvelope,
  LabQueueRow,
  LabRequestDetail,
  LabRequestResponse,
  LabSessionResponse,
  LabWorklistResponse,
  LabWorklistSummary,
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
 * ONE MUTATION IN THIS TREE: sample collection, which calls one authoritative
 * model method and decides nothing locally.
 *
 * THE LANE COUNTS COME FROM THE SERVER, and are never recounted here. They
 * describe the whole date+search scope rather than the rows on screen, so a
 * badge is right before any lane is clicked -- see the note on `summary`.
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
  /*
    THE LANE COUNTS, HELD AS THE SERVER SENT THEM.

    Never recomputed from `rows`: those are narrowed to the selected lane and
    capped at one page, which is exactly the bug this replaces -- an unclicked
    lane read 0, and a lane whose rows fell past the page limit read 0 even
    when selected. The server counts the whole date+search scope instead.
  */
  const [summary, setSummary] = useState<LabWorklistSummary | null>(null);
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
          setSummary(null);
          setQueueError(
            messageFromPayload(payload, "Unable to load the laboratory queue."),
          );
          return;
        }

        setRows(payload.data.rows ?? []);
        // Arrives with the very first response, so every badge is right before
        // the technician clicks a lane. Refreshed by the same effect on a date,
        // Any-day, search or refresh change -- one fetch, one consistent scope.
        setSummary(payload.data.summary ?? null);
        setTruncated(payload.data.meta.truncated);
        setQueueError(null);
      } catch {
        if (!controller.signal.aborted) {
          setRows([]);
          setSummary(null);
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
      /*
        The queue emptied under the selection -- which is EXACTLY what
        collecting a sample does when the Ready lane is showing.

        NOTHING IS SET HERE, deliberately: writing state from an effect body
        cascades a render (react-hooks/set-state-in-effect). In particular
        `detailLoading` is NOT cleared here, and must not be -- it is DERIVED
        at the render site instead (`detailIsLoading` below).

        THE BUG THAT TAUGHT US THIS. Previously the panel consumed
        `detailLoading` raw. On a collection the sequence was: the effect
        re-ran, started a fetch and set the flag true; the queue refetch then
        emptied the lane, so `activeId` went null; the re-run aborted the
        in-flight fetch, whose `finally` skips the reset when aborted; and this
        early return then left the flag true with nothing to turn it off. The
        desk sat on "Loading request…" forever.
      */
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

  /* ---------------- sample collection ---------------- */
  /*
    THE ONLY MUTATION ON THIS SCREEN.

    It posts to the BFF and does not decide anything: whether the request may
    be collected is settled by hospital.laboratory.request
    .action_mark_sample_collected(), which re-runs the financial-clearance gate
    and the state machine server-side on every call. The button is an
    affordance drawn from the server's own derived status.

    `collectingId` is the REQUEST ID rather than a boolean, so a collection in
    flight can never grey out the button of a request the technician has since
    selected instead.
  */
  const [collectingId, setCollectingId] = useState<number | null>(null);
  /*
    The request the technician just collected, kept on screen after it leaves
    the lane. See detailForSelection. Cleared by any fresh selection.
  */
  const [justActedId, setJustActedId] = useState<number | null>(null);
  const [collectErrorFor, setCollectErrorFor] = useState<{
    requestId: number;
    message: string;
  } | null>(null);

  const collect = useCallback(
    async (requestId: number) => {
      // Guard against a second submit slipping past a disabled button.
      if (collectingId !== null) return;
      setCollectingId(requestId);
      setCollectErrorFor(null);
      try {
        const response = await fetch(collectPath(requestId), {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: "{}",
          cache: "no-store",
        });
        const payload = (await response.json()) as ApiEnvelope<LabRequestResponse>;

        if (!response.ok || !payload.success) {
          setCollectErrorFor({
            requestId,
            message: collectErrorMessage(
              messageFromPayload(payload, ""),
              "The sample could not be marked collected.",
            ),
          });
          /*
            RECONCILE AFTER A REFUSAL THE SCREEN CAUSED. A workflow conflict, a
            clearance block or a vanished request all mean the row on screen no
            longer matches the database -- so re-read rather than leave a
            Collect button the server has just refused.
          */
          if (shouldReconcileAfter(codeFromPayload(payload))) {
            refresh();
          }
          return;
        }

        /*
          THE AUTHORITATIVE PAYLOAD WINS. The server re-serialized the request
          AFTER the transition, so the status rendered is the derived one and
          never a value guessed from the click. The detail updates immediately;
          the queue is refetched so its rows, lane membership and counts move
          with it.
        */
        setDetail(payload.data.request);
        // Pin it so the transition stays visible even though the request has
        // just left the Ready lane the technician is looking at.
        setJustActedId(requestId);
        refresh();
      } catch {
        setCollectErrorFor({
          requestId,
          message:
            "Unable to reach the laboratory service. The sample was not marked collected.",
        });
      } finally {
        setCollectingId(null);
      }
    },
    [collectingId, refresh],
  );

  /*
    WHICH REQUEST THE PANEL MAY SHOW.

    Matching the loaded detail against the ACTIVE id is what stops the panel
    showing the previous request for a frame after the selection moves, and it
    is also why the loader never nulls `detail` on a same-request refresh: the
    record stays on screen while "Updating…" carries the feedback.

    `justActedId` IS THE SECOND WAY IN, AND IT EXISTS FOR COLLECTION. A
    collected request leaves the Ready lane the instant it transitions, so
    `activeId` drops it -- and the technician would watch the request they just
    acted on vanish, with no confirmation that anything happened. Keeping it
    pinned means the panel answers the question the click asked: it now reads
    Sample collected, and the Collect button is gone because the status moved.

    The pin is cleared the moment the technician selects anything else, so it
    can never shadow a real selection.
  */
  const detailForSelection = visibleDetail(detail, activeId, justActedId);

  /*
    THE LOADING FLAG THE PANEL ACTUALLY SEES, derived rather than stored.

    `detailLoading` is raw state that the effect above cannot always reset --
    an aborted fetch skips its own `finally`, and the activeId===null branch
    returns without touching it. Deriving here makes a stuck spinner
    structurally impossible: with nothing selected there is nothing to load, and
    a request already on screen is never "loading" from an empty state.
  */
  const panelIsLoading = detailIsLoading(
    activeId,
    detailLoading,
    detailForSelection,
  );

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
        summary={summary}
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
          onSelect={(requestId) => {
            setSelectedId(requestId);
            // A real selection always wins over the post-collection pin.
            setJustActedId(null);
          }}
        />
        <LabRequestPanel
          detail={detailForSelection}
          loading={panelIsLoading}
          error={activeId !== null ? detailError : null}
          empty={visibleRows.length === 0}
          onCollect={() => {
            if (activeId !== null) void collect(activeId);
          }}
          /*
            Both keyed on the ACTIVE request, so a pending collection or a
            refusal belonging to one request can never be shown against
            another after the selection moves.
          */
          collecting={collectingId !== null && collectingId === activeId}
          collectError={
            collectErrorFor && collectErrorFor.requestId === activeId
              ? collectErrorFor.message
              : null
          }
        />
      </div>
    </div>
  );
}
