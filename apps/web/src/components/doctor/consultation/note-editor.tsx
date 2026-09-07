"use client";

import { NOTE_FIELDS } from "@/lib/consultation-format";
import type {
  ConsultationDraft,
  ConsultationNarrativeField,
} from "@/types/doctor-consultation";

import NoteSectionCard from "./note-section-card";

/**
 * The consultation note as a SUMMARY SURFACE.
 *
 * WHAT THIS USED TO BE, AND WHY IT CHANGED. This component rendered six
 * always-open textareas. That made the NOTE tab a form rather than a record:
 * the doctor scrolled past half of it to reach the section they wanted, all six
 * fields carried the same visual weight whether documented or empty, and none
 * of them was tall enough to write a real history in. Reading and writing want
 * opposite layouts, so they are now two surfaces -- compact cards here, a
 * focused modal for the act of writing.
 *
 * PLAIN TEXT, STILL. A rich-text editor would add a dependency, a sanitisation
 * surface and a serialisation format to a field the model stores as Text and
 * the Odoo backend renders raw. Clinicians type prose and line breaks; both
 * survive a textarea, and `whitespace-pre-wrap` renders them back faithfully in
 * the card preview.
 *
 * NO AUTOSAVE, AND THE UI NEVER IMPLIES ONE. Saving stays an explicit act with
 * an explicit button, because a save can be REFUSED -- the version check
 * rejects a write built on a stale read -- and a silent background save that
 * lost that race would be the worst possible outcome: the doctor would believe
 * their note was stored.
 *
 * LAYOUT IS DECLARED HERE, NOT IN THE LIB. consultation-format.ts owns the
 * field list and the save contract, and a test pins it against the model. Row
 * grouping and subtitles are presentation, so they live here and that module --
 * and its tests -- stay untouched by a visual change.
 */

type FieldMeta = {
  /** Very small subtitle. Says what belongs in the field, never how to type. */
  hint: string;
};

const FIELD_META: Record<ConsultationNarrativeField, FieldMeta> = {
  presenting_complaint: { hint: "Immediate clinical summary" },
  history_of_presenting_illness: { hint: "Narrative of current illness" },
  review_of_systems: { hint: "Relevant symptoms by system" },
  examination_findings: { hint: "General and focused examination" },
  assessment: { hint: "Clinical impression" },
  plan: { hint: "Management and follow-up" },
};

/**
 * The consultation reading order, as rows.
 *
 * Pairing HPI with ROS and Assessment with Plan roughly halves the scroll
 * depth, and both pairs are genuinely written together. Presenting complaint
 * and examination stay full width: the first is the headline, the second is the
 * longest continuous prose in the note.
 *
 * WITH CARDS THIS MATTERS MORE THAN IT DID WITH TEXTAREAS. All six sections now
 * fit on one screen at ordinary desktop heights, so the whole note can be
 * scanned in a glance rather than scrolled through.
 */
const ROWS: ConsultationNarrativeField[][] = [
  ["presenting_complaint"],
  ["history_of_presenting_illness", "review_of_systems"],
  ["examination_findings"],
  ["assessment", "plan"],
];

const FIELD_BY_KEY = new Map(NOTE_FIELDS.map((field) => [field.key, field]));

export default function ConsultationNoteEditor({
  draft,
  baseline,
  readOnly,
  onOpenSection,
}: {
  draft: ConsultationDraft;
  baseline: ConsultationDraft;
  /** The consultation is completed: cards open a reader, not an editor. */
  readOnly: boolean;
  onOpenSection: (
    field: ConsultationNarrativeField,
    origin: HTMLButtonElement,
  ) => void;
}) {
  return (
    <div className="flex flex-col gap-2.5">
      {ROWS.map((row) => (
        <div
          key={row.join("+")}
          // Paired rows collapse to one column below `md`, so nothing is
          // clipped on a narrow window. Desktop stays the primary target.
          className={
            row.length > 1
              ? "grid grid-cols-1 gap-2.5 md:grid-cols-2"
              : "grid grid-cols-1"
          }
        >
          {row.map((key) => {
            const field = FIELD_BY_KEY.get(key);
            if (!field) return null;
            return (
              <NoteSectionCard
                key={key}
                fieldKey={key}
                label={field.label}
                hint={FIELD_META[key].hint}
                value={draft[key]}
                dirty={draft[key] !== baseline[key]}
                readOnly={readOnly}
                lead={key === "presenting_complaint"}
                onOpen={onOpenSection}
              />
            );
          })}
        </div>
      ))}
    </div>
  );
}
