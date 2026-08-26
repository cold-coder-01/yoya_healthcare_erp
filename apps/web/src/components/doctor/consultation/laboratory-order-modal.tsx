"use client";

import { useCallback, useRef, useState } from "react";

import {
  labPriorityLabel,
  selectionSummary,
  removeTest,
  testCountLabel,
  testLabel,
} from "@/lib/laboratory-format";
import { labDraftChanged } from "@/lib/order-draft-format";
import type { LabOrderDraft } from "@/lib/order-draft-format";
import type { DoctorDiagnosis } from "@/types/doctor-diagnosis";
import type { LabOrderForm } from "@/types/doctor-laboratory";
import { LAB_PRIORITIES } from "@/types/doctor-laboratory";

import ClinicalOrderModal from "./clinical-order-modal";

/**
 * THE LABORATORY REQUEST BEING COMPOSED, in a centred editor.
 *
 * WHY THE WHOLE REQUEST AND NOT ONE TEST. Priority, indication and clinical
 * notes belong to the REQUEST in hospital.laboratory.request, not to a test
 * line -- one submission raises one request carrying N tests under one set of
 * clinical context. An editor per test would have to invent per-test fields the
 * model does not have, so this holds the tests as removable chips and collects
 * the request's own fields once.
 *
 * NO NEW FIELDS. Priority, diagnosis and clinical notes are exactly what the
 * panel collected inline before, and exactly what buildOrderPayload sends. The
 * change is where they are collected, not what is sent.
 *
 * NOTHING IS SUBMITTED FROM HERE. Save commits the draft to the consultation,
 * where it survives tab switching; the one request that leaves the desk is
 * still Place lab order in the panel behind this dialog.
 */

const FIELD_CLASS =
  "h-9 w-full rounded-md border border-slate-300 bg-white px-2.5 cl-body text-slate-900 outline-none placeholder:text-slate-500 focus-visible:border-emerald-600 focus-visible:ring-2 focus-visible:ring-emerald-600/25";

const TEXTAREA_CLASS =
  "w-full resize-none rounded-md border border-slate-300 bg-white px-2.5 py-2 cl-body leading-[1.55] text-slate-900 caret-emerald-700 outline-none placeholder:text-slate-500 focus-visible:border-emerald-600 focus-visible:ring-2 focus-visible:ring-emerald-600/25";

export default function LaboratoryOrderModal({
  entry,
  mode,
  diagnoses,
  readOnly = false,
  returnFocus,
  onSave,
  onClose,
}: {
  entry: LabOrderDraft;
  mode: "add" | "edit";
  diagnoses: DoctorDiagnosis[];
  readOnly?: boolean;
  returnFocus: HTMLElement | null;
  onSave: (draft: LabOrderDraft) => void;
  onClose: () => void;
}) {
  const [draft, setDraft] = useState(entry);
  const priorityRef = useRef<HTMLSelectElement>(null);
  const changed = labDraftChanged(entry, draft);

  const patchForm = useCallback((values: Partial<LabOrderForm>) => {
    setDraft((current) => ({ ...current, form: { ...current.form, ...values } }));
  }, []);

  const save = useCallback(() => {
    if (draft.tests.length === 0) return;
    onSave(draft);
  }, [draft, onSave]);

  const summary = selectionSummary(draft.tests);

  return (
    <ClinicalOrderModal
      title={summary || "Laboratory request"}
      subtitle={`Laboratory request · ${testCountLabel(draft.tests.length)}`}
      changed={changed}
      readOnly={readOnly}
      saveDisabled={draft.tests.length === 0}
      saveLabel={mode === "add" ? "Add to order" : "Save Changes"}
      shortcutHint={`Ctrl+Enter ${mode === "add" ? "adds to the order" : "saves changes"}`}
      discardPrompt="This laboratory order has unsaved changes. Discard them?"
      closeLabel="Close laboratory order editor"
      initialFocusRef={priorityRef}
      returnFocus={returnFocus}
      onSave={save}
      onClose={onClose}
    >
      <div className="flex flex-col gap-3">
        {/* ---- Tests on this request ---- */}
        <div className="flex flex-col gap-1">
          <span className="cl-secondary font-semibold text-slate-700">
            Tests on this request
          </span>
          {draft.tests.length === 0 ? (
            /* Not an error and not styled as one: the doctor removed the last
               test and can put one back, or cancel. Saving is simply refused
               until there is something to order. */
            <p className="rounded-md border border-amber-300 bg-amber-50 px-2.5 py-1.5 cl-secondary leading-snug text-amber-900">
              No tests left on this request. Add at least one test, or cancel to
              keep the order as it was.
            </p>
          ) : (
            <ul className="flex flex-wrap gap-1.5">
              {draft.tests.map((test) => (
                <li
                  key={test.id}
                  className="inline-flex items-center gap-1.5 rounded border border-emerald-300 bg-emerald-50 px-2 py-0.5 cl-secondary font-semibold text-emerald-900"
                >
                  {testLabel(test)}
                  {readOnly ? null : (
                    <button
                      type="button"
                      aria-label={`Remove ${test.name}`}
                      onClick={() =>
                        setDraft((current) => ({
                          ...current,
                          tests: removeTest(current.tests, test.id),
                        }))
                      }
                      className="cl-secondary font-bold text-emerald-700 outline-none hover:text-emerald-900 focus-visible:ring-1 focus-visible:ring-emerald-600"
                    >
                      ×
                    </button>
                  )}
                </li>
              ))}
            </ul>
          )}
        </div>

        <div className="grid grid-cols-1 gap-x-4 gap-y-3 sm:grid-cols-2">
          <label className="flex min-w-0 flex-col gap-1">
            <span className="cl-secondary font-semibold text-slate-700">
              Priority
            </span>
            <select
              ref={priorityRef}
              value={draft.form.priority}
              disabled={readOnly}
              onChange={(event) =>
                patchForm({ priority: event.target.value as LabOrderForm["priority"] })
              }
              className={`${FIELD_CLASS} font-semibold`}
            >
              {LAB_PRIORITIES.map((priority) => (
                <option key={priority} value={priority}>
                  {labPriorityLabel(priority)}
                </option>
              ))}
            </select>
          </label>

          <label className="flex min-w-0 flex-col gap-1">
            <span className="cl-secondary font-semibold text-slate-700">
              Indication (diagnosis)
            </span>
            <select
              value={draft.form.diagnosis_id ?? ""}
              disabled={readOnly}
              onChange={(event) =>
                patchForm({
                  diagnosis_id: event.target.value
                    ? Number(event.target.value)
                    : null,
                })
              }
              className={`${FIELD_CLASS} font-semibold`}
            >
              <option value="">— none —</option>
              {/* Only THIS consultation's diagnoses. The server refuses any
                  other, including the same patient's diagnosis from an earlier
                  visit. */}
              {diagnoses.map((diagnosis) => (
                <option key={diagnosis.id} value={diagnosis.id}>
                  {diagnosis.disease?.name ?? "Diagnosis"}
                </option>
              ))}
            </select>
          </label>

          <label className="flex min-w-0 flex-col gap-1 sm:col-span-2">
            <span className="cl-secondary font-semibold text-slate-700">
              Clinical indication
            </span>
            <textarea
              value={draft.form.clinical_notes}
              rows={3}
              disabled={readOnly}
              placeholder="Why this test is being requested (optional)…"
              onChange={(event) =>
                patchForm({ clinical_notes: event.target.value })
              }
              className={TEXTAREA_CLASS}
            />
          </label>
        </div>
      </div>
    </ClinicalOrderModal>
  );
}
