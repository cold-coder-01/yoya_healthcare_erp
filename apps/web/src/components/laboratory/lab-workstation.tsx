"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { codeFromPayload, messageFromPayload } from "@/lib/api-error";
import {
  collectErrorMessage,
  collectPath,
  detailIsLoading,
  startProcessingErrorMessage,
  startProcessingPath,
  matchesSearch,
  requestPath,
  activeSelection,
  shouldReconcileAfter,
  visibleDetail,
  worklistPath,
} from "@/lib/lab-desk-format";
import {
  canReleaseResult,
  canValidateResult,
  enterResultPath,
  openResultPath,
  releaseOutcomeText,
  releaseResultPath,
  resultErrorMessage,
  saveResultPath,
  shouldReconcileAfterResult,
  validateResultPath,
} from "@/lib/lab-result-format";
import type { LabResultOutcome } from "@/lib/lab-result-format";
import type {
  ApiEnvelope,
  LabQueueRow,
  LabRequestDetail,
  LabRequestResponse,
  LabResult,
  LabResultResponse,
  LabResultWritePayload,
  LabSessionResponse,
  LabWorklistResponse,
  LabWorklistSummary,
} from "@/types/lab-desk";

import LabFilters, { laneStatuses } from "./lab-filters";
import LabQueue from "./lab-queue";
import LabRequestPanel from "./lab-request-panel";
import LabResultModal from "./lab-result-modal";

/**
 * THE ONE POST SITE ON THIS DESK. Every mutation -- collect, start
 * processing, open a result, save a draft, mark entered -- goes through here to
 * the BFF, so there is exactly one place a request body and its headers are
 * built, and none of them can ever address Odoo.
 */
async function postLab<T>(path: string, body: unknown) {
  const response = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    cache: "no-store",
  });
  const payload = (await response.json()) as ApiEnvelope<T>;
  return { response, payload };
}

/**
 * The result sheet that is open, bound to the request and result it was opened
 * with. A snapshot on purpose: the queue and the detail panel keep refreshing
 * behind the modal, and none of that may replace what it is editing.
 */
