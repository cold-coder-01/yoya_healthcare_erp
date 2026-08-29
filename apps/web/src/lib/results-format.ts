/**
 * Doctor Results presentation decisions, as pure functions.
 *
 * WHY THESE LIVE HERE. Every rule below decides how a clinical finding is
 * shown to a doctor -- which word an abnormality carries, whether a released
 * report is honestly described as empty, whether a measured value survives the
 * trip to the screen intact. Those are exactly the rules worth pinning with
 * tests, and this project tests pure functions with `node:test` and adds no
 * DOM test stack for it. Extracting them means the tests exercise the SHIPPED
 * logic rather than a re-implementation of it in a test file.
 *
 * NOTHING HERE DERIVES CLINICAL MEANING. It does not decide whether a value is
 * abnormal (the laboratory did, and sent its own flag and label), does not
 * decide whether a result is available (the server did, from the result's
 * released state), and does not parse a measurement. A `value` of "< 0.01",
 * "Negative" or "70/100" travels to the screen byte for byte.
 *
 * Nothing here fetches.
 */
import type {
  AbnormalFlag,
  RadiologyImage,
  RadiologyResult,
  ResultStatus,
} from "@/types/doctor-results";

/* ------------------------------------------------------------------ *
 * Status
 * ------------------------------------------------------------------ */

/**
 * The three Results statuses, in the order a doctor scans them.
 *
 * THERE IS DELIBERATELY NO "REVIEWED". No model records that a doctor has read
 * a result, so a fourth chip would be the desk asserting something no record
 * supports -- and on a clinical screen, a false "seen" is worse than no chip.
 */
export const RESULT_STATUS_LABELS: Record<ResultStatus, string> = {
  available: "Result available",
  pending: "Pending",
  cancelled: "Cancelled",
};

export function resultStatusLabel(status: ResultStatus): string {
  return RESULT_STATUS_LABELS[status];
}

/**
 * The visual system for one status: chip, dot and card accent.
 *
 * SHAPED LIKE doctor-badges.STAGE_TONE ON PURPOSE, so a status on this tab and
 * a stage in the header are recognisably the same kind of object rather than
 * two designers' idea of a badge.
 *
 * COLOUR CARRIES ONE MEANING EACH, following the desk's existing rule:
 *
 *   emerald = the thing you were waiting for is here
 *   amber   = something is outstanding
 *   red     = an exception, and a MUTED one here
 *
 * THE DOT IS NOT DECORATION. It is the second, non-textual signal that lets a
 * doctor pick a changed row out of a column of cards at a glance -- but it is
 * never the ONLY signal: every chip renders its word beside it, so the status
 * survives greyscale, a printed copy and a screen reader.
 *
 * CANCELLED IS DELIBERATELY THE WEAKEST RED ON THIS SCREEN. Strong red belongs
 * to a `critical` abnormal flag, which is a clinical emergency; a cancelled
 * order is an administrative fact. If the two competed, the card state would
 * shout down the finding, which is exactly backwards.
 */
export type StatusTone = {
  /** Badge classes. */
  chip: string;
  /** The dot inside the badge. */
  dot: string;
  /** The card's left accent rail. */
  accent: string;
};

const STATUS_TONE: Record<ResultStatus, StatusTone> = {
  available: {
    chip: "border-emerald-500 bg-emerald-50 text-emerald-900",
    dot: "bg-emerald-600",
    accent: "border-l-emerald-600",
  },
  pending: {
    chip: "border-amber-400 bg-amber-50 text-amber-900",
    dot: "bg-amber-500",
    accent: "border-l-amber-500",
  },
  cancelled: {
    chip: "border-red-200 bg-red-50/60 text-red-800",
    dot: "bg-red-300",
    accent: "border-l-red-300",
  },
};

export function resultStatusTone(status: ResultStatus): StatusTone {
  return STATUS_TONE[status] ?? STATUS_TONE.pending;
}

/**
 * A section's state at a glance: "1 available · 2 pending".
 *
 * Ordered by what a doctor acts on first, and EMPTY GROUPS ARE OMITTED -- a
 * header reading "0 cancelled" is noise that makes the counts that matter
 * harder to read. Returns null when there is nothing to summarise, so the
 * header simply has no right-hand side.
 */
export function sectionSummary(rows: ReadonlyArray<{ status: ResultStatus }>): string | null {
  const order: ResultStatus[] = ["available", "pending", "cancelled"];
  const parts = order
    .map((status) => {
      const count = rows.filter((row) => row.status === status).length;
      if (!count) return null;
      // Lower-cased: this is a quiet tally beside a heading, not a second
      // status badge competing with the ones on the cards.
      return `${count} ${RESULT_STATUS_LABELS[status].toLowerCase()}`;
    })
    .filter(Boolean);
  return parts.length ? parts.join(" · ") : null;
}

