/**
 * THE NOTE EDITOR'S LIFECYCLE, pinned where it is wired rather than where it is
 * decided.
 *
 * WHY THIS FILE IS A SOURCE ASSERTION. note-editor-format.test.ts already
 * exercises the DECISIONS -- which close paths ask first, what resolves to an
 * open editor, what the command bar may claim. What it cannot see is the
 * WIRING: whether Cancel is actually connected to the path that clears the
 * selection, whether the modal's render gate is really the same expression the
 * footer reads, whether a second exit was added that forgets to close. This
 * project ships no DOM test stack (see the note atop note-editor-format.test.ts
 * for why), and the same technique the order panels use -- reading the source
 * and asserting on it -- pins exactly the joins that regressed.
 *
 * THE DEFECT THESE TESTS EXIST FOR. The desk could sit in a state equivalent to
 * "a note section is open" with NO editor mounted: the command bar announced
 * "Note open for editing" and there was nothing on screen to close. The
 * structural fix is that ONE resolution -- `openField` -- gates the modal, and
 * `editorOpen` derived from it is the only thing allowed to say an editor is
 * open. These tests hold that shape in place.
 *
 * WHAT THEY DELIBERATELY DO NOT TEST. Nothing here asserts anything about
 * completion ELIGIBILITY. That is the server's, via can_complete and
 * completion_blockers, and completion-format.test.ts pins that the desk reads
 * the verdict rather than re-deriving it.
 */
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import { NOTE_EDITOR_OPEN_TEXT } from "./note-editor-format.ts";

/*
  Line endings are normalised because this repository is worked on from
  Windows: a CRLF checkout would fail every multi-line assertion below for a
  reason that has nothing to do with the code being asserted.
*/
function source(path: string): string {
  return readFileSync(new URL(`../${path}`, import.meta.url), "utf8").replace(
    /\r\n/g,
    "\n",
  );
}

const WORKSPACE = source(
  "components/doctor/consultation/consultation-workspace.tsx",
);
const MODAL = source("components/doctor/consultation/note-editor-modal.tsx");
const CARD = source("components/doctor/consultation/note-section-card.tsx");

/* ------------------------------------------------------------------ *
 * 1. Opening
 * ------------------------------------------------------------------ */

test("a card click sets the open section, and that is what mounts the modal", () => {
  // The card hands back the key AND the button element; the workspace stores
  // the element for focus return and the key as the selection.
  assert.ok(CARD.includes("onOpen(fieldKey, event.currentTarget)"));
  assert.ok(WORKSPACE.includes("onOpenSection={openNoteSection}"));
  assert.ok(WORKSPACE.includes("setOpenSection(field)"));
});

test("a section that would not mount an editor is REFUSED at the source", () => {
  /*
    THE INVARIANT, ENFORCED WHERE THE STATE IS WRITTEN. The selection can only
    be set to a field the render gate will actually mount, so it cannot hold a
    value that makes the workspace believe an editor is open while none is.

    Enforced here rather than repaired by an effect: setState in an effect body
    cascades a render, and a state that cannot be entered needs no repair.
  */
  assert.ok(
    WORKSPACE.includes(
      "if (isStaleNoteSelection(field, activeNoteField(field, NOTE_FIELDS))) {\n        return;\n      }",
    ),
    "the open path no longer refuses an unresolvable section",
  );
});

test("the modal is gated on the RESOLVED field, not on the raw selection", () => {
  /*
    THE STRUCTURAL FIX. A key that resolves to no field must not be able to
    count as "open" anywhere, so the gate reads the resolution.
  */
  assert.ok(
    WORKSPACE.includes("const openField = activeNoteField(openSection, NOTE_FIELDS)"),
    "openField is no longer resolved through activeNoteField",
  );
  assert.ok(
    WORKSPACE.includes("{openField ? (\n        <NoteEditorModal"),
    "the modal's render gate is no longer openField",
  );
});

test("there is exactly ONE render gate for the editor", () => {
  // A second conditional around the modal -- on the section, on loading, on an
  // error state -- is precisely how a selection outlives its editor.
  assert.equal(
    (WORKSPACE.match(/<NoteEditorModal/g) ?? []).length,
    1,
    "a second NoteEditorModal render site appeared",
  );
});

/* ------------------------------------------------------------------ *
 * 2-7. Every close path clears the selection
 * ------------------------------------------------------------------ */

test("the selection is cleared in exactly one place", () => {
  /*
    THE PROPERTY THAT MAKES THE OTHERS HOLD. Every exit routes through
    `closeNoteSection`, so there is no path that dismisses the modal while
    leaving the selection set. Two clearing sites would mean two lifecycles.
  */
  assert.ok(
    WORKSPACE.includes(
      "const closeNoteSection = useCallback(() => {\n    setOpenSection(null);",
    ),
  );
  assert.equal(
    (WORKSPACE.match(/setOpenSection\(null\)/g) ?? []).length,
    1,
    "a second place clears the selection: that is two lifecycles, not one",
  );
});

