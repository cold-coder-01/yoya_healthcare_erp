/**
 * THE LABORATORY RESULT SHEET'S WIRING, HELD AT THE SOURCE.
 *
 * WHY SOURCE ASSERTIONS. lab-result-format.test.ts proves the pure decisions.
 * It cannot prove what the COMPONENTS do with them -- whether Save draft stays
 * open, whether Mark entered closes on a refusal, whether a queue refresh can
 * reach the typed draft -- and this project ships no DOM test stack. Reading
 * the source pins those properties exactly where they would regress, the
 * technique order-wizard-contract.test.ts and lab-workstation-contract.test.ts
 * already use.
 *
 * PROPERTIES HELD HERE:
 *
 *   1. THE MODAL IS LAB-OWNED. It imports nothing from the Doctor Desk: not
 *      ClinicalOrderModal, not the order draft store, not a doctor editor. It
 *      reuses only the pure keyboard/close rules from lib.
 *   2. IT IS A REAL, ACCESSIBLE MODAL DIALOG: dialog semantics, labelled and
 *      described, focus in and back, Tab trapped, alert for errors.
 *   3. UNSAVED TYPING IS PROTECTED on every way out, and Ctrl/Cmd+Enter marks
 *      entered while bare Enter is left alone.
 *   4. SAVE DRAFT STAYS OPEN; MARK ENTERED CLOSES ONLY ON SUCCESS; a failure
 *      never touches the typed draft; busy disables the controls.
 *   5. THE MODAL OWNS ITS DRAFT: it is bound to the result it opened with, and
 *      the workstation's refreshes cannot reach it.
 *   6. SLICE 3 STOPS AT ENTERED: no validate, release, cancel or reset control
 *      or route anywhere in the laboratory tree.
 */
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

function read(relative: string): string {
  return readFileSync(new URL(`../${relative}`, import.meta.url), "utf8");
}

