"use client";

import { useCallback, useEffect, useMemo, useState } from "react";

import { messageFromPayload } from "@/lib/api-error";
import {
  EMPTY_FORM,
  buildAddPayload,
  buildUpdatePayload,
  diagnosisLabel,
  diseaseLabel,
  formFromDiagnosis,
  groupDiagnoses,
  isEmptyUpdate,
} from "@/lib/diagnosis-format";
import type { ApiEnvelope } from "@/types/doctor";
import type {
  DiagnosisForm,
  DiseaseCatalogueResponse,
  DiseaseOption,
  DoctorDiagnosis,
  DoctorDiagnosisResponse,
} from "@/types/doctor-diagnosis";
import {
  CERTAINTIES,
  DIAGNOSIS_PRIMARY_CONFLICT_CODE,
  DIAGNOSIS_TYPES,
  SEVERITIES,
  STATUSES,
} from "@/types/doctor-diagnosis";

/**
 * The DIAGNOSIS section of the active consultation.
 *
 * A SECTION, NOT A PAGE. It renders inside the same workspace shell as the
 * note, driven by local section state in the parent; the authoritative visit
 * and consultation state still come from the server, and nothing here is a
 * second workflow state machine.
 *
 * EVERY MUTATION ANSWERS WITH THE WHOLE LIST. Adding a primary changes what the
 * other rows may become, and removing one frees the primary slot, so the server
 * returns the full set after every write and this component replaces its state
 * with it. There is no client-side patching of an array, and therefore no way
 * for the browser's picture to drift from the record.
 *
 * THE PRIMARY CONFLICT IS SHOWN, NOT RESOLVED. The server refuses a second
 * primary and names the diagnosis holding the slot. Demoting that one is a
 * clinical decision, so the doctor makes it.
 */

type Busy = null | { kind: "add" } | { kind: "row"; id: number };

const TYPE_TONE: Record<string, string> = {
  primary: "border-emerald-300 bg-emerald-50 text-emerald-900",
  secondary: "border-slate-300 bg-slate-100 text-slate-700",
  differential: "border-sky-300 bg-sky-50 text-sky-900",
  history: "border-slate-300 bg-slate-100 text-slate-600",
};

/** Provisional is NOT a warning. It is a normal, expected clinical state. */
const CERTAINTY_TONE: Record<string, string> = {
  provisional: "border-slate-300 bg-white text-slate-600",
  final: "border-emerald-300 bg-emerald-50 text-emerald-800",
};

function Badge({ value, tone }: { value: string | null; tone: string }) {
  if (!value) return null;
  return (
    <span
      className={`inline-flex shrink-0 items-center rounded border px-1.5 py-px text-[9px] font-bold uppercase tracking-wide ${tone}`}
    >
      {diagnosisLabel(value)}
    </span>
  );
}

function Select({
  label,
  value,
  options,
  allowEmpty,
  disabled,
  onChange,
}: {
  label: string;
  value: string;
  options: readonly string[];
  allowEmpty?: boolean;
  disabled: boolean;
  onChange: (value: string) => void;
}) {
  return (
    <label className="flex min-w-0 flex-col gap-0.5">
      <span className="text-[9px] font-bold uppercase tracking-[0.07em] text-slate-500">
        {label}
      </span>
      <select
        value={value}
        disabled={disabled}
        onChange={(event) => onChange(event.target.value)}
        className="h-7 min-w-0 rounded border border-slate-300 bg-white px-1.5 text-[11.5px] font-semibold text-slate-800 outline-none focus-visible:border-emerald-600 focus-visible:ring-1 focus-visible:ring-emerald-600 disabled:cursor-not-allowed disabled:bg-slate-100 disabled:text-slate-500"
      >
        {allowEmpty ? <option value="">—</option> : null}
        {options.map((option) => (
          <option key={option} value={option}>
            {diagnosisLabel(option)}
          </option>
        ))}
      </select>
    </label>
  );
}

