/**
 * Doctor Results presentation decisions.
 *
 * Written against `node:test` and `node:assert`, both Node built-ins, so these
 * add NO dependency to the project -- the same discipline every other test file
 * here follows. Imports are relative rather than aliased so they resolve under
 * any runner.
 *
 * THE PROPERTIES THESE TESTS EXIST FOR:
 *
 *   1. A CLINICAL VALUE REACHES THE SCREEN BYTE FOR BYTE. `result_value` and
 *      `reference_range` are Char on the model and hold "< 0.01", "Negative",
 *      "70/100". Parsing, rounding or re-ranging any of them would fabricate
 *      precision the laboratory never reported.
 *
 *   2. ABNORMALITY IS NEVER COLOUR ALONE, and red means one thing. Every
 *      non-normal flag carries its word, and `critical` is the only flag that
 *      gets red -- spending red on read-only chrome or on every out-of-range
 *      value drains it from the one place it must mean "act now".
 *
 *   3. AN EMPTY RELEASED REPORT SAYS SO. Radiology has no completeness
 *      constraint, so a report can be released with no findings and no
 *      impression, and the database already holds one.
 *
 *   4. THE DESK NEVER CLAIMS A RESULT WAS REVIEWED. No model records it.
 */
import assert from "node:assert/strict";
import test from "node:test";

import {
  AWAITING_CLEARANCE_TEXT,
  EMPTY_REPORT_TEXT,
  NO_ORDERS_TEXT,
  NO_VALUE_TEXT,
  RESULT_STATUS_LABELS,
  abnormalFlagText,
  abnormalTone,
  abnormalToneClass,
  checkedAtText,
  clinicalValue,
  hasAnyOrder,
  isDocumented,
  needsAbnormalWord,
  pendingReason,
  reportPreview,
  IMAGE_UNAVAILABLE_TEXT,
  OPEN_RESULT_TEXT,
  canOpenResult,
  fileSizeText,
  imageContentPath,
  imagingSummary,
  labResultSummary,
  lightboxPosition,
  resultImages,
  stepIndex,
  viewableImages,
  resultStatusLabel,
  resultStatusTone,
  sectionSummary,
  serviceSummary,
  supersededText,
  workflowLabel,
} from "./results-format.ts";

const STATUSES = ["pending", "available", "cancelled"] as const;

/* ------------------------------------------------------------------ *
 * Status vocabulary
 * ------------------------------------------------------------------ */

test("the Results vocabulary is exactly three model-backed statuses", () => {
  assert.deepEqual(
    Object.keys(RESULT_STATUS_LABELS).sort(),
    ["available", "cancelled", "pending"],
  );
});

test("each status has the wording the desk shows", () => {
  assert.equal(resultStatusLabel("pending"), "Pending");
  assert.equal(resultStatusLabel("available"), "Result available");
  assert.equal(resultStatusLabel("cancelled"), "Cancelled");
});

test("NO reviewed/seen/acknowledged status exists", () => {
  /*
    THE PROPERTY THIS FILE PROTECTS MOST. No model records that a doctor has
    read a result, so a fourth chip would be the desk asserting something no
    record supports -- and a false "seen" on a clinical screen is worse than no
    chip at all.
  */
  const blob = JSON.stringify(RESULT_STATUS_LABELS).toLowerCase();
  for (const invented of ["review", "seen", "acknowledg", "sign", "read"]) {
    assert.ok(!blob.includes(invented), invented);
  }
});

test("every status carries a visible word, not colour alone", () => {
  /*
    THE ACCESSIBILITY PROPERTY THIS SCREEN TURNS ON. A doctor reading a
    greyscale print-out, a colour-blind clinician and a screen-reader user all
    get the status from the label; the chip and dot only make it faster for
    everyone else.
  */
  for (const status of STATUSES) {
    const label = resultStatusLabel(status);
    assert.ok(label && label.trim().length > 0, status);
  }
  assert.equal(resultStatusLabel("pending"), "Pending");
  assert.equal(resultStatusLabel("available"), "Result available");
  assert.equal(resultStatusLabel("cancelled"), "Cancelled");
});