/**
 * The ordered services a card is about, as one readable line.
 *
 * THE CLINICAL SERVICE IS THE CARD'S IDENTITY, not the request code. A doctor
 * remembers "the CBC I ordered", not "LABREQ0214", so the names lead and the
 * code recedes to a reference beside them.
 *
 * Capped rather than wrapped to three lines: past a few names the list stops
 * being scannable, and the count is more useful than the tail.
 */
export function serviceSummary(names: string[], max = 3): string {
  if (!names.length) return "—";
  if (names.length <= max) return names.join(", ");
  return `${names.slice(0, max).join(", ")} +${names.length - max} more`;
}

/* ------------------------------------------------------------------ *
 * Abnormality
 * ------------------------------------------------------------------ */

/**
 * The word shown for an abnormality, when the server sent no label.
 *
 * The server's `abnormal_flag_label` is preferred everywhere; this is the
 * fallback for an unknown key, and it upper-cases rather than inventing
 * wording. An unrecognised flag must still show SOMETHING legible next to a
 * clinical value.
 */
export function abnormalFlagText(
  flag: AbnormalFlag | string | null,
  label: string | null,
): string | null {
  if (label) return label;
  if (!flag) return null;
  return flag.toUpperCase();
}

/**
 * The visual weight an abnormality carries.
 *
 * RED IS RESERVED FOR `critical`, and for nothing else on this screen. Spending
 * it on every out-of-range value -- or on a read-only badge -- drains it from
 * the one place it has to mean "act now".
 */
export type AbnormalTone = "critical" | "warn" | "neutral";

export function abnormalTone(flag: AbnormalFlag | string | null): AbnormalTone {
  if (flag === "critical") return "critical";
  if (flag === "low" || flag === "high" || flag === "abnormal") return "warn";
  return "neutral";
}

/** Cell classes per tone. Colour is never the only signal -- see below. */
export function abnormalToneClass(tone: AbnormalTone): string {
  switch (tone) {
    case "critical":
      return "border-red-400 bg-red-50 text-red-900";
    case "warn":
      return "border-amber-400 bg-amber-50 text-amber-900";
    default:
      return "border-slate-200 bg-white text-slate-600";
  }
}

/**
 * Whether the row needs its abnormality spelled out.
 *
 * STATE IS NEVER COLOUR ALONE. Every non-normal flag renders its word, so a
 * doctor who cannot distinguish amber from red -- or who is reading a printed
 * copy -- still sees LOW, HIGH, CRITICAL or ABNORMAL.
 */
export function needsAbnormalWord(flag: AbnormalFlag | string | null): boolean {
  return abnormalTone(flag) !== "neutral";
}

/* ------------------------------------------------------------------ *
 * Compact summaries for the worklist
 * ------------------------------------------------------------------ */

/** The one phrase both services use to open the viewer. */
export const OPEN_RESULT_TEXT = "Open result";

/**
 * Severity order for a one-line tally. Worst first, because the first thing
 * past the test count is the thing the doctor most needs to have seen.
 */
const FLAG_SEVERITY: AbnormalFlag[] = ["critical", "high", "low", "abnormal"];

/**
 * One line describing a released laboratory result, for the collapsed card.
 *
 *   "1 test · Normal"
 *   "5 tests · 1 Critical · 2 High"
 *   "4 tests"                        (something is unflagged)
 *
 * DERIVED FROM `abnormal_flag` AND NOTHING ELSE. It never looks at `value`,
 * `unit` or `reference_range`: those are free text the laboratory wrote, and
 * deciding from them whether a result is abnormal would be this layer forming
 * a clinical opinion no clinician signed.
 *
 * THE UNFLAGGED CASE IS WHY THIS IS NOT A ONE-LINER. If any line carries no
 * flag at all, the set is NOT declared "Normal" -- the honest summary is the
 * count alone. Saying "Normal" over a line the bench never judged would be
 * fabricating exactly the reassurance a doctor would act on.
 *
 * The displayed word for a flag is the SERVER's own label where it sent one,
 * so a laboratory that says "Panic high" is quoted rather than paraphrased.
 */
