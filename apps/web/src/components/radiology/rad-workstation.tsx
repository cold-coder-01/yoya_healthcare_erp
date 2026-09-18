"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { codeFromPayload, messageFromPayload } from "@/lib/api-error";
import {
  ACTIVE_LANE_KEY,
  RAD_SESSION_PATH,
  activeSelection,
  deskRoleLabel,
  detailIsLoading,
  imageErrorMessage,
  imageRemovePath,
  imageUploadForm,
  imagesEditable,
  imagesPath,
  laneStatuses,
  matchesSearch,
  reportEditable,
  reportEnterPath,
  reportEnteredOutcomeText,
  reportErrorMessage,
  reportPath,
  reportSavePath,
  requestPath,
  serializeReportBody,
  shouldReconcileAfterImage,
  shouldReconcileAfterSignoff,
  signoffErrorMessage,
  signoffOutcomeText,
  signoffPath,
  shouldReconcileAfterReport,
  shouldReconcileAfterTransition,
  transitionErrorMessage,
  transitionOutcomeText,
  transitionPath,
  visibleDetail,
  worklistPath,
} from "@/lib/rad-desk-format";
import type { RadReportDraft } from "@/lib/rad-desk-format";
import type {
  ApiEnvelope,
  RadDeskCapabilities,
  RadDeskRoles,
  RadImageResponse,
  RadModalityOption,
  RadOperationalResult,
  RadQueueRow,
  RadReportResponse,
  RadRequestDetail,
  RadRequestResponse,
  RadSignoffKind,
  RadSignoffResponse,
  RadSessionResponse,
  RadTransitionKind,
  RadTransitionResponse,
  RadWorklistResponse,
  RadWorklistSummary,
} from "@/types/rad-desk";

import RadFilters from "./rad-filters";
import RadQueue from "./rad-queue";
import RadReportModal from "./rad-report-modal";
import type { RadImageOutcome, RadReportOutcome } from "./rad-report-modal";
import RadRequestPanel from "./rad-request-panel";

/**
 * THE ONE POST SITE ON THIS DESK. Schedule, Start, Open report and Remove file
 * go through here bodiless; Save draft and Mark entered send the one body
 * serializeReportBody() builds; Upload sends the one form imageUploadForm()
 * builds (the browser sets its multipart boundary, so no Content-Type is set
 * for it). So there is exactly one place a write request is built, and none of
 * them can ever address Odoo.
 */
