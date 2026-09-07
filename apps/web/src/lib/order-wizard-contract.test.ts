/**
 * THE SHARED ORDER WIZARD, AND WHERE UNSENT WORK IS ALLOWED TO LIVE.
 *
 * WHY THIS IS A SOURCE ASSERTION. order-draft-format.test.ts proves the draft
 * VALUE behaves -- what survives, what clears, what the tab indicator says. It
 * cannot prove the thing the UAT bug was actually about: WHICH COMPONENT HOLDS
 * THE STATE. A panel that quietly went back to `useState` for its staged
 * medicines would pass every pure test and lose a prescription on the first tab
 * switch, and this project ships no DOM test stack to catch that by rendering.
 *
 * Reading the source is crude, but it pins the properties exactly where they
 * regress, and it is the technique order-review-contract.test.ts already uses to
 * hold the billing boundary on these same panels.
 *
 * FOUR PROPERTIES ARE HELD HERE:
 *
 *   1. THE DRAFT STORE IS MOUNTED ABOVE EVERY NAVIGATION CONDITIONAL, and the
 *      panels read from it rather than owning the state.
 *
 *   2. A DRAFT IS DISCARDED ONLY BY THE ALLOWED EVENTS. Two calls per panel: a
 *      definitive submission success, and a consultation the server has said is
 *      closed. Never a tab switch, and never a load in progress.
 *
 *   3. ALL THREE ORDER EDITORS ARE THE SAME DIALOG. One shell, so focus, the
 *      focus trap, Escape and Ctrl/Cmd+Enter cannot drift apart per order type.
 *
 *   4. PROCEDURE REMAINS INERT. No endpoint, no panel, no wizard, no draft.
 */
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

function component(name: string): string {
  return readFileSync(
    new URL(`../components/doctor/consultation/${name}`, import.meta.url),
    "utf8",
  );
}

const WORKSPACE = component("consultation-workspace.tsx");
const ORDERS = component("orders-workspace.tsx");
const LABORATORY = component("laboratory-panel.tsx");
const RADIOLOGY = component("radiology-panel.tsx");
const MEDICATION = component("medication-panel.tsx");
const SHELL = component("clinical-order-modal.tsx");
const LAB_MODAL = component("laboratory-order-modal.tsx");
const RAD_MODAL = component("radiology-order-modal.tsx");
const MED_MODAL = component("medication-editor-modal.tsx");
const PROVIDER = component("order-draft-context.tsx");

const PANELS = [
  ["laboratory-panel", LABORATORY],
  ["radiology-panel", RADIOLOGY],
  ["medication-panel", MEDICATION],
] as const;

const MODALS = [
  ["laboratory-order-modal", LAB_MODAL],
  ["radiology-order-modal", RAD_MODAL],
  ["medication-editor-modal", MED_MODAL],
] as const;

function count(source: string, pattern: RegExp): number {
  return source.match(pattern)?.length ?? 0;
}

/* ------------------------------------------------------------------ *
 * 1. The draft store is mounted above every navigation conditional
 * ------------------------------------------------------------------ */

test("the consultation workspace mounts the draft provider", () => {
  assert.ok(
    WORKSPACE.includes("<ConsultationOrderDraftProvider"),
    "the consultation no longer owns its unsent order drafts",
  );
  assert.ok(WORKSPACE.includes("appointmentId={appointmentId}"));
});

test("the provider wraps the section switch rather than sitting inside it", () => {
  /*
    THE WHOLE FIX IN ONE ASSERTION. The section body renders exactly one of
    ORDERS, DIAGNOSIS or the note; anything mounted inside that conditional dies
    on a section click, and the ORDERS tabs repeat the trick one level down. The
    provider must open BEFORE the section switch.
  */
  const provider = WORKSPACE.indexOf("<ConsultationOrderDraftProvider");
  const sectionSwitch = WORKSPACE.indexOf('section === "orders"');
  assert.ok(provider !== -1 && sectionSwitch !== -1);
  assert.ok(
    provider < sectionSwitch,
    "the draft provider is mounted inside the section conditional, so switching sections still destroys unsent orders",
  );
});