export function labResultSummary(
  lines: ReadonlyArray<{
    abnormal_flag: AbnormalFlag | string | null;
    abnormal_flag_label: string | null;
  }>,
): string {
  const count = lines.length;
  if (!count) return "No reported tests";
  const noun = count === 1 ? "1 test" : `${count} tests`;

  const wordFor = (flag: string) =>
    lines.find((line) => line.abnormal_flag === flag)?.abnormal_flag_label ??
    flag.charAt(0).toUpperCase() + flag.slice(1);

  const parts = FLAG_SEVERITY.map((flag) => {
    const n = lines.filter((line) => line.abnormal_flag === flag).length;
    return n ? `${n} ${wordFor(flag)}` : null;
  }).filter(Boolean);

  if (parts.length) return `${noun} · ${parts.join(" · ")}`;
  // No abnormality anywhere. Only claim "Normal" when EVERY line was judged.
  const allJudged = lines.every((line) => line.abnormal_flag === "normal");
  return allJudged ? `${noun} · ${wordFor("normal")}` : noun;
}

/**
 * Whether a request has something a viewer could show.
 *
 * A RELEASED RESULT IS THE ONLY THING THAT OPENS. Pending and cancelled
 * requests have nothing to read, so they get no action at all rather than a
 * disabled control that invites a click and refuses it.
 */
export function canOpenResult(row: { result: unknown | null }): boolean {
  return row.result !== null && row.result !== undefined;
}

/* ------------------------------------------------------------------ *
 * Imaging
 * ------------------------------------------------------------------ */

/**
 * The images on a released report, defaulted.
 *
 * EVERY READ GOES THROUGH HERE because `images` is optional on the wire: a
 * desk deployed against an Odoo predating Slice 8B receives a payload without
 * it, and a Results tab that threw on a released report would be a worse
 * failure than one that shows no imaging section.
 */
export function resultImages(
  result: Pick<RadiologyResult, "images"> | null | undefined,
): RadiologyImage[] {
  return result?.images ?? [];
}

/** Only the items a lightbox can display. A PDF is opened, not paged through. */
export function viewableImages(
  result: Pick<RadiologyResult, "images"> | null | undefined,
): RadiologyImage[] {
  return resultImages(result).filter((image) => image.kind === "image");
}

/**
 * THE ONE URL THE BROWSER USES FOR CLINICAL IMAGERY.
 *
 * Composed here, from ids, and never taken from the payload -- which carries
 * no URL at all, precisely so that an Odoo origin, a /web/content path or an
 * access token has nowhere to hide. Every `<img src>` and every Open button on
 * this screen resolves through this function.
 */
export function imageContentPath(
  appointmentId: number,
  imageId: number,
  disposition?: "attachment",
): string {
  const base = `/api/doctor/visits/${appointmentId}/results/images/${imageId}`;
  return disposition === "attachment" ? `${base}?disposition=attachment` : base;
}

/**
 * The worklist's one-line imaging note: "1 image", "2 images · 1 PDF".
 *
 * Counted by KIND, because the two are different acts for a doctor -- one is
 * looked at, the other is opened. Null when there is nothing attached, so the
 * compact row grows no taller than it did before Slice 8B.
 */
export function imagingSummary(
  result: Pick<RadiologyResult, "images"> | null | undefined,
): string | null {
  const images = resultImages(result);
  if (!images.length) return null;
  const pictures = images.filter((image) => image.kind === "image").length;
  const pdfs = images.length - pictures;
  const parts: string[] = [];
  if (pictures) parts.push(pictures === 1 ? "1 image" : `${pictures} images`);
  if (pdfs) parts.push(pdfs === 1 ? "1 PDF" : `${pdfs} PDFs`);
  return parts.join(" · ");
}

/**
 * A file size a clinician can read at a glance.
 *
 * Rounded for legibility, not for accounting: the number tells a doctor
 * whether a file will open quickly, and no decision turns on the exact byte.
 */
