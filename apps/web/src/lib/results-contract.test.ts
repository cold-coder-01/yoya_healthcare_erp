/**
 * What the Results tab must be, and must never become.
 *
 * WHY THIS IS A SOURCE ASSERTION. results-format.test.ts exercises the
 * DECISIONS -- which word an abnormality carries, whether an empty report says
 * so. What it cannot see is the WIRING: whether the tab is actually live,
 * whether a mutation control crept onto a review-only screen, whether the
 * viewer is still read-only, whether someone reached for a polling timer. This
 * project ships no DOM test stack (see the note atop note-editor-format.test.ts
 * for why), and reading the source is the same technique the order panels use
 * to hold their billing boundary.
 *
 * THE PROPERTY THAT MATTERS MOST IS AN ABSENCE. A Results screen that could
 * write would be a second, unaudited path into clinical records that the
 * laboratory and imaging departments own. Absences do not fail typechecks, so
 * they are asserted here.
 */
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

/*
  Line endings are normalised because this repository is worked on from
  Windows: a CRLF checkout would fail a multi-line assertion for a reason that
  has nothing to do with the code being asserted.
*/
function source(path: string): string {
  return readFileSync(new URL(`../${path}`, import.meta.url), "utf8").replace(
    /\r\n/g,
    "\n",
  );
}

/**
 * The same file with comments removed.
 *
 * THE ABSENCE ASSERTIONS BELOW ARE ABOUT CODE, NOT PROSE. These files explain
 * at length WHY there is no acknowledge control and no dirty state, so a naive
 * substring search finds the very words the explanation uses and fails on the
 * documentation rather than on a regression. Stripping comments first is what
 * makes those assertions mean what they say.
 */