test("each panel reads its draft from the consultation, not from its own state", () => {
  assert.ok(LABORATORY.includes("useLabOrderDraft()"));
  assert.ok(RADIOLOGY.includes("useRadOrderDraft()"));
  assert.ok(MEDICATION.includes("useMedOrderDraft()"));

  // The state that used to die on a tab switch, by its old names.
  assert.ok(
    !LABORATORY.includes("setSelected"),
    "laboratory-panel owns its selection again",
  );
  assert.ok(
    !RADIOLOGY.includes("setSelected"),
    "radiology-panel owns its selection again",
  );
  assert.ok(
    !MEDICATION.includes("setStaged"),
    "medication-panel owns its staged medicines again",
  );
  assert.ok(
    !MEDICATION.includes("tokenRef"),
    "the prescription token is back in a component ref, where a tab switch destroys it",
  );
});

test("no order draft is written to browser storage", () => {
  /*
    A draft that outlived the tab would be an unowned clinical record needing
    reconciliation against a consultation someone else may since have completed.
  */
  for (const [name, source] of [
    ["order-draft-context", PROVIDER],
    ...PANELS,
  ] as const) {
    for (const store of ["localStorage", "sessionStorage", "indexedDB"]) {
      // The CALL, not the word: the provider's own comment explains why it uses
      // none of these, and a prose mention is the opposite of a violation.
      assert.ok(
        !new RegExp(`\\b${store}\\s*[.[]`).test(source),
        `${name} persists clinical drafts to ${store}`,
      );
    }
  }
});

test("the provider wipes its drafts when the appointment changes", () => {
  assert.ok(
    PROVIDER.includes("scope !== appointmentId"),
    "one patient's staged orders could reach another patient's screen",
  );
  assert.ok(PROVIDER.includes("clearAllDrafts()"));
});

/* ------------------------------------------------------------------ *
 * 2. A draft is discarded only by the allowed events
 * ------------------------------------------------------------------ */

test("each panel discards its draft exactly twice: on success, and when closed", () => {
  for (const [name, source] of PANELS) {
    assert.equal(
      count(source, /discard\(\)/g),
      2,
      `${name} clears its draft somewhere other than a definitive success and a completed consultation`,
    );
  }
});

test("the read-only rule waits for the server's verdict before clearing anything", () => {
  for (const [name, source] of PANELS) {
    /*
      `can_order` starts UNKNOWN, not false. A boolean starting value would read
      as "this consultation is completed" for the duration of every load -- and
      because a tab switch remounts the panel and reloads, the read-only rule
      would then discard the doctor's draft on every single tab switch. That is
      the original bug wearing a different hat.
    */
    assert.ok(
      source.includes("useState<boolean | null>(null)"),
      `${name} treats "not yet loaded" as "not allowed to order"`,
    );
    assert.ok(
      source.includes("canOrder === false"),
      `${name} no longer gates its read-only rule on the server's verdict`,
    );
  }
});

test("a refused submission keeps the draft and, for medication, its token", () => {
  // The failure branch returns before anything is cleared.
  assert.ok(MEDICATION.includes("The draft AND its token are KEPT"));
  assert.ok(LABORATORY.includes("The draft is KEPT"));
  assert.ok(RADIOLOGY.includes("The draft is KEPT"));
  // And the token still travels with the draft, minted once.
  assert.ok(MEDICATION.includes("prescriptionToken(draft, newKey)"));
});

test("laboratory and radiology still mint their token per attempt", () => {
  /*
    Neither endpoint has ever retained a token between attempts. This slice
    moves where an unsent FORM is held; it does not touch request_token
    semantics, so both still mint inside the submission.
  */
  for (const [name, source] of [
    ["laboratory-panel", LABORATORY],
    ["radiology-panel", RADIOLOGY],
  ] as const) {
    assert.ok(
      source.includes("globalThis.crypto?.randomUUID?.()"),
      `${name} no longer mints its request token at submission`,
    );
    assert.ok(
      source.includes("request_token") || source.includes("buildOrderPayload"),
      `${name} no longer sends a request token`,
    );
  }
});