export function fileSizeText(bytes: number | null | undefined): string | null {
  if (!bytes || bytes <= 0) return null;
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

/** Shown in place of a thumbnail whose bytes did not arrive. */
export const IMAGE_UNAVAILABLE_TEXT = "Image unavailable";

/** The lightbox position line, or null when there is nothing to page through. */
export function lightboxPosition(index: number, total: number): string | null {
  if (total <= 1) return null;
  return `${index + 1} / ${total}`;
}

/**
 * The next index when paging a lightbox, wrapping at both ends.
 *
 * Wrapping rather than stopping: a doctor comparing two views of the same
 * study should not have to notice which end of the list they are at.
 */
export function stepIndex(index: number, total: number, step: number): number {
  if (total <= 0) return 0;
  return (index + step + total) % total;
}

/* ------------------------------------------------------------------ *
 * Clinical text
 * ------------------------------------------------------------------ */

/** Shown where a measurement was never recorded. Never a blank cell. */
export const NO_VALUE_TEXT = "—";

/**
 * A measured value, EXACTLY as the bench recorded it.
 *
 * The identity function on purpose, with only the empty case handled. It exists
 * so the intent is stated once and a test can pin it: `result_value` and
 * `reference_range` are Char on the model and hold "< 0.01", "Negative", "No
 * growth after 48h", "70/100". Parsing, rounding or re-ranging any of them here
 * would fabricate precision the laboratory did not report.
 */
export function clinicalValue(value: string | null | undefined): string {
  if (value === null || value === undefined || value === "") return NO_VALUE_TEXT;
  return value;
}

/** Whether a narrative field holds anything a colleague could read. */
export function isDocumented(value: string | null | undefined): boolean {
  return typeof value === "string" && value.trim().length > 0;
}

/**
 * What a released radiology report says when it says nothing.
 *
 * A REACHABLE STATE, not a defensive nicety: hospital_radiology has no
 * completeness constraint, so a report can be entered, validated and released
 * with no findings and no impression, and the database already holds one.
 * Blank space reads as a broken screen; this reads as a fact about the record.
 */
export const EMPTY_REPORT_TEXT = "Released with no report text recorded.";

/** The clinician-facing conclusion, or the honest fallback. */
export function reportPreview(result: {
  has_report: boolean;
  impression: string | null;
  findings: string | null;
}): string {
  if (!result.has_report) return EMPTY_REPORT_TEXT;
  // Impression leads: it is the conclusion a clinician acts on. Findings are
  // the fallback only when the radiologist wrote no impression.
  if (isDocumented(result.impression)) return result.impression as string;
  if (isDocumented(result.findings)) return result.findings as string;
  return EMPTY_REPORT_TEXT;
}

/* ------------------------------------------------------------------ *
 * Pending context
 * ------------------------------------------------------------------ */

/**
 * The request's own workflow wording, as SECONDARY context under a pending
 * chip. Restated from the two request models' state selections; a key with no
 * entry falls back to the raw token rather than rendering blank.
 */
const WORKFLOW_LABELS: Record<string, string> = {
  draft: "Draft",
  requested: "Requested",
  sample_collected: "Sample collected",
  scheduled: "Scheduled",
  in_progress: "In progress",
  completed: "Completed",
  cancelled: "Cancelled",
};

export function workflowLabel(state: string | null): string | null {
  if (!state) return null;
  return WORKFLOW_LABELS[state] ?? state;
}

/**
 * Why nothing has come back yet, when the answer is the cashier.
 *
 * A BOOLEAN VERDICT AND A SENTENCE, never a sum. The doctor needs to know the
 * hold-up is financial rather than clinical, because that is a different person
 * to chase; what is owed is the cashier's screen and never appears here.
 */
export const AWAITING_CLEARANCE_TEXT = "Awaiting financial clearance";

export function pendingReason(row: {
  billing_blocked: boolean;
  workflow_status: string | null;
}): string | null {
  if (row.billing_blocked) return AWAITING_CLEARANCE_TEXT;
  return workflowLabel(row.workflow_status);
}

/* ------------------------------------------------------------------ *
 * Counts and empties
 * ------------------------------------------------------------------ */

/** Nothing was ever ordered -- distinct from ordered-but-not-back. */
export const NO_ORDERS_TEXT =
  "No laboratory or radiology orders for this consultation.";

export function hasAnyOrder(payload: {
  laboratory: unknown[];
  radiology: unknown[];
}): boolean {
  return payload.laboratory.length > 0 || payload.radiology.length > 0;
}

/**
 * "Checked at HH:MM", in the browser's own locale.
 *
 * The freshness of a screen the doctor is waiting on is worth stating, and a
 * bare timestamp is enough: the Results tab refetches on open and on demand,
 * so the honest claim is when this tab last asked, not when the result landed.
 */
export function checkedAtText(iso: string | null): string | null {
  if (!iso) return null;
  const when = new Date(iso);
  if (Number.isNaN(when.getTime())) return null;
  return `Checked at ${when.toLocaleTimeString(undefined, {
    hour: "2-digit",
    minute: "2-digit",
  })}`;
}

/**
 * How many earlier released results the server is not showing.
 *
 * Repeat testing is modelled as a NEW result, so more than one released result
 * is a real shape. The newest is rendered; this names the rest rather than
 * letting the card imply it is the only one.
 */
export function supersededText(count: number): string | null {
  if (count <= 0) return null;
  return count === 1
    ? "1 earlier released result exists"
    : `${count} earlier released results exist`;
}
