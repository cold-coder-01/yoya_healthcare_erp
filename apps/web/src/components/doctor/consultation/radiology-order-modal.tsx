"use client";

import { useCallback, useRef, useState } from "react";

import { radDraftChanged } from "@/lib/order-draft-format";
import type { RadOrderDraft } from "@/lib/order-draft-format";
import {
  examCountLabel,
  examLabel,
  radPriorityLabel,
  removeExam,
  selectionNeedsContrast,
  selectionSummary,
} from "@/lib/radiology-format";
import type { DoctorDiagnosis } from "@/types/doctor-diagnosis";
import type { RadOrderForm } from "@/types/doctor-radiology";
import { RAD_PRIORITIES } from "@/types/doctor-radiology";

import ClinicalOrderModal from "./clinical-order-modal";

/**
 * THE RADIOLOGY REQUEST BEING COMPOSED, in a centred editor.
 *
 * A SIBLING OF LaboratoryOrderModal, differing where radiology genuinely
 * differs: it carries a second free-text field the imaging department reads
 * before the patient arrives, and it warns about contrast.
 *
 * CONTRAST IS SHOWN WHILE THE ORDER IS STILL BEING WRITTEN, not only after it
 * is placed. Contrast is patient preparation -- fasting, a cannula, a
 * renal-function check -- and the doctor composing the order is the one who has
 * to tell the patient before they leave the room.
 *
 * NO NEW FIELDS. Priority, diagnosis, clinical indication and preparation are
 * exactly what the panel collected inline before, and exactly what
 * buildOrderPayload sends.
 */

const FIELD_CLASS =
  "h-9 w-full rounded-md border border-slate-300 bg-white px-2.5 cl-body text-slate-900 outline-none placeholder:text-slate-500 focus-visible:border-emerald-600 focus-visible:ring-2 focus-visible:ring-emerald-600/25";

const TEXTAREA_CLASS =
  "w-full resize-none rounded-md border border-slate-300 bg-white px-2.5 py-2 cl-body leading-[1.55] text-slate-900 caret-emerald-700 outline-none placeholder:text-slate-500 focus-visible:border-emerald-600 focus-visible:ring-2 focus-visible:ring-emerald-600/25";

export default function RadiologyOrderModal({
  entry,
  mode,
  diagnoses,
  readOnly = false,
  returnFocus,
  onSave,
  onClose,
}: {
  entry: RadOrderDraft;
  mode: "add" | "edit";
  diagnoses: DoctorDiagnosis[];
  readOnly?: boolean;
  returnFocus: HTMLElement | null;
  onSave: (draft: RadOrderDraft) => void;
  onClose: () => void;
}) {
  const [draft, setDraft] = useState(entry);
  const priorityRef = useRef<HTMLSelectElement>(null);
  const changed = radDraftChanged(entry, draft);

  const patchForm = useCallback((values: Partial<RadOrderForm>) => {
    setDraft((current) => ({ ...current, form: { ...current.form, ...values } }));
  }, []);

  const save = useCallback(() => {
    if (draft.exams.length === 0) return;
    onSave(draft);
  }, [draft, onSave]);

  const summary = selectionSummary(draft.exams);
  const contrastPending = selectionNeedsContrast(draft.exams);

  return (
    <ClinicalOrderModal
      title={summary || "Radiology request"}
      subtitle={`Radiology request · ${examCountLabel(draft.exams.length)}`}
      changed={changed}
      readOnly={readOnly}
      saveDisabled={draft.exams.length === 0}
      saveLabel={mode === "add" ? "Add to order" : "Save Changes"}
      shortcutHint={`Ctrl+Enter ${mode === "add" ? "adds to the order" : "saves changes"}`}
      discardPrompt="This radiology order has unsaved changes. Discard them?"
      closeLabel="Close radiology order editor"
      initialFocusRef={priorityRef}
      returnFocus={returnFocus}
      onSave={save}
      onClose={onClose}
    >
      <div className="flex flex-col gap-3">
        {/* ---- Studies on this request ---- */}
        <div className="flex flex-col gap-1">
          <span className="cl-secondary font-semibold text-slate-700">
            Studies on this request
          </span>
          {draft.exams.length === 0 ? (
            <p className="rounded-md border border-amber-300 bg-amber-50 px-2.5 py-1.5 cl-secondary leading-snug text-amber-900">
              No studies left on this request. Add at least one study, or cancel
              to keep the order as it was.
            </p>
          ) : (
            <ul className="flex flex-wrap gap-1.5">
              {draft.exams.map((exam) => (
                <li
                  key={exam.id}
                  className="inline-flex items-center gap-1.5 rounded border border-emerald-300 bg-emerald-50 px-2 py-0.5 cl-secondary font-semibold text-emerald-900"
                >
                  {examLabel(exam)}
                  {readOnly ? null : (
                    <button
                      type="button"
                      aria-label={`Remove ${exam.name}`}
                      onClick={() =>
                        setDraft((current) => ({
                          ...current,
                          exams: removeExam(current.exams, exam.id),
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

        {contrastPending ? (
          <p className="rounded-md border border-violet-300 bg-violet-50 px-2.5 py-1.5 cl-secondary leading-snug text-violet-900">
            A selected study requires contrast. Confirm the patient&apos;s
            preparation and allergy history before ordering.
          </p>
        ) : null}

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
                patchForm({ priority: event.target.value as RadOrderForm["priority"] })
              }
              className={`${FIELD_CLASS} font-semibold`}
            >
              {RAD_PRIORITIES.map((priority) => (
                <option key={priority} value={priority}>
                  {radPriorityLabel(priority)}
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
              value={draft.form.clinical_indication}
              rows={3}
              disabled={readOnly}
              placeholder="Why this study is being requested (optional)…"
              onChange={(event) =>
                patchForm({ clinical_indication: event.target.value })
              }
              className={TEXTAREA_CLASS}
            />
          </label>

          {/* Kept separate from the indication rather than merged, because they
              are read by different people: the indication by the radiologist
              reporting the study, the preparation by the department booking
              the patient in. */}
          <label className="flex min-w-0 flex-col gap-1 sm:col-span-2">
            <span className="cl-secondary font-semibold text-slate-700">
              Preparation / instructions
            </span>
            <textarea
              value={draft.form.instructions}
              rows={3}
              disabled={readOnly}
              placeholder="For the imaging department — fasting, cannula, previous imaging (optional)…"
              onChange={(event) => patchForm({ instructions: event.target.value })}
              className={TEXTAREA_CLASS}
            />
          </label>
        </div>
      </div>
    </ClinicalOrderModal>
  );
}
