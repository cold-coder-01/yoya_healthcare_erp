"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";

import ErrorBanner from "@/components/clinical/error-banner";
import {
  ClearanceBadge,
  EmergencyTag,
  QueueStageBadge,
} from "@/components/reception/reception-badges";
import ReferenceFilters from "@/components/reception/reference-filters";
import { formatHospitalTime, hospitalToday } from "@/lib/clinical-format";
import {
  formatCardStatus,
  formatEtb,
  formatVisitType,
  QUEUE_STAGE_OPTIONS,
  VISIT_TYPE_OPTIONS,
} from "@/lib/reception-format";
import type {
  ApiEnvelope,
  ReceptionQueueItem,
  ReceptionQueueResponse,
} from "@/types/reception";

function safeMessage(payload: unknown, fallback: string) {
  if (
    typeof payload === "object" &&
    payload !== null &&
    "error" in payload &&
    typeof payload.error === "object" &&
    payload.error !== null &&
    "message" in payload.error &&
    typeof payload.error.message === "string"
  ) {
    return payload.error.message;
  }

  return fallback;
}

function matchesSearch(row: ReceptionQueueItem, needle: string) {
  if (!needle) {
    return true;
  }
  return [
    row.appointment_code,
    row.patient.identification_code,
    row.patient.name,
    row.doctor?.name,
    row.department?.name,
  ]
    .filter(Boolean)
    .some((value) => String(value).toLowerCase().includes(needle));
}

const TH = "px-3 py-2.5 font-semibold";
const TD = "px-3 py-2.5 align-top";
const NUM = "px-3 py-2.5 align-top text-right tabular-nums whitespace-nowrap";

