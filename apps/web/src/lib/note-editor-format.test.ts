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
  DIAGNOSIS_IDLE_TEXT,
  EMPTY_SECTION_TEXT,
  NOTE_EDITOR_OPEN_TEXT,
  NOTE_IDLE_TEXT,
  ORDERS_IDLE_TEXT,
  activeNoteField,
  closeIntent,
  consultationIdleText,
  escapeIntent,
  isDocumented,
  isSaveShortcut,
  isStaleNoteSelection,
  previewText,
  sectionActionLabel,
  shouldCloseAfterSave,
} from "./note-editor-format.ts";
import { NOTE_FIELDS } from "./consultation-format.ts";

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

/* ------------------------------------------------------------------ *
 * WHICH EDITOR IS OPEN -- the invariant, as one resolution
 *
 * THE DEFECT THIS SECTION EXISTS FOR. The desk could sit in a state equivalent
 * to "a note section is open" while NO editor was mounted: the command bar said
 * "Note open for editing" and there was nothing on screen to close. Resolving
 * the open section ONCE, and reading the modal's gate and the footer's sentence
 * off that same value, is what makes the two facts the same fact.
 * ------------------------------------------------------------------ */

test("no section selected resolves to no open editor", () => {
  assert.equal(activeNoteField(null, NOTE_FIELDS), null);
  assert.equal(activeNoteField(undefined, NOTE_FIELDS), null);
  // An empty string is not a section either, and must not resolve to fields[0].
  assert.equal(activeNoteField("", NOTE_FIELDS), null);
});

test("opening a section resolves to that field's descriptor", () => {
  const field = activeNoteField("assessment", NOTE_FIELDS);
  assert.ok(field, "assessment did not resolve to an editable field");
  assert.equal(field.key, "assessment");
  assert.equal(field.label, "Assessment");
});

test("every writable section can be opened", () => {
  // A field the save path knows about but the resolver does not would be a
  // section that silently refuses to open.
  for (const known of NOTE_FIELDS) {
    const field = activeNoteField(known.key, NOTE_FIELDS);
    assert.ok(field, `${known.key} does not resolve`);
    assert.equal(field.key, known.key);
  }
});

test("an unknown section opens nothing", () => {
  // Not a throw: a key the save payload cannot carry is a section that must not
  // be opened, and null is what the staleness check then reports.
  assert.equal(activeNoteField("vital_signs", NOTE_FIELDS), null);
  assert.equal(activeNoteField("diagnosis", NOTE_FIELDS), null);
});

test("a selection that resolves to no field is STALE", () => {
  // THE hidden state, named. The render gate is closed while the selection is
  // still set -- the workspace normalises this to null on sight.
  assert.equal(isStaleNoteSelection("vital_signs", null), true);
});

test("no selection is never stale, so the normaliser cannot loop", () => {
  /*
    The property that keeps the defensive effect safe. After it writes null the
    predicate is false, so the re-render it caused does nothing -- which is the
    difference between a backstop and a render loop.
  */
  assert.equal(isStaleNoteSelection(null, null), false);
  assert.equal(isStaleNoteSelection(undefined, null), false);
  assert.equal(isStaleNoteSelection("", null), false);
});

test("a selection that DID resolve is not stale", () => {
  const field = activeNoteField("plan", NOTE_FIELDS);
  assert.equal(isStaleNoteSelection("plan", field), false);
});

test("resolution and staleness are exhaustive: a set section either opens or is cleared", () => {
  /*
    The invariant stated as a law rather than as cases: for ANY selection, the
    editor is mounted or the selection is reported stale, never neither. That is
    what makes "no modal visible" and "nothing open" the same state.
  */
  for (const key of [
    ...NOTE_FIELDS.map((field) => field.key as string),
    "vital_signs",
    "diagnosis",
    "orders",
    "assessment ",
  ]) {
    const resolved = activeNoteField(key, NOTE_FIELDS);
    const stale = isStaleNoteSelection(key, resolved);
    assert.notEqual(
      resolved !== null,
      stale,
      `${key}: mounted and stale must be opposites, never both or neither`,
    );
  }
});

/* ------------------------------------------------------------------ *
 * The command bar's sentence
 * ------------------------------------------------------------------ */

test("the command bar says an editor is open ONLY when one is", () => {
  // THE DEFECT, as a single assertion. The footer used to say this whenever the
  // NOTE tab was idle, with nothing mounted behind the words.
  assert.equal(
    consultationIdleText({ section: "note", editorOpen: true }),
    NOTE_EDITOR_OPEN_TEXT,
  );
  assert.notEqual(
    consultationIdleText({ section: "note", editorOpen: false }),
    NOTE_EDITOR_OPEN_TEXT,
  );
});

test("NO section can claim an open editor while none is mounted", () => {
  for (const section of ["note", "diagnosis", "orders", "results", "history"]) {
    assert.notEqual(
      consultationIdleText({ section, editorOpen: false }),
      NOTE_EDITOR_OPEN_TEXT,
      section,
    );
  }
});

test("the idle note tab describes a record to read, not an open editor", () => {
  assert.equal(
    consultationIdleText({ section: "note", editorOpen: false }),
    NOTE_IDLE_TEXT,
  );
  assert.equal(NOTE_IDLE_TEXT, "Select a section to write your note");
});

test("the diagnosis and orders sentences are unchanged", () => {
  // They were already true of the section rather than of editor state, so this
  // fix must not have touched them.
  assert.equal(
    consultationIdleText({ section: "diagnosis", editorOpen: false }),
    DIAGNOSIS_IDLE_TEXT,
  );
  assert.equal(DIAGNOSIS_IDLE_TEXT, "Diagnoses save as you record them");
  assert.equal(
    consultationIdleText({ section: "orders", editorOpen: false }),
    ORDERS_IDLE_TEXT,
  );
  assert.equal(ORDERS_IDLE_TEXT, "Orders are placed one at a time");
});

test("an open editor outranks the section, because it is the more specific truth", () => {
  // The modal covers the command bar, so this is rarely read -- but a sentence
  // that is true regardless of which tab is behind it cannot go stale.
  for (const section of ["note", "diagnosis", "orders"]) {
    assert.equal(
      consultationIdleText({ section, editorOpen: true }),
      NOTE_EDITOR_OPEN_TEXT,
      section,
    );
  }
});