/** The shared control block for both the add form and an inline edit. */
function FormFields({
  form,
  disabled,
  onChange,
}: {
  form: DiagnosisForm;
  disabled: boolean;
  onChange: (next: DiagnosisForm) => void;
}) {
  return (
    <>
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
        <Select
          label="Type"
          value={form.diagnosis_type}
          options={DIAGNOSIS_TYPES}
          disabled={disabled}
          onChange={(value) =>
            onChange({ ...form, diagnosis_type: value as DiagnosisForm["diagnosis_type"] })
          }
        />
        <Select
          label="Certainty"
          value={form.certainty}
          options={CERTAINTIES}
          disabled={disabled}
          onChange={(value) =>
            onChange({ ...form, certainty: value as DiagnosisForm["certainty"] })
          }
        />
        <Select
          label="Severity"
          value={form.severity}
          options={SEVERITIES}
          allowEmpty
          disabled={disabled}
          onChange={(value) => onChange({ ...form, severity: value })}
        />
        <Select
          label="Status"
          value={form.status}
          options={STATUSES}
          allowEmpty
          disabled={disabled}
          onChange={(value) => onChange({ ...form, status: value })}
        />
      </div>
      <textarea
        value={form.notes}
        rows={2}
        disabled={disabled}
        placeholder="Clinical comment (optional)…"
        onChange={(event) => onChange({ ...form, notes: event.target.value })}
        className="w-full resize-y rounded border border-slate-300 bg-white px-2 py-1.5 text-[12px] leading-relaxed text-slate-900 caret-emerald-700 outline-none placeholder:text-slate-400 focus-visible:border-emerald-600 focus-visible:ring-1 focus-visible:ring-emerald-600 disabled:cursor-not-allowed disabled:bg-slate-100"
      />
    </>
  );
}