test("each status has a chip, a dot and a card accent", () => {
  // The dot is the second, non-textual signal that lets a changed card be
  // spotted while scanning a column.
  for (const status of STATUSES) {
    const tone = resultStatusTone(status);
    assert.ok(tone.chip.length, `${status} chip`);
    assert.ok(tone.dot.length, `${status} dot`);
    assert.match(tone.accent, /^border-l-/, `${status} accent`);
  }
});

test("colour carries one meaning each: green available, amber pending, red cancelled", () => {
  assert.match(resultStatusTone("available").chip, /emerald/);
  assert.match(resultStatusTone("available").dot, /emerald/);
  assert.match(resultStatusTone("pending").chip, /amber/);
  assert.match(resultStatusTone("pending").dot, /amber/);
  assert.match(resultStatusTone("cancelled").chip, /red/);
});

test("no status borrows another status's colour", () => {
  assert.doesNotMatch(resultStatusTone("pending").chip, /emerald|red/);
  assert.doesNotMatch(resultStatusTone("available").chip, /amber|red/);
  assert.doesNotMatch(resultStatusTone("cancelled").chip, /emerald|amber/);
});

test("cancelled is the WEAKEST red on the screen, so it cannot outshout a critical value", () => {
  /*
    THE PRIORITY THIS SCREEN MUST NEVER INVERT. A `critical` abnormal flag is a
    clinical emergency; a cancelled order is an administrative fact. If the
    card state competed with the finding, the card would win the eye -- which
    is exactly backwards.
  */
  const cancelled = resultStatusTone("cancelled").chip;
  const critical = abnormalToneClass("critical");
  const shade = (classes: string) => {
    const match = classes.match(/border-red-(\d+)/);
    return match ? Number(match[1]) : 0;
  };
  assert.ok(
    shade(cancelled) < shade(critical),
    `cancelled red (${shade(cancelled)}) must be weaker than critical red (${shade(critical)})`,
  );
});

test("pending is not styled as disabled", () => {
  // Pending means the bench is working, not that the card is inert. Amber on
  // white reads as active waiting; slate-on-slate would read as switched off.
  const tone = resultStatusTone("pending");
  assert.doesNotMatch(tone.chip, /slate|opacity|gray/);
});

test("an unknown status degrades to pending rather than rendering unstyled", () => {
  const tone = resultStatusTone("something_new" as never);
  assert.match(tone.chip, /amber/);
});

/* ------------------------------------------------------------------ *
 * Section summaries and card identity
 * ------------------------------------------------------------------ */

test("a section summarises what is in it, busiest state first", () => {
  const rows = [
    { status: "pending" as const },
    { status: "available" as const },
    { status: "pending" as const },
  ];
  assert.equal(sectionSummary(rows), "1 result available · 2 pending");
});

test("a section with one pending request reads exactly as the desk shows it", () => {
  assert.equal(sectionSummary([{ status: "pending" }]), "1 pending");
  assert.equal(sectionSummary([{ status: "available" }]), "1 result available");
});

test("empty groups are omitted rather than shown as zero", () => {
  // "0 cancelled" is noise that makes the counts that matter harder to read.
  const summary = sectionSummary([{ status: "pending" }]);
  assert.ok(summary && !summary.includes("0 "));
  assert.ok(summary && !summary.includes("cancelled"));
});

test("an empty section summarises nothing at all", () => {
  assert.equal(sectionSummary([]), null);
});

test("the clinical service is the card identity, and a long list is capped", () => {
  assert.equal(serviceSummary(["Complete Blood Count"]), "Complete Blood Count");
  assert.equal(serviceSummary(["CBC", "Creatinine"]), "CBC, Creatinine");
  assert.equal(
    serviceSummary(["CBC", "Creatinine", "CRP", "LFT", "TSH"]),
    "CBC, Creatinine, CRP +2 more",
  );
});

test("a card with nothing named still shows a dash, never an empty heading", () => {
  assert.equal(serviceSummary([]), "—");
});

/* ------------------------------------------------------------------ *
 * Values travel verbatim
 * ------------------------------------------------------------------ */

