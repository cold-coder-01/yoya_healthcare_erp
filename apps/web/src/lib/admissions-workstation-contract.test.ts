/**
 * THE ADMISSIONS DESK'S WRITE, PRIVACY AND LAYOUT GUARANTEES, HELD AT THE
 * SOURCE (no DOM test stack exists; the technique the Pharmacy and Radiology
 * workstation contracts use). Comments are stripped before any "must not
 * contain" check.
 *
 *   1. ONE WRITE. The only POST is Admit (Slice 2), sent by the workstation to
 *      admitPath(). No transfer, discharge or cancel control exists; Admit is
 *      offered only on the server's `admit` capability AND the row's
 *      `can_admit`.
 *   2. BFF ONLY. Every fetch goes through a path helper; no Odoo URL.
 *   3. ROLE FIRST. Nothing but the session loads until the server has said the
 *      desk is this user's; a refused role sees "This is not your workstation".
 *   4. SERVER-DECIDED. Lanes, counts, review reasons and clearance arrive from
 *      the server; the browser never derives them and names no money.
 *   5. BED BOARD. A cell names a patient only from `bed.admission`, which the
 *      server omits when the caller may not read it; clicking pins.
 */
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

function read(relative: string): string {
  return readFileSync(new URL(`../${relative}`, import.meta.url), "utf8");
}