function code(text: string): string {
  return text
    .replace(/\/\*[\s\S]*?\*\//g, " ")
    .replace(/^\s*\/\/.*$/gm, " ");
}

const WORKSPACE = source("components/doctor/consultation/results-workspace.tsx");
const VIEWER = source(
  "components/doctor/consultation/clinical-result-viewer-modal.tsx",
);
const VIEWS = source("components/doctor/consultation/result-views.tsx");
const CONSULTATION = source(
  "components/doctor/consultation/consultation-workspace.tsx",
);
const SECTIONS = source("lib/diagnosis-format.ts");
const ROUTE = source("app/api/doctor/visits/[appointmentId]/results/route.ts");
const TYPES = source("types/doctor-results.ts");

/* ------------------------------------------------------------------ *
 * The tab is live, and the others are untouched
 * ------------------------------------------------------------------ */

test("the RESULTS tab is live", () => {
  assert.ok(
    SECTIONS.includes('{ key: "results", label: "Results", live: true }'),
    "the Results tab is still inert",
  );
});

test("activating Results disturbed no other section", () => {
  assert.ok(SECTIONS.includes('{ key: "note", label: "Note", live: true }'));
  assert.ok(SECTIONS.includes('{ key: "diagnosis", label: "Diagnosis", live: true }'));
  assert.ok(SECTIONS.includes('{ key: "orders", label: "Orders", live: true }'));
  // History has no endpoint and no model behind it, so it stays honest text.
  assert.ok(SECTIONS.includes('{ key: "history", label: "History", live: false }'));
});

test("the workspace is mounted only while the Results section is open", () => {
  // Mounting on open is what makes opening the tab refetch, which is the whole
  // refresh strategy. A permanently mounted panel would show a stale card.
  assert.ok(CONSULTATION.includes('section === "results" ?'));
  assert.ok(
    CONSULTATION.includes(
      "<ResultsWorkspace key={appointmentId} appointmentId={appointmentId} />",
    ),
  );
});

/* ------------------------------------------------------------------ *
 * One GET, and no other verb anywhere
 * ------------------------------------------------------------------ */

test("the BFF route exports GET and nothing else", () => {
  assert.ok(ROUTE.includes("export async function GET("));
  for (const verb of ["POST", "PUT", "PATCH", "DELETE"]) {
    assert.ok(
      !ROUTE.includes(`export async function ${verb}(`),
      `the results route exports ${verb}`,
    );
  }
});

test("the route forwards the Odoo envelope and re-derives nothing clinical", () => {
  assert.ok(ROUTE.includes("requireOdooSession()"));
  assert.ok(ROUTE.includes("forwardOdooResult("));
  assert.ok(ROUTE.includes("/visits/${parsed.value}/results"));
  // No clinical rule may be DECIDED in the BFF: it forwards, it does not judge.
  const body = code(ROUTE);
  for (const banned of ["released", "validated", "abnormal", "filter(", "status ==="]) {
    assert.ok(!body.includes(banned), `${banned} leaked into the BFF`);
  }
});

test("the workspace issues no write request", () => {
  const body = code(WORKSPACE);
  const methods = body.match(/method:\s*"(\w+)"/g) ?? [];
  assert.deepEqual(methods, [], "the results workspace sends a non-GET request");
  for (const verb of ['"POST"', '"PUT"', '"PATCH"', '"DELETE"']) {
    assert.ok(!body.includes(verb), `${verb} appears in the workspace`);
  }
});

/* ------------------------------------------------------------------ *
 * No invented review state
 * ------------------------------------------------------------------ */

test("no acknowledge / reviewed / sign-off vocabulary exists anywhere", () => {
  /*
    No model records that a doctor has read a result. A control -- or even a
    type -- would let the desk assert something no record supports, and a false
    "seen" on a clinical screen is worse than no marker at all.
  */
  for (const [name, text] of [
    ["results-workspace", WORKSPACE],
    ["clinical-result-viewer-modal", VIEWER],
    ["doctor-results types", TYPES],
    ["results route", ROUTE],
    ["result-views", VIEWS],
  ] as const) {
    const lower = code(text).toLowerCase();
    for (const banned of [
      "acknowledge",
      "mark reviewed",
      "markreviewed",
      "sign-off",
      "signoff",
      "reviewed_at",
      "reviewed_by",
    ]) {
      assert.ok(!lower.includes(banned), `'${banned}' appeared in ${name}`);
    }
  }
});

test("the type contract offers no permission or editability flag", () => {
  const declared = code(TYPES);
  for (const banned of ["can_order", "can_review", "editable", "cancellable"]) {
    assert.ok(!declared.includes(banned), `${banned} is declared in the contract`);
  }
});

/* ------------------------------------------------------------------ *
 * The viewer is a viewer
 * ------------------------------------------------------------------ */

test("the report viewer holds no draft, no dirty state and no save path", () => {
  /*
    It deliberately does NOT reuse NoteEditorModal: that modal owns a buffer, a
    dirty comparison and a save that can be refused, none of which a report has
    -- and every one of which a later edit could accidentally reanimate on a
    surface that must never write.
  */
  const body = code(VIEWER);
  for (const banned of ["onSave", "onChange", "dirty", "textarea", "confirmDiscard"]) {
    assert.ok(!body.includes(banned), `${banned} appeared in the report viewer`);
  }
});

test("the viewer closes by every conventional route", () => {
  assert.ok(VIEWER.includes('role="dialog"'));
  assert.ok(VIEWER.includes('aria-modal="true"'));
  assert.ok(VIEWER.includes("aria-labelledby={titleId}"));
  assert.ok(VIEWER.includes('event.key !== "Escape"'), "Escape does not close");
  assert.ok(VIEWER.includes('aria-label="Close result"'), "no labelled X");
  // Backdrop, the X and the footer button: three controls, one handler.
  assert.equal(
    (VIEWER.match(/onClick=\{onClose\}/g) ?? []).length,
    3,
    "a close control stopped routing through onClose",
  );
});

test("focus is managed into the dialog and back to the opener", () => {
  assert.ok(VIEWER.includes("closeRef.current?.focus()"));
  assert.ok(VIEWER.includes('if (event.key !== "Tab") return;'), "no focus trap");
  assert.ok(
    WORKSPACE.includes("requestAnimationFrame(() => origin.focus())"),
    "focus does not return to the card that opened the report",
  );
});

test("the read-only treatment is neutral, never red", () => {
  /*
    Red on this screen belongs to a `critical` abnormal flag alone. Spending it
    on chrome would drain it from the one place it has to mean "act now".
  */
  assert.ok(VIEWER.includes('badge = "Read only"'));
  assert.ok(!code(VIEWER).includes("red-"), "the viewer chrome uses red");
});

/* ------------------------------------------------------------------ *
 * Refresh, and the absence of realtime machinery
 * ------------------------------------------------------------------ */

test("results refresh on mount and on demand, with no polling primitive", () => {
  assert.ok(WORKSPACE.includes("void load();"), "no mount fetch");
  assert.ok(
    WORKSPACE.includes("}, [appointmentId, reloadToken]);"),
    "the refresh token no longer drives the read",
  );
  assert.ok(WORKSPACE.includes('aria-label="Refresh results"'), "no refresh control");
  assert.ok(WORKSPACE.includes("checkedAtText(checkedAt)"), "no freshness line");
  const body = code(WORKSPACE);
  for (const banned of ["setInterval", "setTimeout", "EventSource", "WebSocket"]) {
    assert.ok(!body.includes(banned), `${banned} appeared in the workspace`);
  }
});

/* ------------------------------------------------------------------ *
 * Status hierarchy (the 7B polish pass)
 * ------------------------------------------------------------------ */

test("the status badge renders a dot AND the status word", () => {
  /*
    COLOUR IS NEVER THE ONLY SIGNAL. The dot is what lets a doctor spot a card
    that has changed while scanning a column; the label is what makes the
    status survive greyscale, a printed chart and a screen reader. A badge that
    dropped the word would be a regression no typecheck could catch.
  */
  assert.ok(WORKSPACE.includes("function StatusBadge("));
  assert.ok(WORKSPACE.includes("const tone = resultStatusTone(status);"));
  assert.ok(
    WORKSPACE.includes('<span aria-hidden className={`h-1.5 w-1.5 rounded-full ${tone.dot}`} />'),
    "the status dot is gone",
  );
  assert.ok(WORKSPACE.includes("{label}"), "the status word is gone");
  // The dot is decorative, so it is hidden from assistive technology and the
  // label carries the meaning.
  assert.ok(WORKSPACE.includes("aria-hidden className={`h-1.5 w-1.5"));
});

test("every status is rendered through the one badge component", () => {
  /*
    ONE COMPONENT, so a status can never be styled two ways on one screen. It
    appears twice: on the worklist row, and again in the viewer header, where
    the doctor must see the same state they clicked without a second design for
    it.
  */
  assert.equal(
    (code(WORKSPACE).match(/<StatusBadge/g) ?? []).length,
    2,
    "the status is no longer rendered by exactly the row and the viewer header",
  );
  assert.ok(WORKSPACE.includes("status={row.status}"));
  assert.ok(WORKSPACE.includes("label={row.status_label}"));
  assert.ok(WORKSPACE.includes("status={openResult.row.status}"));
});

test("the card carries its state as a left accent, not a full tint", () => {
  /*
    A wash of colour across the card body would fight the abnormality colours
    inside the results table, and a clinical finding must always outrank a
    card's administrative state.
  */
  assert.ok(WORKSPACE.includes("border-l-4"), "no accent rail");
  assert.ok(WORKSPACE.includes("${tone.accent}"), "the rail is not state-driven");
  assert.ok(
    WORKSPACE.includes("bg-white px-3 py-2"),
    "the card body is no longer neutral",
  );
});

test("the clinical service is the card heading, and the request code recedes", () => {
  /*
    A doctor remembers "the CBC I ordered", not "LABREQ0214". The ordered
    service is therefore the strongest type on the card and the code drops to a
    mono reference beside the badge.
  */
  assert.ok(
    WORKSPACE.includes('<h5 className="mt-0.5 min-w-0 cl-body font-semibold leading-snug text-slate-900">'),
    "the identity heading changed",
  );
  assert.ok(WORKSPACE.includes("{identity}"));
  assert.ok(
    WORKSPACE.includes('className="shrink-0 font-mono cl-meta font-semibold text-slate-500"'),
    "the request code no longer recedes",
  );
  assert.ok(WORKSPACE.includes("serviceSummary("), "the identity is not derived from the ordered work");
});

test("both services build their identity from the ordered work", () => {
  // Whichever side of the release the card is on, it names the same thing.
  assert.ok(WORKSPACE.includes("result.lines.map((line) => line.name)"));
  assert.ok(WORKSPACE.includes("row.pending_tests.map((test) => test.name)"));
  assert.ok(WORKSPACE.includes("exams.map((exam) => exam.name)"));
});

test("each service section carries a heading and a quiet tally", () => {
  assert.ok(WORKSPACE.includes("function SectionHeading("));
  assert.ok(WORKSPACE.includes('<SectionHeading title="Laboratory"'));
  assert.ok(WORKSPACE.includes('<SectionHeading title="Radiology"'));
  assert.ok(WORKSPACE.includes("sectionSummary(rows)"));
  // A heading, not a dashboard tile.
  assert.ok(!WORKSPACE.includes("text-3xl"));
  assert.ok(!WORKSPACE.includes("text-2xl"));
});

test("section headings are real headings, in document order", () => {
  const heading = WORKSPACE.indexOf("<h4 className=");
  assert.ok(heading > 0, "the section title is not a heading element");
  // h3 (Results) -> h4 (service) -> h5 (request identity): the outline a
  // screen reader announces matches what the eye sees.
  assert.ok(WORKSPACE.includes("<h3 className="));
  assert.ok(WORKSPACE.includes("<h5 className="));
});

test("pending reads as waiting, not as disabled or broken", () => {
  assert.ok(WORKSPACE.includes('"No released result yet."'));
  assert.ok(
    WORKSPACE.includes('"This order was cancelled. No result was reported."'),
  );
  // No disabled styling on a card that is simply waiting for the bench.
  assert.ok(!code(WORKSPACE).includes("opacity-50"));
});

test("an available card separates when it was REPORTED from the request workflow", () => {
  // Once a result exists, the date a doctor places in time is the report date,
  // not where the request happens to sit.
  assert.ok(WORKSPACE.includes("`Reported ${formatHospitalDate(result.result_date)}`"));
  assert.ok(WORKSPACE.includes("pendingReason(row)"));
});

test("the refresh control stays a quiet secondary action", () => {
  // Polish alignment only: Refresh must not become a primary CTA competing
  // with the clinical content.
  assert.ok(WORKSPACE.includes('aria-label="Refresh results"'));
  const refreshBlock = WORKSPACE.slice(
    WORKSPACE.indexOf('aria-label="Refresh results"'),
    WORKSPACE.indexOf('aria-label="Refresh results"') + 400,
  );
  assert.ok(!refreshBlock.includes("bg-emerald-700"), "Refresh became a primary CTA");
  assert.ok(refreshBlock.includes("border-slate-300"));
});

/* ------------------------------------------------------------------ *
 * A worklist, not a report document
 * ------------------------------------------------------------------ */

test("the laboratory table is NOT rendered inline in the worklist", () => {
  /*
    THE SCALABILITY PROPERTY. Inline tables read well with one order and
    collapse under four: a doctor scrolling two screens of results to find the
    study they were chasing is worse served than one who scans ten rows.
  */
  const body = code(WORKSPACE);
  for (const markup of ["<table", "<thead", "<tbody", 'scope="col"']) {
    assert.ok(
      !body.includes(markup),
      `${markup} is still rendered inline in the worklist`,
    );
  }
});

test("the laboratory table lives in the viewer body instead", () => {
  assert.ok(VIEWS.includes("<table"));
  assert.ok(VIEWS.includes("<thead>"));
  for (const column of ["Test", "Result", "Unit", "Reference", "Flag"]) {
    assert.ok(VIEWS.includes(`>\n                  ${column}\n`), column);
  }
});

test("interpretation, remarks and line notes moved into the viewer", () => {
  // They are part of the document, not part of the row.
  for (const section of ["Interpretation", "Remarks", "Line notes"]) {
    assert.ok(VIEWS.includes(`title="${section}"`), `${section} missing from viewer`);
    assert.ok(
      !code(WORKSPACE).includes(`${section} ·`),
      `${section} is still inline`,
    );
  }
  assert.ok(VIEWS.includes("result.interpretation"));
  assert.ok(VIEWS.includes("result.remarks"));
});

test("the radiology narrative moved into the viewer", () => {
  for (const section of ["Impression", "Findings", "Recommendations"]) {
    assert.ok(VIEWS.includes(`title="${section}"`), section);
  }
  assert.ok(VIEWS.includes("result.impression"));
  assert.ok(VIEWS.includes("result.findings"));
  assert.ok(VIEWS.includes("result.recommendations"));
});

test("the collapsed radiology row keeps the impression to a single line", () => {
  // A three-line preview defeats the point of collapsing.
  assert.ok(WORKSPACE.includes("truncate cl-secondary leading-snug"));
  assert.ok(!code(WORKSPACE).includes("line-clamp-3"));
});

test("both services expose the SAME open action, and it is a real button", () => {
  assert.ok(WORKSPACE.includes("function OpenResultButton("));
  assert.ok(WORKSPACE.includes('type="button"'));
  // One phrase across Laboratory and Radiology.
  assert.ok(WORKSPACE.includes("{OPEN_RESULT_TEXT}"));
  assert.equal(
    (code(WORKSPACE).match(/<OpenResultButton/g) ?? []).length,
    2,
    "expected exactly one open action per service",
  );
});

test("the open action names its request, so a list of them is distinguishable", () => {
  // Ten identical "Open result" controls would be useless to a screen reader.
  assert.ok(
    WORKSPACE.includes("aria-label={`${OPEN_RESULT_TEXT}: ${label}`}"),
    "the open action has no distinguishing accessible name",
  );
  assert.ok(WORKSPACE.includes("label={`${identity} · ${row.request_code}`}"));
});

test("the card is not implicitly clickable", () => {
  /*
    One labelled button, one job. Making the whole card a target would announce
    a paragraph as a control's label and would rob the doctor of selecting the
    request code as text.
  */
  const body = code(WORKSPACE);
  assert.ok(!body.includes("<article\n      onClick"));
  assert.ok(!body.includes("role=\"button\""));
  assert.ok(!body.includes("onKeyDown={(event) => event.key"));
});

test("the open action is offered ONLY when a released result exists", () => {
  // Pending and cancelled rows get no control at all, rather than a disabled
  // one that invites a click and refuses it.
  assert.equal(
    (code(WORKSPACE).match(/canOpenResult\(row\)/g) ?? []).length,
    2,
    "the open action is no longer gated on a released result",
  );
  assert.ok(WORKSPACE.includes("canOpenResult(row) ? ("));
});

/* ------------------------------------------------------------------ *
 * Opening a viewer costs no request
 * ------------------------------------------------------------------ */

test("there is exactly ONE fetch in the whole Results feature", () => {
  /*
    THE PROPERTY THIS SLICE TURNS ON. The workspace already holds every result
    line, interpretation and report the endpoint returned, so opening a viewer
    is a state change over data that is already here. A second fetch would be a
    second code path that could disagree with the worklist about what a result
    says.
  */
  assert.equal(
    (code(WORKSPACE).match(/fetch\(/g) ?? []).length,
    1,
    "the workspace gained a second fetch",
  );
  for (const [name, text] of [
    ["result-views", VIEWS],
    ["clinical-result-viewer-modal", VIEWER],
  ] as const) {
    assert.ok(!code(text).includes("fetch("), `${name} fetches`);
  }
  /*
    The shell DOES hold effects -- focus on open and the Escape listener -- and
    those are chrome, not data. The bodies hold none at all: they are pure
    presentation over a row they were handed, which is what makes "opening a
    viewer costs no request" true by construction rather than by discipline.
  */
  assert.ok(!code(VIEWS).includes("useEffect"), "a result view gained an effect");
  assert.ok(!code(VIEWS).includes("useState"), "a result view gained state");
});

test("opening a viewer only sets state", () => {
  assert.ok(WORKSPACE.includes("const showResult = useCallback("));
  const handler = WORKSPACE.slice(
    WORKSPACE.indexOf("const showResult = useCallback("),
    WORKSPACE.indexOf("/* ---------------- states ---------------- */"),
  );
  assert.ok(handler.includes("setOpenResult(selection)"));
  for (const banned of ["fetch(", "load(", "refresh(", "setReloadToken", "setLoading"]) {
    assert.ok(!handler.includes(banned), `${banned} in the open handler`);
  }
});

test("the viewer receives a row, never an id to resolve", () => {
  // Passing an id would be an invitation to look it up again.
  assert.ok(WORKSPACE.includes("row={openResult.row}"));
  assert.ok(VIEWS.includes("export function LaboratoryResultView({ row }"));
  assert.ok(VIEWS.includes("export function RadiologyResultView({ row }"));
});

test("one viewer shell serves both services", () => {
  assert.equal(
    (code(WORKSPACE).match(/<ClinicalResultViewerModal/g) ?? []).length,
    1,
    "a second modal appeared",
  );
  assert.ok(WORKSPACE.includes('openResult.kind === "laboratory" ? ('));
  assert.ok(WORKSPACE.includes("<LaboratoryResultView"));
  assert.ok(WORKSPACE.includes("<RadiologyResultView"));
});

test("the shell is not named or worded for one service", () => {
  // "Report" is a radiology word; forcing it onto a laboratory panel is how a
  // shared component leaks one service's vocabulary into the other's screen.
  assert.ok(VIEWER.includes("ClinicalResultViewerModal"));
  assert.ok(VIEWER.includes('aria-label="Close result"'));
});

test("the viewer is read-only in every state, with no write affordance", () => {
  for (const [name, text] of [
    ["clinical-result-viewer-modal", VIEWER],
    ["result-views", VIEWS],
  ] as const) {
    const lower = code(text).toLowerCase();
    for (const banned of ["save", "edit", "acknowledge", "reviewed", "sign-off", "<input", "<textarea"]) {
      assert.ok(!lower.includes(banned), `'${banned}' appeared in ${name}`);
    }
  }
});

/* ------------------------------------------------------------------ *
 * Clinical readability
 * ------------------------------------------------------------------ */

test("the laboratory table uses semantic headers", () => {
  assert.ok(VIEWS.includes("<thead>"));
  assert.ok(VIEWS.includes('scope="col"'));
  // The test name is the row header, so a screen reader announces which test a
  // value belongs to.
  assert.ok(VIEWS.includes('scope="row"'));
});

test("wide result tables scroll inside their own container", () => {
  // The dialog must never scroll horizontally.
  assert.ok(VIEWS.includes("overflow-x-auto"));
});

test("abnormality always renders its word, never colour alone", () => {
  assert.ok(VIEWS.includes("abnormalFlagText("));
  assert.ok(VIEWS.includes("abnormalToneClass(tone)"));
  assert.ok(
    VIEWS.includes("{word}"),
    "the abnormal flag no longer renders its text",
  );
});

test("the results table kept its clinical columns through the move", () => {
  /*
    The table moved from the card into the viewer; its columns are the clinical
    contract and did not move with it by accident.
  */
  for (const column of ["Test", "Result", "Unit", "Reference", "Flag"]) {
    assert.ok(VIEWS.includes(`>
                  ${column}
`), column);
  }
});

test("values are rendered through the verbatim helper, never parsed", () => {
  /*
    Moving the table into the viewer must not have introduced a formatter on
    the way. `value` and `reference_range` are free text the laboratory wrote.
  */
  assert.ok(VIEWS.includes("clinicalValue(line.value)"));
  assert.ok(VIEWS.includes("clinicalValue(line.reference_range)"));
  for (const [name, text] of [
    ["results-workspace", WORKSPACE],
    ["result-views", VIEWS],
  ] as const) {
    const body = code(text);
    for (const banned of ["parseFloat", "parseInt", "Number(line", "toFixed"]) {
      assert.ok(!body.includes(banned), `${banned} parses a value in ${name}`);
    }
  }
});

test("no billing or payer vocabulary reaches the results surface", () => {
  const forbidden = [
    "amount", "balance", "outstanding", "receipt", "payer", "sponsor",
    "tariff", "invoice", "credit_limit", "default_price", "prepayment",
  ];
  for (const [name, text] of [
    ["results-workspace", WORKSPACE],
    ["clinical-result-viewer-modal", VIEWER],
    ["result-views", VIEWS],
  ] as const) {
    const lower = code(text).toLowerCase();
    for (const term of forbidden) {
      assert.ok(
        !lower.includes(term),
        `'${term}' appeared in ${name}: Results shows findings, never a sum`,
      );
    }
  }
});

test("no Doctor Desk clinical text is pinned below the readable floor", () => {
  for (const [name, text] of [
    ["results-workspace", WORKSPACE],
    ["clinical-result-viewer-modal", VIEWER],
    ["result-views", VIEWS],
  ] as const) {
    const arbitrary = text.match(/text-\[\d+(?:\.\d+)?px\]/g);
    assert.equal(
      arbitrary,
      null,
      `${name} reintroduced arbitrary font sizes: ${arbitrary?.join(", ")}`,
    );
  }
});
