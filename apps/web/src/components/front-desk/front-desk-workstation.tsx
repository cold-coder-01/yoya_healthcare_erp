"use client";

import { useCallback, useEffect, useMemo, useState } from "react";

import { messageFromPayload } from "@/lib/api-error";
import { hospitalToday } from "@/lib/clinical-format";
import type {
  ApiEnvelope,
  FrontDeskCounts,
  FrontDeskQueueRow,
  FrontDeskVisit,
  FrontDeskWorklist,
} from "@/types/front-desk";

import FrontDeskFilters from "./front-desk-filters";
import FrontDeskNewVisitDialog from "./front-desk-new-visit-dialog";
import FrontDeskPatientPanel from "./front-desk-patient-panel";
import FrontDeskQueue from "./front-desk-queue";
import FrontDeskSummaryBar from "./front-desk-summary-bar";

export default function FrontDeskWorkstation() {
  const [date, setDate] = useState(hospitalToday());
  const [stage, setStage] = useState("");
  const [departmentId, setDepartmentId] = useState("");
  const [doctorId, setDoctorId] = useState("");
  const [search, setSearch] = useState("");
  const [debouncedSearch, setDebouncedSearch] = useState("");
  const [rows, setRows] = useState<FrontDeskQueueRow[]>([]);
  const [counts, setCounts] = useState<FrontDeskCounts | null>(null);
  // Authoritative capability flags, straight from the worklist payload. The
  // triage panel needs `intake` to decide whether the doctor picker is live;
  // every flag is enforced again by the Odoo method behind the action.
  const [capabilities, setCapabilities] = useState<Record<string, boolean>>({});
  const [truncated, setTruncated] = useState(false);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [visit, setVisit] = useState<FrontDeskVisit | null>(null);
  const [queueLoading, setQueueLoading] = useState(true);
  const [detailLoading, setDetailLoading] = useState(false);
  const [queueError, setQueueError] = useState<string | null>(null);
  const [detailError, setDetailError] = useState<string | null>(null);
  const [refreshToken, setRefreshToken] = useState(0);
  const [newVisitOpen, setNewVisitOpen] = useState(false);

  useEffect(() => {
    const timer = setTimeout(() => setDebouncedSearch(search.trim()), 250);
    return () => clearTimeout(timer);
  }, [search]);

  const queryString = useMemo(() => {
    const params = new URLSearchParams();
    if (date) params.set("date", date);
    if (stage) params.set("stage", stage);
    if (departmentId) params.set("department_id", departmentId);
    if (doctorId) params.set("doctor_id", doctorId);
    if (debouncedSearch) params.set("q", debouncedSearch);
    return params.toString();
  }, [date, stage, departmentId, doctorId, debouncedSearch]);

  useEffect(() => {
    const controller = new AbortController();

    async function loadWorklist() {
      setQueueLoading(true);
      try {
        const response = await fetch(`/api/front-desk/worklist?${queryString}`, {
          cache: "no-store",
          signal: controller.signal,
        });
        const payload = (await response.json()) as ApiEnvelope<FrontDeskWorklist>;
        if (controller.signal.aborted) return;

        if (!response.ok || !payload.success) {
          setQueueError(
            messageFromPayload(payload, "Unable to load the front desk worklist."),
          );
          return;
        }

        const nextRows = payload.data.rows ?? [];
        setRows(nextRows);
        setCounts(payload.data.counts);
        setCapabilities(payload.data.capabilities ?? {});
        setTruncated(payload.data.meta.truncated);
        setQueueError(null);
        setSelectedId((current) => {
          if (current && nextRows.some((row) => row.appointment_id === current)) {
            return current;
          }
          return nextRows[0]?.appointment_id ?? null;
        });
      } catch {
        if (!controller.signal.aborted) {
          setQueueError("Unable to reach the front desk service.");
        }
      } finally {
        if (!controller.signal.aborted) setQueueLoading(false);
      }
    }

    void loadWorklist();
    return () => controller.abort();
  }, [queryString, refreshToken]);

  useEffect(() => {
    if (!selectedId) {
      return;
    }

    const controller = new AbortController();

    async function loadVisit() {
      setDetailLoading(true);
      setDetailError(null);
      try {
        const response = await fetch(`/api/front-desk/visits/${selectedId}`, {
          cache: "no-store",
          signal: controller.signal,
        });
        const payload = (await response.json()) as ApiEnvelope<FrontDeskVisit>;
        if (controller.signal.aborted) return;

        if (!response.ok || !payload.success) {
          setVisit(null);
          setDetailError(
            messageFromPayload(payload, "Unable to load the selected visit."),
          );
          return;
        }

        setVisit(payload.data);
      } catch {
        if (!controller.signal.aborted) {
          setVisit(null);
          setDetailError("Unable to reach the selected visit service.");
        }
      } finally {
        if (!controller.signal.aborted) setDetailLoading(false);
      }
    }

    void loadVisit();
    return () => controller.abort();
  }, [selectedId, refreshToken]);

  const changeDepartment = useCallback((value: string) => {
    setDepartmentId(value);
    setDoctorId("");
  }, []);

  const refresh = useCallback(() => {
    setRefreshToken((token) => token + 1);
  }, []);

  /**
   * A visit was just committed by Odoo. Put it on screen without the nurse
   * touching anything.
   *
   * Every filter is reset to the window the new visit is guaranteed to be in --
   * today, all active stages, no search, no department/doctor narrowing. A
   * stale filter is the one way a just-created visit can appear to vanish, and
   * a nurse who has just registered a walk-in wants to see that walk-in, not
   * the slice they were looking at a minute ago.
   *
   * selectedId is set FIRST so the worklist loader keeps it: that loader
   * preserves the current selection when the id is present in the incoming rows
   * and otherwise falls back to the first row.
   *
   * These are batched into one commit, so the fetch effect -- keyed on
   * [queryString, refreshToken] -- runs exactly once. Setters called with the
   * value they already hold bail out, so the common case (no filters set) does
   * not schedule extra work. refreshToken is bumped unconditionally to cover
   * the case where every filter was already at its default and queryString
   * therefore does not change.
   */
  const handleVisitCreated = useCallback((appointmentId: number) => {
    setSelectedId(appointmentId);
    setDate(hospitalToday());
    setStage("");
    setDepartmentId("");
    setDoctorId("");
    setSearch("");
    setDebouncedSearch("");
    setNewVisitOpen(false);
    setRefreshToken((token) => token + 1);
  }, []);

  return (
    /*
      100vh minus the shell chrome: 44px header + 1px border + 24px of main
      padding = 69px. It was 106px under ReceptionShell's three-line header;
      the sidebarless shell hands those ~37px back to the queue and the triage
      form.
    */
    <div className="flex min-h-[640px] flex-col gap-2 min-[1100px]:h-[calc(100vh-69px)] min-[1100px]:min-h-[560px]">
      <FrontDeskSummaryBar
        counts={counts}
        activeStage={stage}
        onStageChange={setStage}
      />
      <FrontDeskFilters
        date={date}
        stage={stage}
        departmentId={departmentId}
        doctorId={doctorId}
        search={search}
        loading={queueLoading}
        onDateChange={setDate}
        onStageChange={setStage}
        onDepartmentChange={changeDepartment}
        onDoctorChange={setDoctorId}
        onSearchChange={setSearch}
        onRefresh={refresh}
        onNewVisit={() => setNewVisitOpen(true)}
      />
      {/*
        52/48, up from 41/59.
        A front desk nurse scans the live queue continuously while working the
        selected patient, so the queue -- not the detail panel -- is the column
        that had to grow. 52% buys roughly 140px at 1366px, which is what pays
        for the doctor column in the queue rows. The detail panel absorbs the
        loss because the vitals form no longer relies on five narrow columns:
        it wraps, so it stays comfortable down to ~380px.
      */}
      <div className="grid min-h-0 flex-1 gap-2 min-[1100px]:grid-cols-[minmax(430px,52%)_minmax(0,48%)]">
        <FrontDeskQueue
          rows={rows}
          selectedId={selectedId}
          loading={queueLoading}
          error={queueError}
          truncated={truncated}
          onSelect={setSelectedId}
        />
        <FrontDeskPatientPanel
          /*
           * Derived, not stored. The loader used to null `visit` on every
           * fetch, which meant a post-save refresh flashed the empty "select a
           * patient" state. Matching the loaded visit against the current
           * selection instead means a refresh of the SAME visit keeps its
           * content on screen (the header's "Updating…" carries the feedback),
           * while switching patients still never shows the previous one's data.
           */
          visit={visit && visit.visit.appointment_id === selectedId ? visit : null}
          loading={detailLoading}
          error={selectedId ? detailError : null}
          capabilities={capabilities}
          // Every triage mutation ends here: one token bump refetches BOTH the
          // worklist (stage counters) and the selected visit, so the panel and
          // the queue can never disagree about the derived stage.
          onMutated={refresh}
        />
      </div>

      {/*
        Mounted only while open so each registration starts from clean state --
        see the note on FrontDeskNewVisitDialog.
      */}
      {newVisitOpen ? (
        <FrontDeskNewVisitDialog
          onClose={() => setNewVisitOpen(false)}
          onCreated={handleVisitCreated}
        />
      ) : null}
    </div>
  );
}
