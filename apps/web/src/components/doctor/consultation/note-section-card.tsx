"use client";

import {
  EMPTY_SECTION_TEXT,
  isDocumented,
  sectionActionLabel,
} from "@/lib/note-editor-format";
import type { ConsultationNarrativeField } from "@/types/doctor-consultation";

/**
 * ONE consultation section, as a compact summary card.
 *
 * WHY THE NOTE IS NO LONGER SIX OPEN TEXTAREAS. Six always-open boxes made the
 * NOTE tab a form to be filled rather than a record to be read: the doctor
 * scrolled past half of it to reach the field they wanted, every field competed
 * for the same visual weight, and none of them was large enough to write a
 * paragraph in comfortably. Splitting the two jobs -- SCAN here, WRITE in the
 * modal -- lets each be sized for what it actually is.
 *
 * THE PREVIEW IS CLAMPED, NOT TRUNCATED IN JAVASCRIPT. `line-clamp-4` is a CSS
 * property: the full text stays in the DOM, so it is selectable, findable by
 * the browser's own search, and reachable by a screen reader. Cutting the
 * string in JS would make the card lie about what the note contains.
 *
 * NEVER HORIZONTALLY SCROLLING. `break-words` plus `whitespace-pre-wrap` keeps
 * a pasted lab value or a long drug name inside the card, and preserves the
 * line breaks a clinician typed -- which carry meaning in a systems review.
 *
 * THE WHOLE CARD IS THE BUTTON. A separate small "edit" link would be a second
 * target to aim at; here the affordance is the card, and the word in the corner
 * says which act it performs.
 */

export type NoteSectionCardProps = {
  fieldKey: ConsultationNarrativeField;
  label: string;
  /** What belongs in this section. Never how to type it. */
  hint: string;
  value: string;
  /** Draft text differs from the last thing the server confirmed. */
  dirty: boolean;
  /** The consultation is completed: the modal opens as a reader. */
  readOnly: boolean;
  /** The presenting complaint carries the emerald keyline and the CC chip. */
  lead?: boolean;
  /*
    Hands back the button element as well as the key. THE ELEMENT IS THE POINT:
    the workspace stashes it and returns focus here when the modal closes, which
    is what stops a keyboard user being dumped at the top of the document after
    every edit. Capturing it at click time is exact -- no ref registry, no
    document query, and no chance of focusing the wrong card.
  */
  onOpen: (field: ConsultationNarrativeField, origin: HTMLButtonElement) => void;
};

export default function NoteSectionCard({
  fieldKey,
  label,
  hint,
  value,
  dirty,
  readOnly,
  lead,
  onOpen,
}: NoteSectionCardProps) {
  const documented = isDocumented(value);
  const action = sectionActionLabel(readOnly);

  /*
    Surface state. Order matters: unsaved outranks everything, because an amber
    card is the one thing on this tab the doctor must act on before leaving. A
    completed note gets a recessed surface and near-full-contrast text -- it is
    read far more often than it was written.
  */
  const surface = readOnly
    ? "border-slate-300 bg-slate-50"
    : dirty
      ? "border-amber-300 bg-amber-50/50 hover:border-amber-400 hover:bg-amber-50"
      : "border-slate-200 bg-white hover:border-emerald-300 hover:bg-emerald-50/30";

  return (
    <button
      type="button"
      data-note-card={fieldKey}
      onClick={(event) => onOpen(fieldKey, event.currentTarget)}
      aria-label={`${action} ${label}`}
      className={`group flex min-w-0 flex-col gap-1 rounded-lg border px-3 py-2.5 text-left shadow-[0_1px_2px_rgba(15,23,42,0.04)] outline-none transition-colors focus-visible:border-emerald-600 focus-visible:ring-2 focus-visible:ring-emerald-600/40 ${surface} ${
        lead ? "border-l-[3px] border-l-emerald-600" : ""
      }`}
    >
      {/* ---- Header ---- */}
      <div className="flex min-w-0 items-center gap-2">
        {lead ? (
          <span
            aria-hidden
            className="shrink-0 rounded bg-emerald-600 px-1 py-px cl-micro font-bold leading-tight text-white"
          >
            CC
          </span>
        ) : null}

        <span className="min-w-0 truncate cl-body font-semibold text-slate-800">
          {label}
        </span>

        <span className="hidden min-w-0 truncate cl-meta font-normal text-slate-500 lg:inline">
          {hint}
        </span>

        <span aria-hidden className="h-px flex-1" />

        {/*
          STATE IS NEVER COLOUR ALONE. Each chip carries its own word, so an
          unsaved section is identifiable without seeing the amber.
        */}
        {dirty ? (
          <span className="inline-flex shrink-0 items-center gap-1 cl-micro font-bold uppercase tracking-wide text-amber-700">
            <span aria-hidden className="h-1.5 w-1.5 rounded-full bg-amber-500" />
            Unsaved
          </span>
        ) : null}

        <span
          className={`shrink-0 cl-micro font-bold uppercase tracking-wide ${
            readOnly
              ? "text-slate-500"
              : "text-emerald-700 opacity-0 transition-opacity group-hover:opacity-100 group-focus-visible:opacity-100"
          }`}
        >
          {action}
        </span>
      </div>

      {/* ---- Preview ---- */}
      {documented ? (
        <p className="line-clamp-4 min-w-0 whitespace-pre-wrap break-words cl-secondary leading-[1.55] text-slate-700">
          {value}
        </p>
      ) : (
        /*
          AN EMPTY SECTION SAYS SO IN WORDS. A blank box reads as a rendering
          fault; "Not documented yet" reads as a fact about the record, and is
          what a colleague reviewing the note needs to see.
        */
        <p className="cl-secondary italic leading-[1.55] text-slate-500">
          {EMPTY_SECTION_TEXT}
        </p>
      )}
    </button>
  );
}