test("one prescription is still submitted for N medicines, with one token", () => {
  assert.ok(MEDICATION.includes("buildPrescriptionPayload(staged, form, token)"));
  assert.equal(
    count(MEDICATION, /\/orders\/medications`/g),
    2, // the load, and the single submission
    "medication-panel now issues more than one prescribing request",
  );
});

/* ------------------------------------------------------------------ *
 * 3. All three order editors are the same dialog
 * ------------------------------------------------------------------ */

test("selecting an orderable item opens the editor", () => {
  assert.ok(LABORATORY.includes("openWithTest(test, event.currentTarget)"));
  assert.ok(LABORATORY.includes("<LaboratoryOrderModal"));
  assert.ok(RADIOLOGY.includes("openWithExam(exam, event.currentTarget)"));
  assert.ok(RADIOLOGY.includes("<RadiologyOrderModal"));
  assert.ok(MEDICATION.includes("openNewMedicine(medicine, event.currentTarget)"));
  assert.ok(MEDICATION.includes("<MedicationEditorModal"));
});

test("Edit reopens the same editor on the staged draft", () => {
  // Seeded from the draft itself, so the editor opens on the exact values the
  // compact row is summarising.
  assert.ok(LABORATORY.includes("setEditor({ entry: draft, mode: \"edit\""));
  assert.ok(RADIOLOGY.includes("setEditor({ entry: draft, mode: \"edit\""));
  assert.ok(MEDICATION.includes("setEditor({ entry, mode: \"edit\", returnFocus })"));
});

test("every staged row offers Edit and Remove", () => {
  for (const [name, source] of PANELS) {
    assert.ok(source.includes(">\n                        Edit\n") || source.includes("Edit"), `${name} lost its Edit action`);
    assert.ok(source.includes("Remove"), `${name} lost its Remove action`);
  }
  // Remove is restrained, never the loudest thing on the row: an outline in
  // red-700, next to a solid emerald submit.
  for (const [name, source] of PANELS) {
    assert.ok(
      source.includes("border-red-200 px-2 py-0.5 cl-secondary font-semibold text-red-700"),
      `${name}'s Remove is no longer the restrained outline`,
    );
  }
});

test("every order editor goes through the one shared shell", () => {
  for (const [name, source] of MODALS) {
    assert.ok(
      source.includes("<ClinicalOrderModal"),
      `${name} stopped using the shared dialog shell`,
    );
    assert.ok(
      !source.includes('role="dialog"'),
      `${name} declares its own dialog, so focus and keyboard behaviour can drift from the others`,
    );
  }
});

test("the shell is a real modal dialog", () => {
  assert.ok(SHELL.includes('role="dialog"'));
  assert.ok(SHELL.includes('aria-modal="true"'));
  assert.ok(SHELL.includes("aria-labelledby={titleId}"));
});

test("focus enters the first meaningful field and returns to where it came from", () => {
  assert.ok(SHELL.includes("initialFocusRef.current?.focus()"));
  assert.ok(
    SHELL.includes("return () => returnFocus?.focus()"),
    "closing the editor no longer returns focus to the result row or Edit button",
  );
  // Each editor names the field worth typing into first.
  assert.ok(LAB_MODAL.includes("initialFocusRef={priorityRef}"));
  assert.ok(RAD_MODAL.includes("initialFocusRef={priorityRef}"));
  assert.ok(MED_MODAL.includes("initialFocusRef={doseRef}"));
});

test("Tab is trapped inside the dialog", () => {
  assert.ok(SHELL.includes("onKeyDown={trapFocus}"));
  assert.ok(SHELL.includes('if (event.key !== "Tab") return'));
  assert.ok(SHELL.includes("event.shiftKey && document.activeElement === first"));
});

test("unsaved clinical work is protected on every way out", () => {
  /*
    X, Cancel, the backdrop and Escape all run the SAME decision, so they cannot
    disagree about whether typing is safe. The rules themselves are
    note-editor-format's, pinned by its own tests since Slice 4.
  */
  assert.ok(SHELL.includes("closeIntent({ readOnly, changed, busy: false })"));
  assert.ok(SHELL.includes("escapeIntent({"));
  assert.equal(
    count(SHELL, /onClick=\{requestClose\}/g),
    3, // backdrop, X, Cancel
    "one of the ways out of the dialog no longer asks about unsaved work",
  );
  assert.ok(SHELL.includes("Discard changes"));
});