test("a numeric-looking value is not parsed", () => {
  assert.equal(clinicalValue("20"), "20");
  assert.equal(clinicalValue("5.0"), "5.0");
  assert.equal(clinicalValue("0092"), "0092");
});

test("a bounded value keeps its operator", () => {
  // THE case that makes this rule non-negotiable: parsing "< 0.01" to 0.01
  // would report a measurement the laboratory explicitly refused to make.
  assert.equal(clinicalValue("< 0.01"), "< 0.01");
  assert.equal(clinicalValue("> 1000"), "> 1000");
});

test("a qualitative value is not coerced", () => {
  assert.equal(clinicalValue("Negative"), "Negative");
  assert.equal(clinicalValue("No growth after 48h"), "No growth after 48h");
  assert.equal(clinicalValue("Trace"), "Trace");
});

test("a reference range is text, not an interval", () => {
  // "70/100" is a real shipped value. Splitting it on '/' would invent a range
  // nobody wrote, and the shape differs between benches anyway.
  assert.equal(clinicalValue("70/100"), "70/100");
  assert.equal(clinicalValue("12-16"), "12-16");
  assert.equal(clinicalValue("< 5"), "< 5");
});

test("an unrecorded value shows a dash, never a blank cell", () => {
  assert.equal(clinicalValue(null), NO_VALUE_TEXT);
  assert.equal(clinicalValue(undefined), NO_VALUE_TEXT);
  assert.equal(clinicalValue(""), NO_VALUE_TEXT);
  assert.equal(NO_VALUE_TEXT, "—");
});

test("whitespace inside a value is preserved", () => {
  // A padded value is the bench's own formatting, not this layer's to trim.
  assert.equal(clinicalValue(" 5.0 "), " 5.0 ");
});

/* ------------------------------------------------------------------ *
 * The collapsed laboratory summary
 * ------------------------------------------------------------------ */

const line = (flag: string | null, label: string | null = null) => ({
  abnormal_flag: flag,
  abnormal_flag_label: label,
});

test("an all-normal panel says so, with the test count", () => {
  assert.equal(labResultSummary([line("normal", "Normal")]), "1 test · Normal");
  assert.equal(
    labResultSummary([line("normal", "Normal"), line("normal", "Normal")]),
    "2 tests · Normal",
  );
});

test("abnormal counts lead, worst first", () => {
  /*
    The first thing past the test count is the thing the doctor most needs to
    have seen, so severity orders the tally rather than the order the bench
    happened to report in.
  */
  const lines = [
    line("normal", "Normal"),
    line("high", "High"),
    line("critical", "Critical"),
    line("high", "High"),
    line("normal", "Normal"),
  ];
  assert.equal(labResultSummary(lines), "5 tests · 1 Critical · 2 High");
});

test("every abnormal grade is counted separately", () => {
  const lines = [
    line("critical", "Critical"),
    line("high", "High"),
    line("low", "Low"),
    line("abnormal", "Abnormal"),
  ];
  assert.equal(
    labResultSummary(lines),
    "4 tests · 1 Critical · 1 High · 1 Low · 1 Abnormal",
  );
});

test("a single abnormal test reads naturally", () => {
  assert.equal(
    labResultSummary([line("high", "High")]),
    "1 test · 1 High",
  );
});

test("an UNFLAGGED line is never summarised as Normal", () => {
  /*
    THE PROPERTY THAT MATTERS MOST HERE. Declaring a set "Normal" when the
    bench never judged one of its lines fabricates exactly the reassurance a
    doctor would act on. The honest summary is the count alone.
  */
  assert.equal(labResultSummary([line("normal", "Normal"), line(null)]), "2 tests");
  assert.equal(labResultSummary([line(null)]), "1 test");
});

test("an unflagged line does not suppress the abnormal counts either", () => {
  // Uncertainty about one line is no reason to hide a critical on another.
  assert.equal(
    labResultSummary([line("critical", "Critical"), line(null)]),
    "2 tests · 1 Critical",
  );
});

test("the summary quotes the laboratory's own word for a flag", () => {
  // A bench that says "Panic high" is quoted, not paraphrased.
  assert.equal(
    labResultSummary([line("high", "Panic high")]),
    "1 test · 1 Panic high",
  );
});

