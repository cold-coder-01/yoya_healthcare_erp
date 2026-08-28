/**
 * The focused note editor's DECISIONS, as pure functions.
 *
 * WHY THESE LIVE HERE RATHER THAN INSIDE THE COMPONENT. Every rule below
 * decides what happens to clinical text a doctor has typed -- whether a close
 * discards it, whether Escape asks first, whether a modal may close after a
 * save. Those are exactly the rules worth pinning with tests, and this project
 * tests pure functions with `node:test` and adds no DOM test stack for it.
 * Extracting them means the tests exercise the SHIPPED logic rather than a
 * re-implementation of it in a test file.
 *
 * Nothing here fetches, writes, or knows what a version token is. Saving stays
 * the workspace's job.
 */

/** Shown in place of a preview when a section has never been written. */
export const EMPTY_SECTION_TEXT = "Not documented yet";

/**
 * Whether a section holds anything a colleague could read.
 *
 * Whitespace does not count. A field holding only spaces or a stray newline is
 * an empty field that happens to have been touched, and rendering four blank
 * preview lines for it would look like a rendering fault.
 */
export function isDocumented(value: string | null | undefined): boolean {
  return typeof value === "string" && value.trim().length > 0;
}

/** The preview text, or the empty-state sentence. Never a blank card. */
export function previewText(value: string | null | undefined): string {
  return isDocumented(value) ? (value as string) : EMPTY_SECTION_TEXT;
}

/**
 * What closing the editor should do RIGHT NOW.
 *
 *   "blocked" a save is in flight; closing would race the response
 *   "confirm"  unsaved clinical text would be lost, so ask first
 *   "close"    nothing to lose
 *
 * READ-ONLY ALWAYS CLOSES. A completed consultation cannot be edited, so there
 * is no unsaved text to protect and a confirmation would be a prompt about
 * nothing.
 */
export type CloseIntent = "close" | "confirm" | "blocked";

export function closeIntent(state: {
  readOnly: boolean;
  changed: boolean;
  busy: boolean;
}): CloseIntent {
  if (state.busy) return "blocked";
  if (state.readOnly) return "close";
  return state.changed ? "confirm" : "close";
}

/**
 * What the Escape key should do.
 *
 * Escape out of the discard prompt returns to the editor rather than leaving:
 * the doctor answered "no" to discarding, so throwing the text away would be
 * the opposite of what they asked for. Otherwise Escape is exactly Cancel --
 * one rule, so the keyboard and the button can never disagree about whether
 * clinical text is safe.
 */
export type EscapeIntent = CloseIntent | "dismiss-confirm";

export function escapeIntent(state: {
  readOnly: boolean;
  changed: boolean;
  busy: boolean;
  confirmingDiscard: boolean;
}): EscapeIntent {
  if (state.confirmingDiscard) return "dismiss-confirm";
  return closeIntent(state);
}

/**
 * Whether a keystroke is the save shortcut.
 *
 * Ctrl/Cmd+Enter only. BARE ENTER IS LEFT ALONE deliberately: it is the newline
 * key in a clinical narrative, and stealing it would make paragraphs impossible
 * to write. Never fires while read-only or while the discard prompt is up.
 */
export function isSaveShortcut(
  event: { key: string; ctrlKey: boolean; metaKey: boolean },
  state: { readOnly: boolean; confirmingDiscard: boolean },
): boolean {
  if (state.readOnly || state.confirmingDiscard) return false;
  return event.key === "Enter" && (event.ctrlKey || event.metaKey);
}

/**
 * Whether the editor may close now that a save has returned.
 *
 * THE WHOLE POINT OF THE BOOLEAN. A save can be REFUSED -- most importantly by
 * the version check, when another tab wrote first -- and a modal that closed on
 * a refused save would tell the doctor their paragraph was stored when the
 * server had rejected it. Closing is therefore conditional on the write having
 * actually landed, never on the request having merely completed.
 */
export function shouldCloseAfterSave(saveSucceeded: boolean): boolean {
  return saveSucceeded === true;
}

