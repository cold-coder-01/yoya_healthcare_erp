"use client";

import { useEffect, useState } from "react";

import { ORDER_KINDS } from "@/lib/laboratory-format";
import type { ApiEnvelope } from "@/types/doctor";
import type {
  DoctorDiagnosis,
  DoctorDiagnosisResponse,
} from "@/types/doctor-diagnosis";

import LaboratoryPanel from "./laboratory-panel";
import MedicationPanel from "./medication-panel";
import RadiologyPanel from "./radiology-panel";

/**
 * The ORDERS section of the active consultation.
 *
 * LABORATORY, RADIOLOGY AND MEDICATION ARE LIVE. Procedure is rendered as inert
 * text with no handler and no tab stop, because a control that looks pressable
 * and does nothing is worse than an honest label in a clinical tool.
 *
 * THIS COMPONENT OWNS THE TABS AND THE DIAGNOSIS LIST, AND NOTHING ELSE. Each
 * order kind's body lives in its own panel: they carry different fields,
 * different status vocabularies and different workflows, and one parameterised
 * panel would have needed a union type at every line of JSX. Medication makes
 * that plainest -- a lab or imaging order is a set of picked items, while a
 * prescription is a set of picked items each carrying its own dose, route,
 * frequency, duration and quantity.
 *
 * THE PANELS ARE MOUNTED ONE AT A TIME, so switching tabs re-reads that kind's
 * orders. That is the honest behaviour for a queue another department is
 * working: a radiology status the doctor left ten minutes ago may well have
 * moved, and showing a stale badge would be worse than a brief spinner.
 */
export default function OrdersWorkspace({
  appointmentId,
}: {
  appointmentId: number;
}) {
  /*
    THIS CONSULTATION'S diagnoses, fetched here rather than in each panel.

    Both indication pickers need them, and the server refuses any diagnosis from
    another consultation -- including the same patient's from an earlier visit.
    Reading them once keeps the two panels from issuing the same request twice,
    and keeps Diagnosis and Orders independent: neither section has to be
    mounted for the other to work.
  */
  const [diagnoses, setDiagnoses] = useState<DoctorDiagnosis[]>([]);
  const [kind, setKind] = useState<string>("laboratory");

  useEffect(() => {
    const controller = new AbortController();

    async function loadDiagnoses() {
      try {
        const response = await fetch(
          `/api/doctor/visits/${appointmentId}/diagnoses`,
          { cache: "no-store", signal: controller.signal },
        );
        const payload =
          (await response.json()) as ApiEnvelope<DoctorDiagnosisResponse>;
        if (controller.signal.aborted) return;
        if (response.ok && payload.success) {
          setDiagnoses(payload.data.diagnoses);
        }
      } catch {
        /* the indication pickers simply offer none; ordering is unaffected */
      }
    }

    void loadDiagnoses();
    return () => controller.abort();
  }, [appointmentId]);

  return (
    <div className="flex flex-col gap-3">
      {/* ---- Order kind sub-navigation ---- */}
      <div className="flex items-center gap-1 border-b border-slate-200">
        {ORDER_KINDS.map((entry) =>
          entry.live ? (
            <button
              key={entry.key}
              type="button"
              aria-current={kind === entry.key ? "page" : undefined}
              onClick={() => setKind(entry.key)}
              className={`-mb-px border-b-2 px-2 py-1.5 cl-meta font-bold uppercase tracking-[0.07em] outline-none transition-colors focus-visible:ring-2 focus-visible:ring-emerald-600 ${
                kind === entry.key
                  ? "border-emerald-600 text-slate-900"
                  : "border-transparent text-slate-500 hover:text-slate-800"
              }`}
            >
              {entry.label}
            </button>
          ) : (
            <span
              key={entry.key}
              title="Arrives in a later clinical slice"
              className="cursor-default border-b-2 border-transparent px-2 py-1.5 cl-meta font-semibold uppercase tracking-[0.07em] text-slate-400"
            >
              {entry.label}
            </span>
          ),
        )}
      </div>

      {kind === "radiology" ? (
        <RadiologyPanel appointmentId={appointmentId} diagnoses={diagnoses} />
      ) : kind === "medication" ? (
        <MedicationPanel appointmentId={appointmentId} diagnoses={diagnoses} />
      ) : (
        <LaboratoryPanel appointmentId={appointmentId} diagnoses={diagnoses} />
      )}
    </div>
  );
}