test("the selection has exactly TWO writers, and no effect among them", () => {
  /*
    One guarded setter and one clearer. A third writer -- most of all a
    normalising effect -- would mean the state can reach a shape something has
    to repair, which is the defect this fix removes rather than patches.
  */
  assert.equal(
    (WORKSPACE.match(/setOpenSection\(/g) ?? []).length,
    2,
    "the number of writers to openSection changed",
  );
  assert.ok(
    !/useEffect\(\(\) => \{[^}]*setOpenSection/.test(WORKSPACE),
    "setOpenSection is called from an effect body",
  );
});

test("the modal's ONLY exit is the workspace's close path", () => {
  assert.ok(
    WORKSPACE.includes("onClose={closeNoteSection}"),
    "the modal no longer closes through closeNoteSection",
  );
  /*
    THE MODAL OWNS NO VISIBILITY STATE. It is mounted and unmounted by the
    workspace, so it cannot hide itself while the selection stands -- which is
    the shape the reported defect would have to take. Its only local state is
    the captured opening text and the discard prompt.
  */
  const locals = MODAL.match(/const \[[A-Za-z]+/g) ?? [];
  assert.deepEqual(locals, ["const [openedWith", "const [confirmDiscard"]);
  // Exactly three exits, all of them the prop: the guarded close, the confirmed
  // discard, and a save that landed.
  assert.equal(
    (MODAL.match(/onClose\(\)/g) ?? []).length + (MODAL.match(/onClick=\{onClose\}/g) ?? []).length,
    3,
    "the number of exits from the modal changed",
  );
});

test("Save & close closes only after the write landed", () => {
  // The optimistic-concurrency rule, at its wiring. `shouldCloseAfterSave` is
  // pinned in note-editor-format.test.ts.
  assert.ok(MODAL.includes("const ok = await onSave();"));
  assert.ok(MODAL.includes("if (shouldCloseAfterSave(ok)) onClose();"));
});

test("Cancel, the X and the backdrop all take the SAME guarded route", () => {
  /*
    Three controls, one handler. If any of them dismissed the panel by its own
    means, that path would bypass both the unsaved-changes prompt and the
    selection clear.
  */
  assert.equal(
    (MODAL.match(/onClick=\{requestClose\}/g) ?? []).length,
    3,
    "a close control stopped routing through requestClose",
  );
  assert.ok(MODAL.includes("const intent = closeIntent({ readOnly, changed, busy });"));
  assert.ok(MODAL.includes('if (intent === "close") {\n      onClose();'));
});

test("Escape takes the same route as Cancel", () => {
  assert.ok(MODAL.includes('if (event.key === "Escape")'));
  assert.ok(MODAL.includes("const intent = escapeIntent({"));
  // Not a second close implementation: Escape delegates to requestClose.
  assert.ok(MODAL.includes('if (intent === "dismiss-confirm") {'));
  assert.ok(MODAL.includes("        requestClose();\n        return;"));
});

test("the read-only viewer's Close clears the selection like any other", () => {
  /*
    A completed consultation opens a READER. It has no Save, so if its Close
    were wired to anything but the shared path it would be the one exit that
    left the selection behind.
  */
  // The viewer's Close IS the Cancel button, relabelled -- so it is one of the
  // three requestClose sites asserted above rather than a fourth control with
  // its own handler.
  assert.ok(MODAL.includes('{readOnly ? "Close" : "Cancel"}'));
  assert.ok(
    MODAL.includes("onClick={requestClose}\n                disabled={busy}"),
    "the read-only Close stopped routing through requestClose",
  );
  // And closeIntent returns "close" for read-only whatever else is true, which
  // note-editor-format.test.ts pins: the viewer is never asked to confirm.
  assert.ok(MODAL.includes("closeIntent({ readOnly, changed, busy })"));
});

test("the discard prompt's own confirm closes through the same prop", () => {
  // "Discard changes" is the one place that closes WITHOUT saving, and it must
  // still clear the selection.
  assert.ok(MODAL.includes("onClick={onClose}"));
});

/* ------------------------------------------------------------------ *
 * 8. The footer cannot claim an editor that is not mounted
 * ------------------------------------------------------------------ */

test("the command bar's sentence comes from the modal's own gate", () => {
  assert.ok(
    WORKSPACE.includes("const editorOpen = openField !== null;"),
    "editorOpen is no longer derived from the render gate",
  );
  assert.ok(
    WORKSPACE.includes("consultationIdleText({ section, editorOpen })"),
    "the footer no longer reads consultationIdleText",
  );
});

test("the phrase is not written into the workspace as a literal", () => {
  /*
    THE DEFECT, PINNED WHERE IT LIVED. "Note open for editing" was the NOTE
    tab's STATIC idle text -- a claim about editor state that the tab made
    whenever it was simply clean. The only copy of the string is now the
    constant, said only when `editorOpen` is true.
  */
  assert.equal(NOTE_EDITOR_OPEN_TEXT, "Note open for editing");
  assert.ok(
    !WORKSPACE.includes(NOTE_EDITOR_OPEN_TEXT),
    "the workspace hard-codes the open-editor sentence again",
  );
});

/* ------------------------------------------------------------------ *
 * 9-10. What the two buttons are actually gated on
 * ------------------------------------------------------------------ */

test("Save Note is gated on the DIRTY note and nothing about the editor", () => {
  /*
    Save reflects unsaved text, full stop. It must not be pressed into service
    as a way to clear editor state -- that would be a redundant write to a
    record the server already holds.
  */
  assert.ok(
    WORKSPACE.includes(
      'disabled={!editable || !dirty || status === "saving" || noteLoading}',
    ),
    "the Save Note gate changed",
  );
  assert.ok(WORKSPACE.includes("hasUnsavedChanges(draft, baseline)"));
});

test("completion is not gated on editor state anywhere in the workspace", () => {
  /*
    THE FALSE BLOCKER THIS DEFECT WAS ABOUT. `mayComplete` is the shipped rule
    from consultation-format, whose entire input surface is pinned in
    completion-format.test.ts; neither `openSection`, `openField` nor
    `editorOpen` may appear in the completion path.
  */
  assert.ok(WORKSPACE.includes("const mayComplete = mayCompleteConsultation(completeState);"));
  const gate = WORKSPACE.slice(
    WORKSPACE.indexOf("const completeState = {"),
    WORKSPACE.indexOf("const complete = useCallback"),
  );
  assert.ok(gate.length > 0, "the completion gate moved");
  for (const forbidden of ["openSection", "openField", "editorOpen"]) {
    assert.ok(
      !gate.includes(forbidden),
      `${forbidden} reached the completion gate: editor state must never block completion`,
    );
  }
});

test("the completion request still sends the version and nothing else", () => {
  // The API contract is untouched by this fix: completion is not an edit, and
  // the server rejects narrative fields on this endpoint by name.
  assert.ok(
    WORKSPACE.includes("JSON.stringify({ version: consultation.version })"),
    "the completion request body changed",
  );
});

test("the server's verdict is still read, never re-derived", () => {
  assert.ok(WORKSPACE.includes("setCanComplete(Boolean(payload.can_complete))"));
  assert.ok(WORKSPACE.includes("setBlockers(payload.completion_blockers ?? [])"));
});

/* ------------------------------------------------------------------ *
 * 11. Unsaved-change protection survives
 * ------------------------------------------------------------------ */

test("closing a changed editor still asks before discarding", () => {
  assert.ok(MODAL.includes("setConfirmDiscard(true)"));
  assert.ok(MODAL.includes("This section has unsaved changes. Discard them?"));
  assert.ok(MODAL.includes("Keep writing"));
  assert.ok(MODAL.includes("Discard changes"));
});

test("no close path was made unconditional to get past the stale state", () => {
  /*
    The lazy fix for this defect would have been to clear the selection on every
    dismissal regardless of intent, which would silently discard clinical text.
    The guarded route is still the only one.
  */
  assert.ok(MODAL.includes('if (intent === "blocked") return;'));
  assert.ok(!MODAL.includes("onClick={() => onClose()}"));
});

/* ------------------------------------------------------------------ *
 * Clearing editor state costs nothing
 * ------------------------------------------------------------------ */

test("closing an editor does not save, refetch or otherwise write", () => {
  /*
    CLEARING HIDDEN EDITOR STATE MUST NEVER COST A ROUND TRIP. A redundant Save
    Note on close would rewrite paragraphs the doctor did not touch, using
    whatever this tab happened to load -- the exact overwrite the version token
    exists to prevent, arriving through the front door. The close path moves
    focus and clears one piece of state; that is all it may do.
  */
  const close = WORKSPACE.slice(
    WORKSPACE.indexOf("const closeNoteSection = useCallback"),
    WORKSPACE.indexOf("const onFieldChange = useCallback"),
  );
  assert.ok(close.length > 0, "the close path moved");
  for (const forbidden of ["fetch(", "save()", "reload(", "setStatus", "setDraft"]) {
    assert.ok(!close.includes(forbidden), `${forbidden} in the close path`);
  }
});
