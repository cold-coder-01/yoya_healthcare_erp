/**
 * What the History tab must be, and must never become.
 *
 * WHY THIS IS A SOURCE ASSERTION. history-format.test.ts exercises the
 * DECISIONS -- whether an unrecorded vital prints as zero, whether a refusal
 * explains itself. What it cannot see is the WIRING: whether the tab is
 * actually live, whether detail is fetched only on demand, whether a mutation
 * control crept onto a read-only surface, whether stale history survives a
 * lapsed care relationship. This project ships no DOM test stack, and reading
 * the source is the same technique results-contract.test.ts already uses.
 *
 * THE PROPERTIES THAT MATTER MOST ARE ABSENCES. A History screen that could
 * write would be an unaudited path into records Slice 9A serves read-only. A
 * History screen that kept its data after a 404 would keep a patient's past on
 * display after the right to read it ended. Absences do not fail typechecks,
 * so they are asserted here.
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
 * at length WHY there is no edit control and no persistence, so a naive
 * substring search finds the very words the explanation uses and fails on the
 * documentation rather than on a regression.
 */
function code(text: string): string {
  return text
    .replace(/\/\*[\s\S]*?\*\//g, " ")
    .replace(/^\s*\/\/.*$/gm, " ");
}

const WORKSPACE = source("components/doctor/consultation/history-workspace.tsx");
const VIEW = source("components/doctor/consultation/history-visit-view.tsx");
const TYPES = source("types/doctor-history.ts");
const FORMAT = source("lib/history-format.ts");
const SECTIONS = source("lib/diagnosis-format.ts");
const CONSULTATION = source(
  "components/doctor/consultation/consultation-workspace.tsx",
);
const SUMMARY_ROUTE = source(
  "app/api/doctor/visits/[appointmentId]/history/route.ts",
);
const DETAIL_ROUTE = source(
  "app/api/doctor/visits/[appointmentId]/history/[historicalAppointmentId]/route.ts",
);
const IMAGE_ROUTE = source(
  "app/api/doctor/visits/[appointmentId]/history/[historicalAppointmentId]/images/[imageId]/route.ts",
);
const RESULTS_WORKSPACE = source(
  "components/doctor/consultation/results-workspace.tsx",
);
const RESULTS_IMAGE_ROUTE = source(
  "app/api/doctor/visits/[appointmentId]/results/images/[imageId]/route.ts",
);

const ALL_HISTORY_FILES = [
  ["history-workspace", WORKSPACE],
  ["history-visit-view", VIEW],
  ["doctor-history types", TYPES],
  ["history-format", FORMAT],
  ["history summary route", SUMMARY_ROUTE],
  ["history detail route", DETAIL_ROUTE],
  ["history image route", IMAGE_ROUTE],
] as const;

/* ------------------------------------------------------------------ *
 * The tab is live and mounted
 * ------------------------------------------------------------------ */
test("HISTORY is a live section", () => {
  assert.ok(
    SECTIONS.includes('{ key: "history", label: "History", live: true }'),
    "history is still inert",
  );
});

test("the consultation workspace mounts History, keyed on the visit", () => {
  const body = code(CONSULTATION);
  assert.ok(body.includes('section === "history"'), "history has no branch");
  assert.ok(
    body.includes("<HistoryWorkspace key={appointmentId} appointmentId={appointmentId} />"),
    "history is not keyed on the visit",
  );
});

test("History is mounted only while its tab is open", () => {
  // The section body is a ternary chain: a section that is not selected is not
  // rendered at all, which is what makes opening the tab refetch. That matters
  // here more than anywhere else -- longitudinal access can lapse.
  const body = code(CONSULTATION);
  const index = body.indexOf('section === "history"');
  assert.ok(index > 0);
  assert.ok(
    body.slice(index, index + 400).includes("<HistoryWorkspace"),
    "History is not rendered inside its own branch",
  );
});

/* ------------------------------------------------------------------ *
 * Two-level loading
 * ------------------------------------------------------------------ */
test("the list asks for a page, and asks for it once", () => {
  const body = code(WORKSPACE);
  assert.ok(
    body.includes("/api/doctor/visits/${appointmentId}/history?limit="),
    "the summary route is not called",
  );
  assert.ok(body.includes("offset=0"), "the first page is not anchored at 0");
});

test("DETAIL IS FETCHED ONLY WHEN AN EPISODE IS OPENED", () => {
  /*
    THE PERFORMANCE PROPERTY THE WHOLE SPLIT EXISTS FOR. If the detail URL
    appeared in the list effect, rendering ten rows would cost eleven requests
    and move several hundred kilobytes of narrative nobody reads.
  */
  const body = code(WORKSPACE);
  const detailCall = "/api/doctor/visits/${appointmentId}/history/${historicalId}";
  assert.ok(body.includes(detailCall), "detail is never fetched");
  // It appears exactly once, and inside the open handler rather than an effect.
  assert.equal(
    body.split(detailCall).length - 1,
    1,
    "the detail route is called from more than one place",
  );
  const openIndex = body.indexOf("openEpisode");
  assert.ok(openIndex > 0 && body.indexOf(detailCall) > openIndex);
});

test("an already-opened episode reopens without a request", () => {
  const body = code(WORKSPACE);
  assert.ok(body.includes("detailCache"), "there is no detail cache");
  assert.ok(body.includes("detailCache.current.get"), "the cache is never read");
});

test("paging APPENDS and never re-sorts", () => {
  /*
    The server returns newest-first and each page continues where the last
    ended. A client-side sort would be a second ordering rule that could
    disagree with the one the server applied.
  */
  const body = code(WORKSPACE);
  assert.ok(body.includes("[...current, ...payload.data.visits]"), "paging replaces");
  assert.ok(!body.includes(".sort("), "the client re-sorts history");
  assert.ok(!body.includes(".reverse("), "the client reverses history");
});

test("the current visit is never filtered out in the browser", () => {
  // The server excludes the open episode by identity. Re-deriving that here
  // would be a second definition of "current" that could drift.
  const body = code(WORKSPACE);
  for (const banned of [
    "visits.filter(",
    "payload.data.visits.filter(",
    ".visits.filter(",
  ]) {
    assert.ok(!body.includes(banned), "the client filters the visit list");
  }
  // And it does not reconstruct the exclusion from the current appointment id.
  assert.ok(
    !body.includes("appointment_id !== appointmentId"),
    "the client re-derives the current-visit exclusion",
  );
});

/* ------------------------------------------------------------------ *
 * A lapsed care relationship
 * ------------------------------------------------------------------ */
test("a REFUSED history drops every row and any open episode", () => {
  /*
    404 is what Slice 9A answers once the care relationship lapses. Keeping the
    previous page on screen would leave a patient's history visible after the
    right to read it ended -- exactly the failure the bounded policy exists to
    prevent.
  */
  const body = code(WORKSPACE);
  const refusal = body.slice(
    body.indexOf("if (!response.ok || !payload.success)"),
  );
  for (const clear of [
    "setData(null)",
    "setVisits([])",
    "setDetail(null)",
    "setOpenVisit(null)",
    "detailCache.current.clear()",
  ]) {
    assert.ok(refusal.includes(clear), `a refusal does not ${clear}`);
  }
});

test("every refusal renders the SAME sentence", () => {
  const body = code(WORKSPACE);
  assert.ok(body.includes("HISTORY_UNAVAILABLE_TEXT"));
  // No branch explains WHICH refusal it was.
  for (const banned of ["forbidden", "not yours", "another patient", "expired"]) {
    assert.ok(!body.toLowerCase().includes(banned), `'${banned}' leaked a reason`);
  }
});

/* ------------------------------------------------------------------ *
 * Read-only
 * ------------------------------------------------------------------ */
test("the History surface offers no write control at all", () => {
  for (const [name, text] of [
    ["history-workspace", WORKSPACE],
    ["history-visit-view", VIEW],
  ] as const) {
    const lower = code(text).toLowerCase();
    for (const banned of [
      "onsave",
      "<input",
      "<textarea",
      "formdata",
      'method: "post"',
      "method: 'post'",
      "acknowledge",
      "oncancel",
      "handlecancel",
      "onedit",
      "onremove",
      "ondelete",
    ]) {
      assert.ok(!lower.includes(banned), `'${banned}' appeared in ${name}`);
    }
  }
});

test("History only ever issues GET", () => {
  for (const [name, text] of ALL_HISTORY_FILES) {
    const body = code(text);
    for (const verb of ["POST", "PUT", "PATCH", "DELETE"]) {
      assert.ok(
        !body.includes(`"${verb}"`),
        `${verb} appeared in ${name}`,
      );
    }
  }
});

test("the BFF exports GET and nothing else", () => {
  for (const [name, text] of [
    ["summary", SUMMARY_ROUTE],
    ["detail", DETAIL_ROUTE],
    ["image", IMAGE_ROUTE],
  ] as const) {
    const body = code(text);
    assert.ok(body.includes("export async function GET"), `${name} has no GET`);
    for (const verb of ["POST", "PUT", "PATCH", "DELETE"]) {
      assert.ok(
        !body.includes(`export async function ${verb}`),
        `${name} exports ${verb}`,
      );
    }
  }
});

/* ------------------------------------------------------------------ *
 * Confidentiality
 * ------------------------------------------------------------------ */
test("no billing, payer or accounting vocabulary reaches History", () => {
  for (const [name, text] of ALL_HISTORY_FILES) {
    const lower = code(text).toLowerCase();
    for (const banned of [
      "payer",
      "insurance",
      "charge",
      "payment",
      "receipt",
      "cashier",
      "accounting",
      "fiscal",
      "cogs",
      "outstanding",
      "tariff",
      "price",
    ]) {
      assert.ok(!lower.includes(banned), `'${banned}' appeared in ${name}`);
    }
  }
});

test("billing_blocked survives ONLY as the key being omitted", () => {
  /*
    It is named in the contract exactly twice, both times inside an Omit<> that
    REMOVES it. Anywhere else -- a component, a route, a plain field -- it
    would be a billing signal on a clinical history screen.
  */
  for (const [name, text] of ALL_HISTORY_FILES) {
    if (name === "doctor-history types") continue;
    assert.ok(
      !code(text).includes("billing_blocked"),
      `billing_blocked appeared in ${name}`,
    );
  }
  const occurrences = code(TYPES).split("billing_blocked").length - 1;
  assert.equal(occurrences, 2, "billing_blocked is named an unexpected number of times");
  assert.equal(
    code(TYPES).split('Omit<LaboratoryReview, "billing_blocked">').length - 1,
    1,
  );
  assert.equal(
    code(TYPES).split('Omit<RadiologyReview, "billing_blocked">').length - 1,
    1,
  );
});

test("the history contract declares no billing_blocked", () => {
  /*
    LaboratoryReview and RadiologyReview both declare it, and the server strips
    it from every history row. The Omit<> is what stops a history row
    typechecking as a Results row and letting a billing signal onto a clinical
    history screen.
  */
  assert.ok(TYPES.includes('Omit<LaboratoryReview, "billing_blocked">'));
  assert.ok(TYPES.includes('Omit<RadiologyReview, "billing_blocked">'));
});

test("no backend origin, attachment id or token can appear anywhere", () => {
  for (const [name, text] of ALL_HISTORY_FILES) {
    const body = code(text);
    for (const banned of [
      "/web/content",
      "/web/image",
      "access_token",
      "8171",
      "localhost",
      "ir.attachment",
      "attachment_id",
      "datas",
      "http://",
      "https://",
    ]) {
      assert.ok(!body.includes(banned), `'${banned}' appeared in ${name}`);
    }
  }
});

test("History is never persisted to the browser", () => {
  /*
    Prior clinical history is the most sensitive payload this desk handles. It
    lives in React state for exactly as long as the tab is open.
  */
  for (const [name, text] of [
    ["history-workspace", WORKSPACE],
    ["history-visit-view", VIEW],
  ] as const) {
    const body = code(text);
    for (const banned of [
      "localStorage",
      "sessionStorage",
      "indexedDB",
      "document.cookie",
      "console.log",
      "console.debug",
      "console.info",
    ]) {
      assert.ok(!body.includes(banned), `'${banned}' appeared in ${name}`);
    }
  }
});

test("no patient id is ever sent from the browser", () => {
  /*
    Slice 9A derives the patient from the caller's own current visit. A
    patient id in a request would be the one thing that could turn this desk
    into a way to walk the hospital's census.
  */
  for (const [name, text] of ALL_HISTORY_FILES) {
    const body = code(text);
    assert.ok(!body.includes("patient_id"), `patient_id appeared in ${name}`);
  }
  assert.ok(!code(TYPES).includes("patient_id"));
});

test("the contract carries no image URL field", () => {
  // The client builds its own BFF path from an id; a URL in the payload is
  // where an Odoo origin would hide.
  assert.ok(!code(TYPES).includes("url"), "the contract declares a URL field");
});

/* ------------------------------------------------------------------ *
 * The BFF
 * ------------------------------------------------------------------ */
test("every History route requires the Odoo session before anything else", () => {
  for (const [name, text] of [
    ["summary", SUMMARY_ROUTE],
    ["detail", DETAIL_ROUTE],
    ["image", IMAGE_ROUTE],
  ] as const) {
    const body = code(text);
    assert.ok(body.includes("await requireOdooSession()"), `${name} skips the session`);
    const sessionAt = body.indexOf("requireOdooSession");
    const paramsAt = body.indexOf("context.params");
    assert.ok(sessionAt < paramsAt, `${name} reads params before authenticating`);
  }
});

test("the browser never receives a base URL and never calls Odoo", () => {
  for (const [name, text] of ALL_HISTORY_FILES) {
    const body = code(text);
    assert.ok(!body.includes("getOdooBaseUrl"), `${name} resolves a base URL`);
    assert.ok(!body.includes("yoya-emr/api"), `${name} names the Odoo path`);
  }
  // Only the BFF names the upstream prefix, and only through the shared const.
  for (const [name, text] of [
    ["summary", SUMMARY_ROUTE],
    ["detail", DETAIL_ROUTE],
    ["image", IMAGE_ROUTE],
  ] as const) {
    assert.ok(code(text).includes("DOCTOR_API"), `${name} does not use DOCTOR_API`);
  }
});

test("a refusal is forwarded, never rewritten", () => {
  /*
    Slice 9A answers a flat 404 for every out-of-scope case. A BFF that
    translated or enriched that would undo the non-disclosure property.
  */
  for (const [name, text] of [
    ["summary", SUMMARY_ROUTE],
    ["detail", DETAIL_ROUTE],
  ] as const) {
    const body = code(text);
    assert.ok(body.includes("forwardOdooResult"), `${name} does not forward`);
    assert.ok(!body.includes("status: 200"), `${name} rewrites a status`);
    assert.ok(!body.includes("=== 404"), `${name} branches on 404`);
  }
});

test("both path ids are validated as strictly as each other", () => {
  const detail = code(DETAIL_ROUTE);
  assert.equal(
    detail.split("parseAppointmentId(").length - 1,
    2,
    "the detail route does not validate both ids",
  );
  const image = code(IMAGE_ROUTE);
  assert.equal(
    image.split("parseAppointmentId(").length - 1,
    3,
    "the image route does not validate all three ids",
  );
});

test("the image route streams and maps exactly one disposition value", () => {
  const body = code(IMAGE_ROUTE);
  assert.ok(body.includes("streamOdooBinary"), "bytes are parsed as JSON");
  assert.ok(
    body.includes('requested === "attachment" ? "?disposition=attachment" : ""'),
    "an arbitrary disposition could reach a header",
  );
});

test("the summary route forwards paging without re-deriving the policy", () => {
  const body = code(SUMMARY_ROUTE);
  assert.ok(body.includes("withQuery"), "paging is not forwarded");
  assert.ok(body.includes("limit"), "limit is not forwarded");
  assert.ok(body.includes("offset"), "offset is not forwarded");
  // Odoo clamps the ceiling and refuses a negative offset; a second policy
  // here could drift from the one actually enforced.
  assert.ok(!body.includes("Math.min"), "the BFF re-derives the limit ceiling");
});

/* ------------------------------------------------------------------ *
 * Reuse, and the current-visit surface it must not disturb
 * ------------------------------------------------------------------ */
test("History renders released results through the RESULTS TAB's own views", () => {
  /*
    Two renderers would eventually disagree about what a value, a flag or an
    empty report means, and a doctor would have no way to tell which screen
    was right.
  */
  const body = code(VIEW);
  assert.ok(body.includes("LaboratoryResultView"), "laboratory is re-implemented");
  assert.ok(body.includes("RadiologyResultView"), "radiology is re-implemented");
  assert.ok(
    body.includes('from "./result-views"'),
    "the views are not the Results tab's own",
  );
});

test("History reuses the one read-only viewer shell", () => {
  const body = code(WORKSPACE);
  assert.ok(body.includes("ClinicalResultViewerModal"), "a second modal exists");
  assert.ok(body.includes("HISTORY_READ_ONLY_TEXT"), "the read-only badge is missing");
});

test("historical imagery resolves through the HISTORY route", () => {
  const body = code(VIEW);
  assert.ok(body.includes("historyImageContentPath"), "history builds no image path");
  assert.ok(body.includes("imageSrc={"), "the builder is not injected");
  assert.ok(
    !body.includes("imageContentPath(appointmentId"),
    "history imagery used the current-visit Results path",
  );
});

test("the CURRENT-VISIT Results tab is untouched by Slice 9B", () => {
  /*
    THE REGRESSION THIS SLICE RISKS. The Results tab must keep composing the
    exact path it always did; omitting the injected builder IS that behaviour.
  */
  const body = code(RESULTS_WORKSPACE);
  assert.ok(
    body.includes("appointmentId={appointmentId}"),
    "the Results tab stopped passing the visit",
  );
  assert.ok(
    !body.includes("imageSrc"),
    "the Results tab now overrides its image path",
  );
  assert.ok(!body.includes("history"), "the Results tab learned about history");
});

test("the CURRENT-VISIT image route still serves only the results path", () => {
  const body = code(RESULTS_IMAGE_ROUTE);
  assert.ok(body.includes("/results/images/"), "the results image path changed");
  assert.ok(
    !body.includes("/history/"),
    "the current-visit image route learned about history",
  );
});

/* ------------------------------------------------------------------ *
 * Accessibility
 * ------------------------------------------------------------------ */
test("an episode is opened by a real button that names itself", () => {
  const body = code(WORKSPACE);
  assert.ok(body.includes('type="button"'), "rows are not buttons");
  assert.ok(body.includes("aria-label={`Open visit"), "rows announce no name");
});

test("focus returns to the row that opened the viewer", () => {
  const body = code(WORKSPACE);
  assert.ok(body.includes("originRef"), "the opener is not remembered");
  assert.ok(body.includes("requestAnimationFrame(() => origin.focus())"));
});

test("loading states are announced, not merely animated", () => {
  assert.ok(code(WORKSPACE).includes('aria-busy="true"'));
  assert.ok(code(VIEW).includes('aria-busy="true"'));
});

test("an error is announced to assistive technology", () => {
  assert.ok(code(WORKSPACE).includes('role="alert"'));
  assert.ok(code(VIEW).includes('role="alert"'));
});

/* ------------------------------------------------------------------ *
 * Reachability before consultation (Slice 9B UAT defect)
 *
 * THE DEFECT. Slice 9A authorizes longitudinal reading from `confirmed`
 * onward, but the Doctor Desk mounts the clinical tab strip only for
 * `in_consultation` and `done`. A confirmed visit therefore rendered the
 * pre-consultation patient panel, which had no History anywhere -- an
 * authorized surface made unreachable by the UI.
 *
 * THE FIX, AND ITS BOUNDARY. The pre-consultation panel gained a two-view
 * strip, Overview and History, and NOTHING ELSE. Note, Diagnosis, Orders and
 * Results stay behind Start Consultation and the four model-layer gates it
 * runs; the consultation workspace's own visibility rule is untouched.
 * ------------------------------------------------------------------ */
const PATIENT_PANEL = source("components/doctor/doctor-patient-panel.tsx");
const WORKSTATION = source("components/doctor/doctor-workstation.tsx");
const CONSULTATION_FORMAT = source("lib/consultation-format.ts");

test("a CONFIRMED visit can reach History from the pre-consultation panel", () => {
  const body = code(PATIENT_PANEL);
  assert.ok(body.includes("hasActiveCareRelationship(visit.state)"), "no gate");
  assert.ok(body.includes("<HistoryWorkspace"), "History is not mounted");
  assert.ok(
    body.includes('view === "history"'),
    "there is no History view to select",
  );
});

test("the History surface is keyed on the visit, like every other one", () => {
  assert.ok(
    code(PATIENT_PANEL).includes(
      "<HistoryWorkspace key={appointmentId} appointmentId={appointmentId} />",
    ),
    "one patient's history could survive onto another's panel",
  );
});

test("the open view resets when the doctor selects a different patient", () => {
  /*
    The view is tagged with the visit it belongs to, so a History tab left open
    on one patient cannot be the view that greets the next -- the same pattern
    the workstation uses for its section and this panel already used for its
    start error.
  */
  const body = code(PATIENT_PANEL);
  assert.ok(body.includes("viewState.appointmentId === appointmentId"));
  assert.ok(body.includes('viewState.view : "overview"'));
});

test("NO clinical WRITE surface is opened before consultation", () => {
  /*
    THE BOUNDARY THIS FIX MUST NOT CROSS. Only History was added. The four
    consultation surfaces carry writes and must stay behind Start Consultation.
  */
  const body = code(PATIENT_PANEL);
  for (const banned of [
    "ConsultationWorkspace",
    "NoteEditor",
    "DiagnosisWorkspace",
    "OrdersWorkspace",
    "ResultsWorkspace",
    "LaboratoryPanel",
    "RadiologyPanel",
    "MedicationPanel",
  ]) {
    assert.ok(!body.includes(banned), `${banned} reached the pre-consultation panel`);
  }
});

test("the pre-consultation panel gained no new write path", () => {
  const body = code(PATIENT_PANEL);
  // start-consultation is the ONE POST this panel has always had, and it is
  // the gate itself rather than a clinical write.
  const posts = body.split('method: "POST"').length - 1;
  assert.equal(posts, 1, "a second POST appeared on the pre-consultation panel");
  assert.ok(body.includes("start-consultation"), "the one POST is not the gate");
  for (const banned of ["<textarea", "FormData", "onSave"]) {
    assert.ok(!body.includes(banned), `${banned} reached the pre-consultation panel`);
  }
});

test("START CONSULTATION gating is untouched", () => {
  const body = code(PATIENT_PANEL);
  // Readiness is still the server's verdict, and the button is still disabled
  // by it alone. Nothing about History participates in this decision.
  assert.ok(body.includes("visitReadiness({"), "readiness is no longer derived");
  assert.ok(
    body.includes("disabled={!readiness.ready || starting}"),
    "the Start Consultation gate changed",
  );
  assert.ok(
    !body.includes("readiness.ready && view"),
    "the view now participates in the start gate",
  );
});

test("IN_CONSULTATION and COMPLETED behaviour is unchanged", () => {
  /*
    isConsultationVisible still answers in_consultation OR done, so the full
    workspace -- with its own live HISTORY tab -- mounts exactly as before.
    The pre-consultation panel is reached only for the states it always was.
  */
  const body = code(CONSULTATION_FORMAT);
  assert.ok(
    body.includes(
      "visitState === CONSULTATION_STATE || visitState === COMPLETED_VISIT_STATE",
    ),
    "the consultation visibility rule was widened",
  );
  assert.ok(
    !body.includes("confirmed"),
    "confirmed was added to the consultation workspace gate",
  );

  const workstation = code(WORKSTATION);
  assert.ok(
    workstation.includes("isConsultationVisible(") &&
      workstation.includes("isConsultationMode("),
    "the workstation stopped deriving its two modes from the visit state",
  );
  assert.ok(
    !workstation.includes("hasActiveCareRelationship"),
    "the workstation now re-derives care state for itself",
  );
});

test("the fix touched no backend file", () => {
  /*
    Slice 9B is frontend and BFF only. The care-relationship policy, the record
    rules and the API contract all remain Slice 9A's, unchanged; this test
    fails loudly if a future edit reaches for the server to make the UI easier.
  */
  const serverPolicy = readFileSync(
    new URL(
      "../../../odoo/custom_addons/yoya_clinical_bridge/models/res_users.py",
      import.meta.url,
    ),
    "utf8",
  );
  assert.ok(
    serverPolicy.includes(
      'ACTIVE_CARE_APPOINTMENT_STATES = ("confirmed", "in_consultation")',
    ),
    "the server's care-relationship policy changed",
  );
});
