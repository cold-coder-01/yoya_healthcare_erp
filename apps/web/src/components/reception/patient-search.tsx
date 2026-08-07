"use client";

/**
 * Patient lookup for guided registration.
 *
 * Searches the reception-safe endpoint, which returns scalar identity fields
 * only -- no clinical or stock relations. MRN is displayed but never entered:
 * it is sequence-assigned by Odoo and the API rejects any attempt to supply it.
 */
import { useEffect, useState } from "react";

import { messageFromPayload } from "@/lib/api-error";
import { formatCardStatus, formatGender } from "@/lib/reception-format";
import type {
  ApiEnvelope,
  ReceptionPatientSearchResult,
} from "@/types/reception";

const MIN_QUERY = 2;

export default function PatientSearch({
  selected,
  onSelect,
}: {
  selected: ReceptionPatientSearchResult | null;
  onSelect: (patient: ReceptionPatientSearchResult) => void;
}) {
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<ReceptionPatientSearchResult[]>([]);
  const [searching, setSearching] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [searched, setSearched] = useState(false);

  const trimmed = query.trim();
  const tooShort = trimmed.length > 0 && trimmed.length < MIN_QUERY;

  // Below the minimum length the effect simply does nothing and the render
  // path hides any stale results (see `showResults`). Clearing state from the
  // effect body instead would trigger a cascading render.
  useEffect(() => {
    if (trimmed.length < MIN_QUERY) {
      return;
    }

    const controller = new AbortController();
    const timer = setTimeout(() => {
      setSearching(true);
      async function run() {
        try {
          const response = await fetch(
            `/api/reception/patients/search?q=${encodeURIComponent(trimmed)}`,
            { cache: "no-store", signal: controller.signal },
          );
          const payload = (await response.json()) as ApiEnvelope<{
            patients: ReceptionPatientSearchResult[];
          }>;
          if (controller.signal.aborted) return;

          if (!response.ok || !payload.success) {
            setError(messageFromPayload(payload, "Unable to search patients."));
            setResults([]);
            return;
          }
          setError(null);
          setResults(payload.data.patients ?? []);
        } catch {
          if (controller.signal.aborted) return;
          setError("Unable to reach the patient search service.");
          setResults([]);
        } finally {
          if (!controller.signal.aborted) {
            setSearching(false);
            setSearched(true);
          }
        }
      }
      void run();
    }, 250);

    return () => {
      controller.abort();
      clearTimeout(timer);
    };
  }, [trimmed]);

  // Derived, not stored: results from an earlier longer query must not show
  // once the box is cleared back below the threshold.
  const showResults = trimmed.length >= MIN_QUERY;
  const visibleResults = showResults ? results : [];

  return (
    <div className="space-y-4">
      <label className="block text-sm font-medium text-slate-700">
        Find patient
        <input
          type="search"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder="MRN, name, phone or mobile"
          className="mt-2 h-11 w-full rounded-md border border-slate-300 px-3 text-sm"
        />
        <span className="mt-1 block text-xs font-normal text-slate-500">
          {tooShort
            ? `Type at least ${MIN_QUERY} characters.`
            : "Existing patients are not charged a new card fee."}
        </span>
      </label>

      {showResults && error ? (
        <p className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-800">
          {error}
        </p>
      ) : null}

      {showResults && searching ? (
        <p className="text-sm text-slate-500">Searching…</p>
      ) : null}

      {showResults && !searching && searched && visibleResults.length === 0 ? (
        <p className="rounded-md border border-slate-200 bg-slate-50 px-3 py-3 text-sm text-slate-600">
          No patient matched “{trimmed}”. Use{" "}
          <span className="font-semibold">Register new patient</span> below if
          this is a first visit.
        </p>
      ) : null}

      {visibleResults.length > 0 ? (
        <ul className="divide-y divide-slate-100 overflow-hidden rounded-lg border border-slate-200">
          {visibleResults.map((patient) => {
            const isSelected = selected?.id === patient.id;
            return (
              <li key={patient.id}>
                <button
                  type="button"
                  onClick={() => onSelect(patient)}
                  aria-pressed={isSelected}
                  className={`flex w-full flex-wrap items-center justify-between gap-3 px-4 py-3 text-left transition ${
                    isSelected
                      ? "bg-emerald-50 ring-1 ring-inset ring-emerald-500"
                      : "bg-white hover:bg-slate-50"
                  }`}
                >
                  <span className="min-w-0">
                    <span className="block font-medium text-slate-900">
                      {patient.name}
                    </span>
                    <span className="block text-xs text-slate-500">
                      {patient.identification_code ?? "No MRN"} ·{" "}
                      {patient.age ?? "—"} yrs · {formatGender(patient.gender)}
                      {patient.phone || patient.mobile
                        ? ` · ${patient.mobile ?? patient.phone}`
                        : ""}
                    </span>
                  </span>
                  <span className="shrink-0 text-xs">
                    <span
                      className={`inline-flex items-center rounded-md px-2 py-0.5 font-semibold ring-1 ring-inset ${
                        patient.has_existing_first_card
                          ? "bg-emerald-100 text-emerald-900 ring-emerald-200"
                          : "bg-amber-100 text-amber-900 ring-amber-200"
                      }`}
                    >
                      {patient.has_existing_first_card
                        ? "Has card"
                        : formatCardStatus(patient.latest_card_status)}
                    </span>
                  </span>
                </button>
              </li>
            );
          })}
        </ul>
      ) : null}
    </div>
  );
}