test("an unlabelled flag still reads as a word", () => {
  assert.equal(labResultSummary([line("critical", null)]), "1 test · 1 Critical");
});

test("the summary never touches a value, a unit or a reference range", () => {
  /*
    It is given ONLY flags. There is no input here from which a numeric
    judgement could be formed, which is the structural guarantee that this
    layer forms no clinical opinion.
  */
  const summary = labResultSummary([line("normal", "Normal")]);
  assert.ok(!summary.includes("0.01"));
  assert.match(summary, /^1 test · Normal$/);
});

test("an empty result set says so rather than claiming zero tests are normal", () => {
  assert.equal(labResultSummary([]), "No reported tests");
});

/* ------------------------------------------------------------------ *
 * What may be opened
 * ------------------------------------------------------------------ */

test("only a released result can be opened", () => {
  assert.equal(canOpenResult({ result: { id: 1 } }), true);
  assert.equal(canOpenResult({ result: null }), false);
});

test("both services share one phrase for the action", () => {
  assert.equal(OPEN_RESULT_TEXT, "Open result");
  // Not a review verb: opening a result records nothing about having read it.
  const lowered = OPEN_RESULT_TEXT.toLowerCase();
  for (const invented of ["review", "acknowledg", "sign"]) {
    assert.ok(!lowered.includes(invented), invented);
  }
});

/* ------------------------------------------------------------------ *
 * Imaging
 * ------------------------------------------------------------------ */

const picture = (over = {}) => ({
  id: 1,
  name: "Brain CT - Axial View",
  caption: null,
  kind: "image" as const,
  mimetype: "image/jpeg",
  filename: "images.jpg",
  file_size: 184320,
  sequence: 10,
  ...over,
});

const pdf = (over = {}) =>
  picture({ id: 2, kind: "pdf" as const, mimetype: "application/pdf", ...over });

test("THE IMAGE PATH IS ALWAYS THE BFF, never an Odoo origin", () => {
  /*
    THE PROPERTY THIS WHOLE SLICE TURNS ON. The payload carries no URL at all,
    precisely so an Odoo origin, a /web/content path or an access token has
    nowhere to hide; every <img src> and every Open button resolves through
    this one function instead.
  */
  const path = imageContentPath(8072, 41);
  assert.equal(path, "/api/doctor/visits/8072/results/images/41");
  assert.ok(path.startsWith("/api/doctor/"));
  for (const banned of ["http", "8171", "/web/content", "access_token", "localhost"]) {
    assert.ok(!path.includes(banned), banned);
  }
});

test("the visit travels in the path, so the scope check has both halves", () => {
  // An id alone would let a doctor swap the appointment and still resolve.
  assert.match(imageContentPath(1, 2), /visits\/1\/results\/images\/2$/);
  assert.notEqual(imageContentPath(1, 2), imageContentPath(9, 2));
});

test("only a PDF asks for a download, and only through the one enum", () => {
  assert.equal(
    imageContentPath(8072, 41, "attachment"),
    "/api/doctor/visits/8072/results/images/41?disposition=attachment",
  );
  // Nothing else is expressible: the parameter is a literal union.
  assert.ok(!imageContentPath(8072, 41).includes("disposition"));
});

test("images default to empty rather than throwing on an older payload", () => {
  /*
    `images` is optional on the wire. A desk deployed against an Odoo predating
    Slice 8B must show no imaging section -- not crash a released report.
  */
  assert.deepEqual(resultImages(null), []);
  assert.deepEqual(resultImages(undefined), []);
  assert.deepEqual(resultImages({ images: undefined }), []);
  assert.deepEqual(resultImages({ images: [] }), []);
});

test("only pictures are paged through in a lightbox", () => {
  // A PDF is opened, not flicked past, so it never enters the index space.
  const images = [picture(), pdf(), picture({ id: 3 })];
  assert.deepEqual(
    viewableImages({ images }).map((image) => image.id),
    [1, 3],
  );
});