export default function DiagnosisWorkspace({
  appointmentId,
}: {
  appointmentId: number;
}) {
  const [rows, setRows] = useState<DoctorDiagnosis[]>([]);
  const [editable, setEditable] = useState(false);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [busy, setBusy] = useState<Busy>(null);

  // Search + add
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<DiseaseOption[]>([]);
  const [truncated, setTruncated] = useState(false);
  const [searching, setSearching] = useState(false);
  const [picked, setPicked] = useState<DiseaseOption | null>(null);
  const [addForm, setAddForm] = useState<DiagnosisForm>(EMPTY_FORM);

  // Inline edit / remove
  const [editingId, setEditingId] = useState<number | null>(null);
  const [editForm, setEditForm] = useState<DiagnosisForm>(EMPTY_FORM);
  const [confirmRemoveId, setConfirmRemoveId] = useState<number | null>(null);

  const applyResponse = useCallback((data: DoctorDiagnosisResponse) => {
    setRows(data.diagnoses);
    setEditable(data.editable);
  }, []);

  /* ---------------- load ---------------- */
  useEffect(() => {
    const controller = new AbortController();

    async function load() {
      setLoading(true);
      setLoadError(null);
      try {
        const response = await fetch(
          `/api/doctor/visits/${appointmentId}/diagnoses`,
          { cache: "no-store", signal: controller.signal },
        );
        const payload =
          (await response.json()) as ApiEnvelope<DoctorDiagnosisResponse>;
        if (controller.signal.aborted) return;
        if (!response.ok || !payload.success) {
          setLoadError(messageFromPayload(payload, "Unable to load diagnoses."));
          return;
        }
        applyResponse(payload.data);
      } catch {
        if (!controller.signal.aborted) {
          setLoadError("Unable to reach the diagnosis service.");
        }
      } finally {
        if (!controller.signal.aborted) setLoading(false);
      }
    }

    void load();
    return () => controller.abort();
  }, [appointmentId, applyResponse]);

  /* ---------------- catalogue search ---------------- */
  useEffect(() => {
    const term = query.trim();
    // Below the threshold there is nothing to fetch AND nothing to clear:
    // `visibleResults` derives emptiness from the query during render, so this
    // effect never writes state just to blank the list. Clearing here would be
    // a synchronous setState in an effect body, which cascades a render and is
    // exactly what react-hooks/set-state-in-effect exists to catch.
    if (term.length < 2) return;

    const controller = new AbortController();
    // Debounced: a clinician types faster than a round trip, and every
    // keystroke firing a query would queue responses that arrive out of order.
    const timer = setTimeout(async () => {
      setSearching(true);
      try {
        const response = await fetch(
          `/api/doctor/catalogue/diseases?q=${encodeURIComponent(term)}`,
          { cache: "no-store", signal: controller.signal },
        );
        const payload =
          (await response.json()) as ApiEnvelope<DiseaseCatalogueResponse>;
        if (controller.signal.aborted) return;
        if (response.ok && payload.success) {
          setResults(payload.data.diseases);
          setTruncated(payload.data.truncated);
        }
      } catch {
        /* the picker simply shows nothing; the add path is unaffected */
      } finally {
        if (!controller.signal.aborted) setSearching(false);
      }
    }, 220);

    return () => {
      controller.abort();
      clearTimeout(timer);
    };
  }, [query]);

  /* ---------------- mutations ---------------- */
  const runMutation = useCallback(
    async (url: string, body: unknown, marker: Busy) => {
      setBusy(marker);
      setActionError(null);
      try {
        const response = await fetch(url, {
          method: "POST",
          cache: "no-store",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body ?? {}),
        });
        const payload =
          (await response.json()) as ApiEnvelope<DoctorDiagnosisResponse>;

        if (!response.ok || !payload.success) {
          const code = payload.success === false ? payload.error.code : null;
          setActionError(
            messageFromPayload(
              payload,
              code === DIAGNOSIS_PRIMARY_CONFLICT_CODE
                ? "This consultation already has a primary diagnosis."
                : "The diagnosis could not be saved.",
            ),
          );
          return false;
        }
        applyResponse(payload.data);
        return true;
      } catch {
        setActionError("Unable to reach the diagnosis service.");
        return false;
      } finally {
        setBusy(null);
      }
    },
    [applyResponse],
  );

  const add = useCallback(async () => {
    if (!picked) return;
    const token =
      globalThis.crypto?.randomUUID?.() ??
      `${Date.now()}-${Math.random().toString(16).slice(2)}`;
    const ok = await runMutation(
      `/api/doctor/visits/${appointmentId}/diagnoses`,
      buildAddPayload(picked.id, addForm, token),
      { kind: "add" },
    );
    if (ok) {
      setPicked(null);
      setAddForm(EMPTY_FORM);
      setQuery("");
      setResults([]);
    }
  }, [addForm, appointmentId, picked, runMutation]);

  const saveEdit = useCallback(
    async (row: DoctorDiagnosis) => {
      const payload = buildUpdatePayload(editForm, formFromDiagnosis(row));
      if (isEmptyUpdate(payload)) {
        setEditingId(null);
        return;
      }
      const ok = await runMutation(
        `/api/doctor/visits/${appointmentId}/diagnoses/${row.id}/update`,
        payload,
        { kind: "row", id: row.id },
      );
      if (ok) setEditingId(null);
    },
    [appointmentId, editForm, runMutation],
  );

  const remove = useCallback(
    async (row: DoctorDiagnosis) => {
      const ok = await runMutation(
        `/api/doctor/visits/${appointmentId}/diagnoses/${row.id}/remove`,
        {},
        { kind: "row", id: row.id },
      );
      if (ok) setConfirmRemoveId(null);
    },
    [appointmentId, runMutation],
  );

  const groups = useMemo(() => groupDiagnoses(rows), [rows]);
  const addBusy = busy?.kind === "add";
  /*
    DERIVED, not stored. The last fetched result set stays in state, but it is
    only shown while the query still justifies it -- so clearing the box hides
    the list during the same render, with no effect and no stale flash.
  */
  const searchTerm = query.trim();
  const visibleResults = searchTerm.length >= 2 ? results : [];

  if (loading && rows.length === 0) {
    return (
      <p className="py-8 text-center text-xs text-slate-500">Loading diagnoses…</p>
    );
  }

  return (
    <div className="flex flex-col gap-3">
      {loadError ? (
        <p
          role="alert"
          className="rounded-md border border-amber-300 bg-amber-50 px-3 py-2 text-[11px] leading-snug text-amber-900"
        >
          {loadError}
        </p>
      ) : null}

      {actionError ? (
        <p
          role="alert"
          className="rounded-md border border-red-300 bg-red-50 px-3 py-2 text-[11px] leading-snug text-red-900"
        >
          {actionError}
        </p>
      ) : null}

      {!editable ? (
        <p className="rounded-md border border-slate-300 bg-white px-3 py-2 text-[11px] leading-snug text-slate-700">
          This consultation is completed. Its diagnoses are locked.
        </p>
      ) : null}

      {/* ---- Recorded diagnoses ---- */}
      {groups.length === 0 ? (
        <p className="text-[12px] text-slate-500">
          No diagnoses recorded for this consultation.
        </p>
      ) : (
        <div className="flex flex-col gap-2.5">
          {groups.map((group) => (
            <section key={group.type} className="flex flex-col gap-1">
              <div className="flex items-center gap-2">
                <h3 className="text-[9px] font-bold uppercase tracking-[0.08em] text-slate-500">
                  {group.label}
                </h3>
                <span aria-hidden className="h-px flex-1 bg-slate-200" />
              </div>

              <ul className="flex flex-col gap-1.5">
                {group.rows.map((row) => {
                  const rowBusy = busy?.kind === "row" && busy.id === row.id;
                  const isEditing = editingId === row.id;
                  return (
                    <li
                      key={row.id}
                      className={`rounded-lg border px-2.5 py-2 ${
                        row.diagnosis_type === "primary"
                          ? "border-l-[3px] border-l-emerald-600 border-slate-200 bg-white"
                          : "border-slate-200 bg-white"
                      }`}
                    >
                      <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
                        <span className="min-w-0 flex-1 truncate text-[12.5px] font-semibold text-slate-900">
                          {diseaseLabel(row)}
                        </span>
                        <Badge
                          value={row.diagnosis_type}
                          tone={TYPE_TONE[row.diagnosis_type ?? ""] ?? TYPE_TONE.secondary}
                        />
                        <Badge
                          value={row.certainty}
                          tone={
                            CERTAINTY_TONE[row.certainty ?? ""] ??
                            CERTAINTY_TONE.provisional
                          }
                        />
                        {row.severity ? (
                          <span className="text-[10px] font-semibold text-slate-500">
                            {diagnosisLabel(row.severity)}
                          </span>
                        ) : null}
                        {row.status ? (
                          <span className="text-[10px] text-slate-500">
                            {diagnosisLabel(row.status)}
                          </span>
                        ) : null}

                        {editable && row.editable ? (
                          <span className="flex shrink-0 items-center gap-1">
                            <button
                              type="button"
                              disabled={rowBusy}
                              onClick={() => {
                                setEditingId(isEditing ? null : row.id);
                                setEditForm(formFromDiagnosis(row));
                                setConfirmRemoveId(null);
                              }}
                              className="rounded border border-slate-300 px-1.5 py-0.5 text-[9px] font-bold uppercase tracking-wide text-slate-600 outline-none hover:bg-slate-50 focus-visible:ring-2 focus-visible:ring-emerald-600 disabled:opacity-60"
                            >
                              {isEditing ? "Close" : "Edit"}
                            </button>
                            {confirmRemoveId === row.id ? (
                              <>
                                <button
                                  type="button"
                                  disabled={rowBusy}
                                  onClick={() => void remove(row)}
                                  className="rounded border border-red-400 bg-red-50 px-1.5 py-0.5 text-[9px] font-bold uppercase tracking-wide text-red-800 outline-none hover:bg-red-100 focus-visible:ring-2 focus-visible:ring-red-600 disabled:opacity-60"
                                >
                                  {rowBusy ? "Removing…" : "Confirm"}
                                </button>
                                <button
                                  type="button"
                                  onClick={() => setConfirmRemoveId(null)}
                                  className="rounded border border-slate-300 px-1.5 py-0.5 text-[9px] font-bold uppercase tracking-wide text-slate-600 outline-none hover:bg-slate-50"
                                >
                                  Cancel
                                </button>
                              </>
                            ) : (
                              // Inline confirmation, not window.confirm(): a
                              // native dialog blocks the whole tab and cannot
                              // be styled or dismissed by keyboard consistently.
                              <button
                                type="button"
                                disabled={rowBusy}
                                onClick={() => {
                                  setConfirmRemoveId(row.id);
                                  setEditingId(null);
                                }}
                                className="rounded border border-slate-300 px-1.5 py-0.5 text-[9px] font-bold uppercase tracking-wide text-slate-600 outline-none hover:border-red-300 hover:bg-red-50 hover:text-red-800 focus-visible:ring-2 focus-visible:ring-red-600 disabled:opacity-60"
                              >
                                Remove
                              </button>
                            )}
                          </span>
                        ) : null}
                      </div>

                      {row.notes && !isEditing ? (
                        <p className="mt-1 whitespace-pre-wrap text-[11px] leading-relaxed text-slate-600">
                          {row.notes}
                        </p>
                      ) : null}

                      {isEditing ? (
                        <div className="mt-2 flex flex-col gap-2 border-t border-slate-200 pt-2">
                          <FormFields
                            form={editForm}
                            disabled={rowBusy}
                            onChange={setEditForm}
                          />
                          <div className="flex justify-end gap-1.5">
                            <button
                              type="button"
                              onClick={() => setEditingId(null)}
                              className="h-7 rounded border border-slate-300 px-2.5 text-[10px] font-bold uppercase tracking-wide text-slate-600 outline-none hover:bg-slate-50"
                            >
                              Cancel
                            </button>
                            <button
                              type="button"
                              disabled={rowBusy}
                              onClick={() => void saveEdit(row)}
                              className="h-7 rounded bg-emerald-700 px-3 text-[10px] font-bold uppercase tracking-wide text-white outline-none hover:bg-emerald-800 focus-visible:ring-2 focus-visible:ring-emerald-700 disabled:bg-slate-200 disabled:text-slate-500"
                            >
                              {rowBusy ? "Saving…" : "Save"}
                            </button>
                          </div>
                        </div>
                      ) : null}
                    </li>
                  );
                })}
              </ul>
            </section>
          ))}
        </div>
      )}

      {/* ---- Add ---- */}
      {editable ? (
        <div className="flex flex-col gap-2 rounded-lg border border-slate-200 bg-slate-50/60 px-2.5 py-2">
          <div className="flex items-center gap-2">
            <h3 className="text-[9px] font-bold uppercase tracking-[0.08em] text-slate-500">
              Add diagnosis
            </h3>
            <span aria-hidden className="h-px flex-1 bg-slate-200" />
            {searching ? (
              <span className="text-[9px] text-slate-400">Searching…</span>
            ) : null}
          </div>

          {picked ? (
            <div className="flex flex-col gap-2">
              <div className="flex items-center gap-2">
                <span className="min-w-0 flex-1 truncate text-[12.5px] font-semibold text-slate-900">
                  {picked.code ? `${picked.name} (${picked.code})` : picked.name}
                </span>
                <button
                  type="button"
                  onClick={() => setPicked(null)}
                  className="shrink-0 rounded border border-slate-300 px-1.5 py-0.5 text-[9px] font-bold uppercase tracking-wide text-slate-600 outline-none hover:bg-white"
                >
                  Change
                </button>
              </div>
              <FormFields form={addForm} disabled={addBusy} onChange={setAddForm} />
              <div className="flex justify-end">
                <button
                  type="button"
                  disabled={addBusy}
                  onClick={() => void add()}
                  className="h-8 rounded-md bg-emerald-700 px-4 text-[11px] font-bold uppercase tracking-[0.06em] text-white shadow-sm outline-none hover:bg-emerald-800 focus-visible:ring-2 focus-visible:ring-emerald-700 disabled:bg-slate-200 disabled:text-slate-500"
                >
                  {addBusy ? "Recording…" : "Record diagnosis"}
                </button>
              </div>
            </div>
          ) : (
            <>
              <input
                type="search"
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                placeholder="Search the diagnosis catalogue by name or code…"
                className="h-8 w-full rounded border border-slate-300 bg-white px-2.5 text-[12px] text-slate-900 outline-none placeholder:text-slate-400 focus-visible:border-emerald-600 focus-visible:ring-1 focus-visible:ring-emerald-600"
              />
              {searchTerm.length >= 2 && visibleResults.length === 0 && !searching ? (
                <p className="text-[11px] text-slate-500">No matching diagnosis.</p>
              ) : null}
              {visibleResults.length > 0 ? (
                <ul className="max-h-52 overflow-y-auto rounded border border-slate-200 bg-white">
                  {visibleResults.map((disease) => (
                    <li key={disease.id}>
                      <button
                        type="button"
                        onClick={() => setPicked(disease)}
                        className="flex w-full items-baseline gap-2 border-b border-slate-100 px-2.5 py-1.5 text-left outline-none last:border-b-0 hover:bg-emerald-50/70 focus-visible:bg-emerald-50"
                      >
                        <span className="min-w-0 flex-1 truncate text-[12px] font-semibold text-slate-800">
                          {disease.name}
                        </span>
                        {disease.code ? (
                          <span className="shrink-0 font-mono text-[10px] text-slate-500">
                            {disease.code}
                          </span>
                        ) : null}
                      </button>
                    </li>
                  ))}
                </ul>
              ) : null}
              {truncated && visibleResults.length > 0 ? (
                <p className="text-[9px] text-slate-400">
                  Showing the first matches only. Refine your search to narrow it.
                </p>
              ) : null}
            </>
          )}
        </div>
      ) : null}
    </div>
  );
}
