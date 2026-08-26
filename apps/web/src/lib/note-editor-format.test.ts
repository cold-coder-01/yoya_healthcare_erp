/**
 * The focused note editor's decisions.
 *
 * Written against `node:test` and `node:assert`, both Node built-ins, so these
 * add NO dependency to the project -- the same discipline every other test file
 * here follows. Imports are relative rather than aliased so they resolve under
 * any runner.
 *
 * WHY THESE ARE UNIT TESTS AND NOT DOM TESTS. This project deliberately ships
 * no DOM test stack, and adding jsdom plus a rendering library to assert "a
 * click opens a modal" would be a large dependency for a shallow assertion. The
 * rules worth protecting are not "does a click work" -- React guarantees that
 * -- but WHAT HAPPENS TO CLINICAL TEXT: when a close discards it, when Escape
 * asks first, and whether a modal may close after a save. Those rules were
 * extracted into note-editor-format.ts precisely so they could be tested here,
 * and the components import the very functions exercised below, so these tests
 * pin the shipped behaviour rather than a copy of it.
 *
 * THE PROPERTIES THESE TESTS EXIST FOR:
 *
 *   1. CLINICAL TEXT IS NEVER DISCARDED SILENTLY. Every close path -- Cancel,
 *      the X, the backdrop, Escape -- asks before losing unsaved words.
 *
 *   2. A MODAL CLOSES ONLY ON A WRITE THAT LANDED. A save can be REFUSED by the
 *      version check; closing on a refusal would tell the doctor their
 *      paragraph was stored when the server rejected it.
 *
 *   3. AN EMPTY SECTION SAYS SO. A blank preview reads as a rendering fault.
 *
 *   4. BARE ENTER STAYS A NEWLINE. It is the paragraph key in a narrative.
 */
import assert from "node:assert/strict";
import test from "node:test";

import {
  EMPTY_SECTION_TEXT,
  closeIntent,
  escapeIntent,
  isDocumented,
  isSaveShortcut,
  previewText,
  sectionActionLabel,
  shouldCloseAfterSave,
} from "./note-editor-format.ts";

const OPEN = { readOnly: false, changed: false, busy: false };

/* ------------------------------------------------------------------ *
 * Card preview: documented vs not
 * ------------------------------------------------------------------ */

test("a written section is documented", () => {
  assert.equal(isDocumented("Fever for three days."), true);
  assert.equal(previewText("Fever for three days."), "Fever for three days.");
});

test("an empty section is not documented and says so in words", () => {
  // A blank card reads as a rendering fault; the sentence reads as a fact
  // about the record, which is what a reviewing colleague needs.
  assert.equal(isDocumented(""), false);
  assert.equal(previewText(""), EMPTY_SECTION_TEXT);
  assert.equal(EMPTY_SECTION_TEXT, "Not documented yet");
});

test("whitespace alone is not documentation", () => {
  // A field holding a stray newline is an empty field that happens to have
  // been touched. Four blank preview lines would look broken.
  for (const blank of ["   ", "\n", "\t", "  \n  "]) {
    assert.equal(isDocumented(blank), false, JSON.stringify(blank));
    assert.equal(previewText(blank), EMPTY_SECTION_TEXT);
  }
});

test("a null or missing value is treated as undocumented", () => {
  // Odoo returns null for an unset Text field.
  assert.equal(isDocumented(null), false);
  assert.equal(isDocumented(undefined), false);
  assert.equal(previewText(null), EMPTY_SECTION_TEXT);
});

test("the preview keeps the whole string, so nothing is truncated in JS", () => {
  // Clamping is a CSS concern. Cutting the string here would make the card lie
  // about what the note contains, and would break browser find and screen
  // readers.
  const long = "Patient reports ".repeat(400);
  assert.equal(previewText(long), long);
});

test("the card names the act its click performs", () => {
  assert.equal(sectionActionLabel(false), "Edit");
  // A completed consultation opens a reader, so promising "Edit" would be a
  // lie the modal then has to walk back.
  assert.equal(sectionActionLabel(true), "Read");
});

/* ------------------------------------------------------------------ *
 * Closing: Cancel, the X and the backdrop
 * ------------------------------------------------------------------ */

test("closing an unchanged editor closes immediately", () => {
  assert.equal(closeIntent(OPEN), "close");
});

test("closing a CHANGED editor asks before discarding", () => {
  // THE property this whole module exists for.
  assert.equal(closeIntent({ ...OPEN, changed: true }), "confirm");
});

