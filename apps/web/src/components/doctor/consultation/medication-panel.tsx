"use client";

import { useCallback, useEffect, useMemo, useState } from "react";

import { messageFromPayload } from "@/lib/api-error";
import {
  MED_STATUS_TONE,
  buildPrescriptionPayload,
  canSubmitPrescription,
  fulfilment,
  medicineContext,
  medicineCountLabel,
  medicineLabel,
  prescribedSummary,
  stageMedicine,
  stagedRegimenSummary,
  stagedErrors,
  unstageMedicine,
} from "@/lib/medication-format";
import { prescriptionToken } from "@/lib/order-draft-format";
import type { ApiEnvelope } from "@/types/doctor";
import type { DoctorDiagnosis } from "@/types/doctor-diagnosis";
import type {
  DoctorPrescription,
  DoctorPrescriptionResponse,
  MedCatalogueResponse,
  MedicineOption,
  StagedMedicine,
} from "@/types/doctor-medication";

import MedicationEditorModal from "./medication-editor-modal";
import { useMedOrderDraft } from "./order-draft-context";

/**
 * The MEDICATION tab of the ORDERS section.
 *
 * A SIBLING OF LaboratoryPanel AND RadiologyPanel, in the same dense clinical
 * style, differing where prescribing genuinely differs.
 *
 * THE ONE STRUCTURAL DIFFERENCE. A lab or imaging order is a set of picked
 * items; a prescription is a set of picked items EACH CARRYING ITS OWN
 * INSTRUCTIONS. So this panel stages a list of small forms rather than a list
 * of chips, and every staged row is editable and removable before submission.
 * The same drug may be staged twice -- a tapering course and a rescue dose are
 * two legitimate lines of one prescription -- which is why the picker does not
 * mark a medicine as already added.
 *
 * ONE PRESCRIPTION PER SUBMISSION, HOWEVER MANY MEDICINES, carrying ONE token.
 * The domain models a prescription as a header with many lines and composes
 * exactly one pharmacy dispense from it, so a five-drug prescription is one
 * trip to the counter. A token per row would defeat idempotency outright: a
 * retry would match some rows and not others and the server would write a
 * second prescription from the remainder.
 *
 * THE DESK WRITES A CLINICAL ORDER; IT NEVER CREATES A CHARGE. Prescribing
 * raises no charge at all -- medication is billed later by the pharmacist, at
 * Mark Ready, using the quantity they intend to hand over. Nothing here knows
 * what a drug costs and the payload carries no money.
 *
 * THE PICKER ONLY EVER OFFERS PRESCRIBABLE MEDICINES. The server filters to
 * those the pharmacy workflow can actually complete -- billing service AND
 * inventory mapping -- so a doctor is never shown a drug whose prescription
 * would be paid for and then refused at the counter.
 */

const FIELD_CLASS =
  "h-8 w-full rounded border border-slate-300 bg-white px-2 cl-body text-slate-900 outline-none placeholder:text-slate-500 focus-visible:border-emerald-600 focus-visible:ring-1 focus-visible:ring-emerald-600";

function newKey() {
  return (
    globalThis.crypto?.randomUUID?.() ??
    `${Date.now()}-${Math.random().toString(16).slice(2)}`
  );
}

