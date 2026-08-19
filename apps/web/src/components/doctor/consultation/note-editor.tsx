"use client";

import { NOTE_FIELDS } from "@/lib/consultation-format";
import type {
  ConsultationDraft,
  ConsultationNarrativeField,
} from "@/types/doctor-consultation";

/**
 * The six narrative fields, as clinical writing surfaces.
 *
 * PLAIN TEXT ON PURPOSE. A rich-text editor would add a dependency, a
 * sanitisation surface and a serialisation format to a field the model stores
 * as Text and the Odoo backend renders raw. Clinicians type prose and line
 * breaks; both survive a textarea exactly.
 *
 * NO AUTOSAVE, AND THE UI NEVER IMPLIES ONE. Saving is an explicit act with an
 * explicit button, because a save can be REFUSED -- the version check rejects a
 * write built on a stale read -- and a silent background save that lost that
 * race would be the worst possible outcome: the doctor would believe their note
 * was stored.
 *
 * WHY THE FIELD IS A CONTAINER RATHER THAN A BARE TEXTAREA.
 * A flat textarea under a floating label read as an inert form control: nothing
 * on screen said "this is where you write" until it was already focused. Each
 * field is now a surface that owns its own header, so the title, the subtitle
 * and the state indicator sit INSIDE the boundary they describe. Every state
 * below is driven by CSS on that container -- `hover:` and `focus-within:` --
 * rather than by React state, so hovering and focusing cost no re-render and
 * cannot desynchronise from the DOM.
 *
 * LAYOUT IS DECLARED HERE, NOT IN THE LIB. consultation-format.ts owns the
 * field list and the save contract, and a test pins it against the model. Row
 * grouping, heights and subtitles are presentation, so they live here and that
 * module -- and its tests -- stay untouched by a visual change.
 */

type FieldMeta = {
  /** Very small subtitle. Says what belongs in the field, never how to type. */
  hint: string;
  /** Comfortable starting height. The textarea stays user-resizable. */
  minHeight: string;
};

const FIELD_META: Record<ConsultationNarrativeField, FieldMeta> = {
  presenting_complaint: {
    hint: "Immediate clinical summary",
    minHeight: "min-h-[46px]",
  },
  history_of_presenting_illness: {
    hint: "Narrative of current illness",
    minHeight: "min-h-[136px]",
  },
  review_of_systems: {
    hint: "Relevant symptoms by system",
    minHeight: "min-h-[136px]",
  },
  examination_findings: {
    hint: "General and focused examination",
    minHeight: "min-h-[112px]",
  },
  assessment: {
    hint: "Clinical impression",
    minHeight: "min-h-[112px]",
  },
  plan: {
    hint: "Management and follow-up",
    minHeight: "min-h-[112px]",
  },
};

/**
 * The consultation reading order, as rows.
 *
 * Pairing HPI with ROS and Assessment with Plan roughly halves the scroll
 * depth, and both pairs are genuinely written together. Presenting complaint
 * and examination stay full width: the first is the headline, the second is
 * the longest continuous prose in the note.
 */
const ROWS: ConsultationNarrativeField[][] = [
  ["presenting_complaint"],
  ["history_of_presenting_illness", "review_of_systems"],
  ["examination_findings"],
  ["assessment", "plan"],
];

const FIELD_BY_KEY = new Map(NOTE_FIELDS.map((field) => [field.key, field]));