function code(source: string): string {
  return source
    .replace(/\/\*[\s\S]*?\*\//g, "")
    .replace(/\{\s*\/\*[\s\S]*?\*\/\s*\}/g, "")
    .replace(/^\s*\/\/.*$/gm, "");
}

const WORKSTATION = read("components/admissions/admissions-workstation.tsx");
const QUEUE = read("components/admissions/admissions-queue.tsx");
const PANEL = read("components/admissions/admission-detail-panel.tsx");
const BEDS = read("components/admissions/bed-board.tsx");
const STRIP = read("components/admissions/ward-occupancy-strip.tsx");
const TOOLBAR = read("components/admissions/admissions-toolbar.tsx");
const PILL = read("components/admissions/admission-lane-pill.tsx");
const FORMAT = read("lib/admissions-desk-format.ts");
const LAYOUT = read("app/admissions/layout.tsx");
const PAGE = read("app/admissions/page.tsx");
const SIDEBAR = read("components/clinical/clinical-sidebar.tsx");
const DIALOG = read("components/admissions/admit-dialog.tsx");
const TRANSFER_DIALOG = read("components/admissions/transfer-dialog.tsx");
const CANCEL_DIALOG = read("components/admissions/cancel-request-dialog.tsx");
const FINALIZE_DIALOG = read("components/admissions/finalize-discharge-dialog.tsx");

const ALL: ReadonlyArray<readonly [string, string]> = [
  ["admissions-workstation.tsx", WORKSTATION],
  ["admissions-queue.tsx", QUEUE],
  ["admission-detail-panel.tsx", PANEL],
  ["bed-board.tsx", BEDS],
  ["ward-occupancy-strip.tsx", STRIP],
  ["admissions-toolbar.tsx", TOOLBAR],
  ["admission-lane-pill.tsx", PILL],
  ["admit-dialog.tsx", DIALOG],
  ["transfer-dialog.tsx", TRANSFER_DIALOG],
  ["cancel-request-dialog.tsx", CANCEL_DIALOG],
  ["finalize-discharge-dialog.tsx", FINALIZE_DIALOG],
  ["admissions-desk-format.ts", FORMAT],
  ["layout.tsx", LAYOUT],
  ["page.tsx", PAGE],
];

/* 1. One write */

test("the only writes are the workstation's Admit, Transfer, Cancel request and Finalize discharge POSTs", () => {
  for (const [name, source] of ALL) {
    const writes = code(source).match(/method:\s*["'](POST|PUT|PATCH|DELETE)["']/g) ?? [];
    if (name === "admissions-workstation.tsx") {
      assert.deepEqual(writes, ['method: "POST"', 'method: "POST"', 'method: "POST"', 'method: "POST"'], name);
    } else {
      assert.equal(writes.length, 0, name);
    }
  }
  const emitted = code(WORKSTATION);
  assert.ok(emitted.includes("fetch(admitPath(current.id)"));
  assert.ok(emitted.includes("fetch(transferPath(current.id)"));
  assert.ok(emitted.includes("fetch(cancelRequestPath(current.id)"));
  assert.ok(emitted.includes("fetch(finalizeDischargePath(current.id)"));
});

test("the only discharge control is Finalize discharge, and there is no assign-bed control", () => {
  // Slice 4: the Admissions Desk FINALIZES; it never declares medical
  // readiness (the Doctor Desk's act) and never "discharges" by another name.
  const forbidden = /\b(Discharge patient|Request discharge|Cancel admission|Assign bed)\b/;
  for (const [name, source] of ALL) {
    const emitted = code(source);
    for (const match of emitted.matchAll(/<button[\s\S]*?<\/button>/g)) {
      assert.doesNotMatch(match[0], forbidden, `${name}: ${match[0].slice(0, 80)}`);
    }
    const allowed = name === "admissions-workstation.tsx";
    assert.doesNotMatch(emitted, /capabilities\.assign_bed/, name);
    if (!allowed) assert.doesNotMatch(emitted, /capabilities\.discharge/, name);
  }
});

test("Finalize discharge is offered only on the server's capability AND the record's affordance", () => {
  const workstation = code(WORKSTATION);
  assert.ok(workstation.includes("const mayDischarge = session?.capabilities.discharge === true;"));
  assert.ok(workstation.includes("{finalizeOpen && shownDetail && mayDischarge ? ("));
  const panel = code(PANEL);
  assert.ok(panel.includes("{mayDischarge && detail.discharge?.can_finalize_discharge && onRequestFinalize ? ("));
  const dialog = code(FINALIZE_DIALOG);
  assert.ok(dialog.includes("if (event.detail === 0) return;"));
  for (const label of ["Patient", "Admission", "Ward / room / bed", "Medical", "Financial", "Revision"]) {
    assert.ok(dialog.includes(`>${label}</dt>`), label);
  }
  assert.ok(dialog.includes("financialLabel(detail.financial)"));
});

test("Transfer and Cancel request are offered only on the server's capability AND the row's affordance", () => {
  const workstation = code(WORKSTATION);
  assert.ok(workstation.includes("const mayTransfer = session?.capabilities.transfer === true;"));
  assert.ok(workstation.includes("const mayCancelRequest = session?.capabilities.cancel_request === true;"));
  assert.ok(workstation.includes("{transferOpen && shownDetail && mayTransfer ? ("));
  assert.ok(workstation.includes("{cancelOpen && shownDetail && mayCancelRequest ? ("));
  const panel = code(PANEL);
  assert.ok(panel.includes("{mayTransfer && detail.can_transfer && onRequestTransfer ? ("));
  assert.ok(panel.includes("{mayCancelRequest && detail.can_cancel_request && onRequestCancel ? ("));
});

test("transfer is two steps, reviews FROM and TO, and the final click is pointer-only", () => {
  const dialog = code(TRANSFER_DIALOG);
  assert.ok(dialog.includes('onClick={() => setStep("confirm")}'));
  assert.ok(dialog.includes("disabled={!selected || !cleaned}"));
  assert.ok(dialog.includes("if (event.detail === 0 || !selected || !cleaned) return;"));
  for (const label of ["Patient", "Admission", "From", "To", "Reason", "Revision"]) {
    assert.ok(dialog.includes(`>${label}</dt>`), label);
  }
  // Destinations are the server's available beds, never the current one.
  assert.ok(dialog.includes('bed.state === "available" && !bed.needs_review && bed.id !== currentBedId'));
});

test("cancel request explains itself and its confirm is pointer-only", () => {
  const dialog = code(CANCEL_DIALOG);
  assert.ok(dialog.includes("The patient has not been admitted."));
  assert.ok(dialog.includes("No bed is occupied by this request."));
  assert.ok(dialog.includes("The request will be cancelled."));
  assert.ok(dialog.includes("if (event.detail === 0) return;"));
});

test("transfer and cancel reuse the kept-on-unknown token and the reload rules", () => {
  const emitted = code(WORKSTATION);
  assert.ok(emitted.includes('tokenFor(pendingRef.current, "transfer", current.id, signature, () => crypto.randomUUID())'));
  assert.ok(emitted.includes('tokenFor(pendingRef.current, "cancel_request", current.id, signature, () => crypto.randomUUID())'));
  assert.equal((emitted.match(/if \(!isResolved\(status, payload !== null\)\)/g) ?? []).length, 4);
});

test("Admit is offered only on the server's role capability AND the row's can_admit", () => {
  const workstation = code(WORKSTATION);
  assert.ok(workstation.includes("const mayAdmit = session?.capabilities.admit === true;"));
  assert.ok(workstation.includes("mayAdmit={mayAdmit}"));
  assert.ok(workstation.includes("{admitOpen && shownDetail && mayAdmit ? ("));
  const panel = code(PANEL);
  assert.ok(panel.includes("{mayAdmit && detail.can_admit && onRequestAdmit ? ("));
  // Four workflow actions, the read-only Preview and the read-only Final
  // settlement window (Advance slice; discharge role, stay in a bed).
  assert.equal((panel.match(/<button/g) ?? []).length, 6);
  assert.ok(panel.includes('{mayDischarge && (detail.state === "admitted" || detail.state === "transferred") ? ('));
  // Only the workstation reads the capability; nothing else invents it.
  for (const [name, source] of ALL) {
    if (name === "admissions-workstation.tsx") continue;
    assert.doesNotMatch(code(source), /capabilities\.admit/, name);
  }
});

/* 2. BFF only */

test("every fetch goes through a BFF path helper and no Odoo address appears", () => {
  const emitted = code(WORKSTATION);
  const fetches = [...emitted.matchAll(/fetch\(\s*([^,\n]+)/g)].map((m) => m[1].trim());
  assert.equal(fetches.length, 10);
  for (const target of fetches) {
    assert.match(
      target,
      /^(ADMISSIONS_SESSION_PATH|ADMISSIONS_WARDS_PATH|worklistPath\(|bedsPath\(|admissionPath\(|admitPath\(|transferPath\(|cancelRequestPath\(|finalizeDischargePath\()/,
      target,
    );
  }
  for (const [name, source] of ALL) {
    if (name === "admissions-workstation.tsx") continue;
    assert.doesNotMatch(code(source), /\bfetch\(/, name);
  }
  for (const [name, source] of ALL) {
    assert.doesNotMatch(code(source), /yoya-emr|:8069|localhost|https?:\/\//, name);
  }
});

/* 3. Role first */

test("nothing but the session loads before the desk is confirmed as this user's", () => {
  const emitted = code(WORKSTATION);
  assert.ok(emitted.includes("const ready = deskAllowed === true;"));
  // Every non-session loader bails out first while not ready.
  assert.equal((emitted.match(/if \(!ready\) return;/g) ?? []).length, 3);
  assert.ok(emitted.includes("if (!ready || activeId === null) return;"));
});

test("a refused role sees the workstation refusal, not an error or an empty census", () => {
  const emitted = code(WORKSTATION);
  assert.ok(emitted.includes("response.status === 403"));
  assert.ok(emitted.includes("This is not your workstation."));
  const refusal = emitted.indexOf("if (deskAllowed === false)");
  assert.ok(refusal > 0 && refusal < emitted.indexOf("<WardOccupancyStrip"));
});

test("an empty census for an authorized role is an intentional message", () => {
  assert.ok(code(WORKSTATION).includes("emptyQueueMessage(session?.scope, lane)"));
  assert.ok(code(QUEUE).includes("{emptyMessage}"));
});

/* 4. Server-decided, no money */

test("lane counts come from the server summary, never from counting rows", () => {
  const emitted = code(TOOLBAR);
  assert.ok(emitted.includes("summary[key as keyof AdmissionLaneSummary]"));
  assert.doesNotMatch(emitted, /rows\.(length|filter)/);
  assert.doesNotMatch(code(WORKSTATION), /\.lane\s*===|review_reasons\.push|billing_blocked\s*=/);
});

test("the queue and panel render the server's lane and review reasons", () => {
  assert.ok(code(QUEUE).includes("<AdmissionLanePill lane={row.lane} compact />"));
  const panel = code(PANEL);
  assert.ok(panel.includes('role="alert"'));
  assert.ok(panel.includes("detail.review_reasons.map"));
  assert.ok(panel.includes("{reason.message}"));
});

test("no surface names money", () => {
  const money = /\b(amount|price|tariff|currency|invoice|receipt|balance|outstanding|daily_rate|admission_fee|ETB|Birr)\b/i;
  for (const [name, source] of ALL) {
    assert.doesNotMatch(code(source), money, name);
  }
});

test("a legacy admission's visit and clearance are shown honestly", () => {
  assert.ok(code(PANEL).includes("encounterLabel(detail.encounter)"));
  assert.ok(code(PANEL).includes("clearanceLabel(detail.clearance)"));
  assert.ok(code(FORMAT).includes("No linked visit (legacy admission)"));
  assert.ok(code(FORMAT).includes("clearance.billing_blocked === null"));
});

/* 5. Bed board */

test("a bed cell names a patient only from the server's admission object", () => {
  const emitted = code(BEDS);
  assert.ok(emitted.includes("const admission = bed.admission;"));
  // Patient identity is only ever read off `admission`.
  assert.doesNotMatch(emitted, /bed\.patient|bed\.mrn|current_admission/);
  assert.ok(emitted.includes("{admission ? ("));
});

test("clicking a visible occupied bed pins its admission", () => {
  assert.ok(code(BEDS).includes("onClick={() => onPin(admission.id)}"));
  assert.ok(code(WORKSTATION).includes("setPinnedId(admissionId)"));
  assert.ok(code(WORKSTATION).includes("pinnedId ?? resolveSelection(rows, selectedId)"));
});

test("the ward strip filters both the census and the bed board", () => {
  const emitted = code(WORKSTATION);
  assert.ok(emitted.includes("worklistPath({ lane, wardId, q: debouncedSearch || null })"));
  assert.ok(emitted.includes("bedsPath({ wardId })"));
  assert.ok(code(STRIP).includes("onSelect(selected ? null : ward.id)"));
});

/* Shell */

test("the page is the workstation and the shell does no authorization", () => {
  assert.ok(code(PAGE).includes("<AdmissionsWorkstation />"));
  assert.doesNotMatch(code(LAYOUT), /redirect\(|notFound\(|has_group|may_admissions/);
});

test("the Inpatient entry points at the desk without disturbing the clinical links", () => {
  assert.ok(SIDEBAR.includes('{ label: "Inpatient", href: "/admissions", disabled: false }'));
  assert.ok(SIDEBAR.includes('{ label: "Evaluation Queue", href: "/triage", disabled: false }'));
});