test("the worklist line counts by kind, because they are different acts", () => {
  assert.equal(imagingSummary({ images: [picture()] }), "1 image");
  assert.equal(
    imagingSummary({ images: [picture(), picture({ id: 3 })] }),
    "2 images",
  );
  assert.equal(imagingSummary({ images: [pdf()] }), "1 PDF");
  assert.equal(
    imagingSummary({ images: [picture(), picture({ id: 3 }), pdf()] }),
    "2 images · 1 PDF",
  );
});

test("a report with no imaging adds no line to the compact row", () => {
  // Null, not an empty string: the row must not grow a blank line and with it
  // a millimetre of height on a screen that is scanned.
  assert.equal(imagingSummary({ images: [] }), null);
  assert.equal(imagingSummary(null), null);
  assert.equal(imagingSummary(undefined), null);
});

test("file sizes read at a glance", () => {
  assert.equal(fileSizeText(512), "512 B");
  assert.equal(fileSizeText(184320), "180 KB");
  assert.equal(fileSizeText(2 * 1024 * 1024), "2.0 MB");
});

test("an unknown or empty size shows nothing rather than '0 B'", () => {
  assert.equal(fileSizeText(0), null);
  assert.equal(fileSizeText(null), null);
  assert.equal(fileSizeText(undefined), null);
});

test("a lone image has no position indicator", () => {
  assert.equal(lightboxPosition(0, 1), null);
  assert.equal(lightboxPosition(0, 0), null);
});

test("several images are numbered from one, as a human counts", () => {
  assert.equal(lightboxPosition(0, 3), "1 / 3");
  assert.equal(lightboxPosition(2, 3), "3 / 3");
});

test("paging wraps at both ends", () => {
  /*
    A doctor comparing two views of the same study should not have to notice
    which end of the list they are at.
  */
  assert.equal(stepIndex(0, 3, 1), 1);
  assert.equal(stepIndex(2, 3, 1), 0);
  assert.equal(stepIndex(0, 3, -1), 2);
  assert.equal(stepIndex(1, 3, -1), 0);
});

test("paging an empty list is a no-op rather than a crash", () => {
  assert.equal(stepIndex(0, 0, 1), 0);
  assert.equal(stepIndex(0, 0, -1), 0);
});

test("an unreachable image says so in words", () => {
  assert.equal(IMAGE_UNAVAILABLE_TEXT, "Image unavailable");
  // Never a backend error, never a stack, never a URL.
  const lowered = IMAGE_UNAVAILABLE_TEXT.toLowerCase();
  for (const banned of ["error", "404", "http", "odoo", "failed"]) {
    assert.ok(!lowered.includes(banned), banned);
  }
});

/* ------------------------------------------------------------------ *
 * Abnormality
 * ------------------------------------------------------------------ */

test("critical is the only flag that gets red", () => {
  assert.equal(abnormalTone("critical"), "critical");
  assert.match(abnormalToneClass(abnormalTone("critical")), /red/);
  for (const flag of ["low", "high", "abnormal", "normal", null]) {
    assert.doesNotMatch(abnormalToneClass(abnormalTone(flag)), /red/, String(flag));
  }
});

test("low, high and abnormal are amber", () => {
  for (const flag of ["low", "high", "abnormal"] as const) {
    assert.equal(abnormalTone(flag), "warn", flag);
    assert.match(abnormalToneClass(abnormalTone(flag)), /amber/, flag);
  }
});

test("normal is neutral, and an unknown flag degrades to neutral", () => {
  assert.equal(abnormalTone("normal"), "neutral");
  assert.equal(abnormalTone(null), "neutral");
  assert.equal(abnormalTone("something_new"), "neutral");
});

test("every non-normal flag is spelled out, so colour is never the only signal", () => {
  /*
    A doctor who cannot distinguish amber from red -- or who is reading a
    printed copy, or a screen-reader user -- must still see the word.
  */
  for (const flag of ["low", "high", "critical", "abnormal"] as const) {
    assert.equal(needsAbnormalWord(flag), true, flag);
    assert.ok(abnormalFlagText(flag, null), flag);
  }
  assert.equal(needsAbnormalWord("normal"), false);
});

test("the server's own label wins over any local wording", () => {
  // The laboratory made the judgement; the desk does not rephrase it.
  assert.equal(abnormalFlagText("critical", "Critical"), "Critical");
  assert.equal(abnormalFlagText("high", "Panic high"), "Panic high");
});

