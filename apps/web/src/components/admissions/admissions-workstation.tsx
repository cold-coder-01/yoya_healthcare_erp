"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
  UNKNOWN_OUTCOME_NOTICE,
  admitBody,
  admitPath,
  admitSignature,
  cancelRequestBody,
  cancelRequestPath,
  cancelRequestSignature,
  isResolved,
  needsBedRefresh,
  needsReload,
  tokenFor,
  transferBody,
  transferPath,
  transferSignature,
  type PendingAdmissionOperation,
} from "@/lib/admissions-desk-actions";
import { codeFromPayload, messageFromPayload } from "@/lib/api-error";
import {
  ACTIVE_LANE_KEY,
  ADMISSIONS_SESSION_PATH,
  ADMISSIONS_WARDS_PATH,
  admissionPath,
  bedsPath,
  deskRoleLabel,
  emptyQueueMessage,
  resolveSelection,
  scopeLabel,
  worklistPath,
} from "@/lib/admissions-desk-format";
import type {
  AdmissionAdmitResponse,
  AdmissionCancelRequestResponse,
  AdmissionDeskSession,
  AdmissionDetail,
  AdmissionDetailResponse,
  AdmissionLaneSummary,
  AdmissionTransferResponse,
  AdmissionWorklistResponse,
  AdmissionWorklistRow,
  ApiEnvelope,
  BedBoardRow,
  BedsResponse,
  WardSummary,
  WardsResponse,
} from "@/types/admissions-desk";

import AdmitDialog from "./admit-dialog";
import CancelRequestDialog from "./cancel-request-dialog";
import TransferDialog from "./transfer-dialog";
import AdmissionDetailPanel from "./admission-detail-panel";
import AdmissionsQueue from "./admissions-queue";
import AdmissionsToolbar from "./admissions-toolbar";
import BedBoard from "./bed-board";
import WardOccupancyStrip from "./ward-occupancy-strip";

/**
 * The Admissions Desk (Admissions Slices 1-2).
 *
 * WARD STRIP and LANE TABS on top; the CENSUS left and the PINNED ADMISSION
 * right; the BED BOARD below. Every read is a GET to /api/admissions/*.
 *
 * THREE WRITES, ONE SHAPE: Admit to bed (Slice 2), Transfer patient and Cancel
 * request (Slice 3) -- POSTs to /api/admissions/[id]/{admit,transfer,
 * cancel-request}, each sent ONCE per confirmed action with one operation
 * token. If the outcome is unknown the token is kept, so a retry replays
 * rather than acting twice. The returned admission is PINNED and shown as-is
 * -- it is the authoritative result -- and the census, wards and bed board are
 * then reloaded. Discharge does not exist here (Slice 4).
 *
 * NOTHING LOADS BEFORE THE ROLE IS KNOWN. The session is read first; a caller
 * the server refuses sees "This is not your workstation" and no census, bed or
 * ward request is ever made. (The server refuses them anyway -- this only keeps
 * the screen honest.)
 *
 * THE COUNTS COME FROM THE SERVER and are never recounted here. The browser
 * never derives a lane, a review reason or a clearance.
 *
 * A BED CLICK PINS its admission in the detail panel even when the census is
 * showing another lane or ward; picking a census row releases the pin.
 */
type ActionMessage = { tone: "red" | "amber" | "green"; text: string };

