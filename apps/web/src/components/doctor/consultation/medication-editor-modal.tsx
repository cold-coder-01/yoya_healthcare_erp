"use client";

import { useCallback, useMemo, useRef, useState } from "react";

import {
  dosageFormLabel,
  medicineEditorChanged,
  medicineLabel,
  quantityError,
  routeLabel,
} from "@/lib/medication-format";
import type { StagedMedicine } from "@/types/doctor-medication";
import { MED_ROUTES } from "@/types/doctor-medication";

import ClinicalOrderModal from "./clinical-order-modal";

/**
 * ONE MEDICINE'S PRESCRIBING DETAIL, in a centred editor.
 *
 * The dialog chrome, focus handling, keyboard rules and unsaved-work protection
 * all come from ClinicalOrderModal, which laboratory and radiology use too. What
 * remains here is the part that is genuinely medication's: dose, route,
 * frequency, duration, quantity and instructions, and the one rule that decides
 * whether the line can be added at all.
 *
 * QUANTITY IS THE ONLY REQUIRED FIELD, and it is validated before the line is
 * staged rather than at submission, because a prescription with a zero-quantity
 * line reaches the pharmacy as an order nobody can mark ready. The server
 * refuses it too; this is the copy of that rule that lets the doctor see it
 * while the medicine is still in front of them.
 *
 * THE DRAFT IS LOCAL UNTIL SAVED. Cancelling leaves the staged line exactly as
 * it was, which is what makes Edit safe to open on a line that is already
 * correct.
 */

export type MedicationEditorModalProps = {
  entry: StagedMedicine;
  mode: "add" | "edit";
  readOnly?: boolean;
  returnFocus: HTMLElement | null;
  onSave: (entry: StagedMedicine) => void;
  onClose: () => void;
};

const FIELD_CLASS =
  "h-9 w-full rounded-md border border-slate-300 bg-white px-2.5 cl-body text-slate-900 outline-none placeholder:text-slate-500 focus-visible:border-emerald-600 focus-visible:ring-2 focus-visible:ring-emerald-600/25";

export default function MedicationEditorModal({
  entry,
  mode,
  readOnly = false,
  returnFocus,
  onSave,
  onClose,
}: MedicationEditorModalProps) {
  const [draft, setDraft] = useState(entry);
  const [showQuantityError, setShowQuantityError] = useState(false);
  const doseRef = useRef<HTMLInputElement>(null);
  const changed = medicineEditorChanged(entry, draft);
  const quantityMessage = showQuantityError ? quantityError(draft.quantity) : null;

  const save = useCallback(() => {
    if (quantityError(draft.quantity)) {
      setShowQuantityError(true);
      return;
    }
    onSave(draft);
  }, [draft, onSave]);

  const metadata = useMemo(() => {
    const medicine = draft.medicine;
    const parts: string[] = [];
    const alternate = medicine.generic_name ?? medicine.brand_name;
    if (alternate && alternate.toLowerCase() !== medicine.name.toLowerCase()) {
      parts.push(alternate);
    }
    const form = dosageFormLabel(medicine.dosage_form);
    if (form) parts.push(form);
    if (medicine.strength) parts.push(medicine.strength);
    if (medicine.code) parts.push(medicine.code);
    return parts.join(" / ");
  }, [draft.medicine]);

  const quantityErrorId = `medication-editor-quantity-${draft.key}`;
  const patchDraft = (values: Partial<StagedMedicine>) =>
    setDraft((current) => ({ ...current, ...values }));

  return (
    <ClinicalOrderModal
      title={medicineLabel(draft.medicine)}
      subtitle={metadata || "Medication catalogue item"}
      changed={changed}
      readOnly={readOnly}
      saveLabel={mode === "add" ? "Add Medicine" : "Save Changes"}
      shortcutHint={`Ctrl+Enter ${mode === "add" ? "adds medicine" : "saves changes"}`}
      discardPrompt="This medicine has unsaved changes. Discard them?"
      closeLabel="Close medication editor"
      initialFocusRef={doseRef}
      returnFocus={returnFocus}
      onSave={save}
      onClose={onClose}
    >
      <div className="grid grid-cols-1 gap-x-4 gap-y-3 sm:grid-cols-2">
        <label className="flex min-w-0 flex-col gap-1">
          <span className="cl-secondary font-semibold text-slate-700">Dose</span>
          <input ref={doseRef} value={draft.dosage} placeholder="e.g. 10mg" disabled={readOnly}
            onChange={(event) => patchDraft({ dosage: event.target.value })} className={FIELD_CLASS} />
        </label>
        <label className="flex min-w-0 flex-col gap-1">
          <span className="cl-secondary font-semibold text-slate-700">Route</span>
          <select value={draft.route} disabled={readOnly}
            onChange={(event) => patchDraft({ route: event.target.value })}
            className={`${FIELD_CLASS} font-semibold`}>
            <option value="">-- select route --</option>
            {MED_ROUTES.map((route) => (
              <option key={route} value={route}>{routeLabel(route)}</option>
            ))}
          </select>
        </label>
        <label className="flex min-w-0 flex-col gap-1">
          <span className="cl-secondary font-semibold text-slate-700">Frequency</span>
          <input value={draft.frequency} placeholder="e.g. twice daily" disabled={readOnly}
            onChange={(event) => patchDraft({ frequency: event.target.value })} className={FIELD_CLASS} />
        </label>
        <label className="flex min-w-0 flex-col gap-1">
          <span className="cl-secondary font-semibold text-slate-700">Duration</span>
          <input value={draft.duration} placeholder="e.g. 5 days" disabled={readOnly}
            onChange={(event) => patchDraft({ duration: event.target.value })} className={FIELD_CLASS} />
        </label>
        <label className="flex min-w-0 flex-col gap-1">
          <span className="cl-secondary font-semibold text-slate-700">
            Quantity <span className="text-red-700">*</span>
          </span>
          <input
            value={draft.quantity}
            inputMode="decimal"
            required
            disabled={readOnly}
            aria-invalid={quantityMessage ? true : undefined}
            aria-describedby={quantityMessage ? quantityErrorId : undefined}
            placeholder="e.g. 10"
            onChange={(event) => patchDraft({ quantity: event.target.value })}
            className={`${FIELD_CLASS} ${
              quantityMessage
                ? "border-red-500 focus-visible:border-red-500 focus-visible:ring-red-500/25"
                : ""
            }`}
          />
          {quantityMessage ? (
            <span id={quantityErrorId} role="alert" className="cl-secondary font-semibold text-red-700">
              {quantityMessage}
            </span>
          ) : null}
        </label>
        <label className="flex min-w-0 flex-col gap-1 sm:col-span-2">
          <span className="cl-secondary font-semibold text-slate-700">Instructions</span>
          <textarea
            value={draft.instructions}
            rows={3}
            disabled={readOnly}
            placeholder="e.g. Take after food"
            onChange={(event) => patchDraft({ instructions: event.target.value })}
            className="w-full resize-none rounded-md border border-slate-300 bg-white px-2.5 py-2 cl-body text-slate-900 outline-none placeholder:text-slate-500 focus-visible:border-emerald-600 focus-visible:ring-2 focus-visible:ring-emerald-600/25"
          />
        </label>
      </div>
    </ClinicalOrderModal>
  );
}