function NoteField({
  fieldKey,
  value,
  dirty,
  disabled,
  savedPulse,
  lead,
  onChange,
}: {
  fieldKey: ConsultationNarrativeField;
  value: string;
  dirty: boolean;
  disabled: boolean;
  savedPulse: boolean;
  /** The presenting complaint carries an emerald keyline and a CC chip. */
  lead?: boolean;
  onChange: (field: ConsultationNarrativeField, value: string) => void;
}) {
  const field = FIELD_BY_KEY.get(fieldKey);
  if (!field) return null;

  const meta = FIELD_META[fieldKey];
  const inputId = `consultation-${fieldKey}`;

  /*
    Container state. Order matters: disabled wins, then dirty, then the neutral
    surface which hover and focus-within act on.

    DISABLED IS LEGIBLE, NOT FADED. A completed note is read far more often than
    it is written, so it gets a distinct recessed surface and near-full-contrast
    text rather than a 45%-opacity wash that is hard to actually read.
  */
  const surface = disabled
    ? "border-slate-300 bg-slate-100/80"
    : dirty
      ? "border-amber-300 bg-amber-50/40 hover:border-amber-400"
      : "border-slate-200 bg-white hover:border-slate-300 hover:bg-slate-50/50";

  const header = disabled
    ? "border-slate-300 bg-slate-200/50"
    : dirty
      ? "border-amber-200 bg-amber-100/50"
      : "border-slate-200 bg-slate-50/80 group-focus-within:border-emerald-200 group-focus-within:bg-emerald-50/70";

  return (
    <div
      className={`group flex min-w-0 flex-col overflow-hidden rounded-lg border shadow-[0_1px_2px_rgba(15,23,42,0.04)] transition-colors focus-within:border-emerald-500 focus-within:shadow-[0_0_0_3px_rgba(5,150,105,0.12)] ${surface} ${
        lead ? "border-l-[3px] border-l-emerald-600" : ""
      }`}
    >
      {/* Title bar, INSIDE the boundary it describes. */}
      <div
        className={`flex shrink-0 items-center gap-2 border-b px-2.5 py-1 transition-colors ${header}`}
      >
        {lead ? (
          <span
            aria-hidden
            className="shrink-0 rounded bg-emerald-600 px-1 py-px text-[9px] font-bold leading-tight text-white"
          >
            CC
          </span>
        ) : null}

        <label htmlFor={inputId} className="min-w-0 cursor-text truncate">
          <span
            className={`text-[10px] font-bold uppercase tracking-[0.07em] transition-colors ${
              disabled
                ? "text-slate-600"
                : "text-slate-600 group-focus-within:text-emerald-800"
            }`}
          >
            {field.label}
          </span>
          <span className="ml-1.5 hidden text-[9px] font-normal normal-case tracking-normal text-slate-400 sm:inline">
            {meta.hint}
          </span>
        </label>

        <span aria-hidden className="h-px flex-1" />

        {/* State chips. Dirty outranks saved; both outrank the focus hint. */}
        {dirty ? (
          <span className="inline-flex shrink-0 items-center gap-1 text-[9px] font-bold uppercase tracking-wide text-amber-700">
            <span aria-hidden className="h-1.5 w-1.5 rounded-full bg-amber-500" />
            Unsaved
          </span>
        ) : savedPulse ? (
          <span className="inline-flex shrink-0 items-center gap-1 text-[9px] font-bold uppercase tracking-wide text-emerald-700">
            <span aria-hidden>✓</span>
            Saved
          </span>
        ) : disabled ? null : (
          // Appears only while this field holds focus, so the doctor always
          // knows which surface their keystrokes are going into.
          <span className="hidden shrink-0 text-[9px] font-bold uppercase tracking-wide text-emerald-700 group-focus-within:inline">
            Editing
          </span>
        )}
      </div>

      {/*
        cursor-text on the wrapper, and the textarea fills it, so the whole
        surface reads and behaves as a writing area rather than as a decorated
        box with a small input somewhere inside it.
      */}
      <textarea
        id={inputId}
        value={value}
        disabled={disabled}
        placeholder={field.placeholder}
        onChange={(event) => onChange(fieldKey, event.target.value)}
        spellCheck
        className={`w-full flex-1 resize-y cursor-text bg-transparent px-2.5 py-2 text-[12.5px] leading-[1.65] text-slate-900 caret-emerald-700 outline-none transition-colors placeholder:text-slate-400 disabled:cursor-not-allowed disabled:text-slate-600 ${meta.minHeight}`}
      />
    </div>
  );
}

export default function ConsultationNoteEditor({
  draft,
  baseline,
  disabled,
  savedPulse,
  onChange,
}: {
  draft: ConsultationDraft;
  baseline: ConsultationDraft;
  disabled: boolean;
  /** True briefly after a successful save, for the per-field ✓ chip. */
  savedPulse: boolean;
  onChange: (field: ConsultationNarrativeField, value: string) => void;
}) {
  return (
    <div className="flex flex-col gap-2.5">
      {ROWS.map((row) => (
        <div
          key={row.join("+")}
          // Paired rows collapse to one column below `md`, so nothing is
          // clipped and no textarea bottom becomes unreachable on a narrow
          // window. Desktop stays the primary target.
          className={
            row.length > 1
              ? "grid grid-cols-1 gap-2.5 md:grid-cols-2"
              : "grid grid-cols-1"
          }
        >
          {row.map((key) => (
            <NoteField
              key={key}
              fieldKey={key}
              value={draft[key]}
              dirty={draft[key] !== baseline[key]}
              disabled={disabled}
              savedPulse={savedPulse}
              lead={key === "presenting_complaint"}
              onChange={onChange}
            />
          ))}
        </div>
      ))}
    </div>
  );
}