test("a read-only viewer always closes, changed or not", () => {
  // A completed consultation cannot be edited, so there is no unsaved text to
  // protect and a confirmation would be a prompt about nothing.
  assert.equal(closeIntent({ ...OPEN, readOnly: true }), "close");
  assert.equal(
    closeIntent({ readOnly: true, changed: true, busy: false }),
    "close",
  );
});

test("closing is blocked while a save is in flight", () => {
  // Closing mid-request would race the response and leave the doctor unsure
  // whether the write landed.
  assert.equal(closeIntent({ ...OPEN, busy: true }), "blocked");
  assert.equal(
    closeIntent({ readOnly: false, changed: true, busy: true }),
    "blocked",
  );
});

/* ------------------------------------------------------------------ *
 * Escape follows exactly the same rule
 * ------------------------------------------------------------------ */

test("Escape on an unchanged editor closes", () => {
  assert.equal(escapeIntent({ ...OPEN, confirmingDiscard: false }), "close");
});

test("Escape on a CHANGED editor asks first", () => {
  // One rule for the keyboard and the buttons, so they can never disagree
  // about whether clinical text is safe.
  assert.equal(
    escapeIntent({ ...OPEN, changed: true, confirmingDiscard: false }),
    "confirm",
  );
});

test("Escape while the discard prompt is up returns to the editor", () => {
  // The doctor answered "no" to discarding; throwing the text away would be
  // the opposite of what they asked for.
  assert.equal(
    escapeIntent({ ...OPEN, changed: true, confirmingDiscard: true }),
    "dismiss-confirm",
  );
});

test("Escape and Cancel agree in every state", () => {
  for (const readOnly of [true, false]) {
    for (const changed of [true, false]) {
      for (const busy of [true, false]) {
        const state = { readOnly, changed, busy };
        assert.equal(
          escapeIntent({ ...state, confirmingDiscard: false }),
          closeIntent(state),
          JSON.stringify(state),
        );
      }
    }
  }
});

/* ------------------------------------------------------------------ *
 * Save shortcut
 * ------------------------------------------------------------------ */

const NOT_CONFIRMING = { readOnly: false, confirmingDiscard: false };

test("Ctrl+Enter and Cmd+Enter save", () => {
  assert.equal(
    isSaveShortcut({ key: "Enter", ctrlKey: true, metaKey: false }, NOT_CONFIRMING),
    true,
  );
  assert.equal(
    isSaveShortcut({ key: "Enter", ctrlKey: false, metaKey: true }, NOT_CONFIRMING),
    true,
  );
});

test("BARE Enter is not the save shortcut", () => {
  // It is the newline key in a clinical narrative. Stealing it would make
  // paragraphs impossible to write.
  assert.equal(
    isSaveShortcut({ key: "Enter", ctrlKey: false, metaKey: false }, NOT_CONFIRMING),
    false,
  );
});

test("the save shortcut is inert while read-only or confirming a discard", () => {
  assert.equal(
    isSaveShortcut(
      { key: "Enter", ctrlKey: true, metaKey: false },
      { readOnly: true, confirmingDiscard: false },
    ),
    false,
  );
  assert.equal(
    isSaveShortcut(
      { key: "Enter", ctrlKey: true, metaKey: false },
      { readOnly: false, confirmingDiscard: true },
    ),
    false,
  );
});

test("other keys are never the save shortcut", () => {
  for (const key of ["a", "Escape", "Tab", "s", " "]) {
    assert.equal(
      isSaveShortcut({ key, ctrlKey: true, metaKey: false }, NOT_CONFIRMING),
      false,
      key,
    );
  }
});

/* ------------------------------------------------------------------ *
 * Closing after a save -- the concurrency-critical rule
 * ------------------------------------------------------------------ */

test("the editor closes only when the write actually landed", () => {
  assert.equal(shouldCloseAfterSave(true), true);
});

test("a REFUSED save leaves the editor open", () => {
  /*
    THE OPTIMISTIC-CONCURRENCY RULE, at the UI boundary.

    consultation-workspace's save() returns false for a version conflict, a
    validation refusal and an unreachable server alike. Closing on any of those
    would discard the doctor's paragraph AND tell them it was stored. The
    workspace keeps the draft, the modal stays open, and the server's own
    sentence is shown above the buttons.
  */
  assert.equal(shouldCloseAfterSave(false), false);
});