/** Source with comments removed: the property is what a file DOES. */
function code(source: string): string {
  return source
    .replace(/\/\*[\s\S]*?\*\//g, "")
    .replace(/\{\s*\/\*[\s\S]*?\*\/\s*\}/g, "")
    .replace(/^\s*\/\/.*$/gm, "");
}

/** The body of one `const name = useCallback(` block, up to the next one. */
function block(source: string, start: string, end: string): string {
  const from = source.indexOf(start);
  assert.ok(from >= 0, `missing ${start}`);
  const to = source.indexOf(end, from + start.length);
  assert.ok(to > from, `missing ${end} after ${start}`);
  return source.slice(from, to);
}

function count(source: string, pattern: RegExp): number {
  return source.match(pattern)?.length ?? 0;
}

const MODAL = read("components/laboratory/lab-result-modal.tsx");
const WORKSTATION = read("components/laboratory/lab-workstation.tsx");
const PANEL = read("components/laboratory/lab-request-panel.tsx");
const FILTERS = read("components/laboratory/lab-filters.tsx");
const QUEUE = read("components/laboratory/lab-queue.tsx");

const LAB_TREE = [
  ["lab-result-modal", MODAL],
  ["lab-workstation", WORKSTATION],
  ["lab-request-panel", PANEL],
  ["lab-filters", FILTERS],
  ["lab-queue", QUEUE],
] as const;

/* ------------------------------------------------------------------ *
 * 1. Lab-owned
 * ------------------------------------------------------------------ */

test("the result modal lives in the laboratory tree and imports nothing from the Doctor Desk", () => {
  for (const [name, source] of LAB_TREE) {
    const emitted = code(source);
    assert.ok(!emitted.includes("@/components/doctor"), `${name} imports a Doctor component`);
    assert.ok(!emitted.includes("clinical-order-modal"), `${name} uses the Doctor shell`);
    assert.ok(!emitted.includes("ClinicalOrderModal"), `${name} uses the Doctor shell`);
    assert.ok(!emitted.includes("order-draft-context"), `${name} uses the consultation draft store`);
    assert.ok(!emitted.includes("order-draft-format"), `${name} uses consultation drafts`);
    assert.ok(!emitted.includes("note-editor-modal"), `${name} imports a Doctor editor`);
  }
});

test("the modal reuses only the pure close/keyboard rules, from lib", () => {
  assert.ok(MODAL.includes('from "@/lib/note-editor-format"'));
  for (const helper of ["closeIntent", "escapeIntent", "isSaveShortcut", "shouldCloseAfterSave"]) {
    assert.ok(MODAL.includes(helper), `the modal should reuse ${helper}`);
  }
});

test("the modal carries no Doctor wording", () => {
  const emitted = code(MODAL).toLowerCase();
  for (const word of ["doctor", "consultation", "prescription", "clinical order", "add to order"]) {
    assert.ok(!emitted.includes(word), `"${word}" is Doctor Desk wording`);
  }
});

/* ------------------------------------------------------------------ *
 * 2. A real, accessible dialog
 * ------------------------------------------------------------------ */

test("the sheet is a labelled, described modal dialog", () => {
  assert.ok(MODAL.includes('role="dialog"'));
  assert.ok(MODAL.includes('aria-modal="true"'));
  assert.ok(MODAL.includes("aria-labelledby={titleId}"));
  assert.ok(MODAL.includes("aria-describedby={subtitleId}"));
  assert.ok(MODAL.includes('aria-label="Close laboratory result sheet"'));
  assert.ok(MODAL.includes("Enter laboratory results"));
});

test("errors inside the modal are announced", () => {
  assert.ok(MODAL.includes('role="alert"'));
  assert.ok(MODAL.includes("{error}"));
});

test("focus enters the first result value and returns to the trigger", () => {
  assert.ok(MODAL.includes("ref={index === 0 ? firstValueRef : undefined}"));
  assert.ok(MODAL.includes("readOnly ? closeRef.current : firstValueRef.current"));
  assert.ok(
    MODAL.includes("return () => returnFocus?.focus()"),
    "closing must return focus to the Enter results / View result button",
  );
  assert.ok(WORKSTATION.includes("onEnterResults={(trigger) =>"));
  assert.ok(PANEL.includes("onEnterResults(event.currentTarget)"));
  assert.ok(PANEL.includes("onViewResult(event.currentTarget)"));
});

test("Tab is trapped inside the sheet", () => {
  assert.ok(MODAL.includes("onKeyDown={trapFocus}"));
  assert.ok(MODAL.includes('if (event.key !== "Tab") return'));
  assert.ok(MODAL.includes("event.shiftKey && document.activeElement === first"));
  assert.ok(MODAL.includes("select:not([disabled])"), "the flag select must be in the trap");
});

/* ------------------------------------------------------------------ *
 * 3. Unsaved typing and the keyboard
 * ------------------------------------------------------------------ */

test("every way out asks before discarding typed results", () => {
  assert.ok(MODAL.includes("closeIntent({ readOnly, changed, busy: isBusy })"));
  assert.equal(
    count(MODAL, /onClick=\{requestClose\}/g),
    3, // backdrop, X, Cancel
    "one of the ways out no longer asks about unsaved results",
  );
  assert.ok(MODAL.includes("Discard changes"));
  assert.ok(MODAL.includes("Keep editing"));
});

test("Escape follows the shared rule, and dismisses the discard prompt first", () => {
  assert.ok(MODAL.includes('event.key === "Escape"'));
  assert.ok(MODAL.includes("escapeIntent({"));
  assert.ok(MODAL.includes('if (intent === "dismiss-confirm") setConfirmDiscard(false)'));
});

test("Ctrl/Cmd+Enter marks entered, and bare Enter is left alone", () => {
  const keyboard = block(MODAL, "function onKeyDown(event: KeyboardEvent)", "document.addEventListener");
  assert.ok(
    keyboard.includes("isSaveShortcut(event, { readOnly, confirmingDiscard: confirmDiscard })"),
  );
  assert.ok(keyboard.includes("void markEntered()"));
  assert.ok(!keyboard.includes("saveDraft"), "the shortcut is Mark entered, not Save draft");
  assert.ok(!MODAL.includes('event.key === "Enter" &&'), "no second copy of the shortcut rule");
  assert.ok(!code(MODAL).includes("<form"), "a form would turn bare Enter into a submit");
});

/* ------------------------------------------------------------------ *
 * 4. Save draft, Mark entered, busy and failure
 * ------------------------------------------------------------------ */

const SAVE_DRAFT = block(MODAL, "const saveDraft = useCallback(", "const markEntered = useCallback(");
const MARK_ENTERED = block(MODAL, "const markEntered = useCallback(", "/* ---------------- keyboard");

test("Save draft stays open and confirms only what the server accepted", () => {
  assert.ok(!SAVE_DRAFT.includes("onClose("), "Save draft must never close the sheet");
  assert.ok(SAVE_DRAFT.includes("await onSaveDraft(resultPayload(draft))"));
  assert.ok(SAVE_DRAFT.includes("setBaseline(draftFromResult(outcome.result))"));
  assert.ok(SAVE_DRAFT.includes("setSavedSinceOpen(true)"));
});

test("Mark entered closes only on success", () => {
  assert.ok(MARK_ENTERED.includes("await onMarkEntered(resultPayload(draft))"));
  assert.ok(MARK_ENTERED.includes("if (shouldCloseAfterSave(ok)) onClose();"));
  assert.equal(count(MARK_ENTERED, /onClose\(/g), 1, "no other close path in Mark entered");
});

test("a failure keeps every typed value and shows the reason", () => {
  for (const [name, source] of [
    ["saveDraft", SAVE_DRAFT],
    ["markEntered", MARK_ENTERED],
  ] as const) {
    assert.ok(!source.includes("setDraft("), `${name} must never replace the typed draft`);
    assert.ok(source.includes("setError(outcome.message)"), `${name} must show the refusal`);
    assert.ok(source.includes("finally {"), `${name} must always clear busy`);
    assert.ok(source.includes("setBusy(null)"), `${name} must always clear busy`);
  }
});

test("a submit in flight blocks a second one and disables the controls", () => {
  assert.ok(SAVE_DRAFT.includes("if (readOnly || busy !== null) return;"));
  assert.ok(MARK_ENTERED.includes("if (readOnly || busy !== null) return;"));
  assert.ok(MODAL.includes("const fieldsDisabled = !readOnly && isBusy;"));
  assert.ok(count(MODAL, /disabled=\{fieldsDisabled\}/g) >= 6, "every text field disables while busy");
  assert.ok(MODAL.includes("disabled={readOnly || fieldsDisabled}"), "the flag select disables too");
  assert.ok(MODAL.includes("disabled={isBusy || !changed}"), "Save draft");
  assert.ok(count(MODAL, /disabled=\{isBusy\}/g) >= 3, "X, Cancel and Mark entered");
});

test("the completeness rule is not restated in the modal", () => {
  // Mark entered is never disabled for a blank value; the model decides.
  const emitted = code(MODAL);
  assert.ok(!emitted.includes(".trim()"), "the modal must not trim result values");
  assert.ok(!/disabled=\{[^}]*result_value/.test(emitted));
});

test("the specimen and the test are shown, never editable", () => {
  assert.ok(MODAL.includes("sampleTypeLabel(source?.sample_type)"));
  assert.ok(MODAL.includes("(read-only)"));
  const emitted = code(MODAL);
  for (const forbidden of ["sample_type: event", "test_id", "request_line_id:", "sequence:"]) {
    assert.ok(!emitted.includes(forbidden), `${forbidden} must not be editable`);
  }
});

test("the abnormal flag offers the model's options and names the default honestly", () => {
  assert.ok(MODAL.includes("result.abnormal_flag_options.map("));
  assert.ok(MODAL.includes("abnormalFlagHint(line.abnormal_flag)"));
  assert.ok(!code(MODAL).toLowerCase().includes("unassessed"));
});

test("a read-only sheet offers no save and no entry", () => {
  assert.ok(MODAL.includes("{readOnly ? null : ("));
  assert.ok(MODAL.includes('{readOnly ? "Close" : "Cancel"}'));
  assert.ok(MODAL.includes("Read only"));
});

/* ------------------------------------------------------------------ *
 * 5. The modal owns its draft
 * ------------------------------------------------------------------ */

test("the draft is copied once from the result the modal opened with", () => {
  assert.ok(MODAL.includes("useState<LabResultDraft>(() =>"));
  assert.equal(
    count(MODAL, /draftFromResult\(result\)/g),
    2,
    "baseline and draft are each captured once from the opening result",
  );
  // No effect may resync the draft from props: the only writers are the
  // technician's own change handlers.
  for (const effect of MODAL.split("useEffect(").slice(1)) {
    const body = effect.slice(0, effect.indexOf("}, ["));
    assert.ok(!body.includes("setDraft("), "an effect must not overwrite the typed draft");
    assert.ok(!body.includes("setBaseline("), "an effect must not move the baseline");
  }
});

test("the workstation binds the modal to its snapshot, not to the live detail", () => {
  assert.ok(WORKSTATION.includes("key={resultEditor.result.id}"));
  assert.ok(WORKSTATION.includes("request={resultEditor.request}"));
  assert.ok(WORKSTATION.includes("result={resultEditor.result}"));
  assert.ok(!WORKSTATION.includes("result={detail"), "a refresh would reach the typed draft");
  assert.ok(!WORKSTATION.includes("LabResultDraft"), "the workstation must not hold the draft");
});

test("a background refresh updates the panel, never the modal", () => {
  const writer = block(WORKSTATION, "const writeResult = useCallback(", "const saveResultDraft = useCallback(");
  assert.ok(writer.includes("setDetail(payload.data.request)"));
  assert.ok(!writer.includes("setResultEditor("), "a write must not replace the open sheet");
});

/* ------------------------------------------------------------------ *
 * No duplicate results from the browser
 * ------------------------------------------------------------------ */

test("a second Enter results click is never sent", () => {
  const opener = block(WORKSTATION, "const openResultEntry = useCallback(", "const viewResult = useCallback(");
  assert.ok(opener.includes("if (pendingId !== null || resultEditor !== null) return;"));
  assert.ok(opener.includes("setPendingId(requestId)"));
  assert.ok(opener.includes("openResultPath(requestId)"));
  assert.ok(PANEL.includes("canEnterResults(detail)"));
});

test("reopening goes back to the server, so it loads the same persisted result", () => {
  // The open route is find-or-create: a reopen returns the saved draft.
  const opener = block(WORKSTATION, "const openResultEntry = useCallback(", "const viewResult = useCallback(");
  assert.ok(opener.includes("result: payload.data.result"));
});

test("View result opens what is on screen read-only and sends nothing", () => {
  const viewer = block(WORKSTATION, "const viewResult = useCallback(", "const writeResult = useCallback(");
  assert.ok(viewer.includes("readOnly: true"));
  assert.ok(!viewer.includes("postLab("));
  assert.ok(!viewer.includes("fetch("));
  assert.ok(PANEL.includes("canViewResult(detail)"));
});

test("the panel never renders entry as a disabled control for the wrong status", () => {
  assert.ok(!PANEL.includes("disabled={!canEnterResults"));
  assert.ok(!PANEL.includes("disabled={!canViewResult"));
});

/* ------------------------------------------------------------------ *
 * 6. Slice 3 stops at entered
 * ------------------------------------------------------------------ */

test("no validate, release, cancel or reset control exists in the laboratory tree", () => {
  for (const [name, source] of LAB_TREE) {
    const emitted = code(source);
    for (const pattern of [
      />\s*Validate\b/,
      />\s*Release\b/,
      />\s*Reset to draft\b/,
      /"Validate"/,
      /"Release"/,
      /\/validate\b/,
      /\/release\b/,
      /\/cancel\b/,
      /\/reset/,
      /onValidate|onRelease|onReset|onCancelResult/,
    ]) {
      assert.ok(!pattern.test(emitted), `${name} matches ${pattern}`);
    }
  }
});

test("the modal performs no fetch and knows no Odoo address", () => {
  const emitted = code(MODAL);
  assert.ok(!emitted.includes("fetch("), "the modal carries its writes through props");
  for (const banned of ["yoya-emr", "ODOO_BASE_URL", "localhost:8", "/api/laboratory"]) {
    assert.ok(!emitted.includes(banned), banned);
  }
});

test("the workstation's result routes are the BFF's", () => {
  for (const helper of ["openResultPath(", "saveResultPath(", "enterResultPath("]) {
    assert.ok(WORKSTATION.includes(helper), helper);
  }
  assert.ok(!WORKSTATION.includes("yoya-emr/api"));
  assert.ok(!WORKSTATION.includes("/save\""));
});

test("Mark entered is ONE request, never save followed by enter", () => {
  const marker = block(WORKSTATION, "const markResultEntered = useCallback(", "WHICH REQUEST THE PANEL MAY SHOW.");
  assert.ok(marker.includes("enterResultPath(resultId)"));
  assert.ok(!marker.includes("saveResultPath("), "the atomic route replaces a save+enter chain");
});

test("no financial vocabulary is rendered by the result sheet or its status", () => {
  const rendered = [code(MODAL), code(block(PANEL, "function ResultStatus(", "export default function LabRequestPanel("))]
    .join("\n")
    .toLowerCase();
  // Whole words: "etb" would otherwise match inside identifiers like setBusy.
  for (const banned of ["amount", "balance", "outstanding", "invoice", "receipt", "payer", "birr", "etb", "cashier"]) {
    assert.ok(!new RegExp(`\\b${banned}\\b`).test(rendered), banned);
  }
});