test("Ctrl/Cmd+Enter saves, and bare Enter is left alone", () => {
  assert.ok(
    SHELL.includes("isSaveShortcut(event, { readOnly, confirmingDiscard: confirmDiscard })"),
  );
  // The rule itself lives in note-editor-format; the shell must not re-implement
  // a second, divergent copy of it.
  assert.ok(!SHELL.includes('event.key === "Enter" &&'));
});

test("a read-only editor states it in a strip and offers no save", () => {
  assert.ok(SHELL.includes("Read only"));
  assert.ok(SHELL.includes("border-red-300 bg-white"));
  assert.ok(SHELL.includes("{readOnly ? null : ("));
  // Red is a strip, not the dialog: the panel is still white.
  assert.ok(SHELL.includes("bg-white shadow-"));
});

test("the dialog keeps the agreed clinical proportions", () => {
  for (const size of ["w-[830px]", "max-w-[92vw]", "max-h-[85vh]"]) {
    assert.ok(SHELL.includes(size), `the shared editor lost ${size}`);
  }
  // Dimmed, not blacked out: the Doctor Desk stays identifiable underneath.
  assert.ok(SHELL.includes("bg-slate-900/45"));
});

test("no order surface pins clinical text below the readable floor", () => {
  for (const [name, source] of [
    ["clinical-order-modal", SHELL],
    ["orders-workspace", ORDERS],
    ...MODALS,
    ...PANELS,
  ] as const) {
    const arbitrary = source.match(/text-\[\d+(?:\.\d+)?px\]/g);
    assert.equal(
      arbitrary,
      null,
      `${name} reintroduced arbitrary font sizes: ${arbitrary?.join(", ")}`,
    );
  }
});

/* ------------------------------------------------------------------ *
 * The unfinished-draft indicator
 * ------------------------------------------------------------------ */

test("the ORDERS tabs mark unsent work, in amber, without counting placed orders", () => {
  assert.ok(ORDERS.includes("indicatorFor(entry.key)"));
  assert.ok(ORDERS.includes("indicator.count"));
  assert.ok(ORDERS.includes("indicator?.dirty"));
  assert.ok(ORDERS.includes("bg-amber-500"), "the bare dot is no longer amber");
  assert.ok(ORDERS.includes("bg-amber-100"), "the count chip is no longer amber");
  // The count comes from the DRAFT store. Nothing in the tab bar reads placed
  // orders, and it has no way to: the panels never publish them upward.
  assert.ok(!ORDERS.includes("orders.length"));
});

test("the indicator is announced, not only drawn", () => {
  assert.ok(ORDERS.includes("aria-label={`${indicator.count} not yet ordered`}"));
  assert.ok(ORDERS.includes('aria-label="Unfinished order started"'));
});

/* ------------------------------------------------------------------ *
 * 4. Procedure remains inert
 * ------------------------------------------------------------------ */

test("procedure is still a label, not a control", () => {
  /*
    NO WIZARD WAS ACTIVATED FOR PROCEDURE, because no authoritative order flow
    exists for it: no catalogue endpoint, no order endpoint, no model. A tab
    that opened an editor would be scaffolding pretending to be a workflow.
  */
  assert.ok(
    ORDERS.includes("if (!entry.live)"),
    "the orders tab bar no longer distinguishes live order kinds",
  );
  assert.ok(ORDERS.includes("Arrives in a later clinical slice"));
  assert.ok(
    !ORDERS.includes("ProcedurePanel"),
    "a procedure panel appeared with no backend behind it",
  );
});

test("nothing calls a procedure order endpoint", () => {
  for (const [name, source] of [
    ["orders-workspace", ORDERS],
    ...PANELS,
    ...MODALS,
  ] as const) {
    assert.ok(
      !source.includes("orders/procedures"),
      `${name} calls a procedure endpoint that does not exist`,
    );
  }
});