export default function MedicationPanel({
  appointmentId,
  diagnoses,
}: {
  appointmentId: number;
  diagnoses: DoctorDiagnosis[];
}) {
  const [prescriptions, setPrescriptions] = useState<DoctorPrescription[]>([]);
  /*
    NULL UNTIL THE SERVER HAS SAID. Prescribing permission is the server's
    verdict, and "not yet known" is a third state that matters: a false starting
    value would read as "this consultation is completed" for the duration of
    every load, and the read-only rule below would discard the doctor's staged
    prescription on every tab switch -- the exact loss this slice exists to stop.
  */
  const [canOrder, setCanOrder] = useState<boolean | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [placing, setPlacing] = useState(false);
  const [cancellingId, setCancellingId] = useState<number | null>(null);
  const [confirmCancelId, setConfirmCancelId] = useState<number | null>(null);

  const [query, setQuery] = useState("");
  const [results, setResults] = useState<MedicineOption[]>([]);
  const [truncated, setTruncated] = useState(false);
  const [searching, setSearching] = useState(false);
  /*
    THE PRESCRIPTION BEING WRITTEN, owned by the consultation rather than by
    this panel: its staged medicines, its header fields and its submission
    token. Held here it died on every tab switch.
  */
  const { draft, setDraft, discard } = useMedOrderDraft();
  const staged = draft.staged;
  const form = draft.form;
  const [showErrors, setShowErrors] = useState(false);
  const [editor, setEditor] = useState<{
    entry: StagedMedicine;
    mode: "add" | "edit";
    returnFocus: HTMLElement;
  } | null>(null);

  /*
    THE TOKEN SURVIVES A FAILED ATTEMPT AND, NOW, A TAB SWITCH.

    It is minted once for a submission and kept until that submission
    definitively succeeds. A retry after a dropped response therefore carries
    the SAME token, which is the whole point: if the first attempt actually
    reached the server, the retry returns that prescription instead of writing a
    second one with a second pharmacy dispense behind it.

    IT USED TO LIVE IN A REF ON THIS COMPONENT, which meant the ORDERS tabs
    destroyed it -- a doctor whose Prescribe failed, who looked at the lab tab
    while deciding what to do, and who then retried, was sending a NEW token for
    the SAME prescription. It now travels with the draft it identifies. The
    token's meaning to the server is unchanged, and server-side uniqueness is
    still what actually enforces this; the disabled button below only stops the
    trivial double-click.
  */

  /*
    THE SERVER'S VERDICT, APPLIED WHERE IT ARRIVES.

    A COMPLETED CONSULTATION HAS NOTHING LEFT TO PRESCRIBE, so the same response
    that says so closes the editor and discards the unsent prescription. Not an
    effect watching `canOrder`: that would cascade a render, and a rule keyed on
    "canOrder is not true" would also fire while a load was in flight -- which
    is every tab switch -- and destroy the prescription on each one.

    This is one of the four things allowed to clear a draft. Tab navigation is
    not among them and never reaches here.
  */
  const applyResponse = useCallback(
    (data: DoctorPrescriptionResponse) => {
      setPrescriptions(data.prescriptions);
      setCanOrder(data.can_order);
      if (!data.can_order) {
        setEditor(null);
        discard();
      }
    },
    [discard],
  );

  /* ---------------- load ---------------- */
  useEffect(() => {
    const controller = new AbortController();

    async function load() {
      setLoading(true);
      setLoadError(null);
      try {
        const response = await fetch(
          `/api/doctor/visits/${appointmentId}/orders/medications`,
          { cache: "no-store", signal: controller.signal },
        );
        const payload =
          (await response.json()) as ApiEnvelope<DoctorPrescriptionResponse>;
        if (controller.signal.aborted) return;
        if (!response.ok || !payload.success) {
          setLoadError(
            messageFromPayload(payload, "Unable to load prescriptions."),
          );
          return;
        }
        applyResponse(payload.data);
      } catch {
        if (!controller.signal.aborted) {
          setLoadError("Unable to reach the pharmacy service.");
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
    // `visibleResults` derives emptiness during render, so this effect never
    // writes state just to blank the list.
    if (term.length < 2) return;

    const controller = new AbortController();
    // Debounced: a clinician types faster than a round trip, and a query per
    // keystroke would queue responses that arrive out of order.
    const timer = setTimeout(async () => {
      setSearching(true);
      try {
        const response = await fetch(
          `/api/doctor/catalogue/medicines?q=${encodeURIComponent(term)}`,
          { cache: "no-store", signal: controller.signal },
        );
        const payload =
          (await response.json()) as ApiEnvelope<MedCatalogueResponse>;
        if (controller.signal.aborted) return;
        if (response.ok && payload.success) {
          setResults(payload.data.medicines);
          setTruncated(payload.data.truncated);
        }
      } catch {
        /* the picker shows nothing; prescribing is unaffected */
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
  const prescribe = useCallback(async () => {
    const errors = stagedErrors(staged);
    if (Object.keys(errors).length > 0) {
      setShowErrors(true);
      return;
    }
    if (!staged.length) return;

    setPlacing(true);
    setActionError(null);
    /*
      Minted on the first attempt and reused on every later one. Written back
      into the draft immediately, so the token outlives a failure, a tab switch
      and the two together.
    */
    const token = prescriptionToken(draft, newKey);
    setDraft((current) => ({ ...current, token }));

    try {
      const response = await fetch(
        `/api/doctor/visits/${appointmentId}/orders/medications`,
        {
          method: "POST",
          cache: "no-store",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(buildPrescriptionPayload(staged, form, token)),
        },
      );
      const payload =
        (await response.json()) as ApiEnvelope<DoctorPrescriptionResponse>;
      if (!response.ok || !payload.success) {
        // The draft AND its token are KEPT, so the doctor still has the
        // prescription they wrote and a retry is still deduplicated
        // server-side.
        setActionError(
          messageFromPayload(payload, "The prescription could not be written."),
        );
        return;
      }
      applyResponse(payload.data);
      /*
        Definitive success: this submission is over, so the whole draft goes --
        staged medicines, header fields and the token with them. The next
        prescription is a new submission and needs a new token.
      */
      discard();
      setShowErrors(false);
      setQuery("");
      setResults([]);
    } catch {
      setActionError("Unable to reach the pharmacy service.");
    } finally {
      setPlacing(false);
    }
  }, [appointmentId, applyResponse, discard, draft, form, setDraft, staged]);

  const cancel = useCallback(
    async (prescription: DoctorPrescription) => {
      setCancellingId(prescription.id);
      setActionError(null);
      try {
        const response = await fetch(
          `/api/doctor/visits/${appointmentId}/orders/medications/${prescription.id}/cancel`,
          {
            method: "POST",
            cache: "no-store",
            headers: { "Content-Type": "application/json" },
            body: "{}",
          },
        );
        const payload =
          (await response.json()) as ApiEnvelope<DoctorPrescriptionResponse>;
        if (!response.ok || !payload.success) {
          // Odoo's own sentence: it names which gate refused, which no message
          // invented here could do.
          setActionError(
            messageFromPayload(payload, "The prescription could not be cancelled."),
          );
          return;
        }
        applyResponse(payload.data);
        setConfirmCancelId(null);
      } catch {
        setActionError("Unable to reach the pharmacy service.");
      } finally {
        setCancellingId(null);
      }
    },
    [appointmentId, applyResponse],
  );

  const searchTerm = query.trim();
  const visibleResults = searchTerm.length >= 2 ? results : [];
  const errors = useMemo(() => stagedErrors(staged), [staged]);
  const submittable = useMemo(
    () => canSubmitPrescription(staged, placing),
    [staged, placing],
  );

  const openNewMedicine = useCallback(
    (medicine: MedicineOption, returnFocus: HTMLElement) => {
      const [entry] = stageMedicine([], medicine, newKey());
      setEditor({ entry, mode: "add", returnFocus });
    },
    [],
  );

  const openStagedMedicine = useCallback(
    (entry: StagedMedicine, returnFocus: HTMLElement) => {
      setEditor({ entry, mode: "edit", returnFocus });
    },
    [],
  );

  const saveEditor = useCallback(
    (entry: StagedMedicine) => {
      setDraft((current) => ({
        ...current,
        staged:
          editor?.mode === "edit"
            ? current.staged.map((item) =>
                item.key === entry.key ? entry : item,
              )
            : [...current.staged, entry],
      }));
      setShowErrors(false);
      setEditor(null);
    },
    [editor?.mode, setDraft],
  );

  return (
    <div className="flex flex-col gap-3">
      {editor && canOrder !== false ? (
        <MedicationEditorModal
          key={editor.entry.key}
          entry={editor.entry}
          mode={editor.mode}
          readOnly={canOrder !== true}
          returnFocus={editor.returnFocus}
          onSave={saveEditor}
          onClose={() => setEditor(null)}
        />
      ) : null}

      {loadError ? (
        <p
          role="alert"
          className="rounded-md border border-amber-300 bg-amber-50 px-3 py-2 cl-secondary leading-snug text-amber-900"
        >
          {loadError}
        </p>
      ) : null}

      {actionError ? (
        <p
          role="alert"
          className="rounded-md border border-red-300 bg-red-50 px-3 py-2 cl-secondary leading-snug text-red-900"
        >
          {actionError}
        </p>
      ) : null}

      {loading && prescriptions.length === 0 ? (
        <p className="py-8 text-center cl-body text-slate-500">
          Loading prescriptions…
        </p>
      ) : (
        <>
          {/* ---- Write a prescription ---- */}
          {canOrder === true ? (
            <div className="flex flex-col gap-2 rounded-lg border border-slate-200 bg-slate-50/60 px-2.5 py-2">
              <div className="flex items-center gap-2">
                <h3 className="cl-meta font-bold uppercase tracking-[0.08em] text-slate-600">
                  New prescription
                </h3>
                <span aria-hidden className="h-px flex-1 bg-slate-200" />
                {searching ? (
                  <span className="cl-micro text-slate-400">Searching…</span>
                ) : null}
              </div>

              <input
                type="search"
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                aria-label="Search medicines"
                placeholder="Search medicines by name, code, generic or brand…"
                className="h-9 w-full rounded border border-slate-300 bg-white px-2.5 cl-body text-slate-900 outline-none placeholder:text-slate-500 focus-visible:border-emerald-600 focus-visible:ring-1 focus-visible:ring-emerald-600"
              />

              {searchTerm.length >= 2 &&
              visibleResults.length === 0 &&
              !searching ? (
                <p className="cl-secondary text-slate-500">
                  No matching medicine available for prescribing.
                </p>
              ) : null}

              {visibleResults.length > 0 ? (
                <ul className="max-h-44 overflow-y-auto rounded border border-slate-200 bg-white">
                  {visibleResults.map((medicine) => {
                    const context = medicineContext(medicine);
                    return (
                      <li key={medicine.id}>
                        <button
                          type="button"
                          onClick={(event) =>
                            openNewMedicine(medicine, event.currentTarget)
                          }
                          className="flex w-full items-baseline gap-2 border-b border-slate-100 px-2.5 py-1.5 text-left outline-none last:border-b-0 hover:bg-emerald-50/70 focus-visible:bg-emerald-50"
                        >
                          <span className="min-w-0 flex-1 truncate cl-body font-semibold text-slate-900">
                            {medicineLabel(medicine)}
                          </span>
                          {context ? (
                            <span className="shrink-0 cl-meta text-slate-500">
                              {context}
                            </span>
                          ) : null}
                          {medicine.code ? (
                            <span className="shrink-0 font-mono cl-meta text-slate-500">
                              {medicine.code}
                            </span>
                          ) : null}
                          {/* No "Added" state: the same drug may legitimately
                              be prescribed twice on one prescription. */}
                          <span className="shrink-0 cl-meta font-bold text-emerald-700">
                            +
                          </span>
                        </button>
                      </li>
                    );
                  })}
                </ul>
              ) : null}

              {truncated && visibleResults.length > 0 ? (
                <p className="cl-micro text-slate-400">
                  Showing the first matches only. Refine your search to narrow it.
                </p>
              ) : null}

              {/* ---- Staged medicines ---- */}
              {staged.length > 0 ? (
                <>
                  <ul className="flex flex-col gap-2">
                    {staged.map((entry) => {
                      const error = showErrors ? errors[entry.key] : undefined;
                      return (
                        <li
                          key={entry.key}
                          className="rounded-md border border-slate-200 bg-white px-3 py-2"
                        >
                          <div className="flex items-start gap-3">
                            <div className="min-w-0 flex-1">
                              <p className="truncate cl-strong font-semibold text-slate-900">
                                {medicineLabel(entry.medicine)}
                              </p>
                              <p className="truncate cl-secondary text-slate-700">
                                {stagedRegimenSummary(entry)}
                              </p>
                              {entry.instructions ? (
                                <p title={entry.instructions} className="truncate cl-secondary text-slate-500">
                                  {entry.instructions}
                                </p>
                              ) : null}
                            </div>
                            <button
                              type="button"
                              aria-label={`Edit ${entry.medicine.name}`}
                              onClick={(event) =>
                                openStagedMedicine(entry, event.currentTarget)
                              }
                              className="shrink-0 rounded border border-sky-300 bg-sky-50 px-2 py-0.5 cl-secondary font-semibold text-sky-800 outline-none hover:bg-sky-100 focus-visible:ring-2 focus-visible:ring-sky-600"
                            >
                              Edit
                            </button>
                            <button
                              type="button"
                              aria-label={`Remove ${entry.medicine.name}`}
                              onClick={() =>
                                setDraft((current) => ({
                                  ...current,
                                  staged: unstageMedicine(
                                    current.staged,
                                    entry.key,
                                  ),
                                }))
                              }
                              className="shrink-0 rounded border border-red-200 px-2 py-0.5 cl-secondary font-semibold text-red-700 outline-none hover:border-red-300 hover:bg-red-50 focus-visible:ring-2 focus-visible:ring-red-600"
                            >
                              Remove
                            </button>
                          </div>
                          {error ? (
                            <p
                              id={`qty-error-${entry.key}`}
                              role="alert"
                              className="mt-1 cl-secondary font-semibold text-red-700"
                            >
                              {error}
                            </p>
                          ) : null}
                        </li>
                      );
                    })}
                  </ul>

                  {/* ---- Prescription header ---- */}
                  <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
                    <label className="flex min-w-0 flex-col gap-0.5">
                      <span className="cl-meta font-bold uppercase tracking-[0.07em] text-slate-600">
                        Indication (diagnosis)
                      </span>
                      <select
                        value={form.diagnosis_id ?? ""}
                        onChange={(event) =>
                          setDraft((current) => ({
                            ...current,
                            form: {
                              ...current.form,
                              diagnosis_id: event.target.value
                                ? Number(event.target.value)
                                : null,
                            },
                          }))
                        }
                        className="h-8 rounded border border-slate-300 bg-white px-2 cl-body font-semibold text-slate-800 outline-none focus-visible:border-emerald-600 focus-visible:ring-1 focus-visible:ring-emerald-600"
                      >
                        <option value="">— none —</option>
                        {/* Only THIS consultation's diagnoses. The server
                            refuses any other, including the same patient's
                            diagnosis from an earlier visit. */}
                        {diagnoses.map((diagnosis) => (
                          <option key={diagnosis.id} value={diagnosis.id}>
                            {diagnosis.disease?.name ?? "Diagnosis"}
                          </option>
                        ))}
                      </select>
                    </label>

                    <label className="flex min-w-0 flex-col gap-0.5">
                      <span className="cl-meta font-bold uppercase tracking-[0.07em] text-slate-600">
                        Note for the pharmacy
                      </span>
                      <input
                        value={form.notes}
                        placeholder="Optional…"
                        onChange={(event) =>
                          setDraft((current) => ({
                            ...current,
                            form: { ...current.form, notes: event.target.value },
                          }))
                        }
                        className={FIELD_CLASS}
                      />
                    </label>
                  </div>

                  <div className="flex items-center justify-end gap-2">
                    <span className="cl-meta text-slate-500">
                      {medicineCountLabel(staged.length)} · one prescription
                    </span>
                    <button
                      type="button"
                      disabled={!submittable}
                      onClick={() => void prescribe()}
                      className="h-9 rounded-md bg-emerald-700 px-4 cl-body font-semibold text-white shadow-sm outline-none hover:bg-emerald-800 focus-visible:ring-2 focus-visible:ring-emerald-700 disabled:bg-slate-300 disabled:text-slate-600"
                    >
                      {placing ? "Prescribing…" : "Prescribe"}
                    </button>
                  </div>
                </>
              ) : null}
            </div>
          ) : canOrder === false ? (
            <div className="rounded-md border border-red-200 bg-red-50/60 px-3 py-2">
              <div className="flex items-center gap-2">
                <span className="rounded border border-red-300 bg-white px-1.5 py-px cl-micro font-bold uppercase tracking-wide text-red-800">
                  Read only
                </span>
                <p className="cl-secondary leading-snug text-red-900">
                  This consultation is completed. Medication details can be
                  reviewed but not changed.
                </p>
              </div>
            </div>
          ) : null}

          {/* ---- Current prescriptions ---- */}
          <div className="flex flex-col gap-1">
            <div className="flex items-center gap-2">
              <h3 className="cl-meta font-bold uppercase tracking-[0.08em] text-slate-600">
                Prescriptions
              </h3>
              <span aria-hidden className="h-px flex-1 bg-slate-200" />
            </div>

            {prescriptions.length === 0 ? (
              <p className="cl-body text-slate-600">
                No prescriptions for this consultation.
              </p>
            ) : (
              <ul className="flex flex-col gap-1.5">
                {prescriptions.map((prescription) => {
                  const busy = cancellingId === prescription.id;
                  return (
                    <li
                      key={prescription.id}
                      className="rounded-lg border border-slate-200 bg-white px-2.5 py-2"
                    >
                      <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
                        <span className="shrink-0 font-mono cl-meta font-semibold text-slate-500">
                          {prescription.prescription_code}
                        </span>
                        <span className="min-w-0 flex-1 cl-meta text-slate-500">
                          {medicineCountLabel(prescription.medicines.length)}
                        </span>
                        {/* The SERVER's label. Statuses are derived from the
                            linked pharmacy dispense, never from
                            prescription.state, which stays `confirmed` however
                            much has been handed over. */}
                        <span
                          className={`shrink-0 rounded border px-1.5 py-px cl-meta font-semibold uppercase tracking-wide ${
                            MED_STATUS_TONE[prescription.status] ??
                            MED_STATUS_TONE.draft
                          }`}
                        >
                          {prescription.status_label}
                        </span>

                        {prescription.cancellable ? (
                          confirmCancelId === prescription.id ? (
                            <span className="flex shrink-0 items-center gap-1">
                              <button
                                type="button"
                                disabled={busy}
                                onClick={() => void cancel(prescription)}
                                className="rounded border border-red-400 bg-red-50 px-2 py-0.5 cl-meta font-semibold text-red-800 outline-none hover:bg-red-100 focus-visible:ring-2 focus-visible:ring-red-600 disabled:opacity-60"
                              >
                                {busy ? "Cancelling…" : "Confirm"}
                              </button>
                              <button
                                type="button"
                                onClick={() => setConfirmCancelId(null)}
                                className="rounded border border-slate-300 px-2 py-0.5 cl-meta font-semibold text-slate-600 outline-none hover:bg-slate-50"
                              >
                                Keep
                              </button>
                            </span>
                          ) : (
                            <button
                              type="button"
                              onClick={() => setConfirmCancelId(prescription.id)}
                              className="shrink-0 rounded border border-slate-300 px-2 py-0.5 cl-meta font-semibold text-slate-600 outline-none hover:border-red-300 hover:bg-red-50 hover:text-red-800 focus-visible:ring-2 focus-visible:ring-red-600"
                            >
                              Cancel
                            </button>
                          )
                        ) : null}
                      </div>

                      <ul className="mt-1 flex flex-col gap-1">
                        {prescription.medicines.map((line) => {
                          const progress = fulfilment(line);
                          const summary = prescribedSummary(line);
                          return (
                            <li key={line.id} className="flex flex-col">
                              <div className="flex flex-wrap items-baseline gap-x-2">
                                <span className="cl-body font-semibold text-slate-900">
                                  {medicineLabel({
                                    name: line.name,
                                    strength: line.strength,
                                  })}
                                </span>
                                <span className="cl-secondary text-slate-700">
                                  Qty {line.quantity}
                                </span>
                                {progress ? (
                                  <span
                                    className={`cl-meta font-semibold ${
                                      progress.complete
                                        ? "text-emerald-800"
                                        : progress.started
                                          ? "text-amber-800"
                                          : "text-slate-500"
                                    }`}
                                  >
                                    {progress.dispensed} dispensed ·{" "}
                                    {progress.remaining} remaining
                                  </span>
                                ) : null}
                              </div>
                              {summary ? (
                                <span className="cl-secondary leading-[1.55] text-slate-700">
                                  {summary}
                                </span>
                              ) : null}
                              {line.instructions ? (
                                <span className="cl-secondary leading-[1.55] text-slate-600">
                                  {line.instructions}
                                </span>
                              ) : null}
                            </li>
                          );
                        })}
                      </ul>

                      {/* Said once per prescription rather than as a blank cell
                          on each line: the numbers are absent because the
                          server could not correlate them with certainty, and
                          silence about that would read as "nothing dispensed". */}
                      {!prescription.progress_itemised &&
                      prescription.status !== "awaiting_pharmacy" &&
                      prescription.status !== "cancelled" ? (
                        <p className="mt-0.5 cl-meta text-slate-500">
                          Per-medicine dispensing progress is not itemised for
                          this prescription. See the pharmacy record.
                        </p>
                      ) : null}

                      {prescription.diagnosis || prescription.notes ? (
                        <p className="mt-1 cl-secondary leading-[1.55] text-slate-700">
                          {prescription.diagnosis ? (
                            <span className="font-semibold text-slate-900">
                              {prescription.diagnosis.name}
                              {prescription.notes ? " · " : ""}
                            </span>
                          ) : null}
                          {prescription.notes ? (
                            <span className="whitespace-pre-wrap">
                              {prescription.notes}
                            </span>
                          ) : null}
                        </p>
                      ) : null}
                    </li>
                  );
                })}
              </ul>
            )}
          </div>
        </>
      )}
    </div>
  );
}