test("an unlabelled flag still renders something legible", () => {
  assert.equal(abnormalFlagText("critical", null), "CRITICAL");
  assert.equal(abnormalFlagText("brand_new", null), "BRAND_NEW");
  assert.equal(abnormalFlagText(null, null), null);
});

/* ------------------------------------------------------------------ *
 * The empty released report
 * ------------------------------------------------------------------ */

test("a released report with no text says exactly that", () => {
  assert.equal(
    reportPreview({ has_report: false, impression: null, findings: null }),
    EMPTY_REPORT_TEXT,
  );
  assert.equal(EMPTY_REPORT_TEXT, "Released with no report text recorded.");
});

test("the impression leads the preview, because it is the conclusion", () => {
  assert.equal(
    reportPreview({
      has_report: true,
      impression: "No acute process.",
      findings: "Lungs clear. Heart size normal.",
    }),
    "No acute process.",
  );
});

test("findings stand in when no impression was written", () => {
  assert.equal(
    reportPreview({
      has_report: true,
      impression: null,
      findings: "Lungs clear.",
    }),
    "Lungs clear.",
  );
});

test("has_report=false wins even if a field looks populated", () => {
  // The server decided; the client does not second-guess it into showing
  // whitespace as a report.
  assert.equal(
    reportPreview({ has_report: false, impression: "   ", findings: "  " }),
    EMPTY_REPORT_TEXT,
  );
});

test("whitespace is not documentation", () => {
  assert.equal(isDocumented("Fever."), true);
  for (const blank of ["", "   ", "\n", "\t", null, undefined]) {
    assert.equal(isDocumented(blank), false, JSON.stringify(blank));
  }
});

/* ------------------------------------------------------------------ *
 * Pending context
 * ------------------------------------------------------------------ */

test("a financial hold is named as one, and never as a sum", () => {
  const reason = pendingReason({
    billing_blocked: true,
    workflow_status: "requested",
  });
  assert.equal(reason, AWAITING_CLEARANCE_TEXT);
  // The doctor needs to know it is the cashier, not the bench. What is owed
  // is the cashier's screen.
  assert.doesNotMatch(reason ?? "", /\d/);
});

test("otherwise the request's own workflow wording is the context", () => {
  assert.equal(
    pendingReason({ billing_blocked: false, workflow_status: "sample_collected" }),
    "Sample collected",
  );
  assert.equal(
    pendingReason({ billing_blocked: false, workflow_status: "in_progress" }),
    "In progress",
  );
});

test("an unknown workflow key degrades to the raw token, never to blank", () => {
  assert.equal(workflowLabel("some_new_state"), "some_new_state");
  assert.equal(workflowLabel(null), null);
});

/* ------------------------------------------------------------------ *
 * Empties, freshness and superseded results
 * ------------------------------------------------------------------ */

test("no orders is distinguished from orders with nothing back yet", () => {
  assert.equal(hasAnyOrder({ laboratory: [], radiology: [] }), false);
  assert.equal(hasAnyOrder({ laboratory: [{}], radiology: [] }), true);
  assert.equal(hasAnyOrder({ laboratory: [], radiology: [{}] }), true);
  assert.equal(
    NO_ORDERS_TEXT,
    "No laboratory or radiology orders for this consultation.",
  );
});

test("the freshness line states when this tab last asked", () => {
  const text = checkedAtText("2026-08-28T09:41:00.000Z");
  assert.ok(text && text.startsWith("Checked at "));
});

test("a missing or unparseable timestamp shows nothing rather than 'Invalid Date'", () => {
  assert.equal(checkedAtText(null), null);
  assert.equal(checkedAtText("not-a-date"), null);
});

test("earlier released results are counted, never silently dropped", () => {
  /*
    Repeat testing is modelled as a NEW result, so more than one released
    result is a real shape. The card renders the newest; a doctor must not be
    left believing it is the only one.
  */
  assert.equal(supersededText(0), null);
  assert.equal(supersededText(1), "1 earlier released result exists");
  assert.equal(supersededText(3), "3 earlier released results exist");
});
