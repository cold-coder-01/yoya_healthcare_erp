/**
 * Pharmacy Desk mutations (Slice 2): drafts, request bodies, confirmation
 * summaries and operation-token reuse. Pure functions -- nothing here fetches.
 *
 * THE RULES THIS FILE KEEPS:
 *
 *   * Quantities are CUMULATIVE. The pharmacist types the total intended to
 *     have been supplied after the next validation -- 10, not "6 more" -- and
 *     the increment is shown as a consequence, never typed.
 *   * The browser checks the obvious bounds only to explain them early. The
 *     server re-checks everything under its locks and its answer wins.
 *   * ONE operation token per intended action. A retry of the SAME unresolved
 *     request reuses it, so a double click or a lost response is replayed by
 *     the server rather than applied twice. A changed request gets a new one.
 *   * Every URL built here is a BFF path.
 */
import type {
  PharmacyDispenseDetail,
  PharmacyDispenseLine,
  PharmacyPrepareRequest,
  PharmacyValidateRequest,
} from "@/types/pharmacy-desk";

/* ------------------------------------------------------------------ *
 * Paths
 * ------------------------------------------------------------------ */

export function preparePath(dispenseId: number) {
  return `/api/pharmacy/dispenses/${dispenseId}/prepare`;
}

export function validatePath(dispenseId: number) {
  return `/api/pharmacy/dispenses/${dispenseId}/validate`;
}

/* ------------------------------------------------------------------ *
 * Quantities -- three decimals, like the server
 * ------------------------------------------------------------------ */

export function round3(value: number) {
  return Math.round(value * 1000) / 1000;
}

/** A typed quantity, or null when it is not a finite number. */
export function parseQuantity(raw: string): number | null {
  const trimmed = raw.trim();
  if (!/^\d+(\.\d+)?$/.test(trimmed)) return null;
  const value = Number(trimmed);
  return Number.isFinite(value) ? round3(value) : null;
}

/* ------------------------------------------------------------------ *
 * Drafts
 * ------------------------------------------------------------------ */

/** line id -> the text in its input. */
export type PrepareDraft = Record<number, string>;

/** Seeded from the SERVER's current intended quantities. */
export function draftFromDetail(detail: PharmacyDispenseDetail): PrepareDraft {
  const draft: PrepareDraft = {};
  for (const line of detail.lines) {
    draft[line.id] = String(round3(line.intended_quantity));
  }
  return draft;
}

/** Which dispense and revision a draft was seeded from. */
export function draftKey(detail: Pick<PharmacyDispenseDetail, "id" | "workflow_revision">) {
  return `${detail.id}:${detail.workflow_revision}`;
}

export type LineIssue = "invalid" | "below_supplied" | "above_prescribed";

export type PrepareLinePlan = {
  line: PharmacyDispenseLine;
  intended: number | null;
  supplied: number;
  increment: number;
  issue: LineIssue | null;
};

export type PreparePlan = {
  lines: PrepareLinePlan[];
  valid: boolean;
  hasIncrement: boolean;
};

/** The plan the confirmation step shows, with the obvious bounds explained. */
export function preparePlan(detail: PharmacyDispenseDetail, draft: PrepareDraft): PreparePlan {
  const lines = detail.lines.map((line) => {
    const intended = parseQuantity(draft[line.id] ?? "");
    const supplied = round3(line.minimum_intended_quantity);
    let issue: LineIssue | null = null;
    if (intended === null) issue = "invalid";
    else if (intended < supplied) issue = "below_supplied";
    else if (intended > round3(line.prescribed_quantity)) issue = "above_prescribed";
    const increment = intended === null ? 0 : round3(Math.max(intended - supplied, 0));
    return { line, intended, supplied, increment, issue };
  });
  const valid = lines.every((plan) => plan.issue === null);
  return { lines, valid, hasIncrement: lines.some((plan) => plan.increment > 0) };
}

export function lineIssueText(issue: LineIssue | null) {
  if (issue === "invalid") return "Enter a number";
  if (issue === "below_supplied") return "Below already supplied";
  if (issue === "above_prescribed") return "Above prescribed";
  return null;
}

/* ------------------------------------------------------------------ *
 * Request bodies -- exactly the fields the server accepts
 * ------------------------------------------------------------------ */

export function prepareBody(
  detail: PharmacyDispenseDetail,
  plan: PreparePlan,
  operationToken: string,
): PharmacyPrepareRequest {
  return {
    operation_token: operationToken,
    expected_revision: detail.workflow_revision,
    lines: plan.lines.map((entry) => ({
      line_id: entry.line.id,
      intended_quantity: entry.intended ?? 0,
    })),
  };
}

export function validateBody(
  detail: PharmacyDispenseDetail,
  operationToken: string,
): PharmacyValidateRequest {
  return { operation_token: operationToken, expected_revision: detail.workflow_revision };
}

/* ------------------------------------------------------------------ *
 * Validation summary -- what will be handed over NOW
 * ------------------------------------------------------------------ */

export type ValidateLineSummary = {
  line: PharmacyDispenseLine;
  intended: number;
  delivered: number;
  increment: number;
};

/** Per line: cumulative intent, already delivered, and the increment now. */
export function validationSummary(detail: PharmacyDispenseDetail): ValidateLineSummary[] {
  return detail.lines.map((line) => {
    const delivered = round3(line.minimum_intended_quantity);
    const intended = round3(line.intended_quantity);
    return { line, intended, delivered, increment: round3(Math.max(intended - delivered, 0)) };
  });
}

/* ------------------------------------------------------------------ *
 * Operation tokens
 * ------------------------------------------------------------------ */

/** An unresolved request: sent, but no answer was received. */
export type PendingOperation = {
  kind: "prepare" | "validate";
  dispenseId: number;
  signature: string;
  token: string;
};

/** Everything that makes two requests "the same request", except the token. */
export function requestSignature(body: { expected_revision: number; lines?: unknown }) {
  return JSON.stringify({ revision: body.expected_revision, lines: body.lines ?? null });
}

/**
 * The token for this request: the pending one when this is a retry of the SAME
 * unresolved request, otherwise a fresh UUID.
 */
export function tokenFor(
  pending: PendingOperation | null,
  kind: PendingOperation["kind"],
  dispenseId: number,
  signature: string,
  mint: () => string,
): string {
  if (
    pending &&
    pending.kind === kind &&
    pending.dispenseId === dispenseId &&
    pending.signature === signature
  ) {
    return pending.token;
  }
  return mint();
}

/**
 * Whether an answer RESOLVES the request (the token may be forgotten). Any
 * JSON answer from the server does -- success, or a refusal after which
 * nothing was changed. Only a transport failure or a non-JSON answer leaves
 * the outcome unknown, and then the next attempt must carry the same token.
 */
export function isResolved(status: number | null, gotJson: boolean) {
  return status !== null && gotJson;
}

/* ------------------------------------------------------------------ *
 * Wording
 * ------------------------------------------------------------------ */

export const QUEUE_REFRESH_FAILED_NOTICE =
  "Dispense updated. Queue refresh failed; the displayed result is authoritative.";

export const UNKNOWN_OUTCOME_NOTICE =
  "The pharmacy service did not answer. Retry to finish the same request safely; it will not be applied twice.";

/** Codes after which the pharmacist must reload before trying again. */
export function needsReload(code: string | null) {
  return (
    code === "pharmacy_dispense_revision_conflict" ||
    code === "pharmacy_dispense_state_conflict" ||
    code === "pharmacy_concurrent_conflict"
  );
}