export default function ReceptionQueueClient() {
  const [date, setDate] = useState(hospitalToday());
  const [stage, setStage] = useState("");
  const [departmentId, setDepartmentId] = useState("");
  const [doctorId, setDoctorId] = useState("");
  const [visitType, setVisitType] = useState("");
  const [search, setSearch] = useState("");
  const [queue, setQueue] = useState<ReceptionQueueItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [refreshToken, setRefreshToken] = useState(0);

  // `search` is deliberately NOT part of the query. The server already caps
  // the day at 500 rows, so filtering client-side keeps typing instant and
  // avoids one round-trip per keystroke. This matches the clinical queue.
  const queryString = useMemo(() => {
    const params = new URLSearchParams();
    if (date) params.set("date", date);
    if (stage) params.set("stage", stage);
    if (departmentId) params.set("department_id", departmentId);
    if (doctorId) params.set("doctor_id", doctorId);
    if (visitType) params.set("visit_type", visitType);
    return params.toString();
  }, [date, departmentId, doctorId, stage, visitType]);

  // The effect is the single owner of this fetch. Refresh re-runs it by
  // bumping a token rather than duplicating the request logic, and the
  // AbortController means a superseded request can never overwrite newer
  // results when filters change quickly.
  useEffect(() => {
    const controller = new AbortController();

    async function run() {
      try {
        const response = await fetch(`/api/reception/queue?${queryString}`, {
          cache: "no-store",
          signal: controller.signal,
        });
        const payload =
          (await response.json()) as ApiEnvelope<ReceptionQueueResponse>;

        if (controller.signal.aborted) return;

        if (!response.ok || !payload.success) {
          setError(safeMessage(payload, "Unable to load the reception queue."));
          setQueue([]);
          return;
        }

        setError(null);
        setQueue(payload.data.queue ?? []);
      } catch {
        if (controller.signal.aborted) return;
        setError("Unable to reach the reception service.");
        setQueue([]);
      } finally {
        if (!controller.signal.aborted) {
          setLoading(false);
        }
      }
    }

    void run();
    return () => controller.abort();
  }, [queryString, refreshToken]);

  const refresh = useCallback(() => {
    setLoading(true);
    setRefreshToken((token) => token + 1);
  }, []);

  // Changing department invalidates the doctor: a doctor from the previous
  // department would silently filter the queue to nothing.
  const handleDepartmentChange = useCallback((value: string) => {
    setDepartmentId(value);
    setDoctorId("");
  }, []);

  const filteredQueue = useMemo(() => {
    const needle = search.trim().toLowerCase();
    return queue.filter((row) => matchesSearch(row, needle));
  }, [queue, search]);

  const summary = useMemo(
    () => ({
      total: queue.length,
      awaitingPayment: queue.filter(
        (row) => row.clinical_queue_stage === "awaiting_payment",
      ).length,
      awaitingTriage: queue.filter(
        (row) => row.clinical_queue_stage === "awaiting_triage",
      ).length,
      inTriage: queue.filter((row) => row.clinical_queue_stage === "in_triage")
        .length,
      awaitingDoctor: queue.filter(
        (row) => row.clinical_queue_stage === "awaiting_doctor",
      ).length,
      emergency: queue.filter((row) => row.emergency).length,
    }),
    [queue],
  );

  const outstandingTotal = useMemo(
    () =>
      queue.reduce(
        (total, row) => total + (row.reception_clearance.outstanding ?? 0),
        0,
      ),
    [queue],
  );

  return (
    <div className="space-y-5">
      <div className="grid gap-3 sm:grid-cols-3 xl:grid-cols-6">
        {(
          [
            ["Total visits", String(summary.total)],
            ["Awaiting payment", String(summary.awaitingPayment)],
            ["Awaiting triage", String(summary.awaitingTriage)],
            ["In triage", String(summary.inTriage)],
            ["Awaiting doctor", String(summary.awaitingDoctor)],
            ["Emergency", String(summary.emergency)],
          ] as const
        ).map(([label, value]) => (
          <div
            key={label}
            className="rounded-xl border border-slate-200 bg-white p-3 shadow-sm"
          >
            <div className="text-[11px] font-semibold uppercase tracking-[0.14em] text-slate-500">
              {label}
            </div>
            <div className="mt-1.5 text-2xl font-semibold text-slate-950">
              {value}
            </div>
          </div>
        ))}
      </div>

      <div className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
          <label className="block text-sm font-medium text-slate-700">
            Date
            <input
              type="date"
              value={date}
              onChange={(event) => setDate(event.target.value)}
              className="mt-2 h-11 w-full rounded-md border border-slate-300 px-3 text-sm"
            />
          </label>

          <label className="block text-sm font-medium text-slate-700">
            Queue stage
            <select
              value={stage}
              onChange={(event) => setStage(event.target.value)}
              className="mt-2 h-11 w-full rounded-md border border-slate-300 bg-white px-3 text-sm"
            >
              <option value="">All stages</option>
              {QUEUE_STAGE_OPTIONS.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
          </label>

          <label className="block text-sm font-medium text-slate-700">
            Visit type
            <select
              value={visitType}
              onChange={(event) => setVisitType(event.target.value)}
              className="mt-2 h-11 w-full rounded-md border border-slate-300 bg-white px-3 text-sm"
            >
              <option value="">Any visit type</option>
              {VISIT_TYPE_OPTIONS.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
          </label>

          <ReferenceFilters
            departmentId={departmentId}
            doctorId={doctorId}
            onDepartmentChange={handleDepartmentChange}
            onDoctorChange={setDoctorId}
          />

          <label className="block text-sm font-medium text-slate-700">
            Search
            <input
              type="search"
              value={search}
              onChange={(event) => setSearch(event.target.value)}
              className="mt-2 h-11 w-full rounded-md border border-slate-300 px-3 text-sm"
              placeholder="Patient, MRN, doctor"
            />
          </label>
        </div>

        <div className="mt-4 flex flex-wrap items-center justify-between gap-3 border-t border-slate-100 pt-4">
          <p className="text-sm text-slate-600">
            Showing{" "}
            <span className="font-semibold text-slate-900">
              {filteredQueue.length}
            </span>{" "}
            of {queue.length} · Outstanding{" "}
            <span className="font-semibold text-slate-900">
              {formatEtb(outstandingTotal)}
            </span>
          </p>
          <div className="flex items-center gap-3">
            <button
              type="button"
              onClick={refresh}
              disabled={loading}
              className="h-11 rounded-md bg-emerald-700 px-5 text-sm font-semibold text-white transition hover:bg-emerald-800 disabled:cursor-not-allowed disabled:bg-slate-400"
            >
              {loading ? "Refreshing…" : "Refresh"}
            </button>
            <Link
              href="/reception/new"
              className="inline-flex h-11 items-center justify-center rounded-md border border-slate-300 bg-white px-5 text-sm font-semibold text-slate-700 transition hover:bg-slate-50"
            >
              New Visit
            </Link>
          </div>
        </div>
      </div>

      <ErrorBanner message={error} title="Unable to load reception queue" />

      <div className="overflow-hidden rounded-xl border border-slate-200 bg-white shadow-sm">
        {/* Horizontal scroll is a fallback for narrow screens only; the
            compacted column set fits a normal laptop without it. */}
        <div className="overflow-x-auto">
          <table className="min-w-full divide-y divide-slate-200 text-sm">
            <caption className="sr-only">
              Reception visit queue for {date}, showing clearance amounts and
              clinical queue stage for each registered visit.
            </caption>
            <thead className="bg-slate-50 text-left text-xs uppercase tracking-wide text-slate-600">
              <tr>
                <th scope="col" className={TH}>
                  Appointment
                </th>
                <th scope="col" className={TH}>
                  Patient
                </th>
                <th scope="col" className={TH}>
                  Visit
                </th>
                <th scope="col" className={TH}>
                  Care team
                </th>
                <th scope="col" className={TH}>
                  Card
                </th>
                <th scope="col" className={`${TH} text-right`}>
                  Required
                </th>
                <th scope="col" className={`${TH} text-right`}>
                  Received
                </th>
                <th scope="col" className={`${TH} text-right`}>
                  Outstanding
                </th>
                <th scope="col" className={TH}>
                  Clearance
                </th>
                <th scope="col" className={TH}>
                  Queue stage
                </th>
                <th scope="col" className={`${TH} text-right`}>
                  <span className="sr-only">Actions</span>
                </th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {loading ? (
                <tr>
                  <td className="px-3 py-10 text-center text-slate-500" colSpan={11}>
                    Loading queue…
                  </td>
                </tr>
              ) : filteredQueue.length === 0 ? (
                <tr>
                  <td className="px-3 py-10 text-center text-slate-500" colSpan={11}>
                    No visits match these filters.
                  </td>
                </tr>
              ) : (
                filteredQueue.map((row) => (
                  <tr key={row.id} className="hover:bg-slate-50/75">
                    <th scope="row" className={`${TD} text-left font-medium`}>
                      <div className="whitespace-nowrap text-slate-950">
                        {row.appointment_code ?? `#${row.id}`}
                      </div>
                      <div className="text-xs font-normal text-slate-500">
                        {formatHospitalTime(row.appointment_date)}
                      </div>
                    </th>
                    <td className={TD}>
                      <div className="font-medium text-slate-900">
                        {row.patient.name}
                      </div>
                      <div className="text-xs text-slate-500">
                        {row.patient.identification_code ?? "No MRN"}
                      </div>
                    </td>
                    <td className={TD}>
                      <div className="flex flex-col items-start gap-1">
                        <span className="whitespace-nowrap text-slate-700">
                          {formatVisitType(row.visit_type)}
                        </span>
                        {row.emergency ? <EmergencyTag /> : null}
                      </div>
                    </td>
                    <td className={TD}>
                      <div className="text-slate-700">
                        {row.doctor?.name ?? "Unassigned"}
                      </div>
                      <div className="text-xs text-slate-500">
                        {row.department?.name ?? "No department"}
                      </div>
                    </td>
                    <td className={`${TD} whitespace-nowrap text-slate-700`}>
                      {formatCardStatus(row.card_status)}
                    </td>
                    <td className={`${NUM} text-slate-700`}>
                      {formatEtb(row.reception_clearance.required)}
                    </td>
                    <td className={`${NUM} text-slate-700`}>
                      {formatEtb(row.reception_clearance.received)}
                    </td>
                    <td
                      className={`${NUM} font-semibold ${
                        row.reception_clearance.outstanding > 0
                          ? "text-amber-700"
                          : "text-slate-600"
                      }`}
                    >
                      {formatEtb(row.reception_clearance.outstanding)}
                    </td>
                    <td className={TD}>
                      <ClearanceBadge
                        state={row.reception_clearance.state}
                        ok={row.reception_clearance.ok}
                      />
                    </td>
                    <td className={TD}>
                      <QueueStageBadge stage={row.clinical_queue_stage} />
                    </td>
                    <td className={`${TD} text-right`}>
                      <Link
                        href={`/reception/visits/${row.id}`}
                        className="inline-flex h-8 items-center justify-center whitespace-nowrap rounded-md border border-emerald-700 px-3 text-xs font-semibold text-emerald-800 transition hover:bg-emerald-50"
                        aria-label={`Open visit ${
                          row.appointment_code ?? row.id
                        } for ${row.patient.name}`}
                      >
                        Open
                      </Link>
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