async function postRad<T>(path: string, body: string | FormData = "{}") {
  const response = await fetch(path, {
    method: "POST",
    headers: typeof body === "string" ? { "Content-Type": "application/json" } : undefined,
    body,
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
 * 2) and Open report, Save draft and Mark entered (Slice 3), all through
 * `postRad`, all gated upstream and re-checked under a row lock.
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

  /* ---------------- the report (Slice 3) ---------------- */
  /*
    The open report sheet: the request and report as the SERVER last returned
    them. The sheet copies the report once and owns its draft; replacing
    `report` after a save does not remount it (same id and state), while the
    entered report the server returns does -- as a read-only sheet.
  */
  const [report, setReport] = useState<{
    request: RadRequestDetail;
    result: RadOperationalResult;
    /** Slice 5: the sheet was opened to validate or release. */
    signoff?: RadSignoffKind | null;
  } | null>(null);
  const [reportOpener, setReportOpener] = useState<HTMLElement | null>(null);
  const [reportPendingId, setReportPendingId] = useState<number | null>(null);

  /** Open report: the server finds or creates THE one draft. */
  const openReport = useCallback(
    async (requestId: number, opener: HTMLElement) => {
      if (pendingId !== null || reportPendingId !== null) return;
      setReportPendingId(requestId);
      setActionErrorFor(null);
      setOutcomeFor(null);
      try {
        const { response, payload } = await postRad<RadReportResponse>(reportPath(requestId));
        if (!response.ok || !payload.success) {
          setActionErrorFor({
            requestId,
            message: reportErrorMessage(messageFromPayload(payload, ""), "open"),
          });
          if (shouldReconcileAfterReport(codeFromPayload(payload))) refresh();
          return;
        }
        setDetail(payload.data.request);
        setDetailStaleFor(null);
        setReportOpener(opener);
        setReport({ request: payload.data.request, result: payload.data.result });
        // A newly created draft changes the queue row's report column.
        if (payload.data.created) refresh();
      } catch {
        setActionErrorFor({
          requestId,
          message: "Unable to reach the radiology service. The report was not opened.",
        });
        refresh();
      } finally {
        setReportPendingId(null);
      }
    },
    [pendingId, refresh, reportPendingId],
  );

  /**
   * View / Validate / Release: open the sheet on the report the request
   * ALREADY has, straight from the server's last detail payload -- nothing is
   * sent. Validate and release put the sheet in its read-only sign-off mode.
   */
  const viewReport = useCallback(
    (requestId: number, opener: HTMLElement, signoff: RadSignoffKind | null = null) => {
      const current = detailRef.current;
      if (!current || current.id !== requestId || !current.result) return;
      setActionErrorFor(null);
      setOutcomeFor(null);
      setReportOpener(opener);
      setReport({ request: current, result: current.result, signoff });
    },
    [],
  );

  /**
   * Validate / Release, from the sheet's final confirmation. On the server's
   * word the sheet closes, the request it returned is shown and PINNED -- it
   * leaves its lane -- and the queue refetches so the lanes and counts move.
   * A refusal changes nothing on screen and keeps the sheet open.
   */
  const runSignoff = useCallback(
    async (kind: RadSignoffKind): Promise<RadImageOutcome> => {
      if (!report) return { ok: false, message: signoffErrorMessage(null, kind) };
      try {
        const { response, payload } = await postRad<RadSignoffResponse>(signoffPath(kind, report.result.id));
        if (!response.ok || !payload.success) {
          if (shouldReconcileAfterSignoff(codeFromPayload(payload))) refresh();
          return { ok: false, message: signoffErrorMessage(messageFromPayload(payload, ""), kind) };
        }
        const confirmed = payload.data;
        setDetail(confirmed.request);
        setDetailStaleFor(null);
        setJustActedId(confirmed.request.id);
        setOutcomeFor({
          requestId: confirmed.request.id,
          text: signoffOutcomeText(kind, confirmed),
        });
        setReport(null);
        refresh();
        return { ok: true };
      } catch {
        refresh();
        return {
          ok: false,
          message:
            "Unable to confirm with the radiology service. The request is being re-read; check its lane before trying again.",
        };
      }
    },
    [refresh, report],
  );

  /**
   * Save draft / Mark entered. Resolves ok ONLY on the server's confirmation.
   * A lost response is not reported as "nothing changed": the request is
   * re-read, and the sheet says to check the report.
   */
  const writeReport = useCallback(
    async (kind: "save" | "enter", draft: RadReportDraft): Promise<RadReportOutcome> => {
      if (!report) return { ok: false, message: reportErrorMessage(null, kind) };
      const resultId = report.result.id;
      try {
        const { response, payload } = await postRad<RadReportResponse>(
          kind === "save" ? reportSavePath(resultId) : reportEnterPath(resultId),
          serializeReportBody(draft),
        );
        if (!response.ok || !payload.success) {
          if (shouldReconcileAfterReport(codeFromPayload(payload))) refresh();
          return {
            ok: false,
            message: reportErrorMessage(messageFromPayload(payload, ""), kind),
          };
        }
        const confirmed = payload.data;
        setDetail(confirmed.request);
        setDetailStaleFor(null);
        setReport({ request: confirmed.request, result: confirmed.result });
        if (kind === "enter") {
          // The lane moves (Awaiting report -> Awaiting validation): pin the
          // request so it stays on screen, say what happened, refetch counts.
          setJustActedId(confirmed.request.id);
          setOutcomeFor({
            requestId: confirmed.request.id,
            text: reportEnteredOutcomeText(confirmed.result, confirmed.request),
          });
          refresh();
        }
        return { ok: true, result: confirmed.result };
      } catch {
        refresh();
        return {
          ok: false,
          message:
            "Unable to confirm with the radiology service. The request is being re-read; check the report before trying again.",
        };
      }
    },
    [refresh, report],
  );

  /**
   * Upload / remove a file. The server's report replaces the one on screen --
   * so the file list is always the confirmed one -- and the request stays
   * pinned; no lane moves, but the queue refetches so its image count follows.
   */
  const writeImage = useCallback(
    async (
      kind: "upload" | "remove",
      target: { file: File; caption: string } | { imageId: number },
    ): Promise<RadImageOutcome> => {
      if (!report) return { ok: false, message: imageErrorMessage(null, kind) };
      const resultId = report.result.id;
      try {
        let sent;
        if ("file" in target) {
          sent = await postRad<RadImageResponse>(imagesPath(resultId), imageUploadForm(target.file, target.caption));
        } else {
          sent = await postRad<RadImageResponse>(imageRemovePath(resultId, target.imageId));
        }
        const { response, payload } = sent;
        if (!response.ok || !payload.success) {
          if (shouldReconcileAfterImage(codeFromPayload(payload))) refresh();
          return { ok: false, message: imageErrorMessage(messageFromPayload(payload, ""), kind) };
        }
        const confirmed = payload.data;
        setDetail(confirmed.request);
        setDetailStaleFor(null);
        setReport({ request: confirmed.request, result: confirmed.result });
        setJustActedId(confirmed.request.id);
        refresh();
        return { ok: true };
      } catch {
        refresh();
        return {
          ok: false,
          message:
            "Unable to confirm with the radiology service. The request is being re-read; check the files before trying again.",
        };
      }
    },
    [refresh, report],
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
          reportPending={
            reportPendingId !== null &&
            detailForSelection !== null &&
            reportPendingId === detailForSelection.id
          }
          onOpenReport={(requestId, opener) => void openReport(requestId, opener)}
          onViewReport={(requestId, opener, signoff) => viewReport(requestId, opener, signoff)}
        />
      </div>

      {report ? (
        <RadReportModal
          key={`${report.result.id}-${report.result.state}-${report.signoff ?? "report"}`}
          request={report.request}
          result={report.result}
          editable={!report.signoff && reportEditable(report.result, capabilities)}
          canAuthor={capabilities?.edit_report === true}
          imagesEditable={!report.signoff && imagesEditable(report.result, capabilities)}
          signoff={report.signoff ?? null}
          onSignoff={report.signoff ? () => runSignoff(report.signoff as RadSignoffKind) : undefined}
          onUploadImage={(file, caption) => writeImage("upload", { file, caption })}
          onRemoveImage={(imageId) => writeImage("remove", { imageId })}
          returnFocus={reportOpener}
          onSaveDraft={(draft) => writeReport("save", draft)}
          onMarkEntered={(draft) => writeReport("enter", draft)}
          onClose={() => setReport(null)}
        />
      ) : null}
    </div>
  );
}
