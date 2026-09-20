"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { codeFromPayload, messageFromPayload } from "@/lib/api-error";
import {
  QUEUE_REFRESH_FAILED_NOTICE,
  UNKNOWN_OUTCOME_NOTICE,
  draftFromDetail,
  draftKey,
  isResolved,
  needsReload,
  prepareBody,
  preparePath,
  preparePlan,
  requestSignature,
  tokenFor,
  validateBody,
  validatePath,
  validationSummary,
  type PendingOperation,
  type PrepareDraft,
} from "@/lib/pharmacy-desk-actions";
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
  PharmacyMutationResponse,
  PharmacyQueueRow,
  PharmacySessionResponse,
  PharmacyWorklistResponse,
  PharmacyWorklistSummary,
} from "@/types/pharmacy-desk";

import PharmacyConfirmDialog, { type ConfirmMode } from "./pharmacy-confirm-dialog";
import PharmacyDispensePanel from "./pharmacy-dispense-panel";
import PharmacyFilters from "./pharmacy-filters";
import PharmacyQueue from "./pharmacy-queue";

type ActionMessage = { tone: "red" | "amber" | "green"; text: string };

/**
 * The Pharmacy Desk.
 *
 * QUEUE LEFT, DISPENSE DETAIL RIGHT, one screen. Every read is a GET to
 * /api/pharmacy/*; the two writes are POSTs to the prepare and validate BFF
 * routes, each sent ONCE per confirmed action with one operation token. The
 * browser never addresses Odoo and never writes a line directly.
 *
 * AFTER A SUCCESSFUL ACTION the server's returned dispense is PINNED and shown
 * as-is -- it is the authoritative result, even if the dispense has left the
 * lane being viewed -- and the queue is reconciled from the server. If that
 * reconciliation fails, the pinned result stays and says so.
 *
 * THE LANE COUNTS COME FROM THE SERVER and are never recounted here.
 */
