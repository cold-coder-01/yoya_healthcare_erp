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
