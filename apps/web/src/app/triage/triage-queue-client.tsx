"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";
import BillingWarning from "@/components/clinical/billing-warning";
import StatusBadge from "@/components/clinical/status-badge";
import type { ApiEnvelope, EvaluationQueueResponse, EvaluationQueueRow } from "@/types/clinical";

function todayIso() {
  return new Date().toISOString().slice(0, 10);
}

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

function formatTime(value: string | null) {
  if (!value) {
    return "-";
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value;
  }
  return new Intl.DateTimeFormat(undefined, {
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
}

function includesText(row: EvaluationQueueRow, search: string) {
  const needle = search.trim().toLowerCase();
  if (!needle) {
    return true;
  }

  return [
    row.appointment_code,
    row.patient.identification_code,
    row.patient.name,
    row.doctor_id?.name,
    row.department_id?.name,
  ]
    .filter(Boolean)
    .some((value) => String(value).toLowerCase().includes(needle));
}

export default function TriageQueueClient() {
  const [date, setDate] = useState(todayIso());
  const [state, setState] = useState("");
  const [departmentId, setDepartmentId] = useState("");
  const [doctorId, setDoctorId] = useState("");
  const [search, setSearch] = useState("");
  const [queue, setQueue] = useState<EvaluationQueueRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const loadQueue = useCallback(async () => {
    setLoading(true);
    setError(null);

    const params = new URLSearchParams();
    if (date) params.set("date", date);
    if (state) params.set("state", state);
    if (departmentId) params.set("department_id", departmentId);
    if (doctorId) params.set("doctor_id", doctorId);

    try {
      const response = await fetch(`/api/clinical/evaluation-queue?${params}`, {
        cache: "no-store",
      });
      const payload = (await response.json()) as ApiEnvelope<EvaluationQueueResponse>;

      if (!response.ok || !payload.success) {
        setError(safeMessage(payload, "Unable to load the evaluation queue."));
        setQueue([]);
        return;
      }

      setQueue(payload.data.queue);
    } catch {
      setError("Unable to reach the clinical queue service.");
      setQueue([]);
    } finally {
      setLoading(false);
    }
  }, [date, departmentId, doctorId, state]);

  useEffect(() => {
    let cancelled = false;

    async function loadInitial() {
      setLoading(true);
      setError(null);
      try {
        const params = new URLSearchParams({ date });
        const response = await fetch(`/api/clinical/evaluation-queue?${params}`, {
          cache: "no-store",
        });
        const payload = (await response.json()) as ApiEnvelope<EvaluationQueueResponse>;
        if (cancelled) return;
        if (!response.ok || !payload.success) {
          setError(safeMessage(payload, "Unable to load the evaluation queue."));
          setQueue([]);
          return;
        }
        setQueue(payload.data.queue);
      } catch {
        if (!cancelled) {
          setError("Unable to reach the clinical queue service.");
          setQueue([]);
        }
      } finally {
        if (!cancelled) {
          setLoading(false);
        }
      }
    }

    void loadInitial();
    return () => {
      cancelled = true;
    };
  }, [date]);

  const filteredQueue = useMemo(
    () => queue.filter((row) => includesText(row, search)),
    [queue, search],
  );

  const summary = useMemo(
    () => ({
      total: queue.length,
      notStarted: queue.filter((row) => row.evaluation.status === "not_started" || row.evaluation.status === "waiting").length,
      inProgress: queue.filter((row) => row.evaluation.status === "in_progress").length,
      completed: queue.filter((row) => row.evaluation.status === "completed").length,
      billingBlocked: queue.filter((row) => row.billing_blocked).length,
    }),
    [queue],
  );

  return (
    <div className="space-y-4">
      <div className="grid gap-3 md:grid-cols-5">
        {[
          ["Total", summary.total],
          ["Not started", summary.notStarted],
          ["In progress", summary.inProgress],
          ["Completed", summary.completed],
          ["Billing blocked", summary.billingBlocked],
        ].map(([label, value]) => (
          <div key={label} className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm">
            <div className="text-xs font-semibold uppercase tracking-wide text-slate-500">{label}</div>
            <div className="mt-2 text-2xl font-semibold text-slate-950">{value}</div>
          </div>
        ))}
      </div>

      <div className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm">
        <div className="grid gap-3 lg:grid-cols-5">
          <label className="text-sm font-medium text-slate-700">
            Date
            <input
              type="date"
              value={date}
              onChange={(event) => setDate(event.target.value)}
              className="mt-1 h-10 w-full rounded-md border border-slate-300 px-3 text-sm"
            />
          </label>
          <label className="text-sm font-medium text-slate-700">
            State
            <select
              value={state}
              onChange={(event) => setState(event.target.value)}
              className="mt-1 h-10 w-full rounded-md border border-slate-300 px-3 text-sm"
            >
              <option value="">All active</option>
              <option value="confirmed">Confirmed</option>
              <option value="in_consultation">In consultation</option>
            </select>
          </label>
          <label className="text-sm font-medium text-slate-700">
            Department ID
            <input
              inputMode="numeric"
              value={departmentId}
              onChange={(event) => setDepartmentId(event.target.value)}
              className="mt-1 h-10 w-full rounded-md border border-slate-300 px-3 text-sm"
              placeholder="Optional"
            />
          </label>
          <label className="text-sm font-medium text-slate-700">
            Doctor ID
            <input
              inputMode="numeric"
              value={doctorId}
              onChange={(event) => setDoctorId(event.target.value)}
              className="mt-1 h-10 w-full rounded-md border border-slate-300 px-3 text-sm"
              placeholder="Optional"
            />
          </label>
          <label className="text-sm font-medium text-slate-700">
            Search
            <input
              value={search}
              onChange={(event) => setSearch(event.target.value)}
              className="mt-1 h-10 w-full rounded-md border border-slate-300 px-3 text-sm"
              placeholder="MRN, patient, doctor"
            />
          </label>
        </div>
        <div className="mt-4 flex justify-end">
          <button
            type="button"
            onClick={loadQueue}
            disabled={loading}
            className="h-10 rounded-md bg-emerald-700 px-4 text-sm font-semibold text-white transition hover:bg-emerald-800 disabled:cursor-not-allowed disabled:bg-slate-400"
          >
            {loading ? "Loading..." : "Apply filters"}
          </button>
        </div>
      </div>

      {error ? <BillingWarning blocked message={error} /> : null}

      <div className="overflow-hidden rounded-lg border border-slate-200 bg-white shadow-sm">
        <div className="overflow-x-auto">
          <table className="min-w-full divide-y divide-slate-200 text-sm">
            <thead className="bg-slate-50 text-left text-xs font-semibold uppercase tracking-wide text-slate-600">
              <tr>
                <th className="px-3 py-3">Appointment</th>
                <th className="px-3 py-3">MRN</th>
                <th className="px-3 py-3">Patient</th>
                <th className="px-3 py-3">Age/Gender</th>
                <th className="px-3 py-3">Time</th>
                <th className="px-3 py-3">Doctor</th>
                <th className="px-3 py-3">Department</th>
                <th className="px-3 py-3">Evaluation</th>
                <th className="px-3 py-3">Priority</th>
                <th className="px-3 py-3">Billing</th>
                <th className="px-3 py-3 text-right">Action</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-200">
              {loading ? (
                <tr><td className="px-3 py-8 text-center text-slate-500" colSpan={11}>Loading queue...</td></tr>
              ) : filteredQueue.length === 0 ? (
                <tr><td className="px-3 py-8 text-center text-slate-500" colSpan={11}>No evaluation queue records match these filters.</td></tr>
              ) : (
                filteredQueue.map((row) => (
                  <tr key={row.id} className="align-top">
                    <td className="px-3 py-3 font-medium text-slate-950">{row.appointment_code ?? row.id}</td>
                    <td className="px-3 py-3 text-slate-700">{row.patient.identification_code ?? "-"}</td>
                    <td className="px-3 py-3 text-slate-900">{row.patient.name}</td>
                    <td className="px-3 py-3 text-slate-700">{row.patient.age ?? "-"} / {row.patient.gender ?? "-"}</td>
                    <td className="px-3 py-3 text-slate-700">{formatTime(row.appointment_date)}</td>
                    <td className="px-3 py-3 text-slate-700">{row.doctor_id?.name ?? "-"}</td>
                    <td className="px-3 py-3 text-slate-700">{row.department_id?.name ?? "-"}</td>
                    <td className="px-3 py-3"><StatusBadge value={row.evaluation.status} /></td>
                    <td className="px-3 py-3"><StatusBadge value={row.evaluation.triage_priority} /></td>
                    <td className="px-3 py-3 text-slate-700">{row.billing_blocked ? "Blocked" : "Clear"}</td>
                    <td className="px-3 py-3 text-right">
                      <Link className="font-semibold text-emerald-700 hover:text-emerald-900" href={`/triage/${row.id}`}>
                        Open Evaluation
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