type ResultEditor = {
  request: LabRequestDetail;
  result: LabResult;
  readOnly: boolean;
  /** Opened by "Validate result": read-only, with the two-step confirmation. */
  validating?: boolean;
  /** Opened by "Release result": read-only, with the two-step confirmation. */
  releasing?: boolean;
  returnFocus: HTMLElement | null;
};

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
 * TWO BENCH TRANSITIONS -- collect a sample, start processing it. Each calls
 * one authoritative model method and decides nothing locally; both run through
 * the same `runTransition` so their pending, error and reconciliation
 * behaviour cannot drift apart.
 *
 * RESULT ENTRY (Slice 3) -- open the request's one operational result, save a
 * draft, mark it entered. The result sheet is a modal that owns its own typed
 * draft; this component only carries its requests to the BFF.
 *
 * VALIDATION (Slice 3B) and RELEASE (Slice 3C) -- the same sheet, read-only,
 * with a two-step confirmation; one POST each with no body. Release completes
 * the request and moves it out of In progress, so the released request is
 * pinned on screen with its server-reported outcome.
 *
 * Every POST in this file goes through `postLab`.
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
  /*
    A REFRESH THAT FAILS MUST NOT ERASE A CONFIRMED STATE.

    After Validate succeeds the panel shows the server's own re-serialized
    request (VALIDATED) and a background re-read follows. If that re-read
    fails, blanking the panel into an error would hide an irreversible success
    the server already confirmed. So a failed re-read of the request ALREADY on
    screen keeps it, marked stale; only a failed FIRST load of a request shows
    the error. The ref lets the async loader ask "is this the request on
    screen?" without re-running the effect on every detail change.
  */
  const [detailStaleFor, setDetailStaleFor] = useState<number | null>(null);
  const detailRef = useRef<LabRequestDetail | null>(null);
  useEffect(() => {
    detailRef.current = detail;
  }, [detail]);

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
   *
   * A PINNED REQUEST WINS (activeSelection). After a release or a collection
   * moves a request out of the lane, falling to the top row would load an
   * unrelated request over the confirmation the technician is reading. The pin
   * is cleared by a real selection or a lane change.
   */
  const [justActedId, setJustActedId] = useState<number | null>(null);
  const activeId = useMemo(
    () => activeSelection(visibleRows, selectedId, justActedId),
    [visibleRows, selectedId, justActedId],
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
          if (detailRef.current?.id === requestId) {
            // A RE-READ failed: keep the last confirmed state, marked stale.
            setDetailStaleFor(requestId);
            return;
          }
          setDetail(null);
          setDetailError(
            messageFromPayload(payload, "Unable to load the selected request."),
          );
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

  /* ---------------- bench transitions ---------------- */
  /*
    THE ONLY MUTATIONS ON THIS SCREEN, and neither decides anything.

    Each posts to the BFF and reports what the model said. Whether a request
    may be collected is settled by action_mark_sample_collected() (which
    re-runs the financial-clearance gate); whether it may start processing is
    settled by action_mark_in_progress(). Both re-run their state machine
    server-side on every call, so the buttons are affordances drawn from the
    server's own derived status and nothing more.

    `pendingId` is the REQUEST ID rather than a boolean, so an action in flight
    can never grey out the button of a request the technician has since
    selected instead.
  */
  const [pendingId, setPendingId] = useState<number | null>(null);
  /*
    `justActedId` -- the request the technician just acted on, kept on screen
    after it leaves the lane -- is declared with activeId above, because it now
    decides the active request. Cleared by any fresh selection or lane change.
  */
  const [actionErrorFor, setActionErrorFor] = useState<{
    requestId: number;
    message: string;
  } | null>(null);

  /**
   * Run one bench transition through the BFF.
   *
   * SHARED BY BOTH ACTIONS ON PURPOSE. Collection and start-processing differ
   * only in which route they post to and how a failure is worded; everything
   * that proved delicate in UAT -- the pending guard, pinning the request so
   * it stays visible after it leaves the lane, reconciling a stale screen, and
   * never leaving a spinner behind -- is written once here.
   */
  const runTransition = useCallback(
    async (
      requestId: number,
      path: string,
      message: (server: string) => string,
      transportMessage: string,
    ) => {
      // Guard against a second submit slipping past a disabled button.
      if (pendingId !== null) return;
      setPendingId(requestId);
      setActionErrorFor(null);
      try {
        const { response, payload } = await postLab<LabRequestResponse>(path, {});

        if (!response.ok || !payload.success) {
          setActionErrorFor({
            requestId,
            message: message(messageFromPayload(payload, "")),
          });
          /*
            RECONCILE AFTER A REFUSAL THE SCREEN CAUSED. A workflow conflict, a
            clearance block or a vanished request all mean the row on screen no
            longer matches the database -- so re-read rather than leave a
            button the server has just refused.
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
        // just left the lane the technician is looking at.
        setJustActedId(requestId);
        refresh();
      } catch {
        setActionErrorFor({ requestId, message: transportMessage });
      } finally {
        setPendingId(null);
      }
    },
    [pendingId, refresh],
  );

  const collect = useCallback(
    (requestId: number) =>
      runTransition(
        requestId,
        collectPath(requestId),
        (server) =>
          collectErrorMessage(server, "The sample could not be marked collected."),
        "Unable to reach the laboratory service. The sample was not marked collected.",
      ),
    [runTransition],
  );

  const startProcessing = useCallback(
    (requestId: number) =>
      runTransition(
        requestId,
        startProcessingPath(requestId),
        (server) =>
          startProcessingErrorMessage(server, "Processing could not be started."),
        "Unable to reach the laboratory service. Processing was not started.",
      ),
    [runTransition],
  );

  /* ---------------- result entry (Slice 3) ---------------- */
  const [resultEditor, setResultEditor] = useState<ResultEditor | null>(null);

  /**
   * "Enter results": find or create THE operational result, then open it.
   *
   * The server decides everything -- whether entry is open for this request,
   * whether a draft already exists to resume, whether an existing result
   * forbids a new one -- under a row lock, so a double click returns the same
   * result rather than creating two. `pendingId` stops the second click being
   * sent at all.
   */
  const openResultEntry = useCallback(
    async (requestId: number, returnFocus: HTMLElement | null) => {
      if (pendingId !== null || resultEditor !== null) return;
      setPendingId(requestId);
      setActionErrorFor(null);
      try {
        const { response, payload } = await postLab<LabResultResponse>(
          openResultPath(requestId),
          {},
        );
        if (!response.ok || !payload.success) {
          setActionErrorFor({
            requestId,
            message: resultErrorMessage(
              messageFromPayload(payload, ""),
              "Result entry could not be opened.",
            ),
          });
          if (shouldReconcileAfterResult(codeFromPayload(payload))) refresh();
          return;
        }
        setDetail(payload.data.request);
        setResultEditor({
          request: payload.data.request,
          result: payload.data.result,
          // The server may hand back an entered result if the screen was stale;
          // it is then shown, never edited.
          readOnly: payload.data.result.state !== "draft",
          returnFocus,
        });
        if (payload.data.created) refresh();
      } catch {
        setActionErrorFor({
          requestId,
          message:
            "Unable to reach the laboratory service. Result entry was not opened.",
        });
      } finally {
        setPendingId(null);
      }
    },
    [pendingId, refresh, resultEditor],
  );

  /** "View result": the result already on screen, read-only. No request made. */
  const viewResult = useCallback(
    (returnFocus: HTMLElement | null) => {
      const current = visibleDetail(detail, activeId, justActedId);
      if (!current?.result || resultEditor !== null) return;
      setResultEditor({
        request: current,
        result: current.result,
        readOnly: true,
        returnFocus,
      });
    },
    [activeId, detail, justActedId, resultEditor],
  );

  /**
   * One result write through the BFF, reported back to the modal.
   *
   * The DETAIL is refreshed from the server's own re-serialization on success,
   * so the panel behind the modal is current. The MODAL is not touched from
   * here: it owns its typed draft and decides for itself what to do with the
   * outcome.
   */
  const writeResult = useCallback(
    async (
      path: string,
      body: LabResultWritePayload,
      fallback: string,
      transportMessage: string,
    ): Promise<LabResultOutcome> => {
      try {
        const { response, payload } = await postLab<LabResultResponse>(path, body);
        if (!response.ok || !payload.success) {
          if (shouldReconcileAfterResult(codeFromPayload(payload))) refresh();
          return {
            ok: false,
            message: resultErrorMessage(messageFromPayload(payload, ""), fallback),
          };
        }
        setDetail(payload.data.request);
        return { ok: true, result: payload.data.result };
      } catch {
        return { ok: false, message: transportMessage };
      }
    },
    [refresh],
  );

  const saveResultDraft = useCallback(
    (resultId: number, body: LabResultWritePayload) =>
      writeResult(
        saveResultPath(resultId),
        body,
        "The draft could not be saved.",
        "Unable to reach the laboratory service. The draft was not saved.",
      ),
    [writeResult],
  );

  const markResultEntered = useCallback(
    async (resultId: number, body: LabResultWritePayload) => {
      const outcome = await writeResult(
        enterResultPath(resultId),
        body,
        "The result could not be marked entered.",
        "Unable to reach the laboratory service. The result was not marked entered.",
      );
      // The queue's result counts moved; the request's lane did not.
      if (outcome.ok) refresh();
      return outcome;
    },
    [refresh, writeResult],
  );

  /* ---------------- validation (Slice 3B) ---------------- */

  /**
   * "Validate result": open the result ON SCREEN in validation mode. No request
   * is made to open it -- the values are read-only and the Lab API re-checks
   * every condition when Confirm validation is actually pressed.
   */
  const openValidation = useCallback(
    (returnFocus: HTMLElement | null) => {
      const current = visibleDetail(detail, activeId, justActedId);
      if (!current?.result || resultEditor !== null) return;
      if (!canValidateResult(current)) return;
      setResultEditor({
        request: current,
        result: current.result,
        readOnly: true,
        validating: true,
        returnFocus,
      });
    },
    [activeId, detail, justActedId, resultEditor],
  );

  /**
   * Confirm validation: ONE POST, no body, reported back to the modal.
   *
   * THE AUTHORITATIVE PAYLOAD WINS, AND IT IS PINNED. On success the panel shows
   * the server's own re-serialized request (VALIDATED, still In progress) and
   * the request is pinned with justActedId, so neither a queue refresh that
   * returns nothing nor a detail re-read that fails can take the confirmed state
   * off screen -- see detailStaleFor.
   *
   * A LOST RESPONSE IS NOT REPORTED AS "NOT VALIDATED". The server may have
   * validated before the connection dropped, so the message says the outcome
   * could not be confirmed and the request is re-read; a retry against a
   * validated result gets a clean 409, never a second delivery.
   */
  const validateResult = useCallback(
    async (resultId: number): Promise<LabResultOutcome> => {
      try {
        const { response, payload } = await postLab<LabResultResponse>(
          validateResultPath(resultId),
          {},
        );
        if (!response.ok || !payload.success) {
          if (shouldReconcileAfterResult(codeFromPayload(payload))) refresh();
          return {
            ok: false,
            message: resultErrorMessage(
              messageFromPayload(payload, ""),
              "The laboratory result could not be validated.",
            ),
          };
        }
        setDetail(payload.data.request);
        setDetailStaleFor(null);
        setJustActedId(payload.data.request.id);
        refresh();
        return { ok: true, result: payload.data.result };
      } catch {
        refresh();
        return {
          ok: false,
          message:
            "Unable to confirm the validation with the laboratory service. The request is being re-read; check its result state before trying again.",
        };
      }
    },
    [refresh],
  );

  /* ---------------- release (Slice 3C) ---------------- */

  /*
    THE LAST RELEASE'S OUTCOME, as the server reported it, keyed on the
    request so it can never be shown against another. Cleared by any fresh
    selection, like the justActedId pin it travels with.
  */
  const [releaseOutcomeFor, setReleaseOutcomeFor] = useState<{
    requestId: number;
    text: string;
    completed: boolean;
  } | null>(null);

  /**
   * "Release result": open the validated result ON SCREEN in release mode. No
   * request is made to open it; the Lab API re-checks everything on Confirm
   * release.
   */
  const openRelease = useCallback(
    (returnFocus: HTMLElement | null) => {
      const current = visibleDetail(detail, activeId, justActedId);
      if (!current?.result || resultEditor !== null) return;
      if (!canReleaseResult(current)) return;
      setResultEditor({
        request: current,
        result: current.result,
        readOnly: true,
        releasing: true,
        returnFocus,
      });
    },
    [activeId, detail, justActedId, resultEditor],
  );

  /**
   * Confirm release: ONE POST, no body, reported back to the modal.
   *
   * RELEASE MOVES THE REQUEST OUT OF THE LANE. A completed request leaves In
   * progress the instant the server answers, so the queue refetch drops it and
   * `activeId` moves on. The request is therefore PINNED with justActedId --
   * the same mechanism collection uses -- so the panel keeps answering the
   * question the click asked: RELEASED, request COMPLETED. The server's own
   * re-serialized request is shown, never a status guessed from the click, and
   * detailStaleFor keeps it on screen if the background re-read fails.
   *
   * A LOST RESPONSE IS NOT REPORTED AS "NOT RELEASED": the server may have
   * released before the connection dropped. The request is re-read instead; a
   * retry against a released result gets a clean refusal, never a second
   * release.
   */
  const releaseResult = useCallback(
    async (resultId: number): Promise<LabResultOutcome> => {
      try {
        const { response, payload } = await postLab<LabResultResponse>(
          releaseResultPath(resultId),
          {},
        );
        if (!response.ok || !payload.success) {
          if (shouldReconcileAfterResult(codeFromPayload(payload))) refresh();
          return {
            ok: false,
            message: resultErrorMessage(
              messageFromPayload(payload, ""),
              "The laboratory result could not be released.",
            ),
          };
        }
        const released = payload.data.request;
        setDetail(released);
        setDetailStaleFor(null);
        setJustActedId(released.id);
        const text = releaseOutcomeText(payload.data.completion);
        setReleaseOutcomeFor(
          text
            ? {
                requestId: released.id,
                text,
                completed: Boolean(payload.data.completion?.completed),
              }
            : null,
        );
        refresh();
        return { ok: true, result: payload.data.result };
      } catch {
        refresh();
        return {
          ok: false,
          message:
            "Unable to confirm the release with the laboratory service. The request is being re-read; check its result state before trying again.",
        };
      }
    },
    [refresh],
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
        onLaneChange={(nextLane) => {
          setLane(nextLane);
          // Choosing a lane is choosing new work: release the post-action pin.
          setJustActedId(null);
          setReleaseOutcomeFor(null);
        }}
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
            setReleaseOutcomeFor(null);
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
          onStartProcessing={() => {
            if (activeId !== null) void startProcessing(activeId);
          }}
          onEnterResults={(trigger) => {
            if (activeId !== null) void openResultEntry(activeId, trigger);
          }}
          onViewResult={(trigger) => viewResult(trigger)}
          onValidateResult={(trigger) => openValidation(trigger)}
          onReleaseResult={(trigger) => openRelease(trigger)}
          releaseOutcome={
            releaseOutcomeFor &&
            detailForSelection !== null &&
            releaseOutcomeFor.requestId === detailForSelection.id
              ? releaseOutcomeFor
              : null
          }
          stale={
            detailForSelection !== null &&
            detailStaleFor === detailForSelection.id
          }
          /*
            Both keyed on the ACTIVE request, so a pending collection or a
            refusal belonging to one request can never be shown against
            another after the selection moves.
          */
          pending={pendingId !== null && pendingId === activeId}
          actionError={
            actionErrorFor && actionErrorFor.requestId === activeId
              ? actionErrorFor.message
              : null
          }
        />
      </div>

      {/*
        THE RESULT SHEET, bound to the request and result it opened with --
        never to the live selection -- and keyed on the result, so a different
        result is a different modal rather than a silent swap underneath the
        technician's typing. The modal is aria-modal over a backdrop, so the
        queue cannot be clicked while it is open.
      */}
      {resultEditor ? (
        <LabResultModal
          key={resultEditor.result.id}
          request={resultEditor.request}
          result={resultEditor.result}
          readOnly={resultEditor.readOnly}
          returnFocus={resultEditor.returnFocus}
          onSaveDraft={(body) => saveResultDraft(resultEditor.result.id, body)}
          onMarkEntered={(body) => markResultEntered(resultEditor.result.id, body)}
          onValidate={
            resultEditor.validating
              ? () => validateResult(resultEditor.result.id)
              : undefined
          }
          onRelease={
            resultEditor.releasing
              ? () => releaseResult(resultEditor.result.id)
              : undefined
          }
          onClose={() => setResultEditor(null)}
        />
      ) : null}
    </div>
  );
}
