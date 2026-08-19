"use client";

import { NOTE_FIELDS } from "@/lib/consultation-format";
import type {
  ConsultationDraft,
  ConsultationNarrativeField,
} from "@/types/doctor-consultation";

/**
 * The six narrative fields, as plain textareas.
 *
 * PLAIN TEXT ON PURPOSE. A rich-text editor would add a dependency, a
 * sanitisation surface and a serialisation format to a field the model stores
 * as Text and the Odoo backend renders raw. Clinicians type prose and line
 * breaks; both survive a textarea exactly, and `whitespace-pre-wrap` on every
 * read-back renders them unchanged.
 *
 * NO AUTOSAVE, AND THE UI NEVER IMPLIES ONE. Saving is an explicit act with an
 * explicit button, because a save can be REFUSED -- the version check rejects a
 * write built on a stale read -- and a silent background save that lost that
 * race would be the worst possible outcome: the doctor would believe their note
 * was stored. See the note on the conflict banner in consultation-workspace.
 *
 * A field is marked with an unsaved dot when it differs from what the server
 * last confirmed, so at a glance the doctor can see exactly which paragraphs
 * are still only in the browser.
 */
export default function ConsultationNoteEditor({
  draft,
  baseline,
  disabled,
  onChange,
}: {
  draft: ConsultationDraft;
  baseline: ConsultationDraft;
  disabled: boolean;
  onChange: (field: ConsultationNarrativeField, value: string) => void;
}) {
  return (
    <div className="flex flex-col gap-2.5">
      {NOTE_FIELDS.map((field) => {
        const dirty = draft[field.key] !== baseline[field.key];
        const inputId = `consultation-${field.key}`;
        return (
          <div key={field.key} className="flex flex-col gap-1">
            <div className="flex items-center gap-1.5">
              <label
                htmlFor={inputId}
                className="text-[10px] font-bold uppercase tracking-[0.08em] text-slate-500"
              >
                {field.label}
              </label>
              {dirty ? (
                <span
                  className="inline-flex items-center gap-1 text-[9px] font-semibold uppercase tracking-wide text-amber-700"
                  // Announced, not just coloured: the dot alone would be
                  // invisible to a screen reader and to anyone who cannot
                  // distinguish it from the label.
                >
                  <span aria-hidden className="h-1.5 w-1.5 rounded-full bg-amber-500" />
                  Unsaved
                </span>
              ) : null}
              <span aria-hidden className="h-px flex-1 bg-slate-200" />
            </div>
            <textarea
              id={inputId}
              value={draft[field.key]}
              rows={field.rows}
              disabled={disabled}
              placeholder={field.placeholder}
              onChange={(event) => onChange(field.key, event.target.value)}
              spellCheck
              className={`w-full resize-y rounded-md border bg-white px-2.5 py-1.5 text-[12px] leading-relaxed text-slate-900 shadow-sm outline-none transition-colors placeholder:text-slate-400 focus-visible:border-emerald-600 focus-visible:ring-1 focus-visible:ring-emerald-600 disabled:cursor-not-allowed disabled:bg-slate-50 disabled:text-slate-500 ${
                dirty ? "border-amber-300" : "border-slate-200"
              }`}
            />
          </div>
        );
      })}
    </div>
  );
}