export default function PharmacyWorkstation() {
  const [lane, setLane] = useState(ACTIVE_LANE_KEY);
  const [date, setDate] = useState("");
  const [search, setSearch] = useState("");
  const [debouncedSearch, setDebouncedSearch] = useState("");

  const [rows, setRows] = useState<PharmacyQueueRow[]>([]);
  const [summary, setSummary] = useState<PharmacyWorklistSummary | null>(null);
  const [truncated, setTruncated] = useState(false);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  /** A dispense just changed by an action: shown whatever the queue does. */
  const [pinnedId, setPinnedId] = useState<number | null>(null);
  const [detail, setDetail] = useState<PharmacyDispenseDetail | null>(null);
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
  const [queueToken, setQueueToken] = useState(0);
  const [detailToken, setDetailToken] = useState(0);
  /** Set when the queue reload follows a successful action. */
  const reconcilingRef = useRef(false);

  const [draftState, setDraftState] = useState<{ key: string; draft: PrepareDraft } | null>(null);
  const [confirm, setConfirm] = useState<ConfirmMode | null>(null);
  const [busy, setBusy] = useState(false);
  const [actionMessage, setActionMessage] = useState<ActionMessage | null>(null);
  /** The request whose outcome is unknown; a retry reuses its token. */
  const pendingRef = useRef<PendingOperation | null>(null);

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
      const reconciling = reconcilingRef.current;
      reconcilingRef.current = false;
      try {
        const response = await fetch(
          worklistPath({ status: statuses, date: date || null, q: debouncedSearch || null }),
          { cache: "no-store", signal: controller.signal },
        );
        const payload = (await response.json()) as ApiEnvelope<PharmacyWorklistResponse>;
        if (controller.signal.aborted) return;

        if (!response.ok || !payload.success) {
          if (reconciling) {
            setActionMessage({ tone: "amber", text: QUEUE_REFRESH_FAILED_NOTICE });
            return;
          }
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
          if (reconciling) {
            setActionMessage({ tone: "amber", text: QUEUE_REFRESH_FAILED_NOTICE });
            return;
          }
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
  }, [statuses, date, debouncedSearch, queueToken]);

  const visibleRows = useMemo(
    () => rows.filter((row) => matchesSearch(row, search)),
    [rows, search],
  );

  const activeId = useMemo(
    () => pinnedId ?? resolveSelection(visibleRows, selectedId),
    [pinnedId, visibleRows, selectedId],
  );

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
  }, [activeId, detailToken]);

  const refresh = useCallback(() => {
    setActionMessage(null);
    setQueueToken((token) => token + 1);
    setDetailToken((token) => token + 1);
  }, []);

  const selectRow = useCallback((dispenseId: number) => {
    setPinnedId(null);
    setActionMessage(null);
    setSelectedId(dispenseId);
  }, []);

  const detailForSelection = visibleDetail(detail, activeId);
  const panelIsLoading = detailIsLoading(activeId, detailLoading, detailForSelection);

  /* ---------------- prepare draft ---------------- */
  const draft = useMemo<PrepareDraft | null>(() => {
    if (!detailForSelection || !detailForSelection.can_prepare) return null;
    const key = draftKey(detailForSelection);
    return draftState?.key === key ? draftState.draft : draftFromDetail(detailForSelection);
  }, [detailForSelection, draftState]);

  const plan = useMemo(
    () => (detailForSelection && draft ? preparePlan(detailForSelection, draft) : null),
    [detailForSelection, draft],
  );

  const changeDraft = useCallback(
    (lineId: number, value: string) => {
      if (!detailForSelection || !draft) return;
      setDraftState({ key: draftKey(detailForSelection), draft: { ...draft, [lineId]: value } });
    },
    [detailForSelection, draft],
  );

  /* ---------------- actions ---------------- */
  const requestPrepare = useCallback(() => {
    if (!plan || !plan.valid || !plan.hasIncrement) return;
    setActionMessage(null);
    setConfirm({ kind: "prepare", plan });
  }, [plan]);

  const requestValidate = useCallback(() => {
    if (!detailForSelection) return;
    setActionMessage(null);
    setConfirm({ kind: "validate", summary: validationSummary(detailForSelection) });
  }, [detailForSelection]);

  const submit = useCallback(async () => {
    const current = detailForSelection;
    if (!current || !confirm || busy) return;

    const kind = confirm.kind;
    const unsigned =
      kind === "prepare" && plan
        ? prepareBody(current, plan, "")
        : validateBody(current, "");
    const signature = requestSignature(unsigned);
    const token = tokenFor(pendingRef.current, kind, current.id, signature, () =>
      crypto.randomUUID(),
    );
    const body = { ...unsigned, operation_token: token };
    pendingRef.current = { kind, dispenseId: current.id, signature, token };

    setBusy(true);
    let status: number | null = null;
    let payload: ApiEnvelope<PharmacyMutationResponse> | null = null;
    try {
      const response = await fetch(kind === "prepare" ? preparePath(current.id) : validatePath(current.id), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
        cache: "no-store",
      });
      status = response.status;
      payload = (await response.json()) as ApiEnvelope<PharmacyMutationResponse>;
    } catch {
      payload = null;
    } finally {
      setBusy(false);
    }

    if (!isResolved(status, payload !== null)) {
      // Outcome unknown: keep the token so a retry replays, never re-applies.
      setActionMessage({ tone: "amber", text: UNKNOWN_OUTCOME_NOTICE });
      return;
    }
    pendingRef.current = null;
    setConfirm(null);

    if (payload && payload.success) {
      const updated = payload.data.dispense;
      setDetail(updated);
      setDetailStaleFor(null);
      setDraftState(null);
      setPinnedId(updated.id);
      setSelectedId(updated.id);
      setActionMessage({
        tone: "green",
        text:
          kind === "prepare"
            ? `Preparation recorded. ${updated.lane_label}.`
            : `Validation recorded. ${updated.lane_label}.`,
      });
      reconcilingRef.current = true;
      setQueueToken((value) => value + 1);
      return;
    }

    const code = codeFromPayload(payload);
    setActionMessage({
      tone: "red",
      text: messageFromPayload(payload, "The pharmacy action could not be completed. Nothing was changed."),
    });
    if (needsReload(code)) {
      setDraftState(null);
      setDetailToken((value) => value + 1);
      setQueueToken((value) => value + 1);
    }
  }, [busy, confirm, detailForSelection, plan]);

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
          onSelect={selectRow}
        />
        <PharmacyDispensePanel
          detail={detailForSelection}
          loading={panelIsLoading}
          error={activeId !== null ? detailError : null}
          empty={visibleRows.length === 0 && pinnedId === null}
          stale={detailForSelection !== null && detailStaleFor === detailForSelection.id}
          draft={draft}
          plan={plan}
          busy={busy}
          actionMessage={actionMessage}
          onDraftChange={changeDraft}
          onRequestPrepare={requestPrepare}
          onRequestValidate={requestValidate}
        />
      </div>

      {confirm && detailForSelection ? (
        <PharmacyConfirmDialog
          detail={detailForSelection}
          mode={confirm}
          busy={busy}
          onBack={() => setConfirm(null)}
          onConfirm={() => void submit()}
        />
      ) : null}
    </div>
  );
}