export default function AdmissionsWorkstation() {
  const [session, setSession] = useState<AdmissionDeskSession | null>(null);
  const [deskAllowed, setDeskAllowed] = useState<boolean | null>(null);
  const [sessionError, setSessionError] = useState<string | null>(null);

  const [lane, setLane] = useState(ACTIVE_LANE_KEY);
  const [wardId, setWardId] = useState<number | null>(null);
  const [search, setSearch] = useState("");
  const [debouncedSearch, setDebouncedSearch] = useState("");
  const [refreshToken, setRefreshToken] = useState(0);

  const [wards, setWards] = useState<WardSummary[]>([]);
  const [wardsLoading, setWardsLoading] = useState(false);
  const [wardsError, setWardsError] = useState<string | null>(null);

  const [rows, setRows] = useState<AdmissionWorklistRow[]>([]);
  const [summary, setSummary] = useState<AdmissionLaneSummary | null>(null);
  const [truncated, setTruncated] = useState(false);
  const [queueLoading, setQueueLoading] = useState(false);
  const [queueError, setQueueError] = useState<string | null>(null);

  const [beds, setBeds] = useState<BedBoardRow[]>([]);
  const [bedsLoading, setBedsLoading] = useState(false);
  const [bedsError, setBedsError] = useState<string | null>(null);

  const [selectedId, setSelectedId] = useState<number | null>(null);
  /** An admission opened from the bed board: shown whatever the census does. */
  const [pinnedId, setPinnedId] = useState<number | null>(null);
  const [detail, setDetail] = useState<AdmissionDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState<string | null>(null);
  const [detailStale, setDetailStale] = useState(false);
  const detailRef = useRef<AdmissionDetail | null>(null);

  /* ---- Admit (Slice 2) ---- */
  const [admitOpen, setAdmitOpen] = useState(false);
  const [admitBeds, setAdmitBeds] = useState<BedBoardRow[]>([]);
  const [admitBedsLoading, setAdmitBedsLoading] = useState(false);
  const [admitBedsError, setAdmitBedsError] = useState<string | null>(null);
  const [admitBusy, setAdmitBusy] = useState(false);
  const [admitMessage, setAdmitMessage] = useState<ActionMessage | null>(null);
  const [actionMessage, setActionMessage] = useState<ActionMessage | null>(null);
  /* ---- Transfer and Cancel request (Slice 3) ---- */
  const [transferOpen, setTransferOpen] = useState(false);
  const [transferBusy, setTransferBusy] = useState(false);
  const [transferMessage, setTransferMessage] = useState<ActionMessage | null>(null);
  const [cancelOpen, setCancelOpen] = useState(false);
  const [cancelBusy, setCancelBusy] = useState(false);
  const [cancelMessage, setCancelMessage] = useState<ActionMessage | null>(null);
  /** The act whose outcome is unknown; a retry of the same request reuses it. */
  const pendingRef = useRef<PendingAdmissionOperation | null>(null);
  useEffect(() => {
    detailRef.current = detail;
  }, [detail]);

  useEffect(() => {
    const timer = setTimeout(() => setDebouncedSearch(search.trim()), 250);
    return () => clearTimeout(timer);
  }, [search]);

  /* ---------------- session: first, and alone ---------------- */
  useEffect(() => {
    const controller = new AbortController();
    async function loadSession() {
      try {
        const response = await fetch(ADMISSIONS_SESSION_PATH, { cache: "no-store", signal: controller.signal });
        const payload = (await response.json()) as ApiEnvelope<AdmissionDeskSession>;
        if (controller.signal.aborted) return;
        if (response.ok && payload.success) {
          setSession(payload.data);
          setDeskAllowed(payload.data.capabilities.admissions_desk);
        } else if (response.status === 403) {
          setDeskAllowed(false);
        } else {
          setSessionError(messageFromPayload(payload, "Unable to load your session."));
        }
      } catch {
        if (!controller.signal.aborted) setSessionError("Unable to reach the admissions service.");
      }
    }
    void loadSession();
    return () => controller.abort();
  }, []);

  const ready = deskAllowed === true;

  /* ---------------- wards ---------------- */
  useEffect(() => {
    if (!ready) return;
    const controller = new AbortController();
    async function loadWards() {
      setWardsLoading(true);
      try {
        const response = await fetch(ADMISSIONS_WARDS_PATH, { cache: "no-store", signal: controller.signal });
        const payload = (await response.json()) as ApiEnvelope<WardsResponse>;
        if (controller.signal.aborted) return;
        if (response.ok && payload.success) {
          setWards(payload.data.wards);
          setWardsError(null);
        } else {
          setWardsError(messageFromPayload(payload, "Unable to load the ward occupancy."));
        }
      } catch {
        if (!controller.signal.aborted) setWardsError("Unable to reach the admissions service.");
      } finally {
        if (!controller.signal.aborted) setWardsLoading(false);
      }
    }
    void loadWards();
    return () => controller.abort();
  }, [ready, refreshToken]);

  /* ---------------- census ---------------- */
  useEffect(() => {
    if (!ready) return;
    const controller = new AbortController();
    async function loadQueue() {
      setQueueLoading(true);
      try {
        const response = await fetch(worklistPath({ lane, wardId, q: debouncedSearch || null }), {
          cache: "no-store",
          signal: controller.signal,
        });
        const payload = (await response.json()) as ApiEnvelope<AdmissionWorklistResponse>;
        if (controller.signal.aborted) return;
        if (!response.ok || !payload.success) {
          setRows([]);
          setSummary(null);
          setQueueError(messageFromPayload(payload, "Unable to load the admissions census."));
          return;
        }
        setRows(payload.data.rows);
        setSummary(payload.data.summary);
        setTruncated(payload.data.meta.truncated);
        setQueueError(null);
      } catch {
        if (!controller.signal.aborted) {
          setRows([]);
          setSummary(null);
          setQueueError("Unable to reach the admissions service.");
        }
      } finally {
        if (!controller.signal.aborted) setQueueLoading(false);
      }
    }
    void loadQueue();
    return () => controller.abort();
  }, [ready, lane, wardId, debouncedSearch, refreshToken]);

  /* ---------------- bed board: follows the ward filter only ---------------- */
  useEffect(() => {
    if (!ready) return;
    const controller = new AbortController();
    async function loadBeds() {
      setBedsLoading(true);
      try {
        const response = await fetch(bedsPath({ wardId }), { cache: "no-store", signal: controller.signal });
        const payload = (await response.json()) as ApiEnvelope<BedsResponse>;
        if (controller.signal.aborted) return;
        if (response.ok && payload.success) {
          setBeds(payload.data.beds);
          setBedsError(null);
        } else {
          setBeds([]);
          setBedsError(messageFromPayload(payload, "Unable to load the bed board."));
        }
      } catch {
        if (!controller.signal.aborted) {
          setBeds([]);
          setBedsError("Unable to reach the admissions service.");
        }
      } finally {
        if (!controller.signal.aborted) setBedsLoading(false);
      }
    }
    void loadBeds();
    return () => controller.abort();
  }, [ready, wardId, refreshToken]);

  const activeId = useMemo(
    () => pinnedId ?? resolveSelection(rows, selectedId),
    [pinnedId, rows, selectedId],
  );

  /* ---------------- selected / pinned admission ---------------- */
  useEffect(() => {
    if (!ready || activeId === null) return;
    const controller = new AbortController();
    async function loadDetail(admissionId: number) {
      setDetailLoading(true);
      setDetailError(null);
      try {
        const response = await fetch(admissionPath(admissionId), { cache: "no-store", signal: controller.signal });
        const payload = (await response.json()) as ApiEnvelope<AdmissionDetailResponse>;
        if (controller.signal.aborted) return;
        if (!response.ok || !payload.success) {
          if (detailRef.current?.id === admissionId) {
            setDetailStale(true);
            return;
          }
          setDetail(null);
          setDetailError(messageFromPayload(payload, "Unable to load the selected admission."));
          return;
        }
        setDetail(payload.data.admission);
        setDetailStale(false);
      } catch {
        if (controller.signal.aborted) return;
        if (detailRef.current?.id === admissionId) {
          setDetailStale(true);
          return;
        }
        setDetail(null);
        setDetailError("Unable to reach the admissions service.");
      } finally {
        if (!controller.signal.aborted) setDetailLoading(false);
      }
    }
    void loadDetail(activeId);
    return () => controller.abort();
  }, [ready, activeId, refreshToken]);

  const refresh = useCallback(() => setRefreshToken((token) => token + 1), []);

  const selectRow = useCallback((admissionId: number) => {
    setPinnedId(null);
    setActionMessage(null);
    setSelectedId(admissionId);
  }, []);

  const pinFromBed = useCallback((admissionId: number) => {
    setPinnedId(admissionId);
  }, []);

  const changeWard = useCallback((next: number | null) => {
    setWardId(next);
    setPinnedId(null);
  }, []);

  const shownDetail = detail && detail.id === activeId ? detail : null;
  const mayAdmit = session?.capabilities.admit === true;
  const mayTransfer = session?.capabilities.transfer === true;
  const mayCancelRequest = session?.capabilities.cancel_request === true;

  /* ---------------- admit ---------------- */
  const loadAvailableBeds = useCallback(async () => {
    setAdmitBedsLoading(true);
    setAdmitBedsError(null);
    try {
      const response = await fetch(bedsPath({ wardId: null, state: "available" }), { cache: "no-store" });
      const payload = (await response.json()) as ApiEnvelope<BedsResponse>;
      if (response.ok && payload.success) {
        setAdmitBeds(payload.data.beds);
      } else {
        setAdmitBeds([]);
        setAdmitBedsError(messageFromPayload(payload, "Unable to load the available beds."));
      }
    } catch {
      setAdmitBeds([]);
      setAdmitBedsError("Unable to reach the admissions service.");
    } finally {
      setAdmitBedsLoading(false);
    }
  }, []);

  const openAdmit = useCallback(() => {
    setActionMessage(null);
    setAdmitMessage(null);
    setAdmitOpen(true);
    void loadAvailableBeds();
  }, [loadAvailableBeds]);

  const closeAdmit = useCallback(() => {
    if (admitBusy) return;
    setAdmitOpen(false);
    setAdmitMessage(null);
  }, [admitBusy]);

  const submitAdmit = useCallback(
    async (bedId: number) => {
      const current = shownDetail;
      if (!current || admitBusy) return;
      const signature = admitSignature(current.workflow_revision, bedId);
      const token = tokenFor(pendingRef.current, "admit", current.id, signature, () => crypto.randomUUID());
      pendingRef.current = { kind: "admit", targetId: current.id, signature, token };

      setAdmitBusy(true);
      let status: number | null = null;
      let payload: ApiEnvelope<AdmissionAdmitResponse> | null = null;
      try {
        const response = await fetch(admitPath(current.id), {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(admitBody(current.workflow_revision, bedId, token)),
          cache: "no-store",
        });
        status = response.status;
        payload = (await response.json()) as ApiEnvelope<AdmissionAdmitResponse>;
      } catch {
        payload = null;
      } finally {
        setAdmitBusy(false);
      }

      if (!isResolved(status, payload !== null)) {
        // Outcome unknown: keep the token so a retry replays, never re-applies.
        setAdmitMessage({ tone: "amber", text: UNKNOWN_OUTCOME_NOTICE });
        return;
      }
      pendingRef.current = null;

      if (payload && payload.success) {
        const updated = payload.data.admission;
        setDetail(updated);
        setDetailStale(false);
        setPinnedId(updated.id);
        setSelectedId(updated.id);
        setAdmitOpen(false);
        setAdmitMessage(null);
        setActionMessage({
          tone: "green",
          text: payload.data.operation.replayed
            ? `Already admitted: ${updated.reference}.`
            : `Admitted ${updated.reference} to ${updated.location.bed?.code ?? updated.location.bed?.name ?? "the bed"}.`,
        });
        setRefreshToken((value) => value + 1);
        return;
      }

      const code = codeFromPayload(payload);
      setAdmitMessage({
        tone: "red",
        text: messageFromPayload(payload, "The admission could not be completed. Nothing was changed."),
      });
      if (needsBedRefresh(code)) {
        void loadAvailableBeds();
      }
      if (needsReload(code)) {
        setRefreshToken((value) => value + 1);
      }
    },
    [admitBusy, loadAvailableBeds, shownDetail],
  );

  /* ---------------- transfer (Slice 3) ---------------- */
  const openTransfer = useCallback(() => {
    setActionMessage(null);
    setTransferMessage(null);
    setTransferOpen(true);
    void loadAvailableBeds();
  }, [loadAvailableBeds]);

  const closeTransfer = useCallback(() => {
    if (transferBusy) return;
    setTransferOpen(false);
    setTransferMessage(null);
  }, [transferBusy]);

  const submitTransfer = useCallback(
    async (bedId: number, reason: string) => {
      const current = shownDetail;
      if (!current || transferBusy) return;
      const signature = transferSignature(current.workflow_revision, bedId, reason);
      const token = tokenFor(pendingRef.current, "transfer", current.id, signature, () => crypto.randomUUID());
      pendingRef.current = { kind: "transfer", targetId: current.id, signature, token };

      setTransferBusy(true);
      let status: number | null = null;
      let payload: ApiEnvelope<AdmissionTransferResponse> | null = null;
      try {
        const response = await fetch(transferPath(current.id), {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(transferBody(current.workflow_revision, bedId, reason, token)),
          cache: "no-store",
        });
        status = response.status;
        payload = (await response.json()) as ApiEnvelope<AdmissionTransferResponse>;
      } catch {
        payload = null;
      } finally {
        setTransferBusy(false);
      }

      if (!isResolved(status, payload !== null)) {
        // Outcome unknown: keep the token so a retry replays, never re-applies.
        setTransferMessage({ tone: "amber", text: UNKNOWN_OUTCOME_NOTICE });
        return;
      }
      pendingRef.current = null;

      if (payload && payload.success) {
        const updated = payload.data.admission;
        setDetail(updated);
        setDetailStale(false);
        setPinnedId(updated.id);
        setSelectedId(updated.id);
        setTransferOpen(false);
        setTransferMessage(null);
        setActionMessage({
          tone: "green",
          text: payload.data.operation.replayed
            ? `Already transferred: ${updated.reference}.`
            : `Transferred ${updated.reference} to ${updated.location.bed?.code ?? updated.location.bed?.name ?? "the bed"}.`,
        });
        setRefreshToken((value) => value + 1);
        return;
      }

      const code = codeFromPayload(payload);
      setTransferMessage({
        tone: "red",
        text: messageFromPayload(payload, "The transfer could not be completed. Nothing was changed."),
      });
      if (needsBedRefresh(code)) {
        void loadAvailableBeds();
      }
      if (needsReload(code)) {
        setRefreshToken((value) => value + 1);
      }
    },
    [loadAvailableBeds, shownDetail, transferBusy],
  );

  /* ---------------- cancel request (Slice 3) ---------------- */
  const openCancel = useCallback(() => {
    setActionMessage(null);
    setCancelMessage(null);
    setCancelOpen(true);
  }, []);

  const closeCancel = useCallback(() => {
    if (cancelBusy) return;
    setCancelOpen(false);
    setCancelMessage(null);
  }, [cancelBusy]);

  const submitCancel = useCallback(async () => {
    const current = shownDetail;
    if (!current || cancelBusy) return;
    const signature = cancelRequestSignature(current.workflow_revision);
    const token = tokenFor(pendingRef.current, "cancel_request", current.id, signature, () => crypto.randomUUID());
    pendingRef.current = { kind: "cancel_request", targetId: current.id, signature, token };

    setCancelBusy(true);
    let status: number | null = null;
    let payload: ApiEnvelope<AdmissionCancelRequestResponse> | null = null;
    try {
      const response = await fetch(cancelRequestPath(current.id), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(cancelRequestBody(current.workflow_revision, token)),
        cache: "no-store",
      });
      status = response.status;
      payload = (await response.json()) as ApiEnvelope<AdmissionCancelRequestResponse>;
    } catch {
      payload = null;
    } finally {
      setCancelBusy(false);
    }

    if (!isResolved(status, payload !== null)) {
      setCancelMessage({ tone: "amber", text: UNKNOWN_OUTCOME_NOTICE });
      return;
    }
    pendingRef.current = null;

    if (payload && payload.success) {
      const updated = payload.data.admission;
      setDetail(updated);
      setDetailStale(false);
      setPinnedId(updated.id);
      setSelectedId(updated.id);
      setCancelOpen(false);
      setCancelMessage(null);
      setActionMessage({
        tone: "green",
        text: payload.data.operation.replayed
          ? `Already cancelled: ${updated.reference}.`
          : `Admission request ${updated.reference} cancelled.`,
      });
      setRefreshToken((value) => value + 1);
      return;
    }

    setCancelMessage({
      tone: "red",
      text: messageFromPayload(payload, "The request could not be cancelled. Nothing was changed."),
    });
    if (needsReload(codeFromPayload(payload))) {
      setRefreshToken((value) => value + 1);
    }
  }, [cancelBusy, shownDetail]);

  if (deskAllowed === false) {
    return (
      <div className="rounded-lg border border-amber-300 bg-amber-50 px-4 py-3 cl-body text-amber-900">
        <span className="font-bold">This is not your workstation.</span> The Admissions Desk is open to
        reception, nursing, doctors, hospital managers and system administrators. Pharmacy, laboratory
        and billing work continue on their own desks.
      </div>
    );
  }

  if (deskAllowed === null) {
    return (
      <div className="rounded-lg border border-slate-200 bg-white px-4 py-3 cl-body text-slate-500">
        {sessionError ?? "Opening the Admissions Desk…"}
      </div>
    );
  }

  return (
    <div className="grid h-full min-h-[720px] grid-rows-[auto_auto_minmax(0,1fr)_minmax(200px,34%)] gap-2">
      <WardOccupancyStrip
        wards={wards}
        selectedWardId={wardId}
        loading={wardsLoading}
        error={wardsError}
        onSelect={changeWard}
      />

      <AdmissionsToolbar
        lane={lane}
        search={search}
        summary={summary}
        loading={queueLoading}
        roleLabel={deskRoleLabel(session)}
        scopeText={scopeLabel(session?.scope)}
        onLaneChange={setLane}
        onSearchChange={setSearch}
        onRefresh={refresh}
      />

      <div className="grid min-h-0 gap-2 min-[1100px]:grid-cols-[minmax(420px,44%)_minmax(0,1fr)]">
        <AdmissionsQueue
          rows={rows}
          selectedId={pinnedId === null ? activeId : null}
          loading={queueLoading}
          error={queueError}
          truncated={truncated}
          emptyMessage={emptyQueueMessage(session?.scope, lane)}
          onSelect={selectRow}
        />
        <AdmissionDetailPanel
          detail={shownDetail}
          loading={detailLoading}
          error={activeId !== null ? detailError : null}
          empty={rows.length === 0 && pinnedId === null}
          stale={detailStale && shownDetail !== null}
          mayAdmit={mayAdmit}
          mayTransfer={mayTransfer}
          mayCancelRequest={mayCancelRequest}
          actionMessage={actionMessage}
          onRequestAdmit={openAdmit}
          onRequestTransfer={openTransfer}
          onRequestCancel={openCancel}
        />
      </div>

      <BedBoard
        beds={beds}
        loading={bedsLoading}
        error={bedsError}
        pinnedAdmissionId={pinnedId}
        onPin={pinFromBed}
      />

      {admitOpen && shownDetail && mayAdmit ? (
        <AdmitDialog
          detail={shownDetail}
          beds={admitBeds}
          bedsLoading={admitBedsLoading}
          bedsError={admitBedsError}
          busy={admitBusy}
          message={admitMessage}
          onConfirm={(bedId) => void submitAdmit(bedId)}
          onClose={closeAdmit}
        />
      ) : null}

      {transferOpen && shownDetail && mayTransfer ? (
        <TransferDialog
          detail={shownDetail}
          beds={admitBeds}
          bedsLoading={admitBedsLoading}
          bedsError={admitBedsError}
          busy={transferBusy}
          message={transferMessage}
          onConfirm={(bedId, reason) => void submitTransfer(bedId, reason)}
          onClose={closeTransfer}
        />
      ) : null}

      {cancelOpen && shownDetail && mayCancelRequest ? (
        <CancelRequestDialog
          detail={shownDetail}
          busy={cancelBusy}
          message={cancelMessage}
          onConfirm={() => void submitCancel()}
          onClose={closeCancel}
        />
      ) : null}
    </div>
  );
}