/**
 * The word the card shows for the act its click performs.
 *
 * A completed consultation opens a reader, so promising "Edit" would be a lie
 * the modal then has to walk back.
 */
export function sectionActionLabel(readOnly: boolean): "Read" | "Edit" {
  return readOnly ? "Read" : "Edit";
}

/* ------------------------------------------------------------------ *
 * WHICH EDITOR IS OPEN -- resolved ONCE, for every consumer
 * ------------------------------------------------------------------ */

/**
 * The field descriptor for the open section, or null when nothing is open.
 *
 * THE INVARIANT THIS FUNCTION EXISTS TO ENFORCE. The workspace holds the open
 * section as a KEY, but the modal needs a DESCRIPTOR (label, placeholder) and
 * the command bar needs a BOOLEAN. Resolving the key in each place separately
 * is how the three drift apart -- and the drift has exactly one visible shape:
 * a footer announcing "Note open for editing" with no editor on screen.
 *
 * So the resolution happens here, once, and the render gate, the footer
 * sentence and the staleness check are all read off the SAME value. "An editor
 * is open" and "the modal is mounted" then cannot be different facts, because
 * they are the same expression.
 *
 * Returns null for an unknown key rather than throwing: a key the save path
 * does not recognise is a section that must not be opened, and null is what
 * `isStaleNoteSelection` then reports.
 */
export function activeNoteField<Field extends { key: string }>(
  openSection: string | null | undefined,
  fields: readonly Field[],
): Field | null {
  if (!openSection) return null;
  return fields.find((field) => field.key === openSection) ?? null;
}

/**
 * Whether a section is selected that resolves to NO editor.
 *
 * The one way the selection and the rendered modal could disagree: a key set
 * that `activeNoteField` cannot resolve, leaving the render gate closed while
 * the selection is still non-null. That is the hidden "open for editing" state.
 *
 * USED AS A GUARD, NOT AS A REPAIR. The workspace asks this BEFORE writing the
 * selection and refuses the write, so the state is never entered rather than
 * cleared afterwards -- which also means no effect calls setState to fix it,
 * and no render cascades off one that did.
 *
 * Deliberately false for `openSection === null`: "nothing is open" is the
 * resting state, not a fault to be corrected.
 */
export function isStaleNoteSelection(
  openSection: string | null | undefined,
  resolved: unknown,
): boolean {
  return Boolean(openSection) && resolved === null;
}

/* ------------------------------------------------------------------ *
 * The command bar's idle sentence
 * ------------------------------------------------------------------ */

/**
 * THIS SENTENCE IS A CLAIM ABOUT STATE, so it may only be said when the state
 * holds. It used to be the NOTE tab's static idle text, which meant the desk
 * announced an open editor whenever the note was simply sitting there clean --
 * indistinguishable, to a doctor reading the footer, from an editor stuck open
 * behind nothing.
 */
export const NOTE_EDITOR_OPEN_TEXT = "Note open for editing";
/** What the NOTE tab actually is when no editor is open: a record to read. */
export const NOTE_IDLE_TEXT = "Select a section to write your note";
export const DIAGNOSIS_IDLE_TEXT = "Diagnoses save as you record them";
export const ORDERS_IDLE_TEXT = "Orders are placed one at a time";

/**
 * The command bar's resting sentence for the section on screen.
 *
 * `editorOpen` is the SAME value the modal's render gate reads, so this can
 * never describe an editor that is not mounted. It is tested first because it
 * is the only branch making a claim about editor state; the rest describe the
 * section, which is always true of it.
 */
export function consultationIdleText(state: {
  section: string;
  editorOpen: boolean;
}): string {
  if (state.editorOpen) return NOTE_EDITOR_OPEN_TEXT;
  if (state.section === "diagnosis") return DIAGNOSIS_IDLE_TEXT;
  if (state.section === "orders") return ORDERS_IDLE_TEXT;
  return NOTE_IDLE_TEXT;
}
