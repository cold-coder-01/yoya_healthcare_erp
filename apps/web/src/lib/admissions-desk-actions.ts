/**
 * Admission mutations (Admissions Slices 2-3): paths, bodies, retry tokens and
 * wording, for the Admissions Desk (admit, transfer, cancel request) and the
 * Doctor Desk (request, cancel own request).
 *
 * Pure and import-free, so node:test runs it directly.
 *
 * THE RETRY RULE, the Pharmacy Desk's exactly. One operation token per
 * intended act. If the outcome is UNKNOWN -- no status, or no JSON back -- the
 * token is kept, and a retry of the SAME request reuses it, so the server
 * replays the first result instead of acting twice. Any resolved answer
 * (success or a fixed refusal) clears it.
 */

/* ------------------------------------------------------------------ *
 * BFF paths -- the only addresses the browser uses
 * ------------------------------------------------------------------ */

export function admitPath(admissionId: number): string {
  return `/api/admissions/${admissionId}/admit`;
}

export function admissionRequestPath(appointmentId: number): string {
  return `/api/doctor/visits/${appointmentId}/admission-request`;
}

export function transferPath(admissionId: number): string {
  return `/api/admissions/${admissionId}/transfer`;
}

/** Shared by both desks: the doctor cancels through the same route. */
export function cancelRequestPath(admissionId: number): string {
  return `/api/admissions/${admissionId}/cancel-request`;
}

/** Slice 4: the Admissions Desk's administrative final discharge. */
export function finalizeDischargePath(admissionId: number): string {
  return `/api/admissions/${admissionId}/finalize-discharge`;
}

/** Slice 4: the Doctor Desk's medical discharge, addressed by the VISIT. */
export function dischargeRequestPath(appointmentId: number): string {
  return `/api/doctor/visits/${appointmentId}/discharge-request`;
}

/* ------------------------------------------------------------------ *
 * Bodies -- exactly the fields the server accepts
 * ------------------------------------------------------------------ */

export function admitBody(expectedRevision: number, bedId: number, token: string) {
  return { operation_token: token, expected_revision: expectedRevision, bed_id: bedId };
}

export function admissionRequestBody(reason: string, token: string) {
  return { operation_token: token, reason };
}

export function transferBody(expectedRevision: number, bedId: number, reason: string, token: string) {
  return { operation_token: token, expected_revision: expectedRevision, bed_id: bedId, reason };
}

export function cancelRequestBody(expectedRevision: number, token: string) {
  return { operation_token: token, expected_revision: expectedRevision };
}

export function finalizeDischargeBody(expectedRevision: number, token: string) {
  return { operation_token: token, expected_revision: expectedRevision };
}

export function dischargeRequestBody(expectedRevision: number, summary: string, token: string) {
  return { operation_token: token, expected_revision: expectedRevision, summary };
}

/** The admission reason the server will accept: trimmed, non-empty, bounded. */
export const ADMISSION_REASON_MAX = 2000;

export function cleanReason(raw: string): string | null {
  const reason = raw.trim();
  if (!reason || reason.length > ADMISSION_REASON_MAX) return null;
  return reason;
}

/* ------------------------------------------------------------------ *
 * Idempotent retry
 * ------------------------------------------------------------------ */

export type PendingAdmissionOperation = {
  kind: "admit" | "request" | "transfer" | "cancel_request" | "medical_discharge" | "final_discharge";
  targetId: number;
  signature: string;
  token: string;
};

/** Everything that makes two requests "the same request", except the token. */
export function admitSignature(expectedRevision: number, bedId: number): string {
  return JSON.stringify({ revision: expectedRevision, bed: bedId });
}

export function requestSignature(reason: string): string {
  return JSON.stringify({ reason: reason.trim() });
}

export function transferSignature(expectedRevision: number, bedId: number, reason: string): string {
  return JSON.stringify({ revision: expectedRevision, bed: bedId, reason: reason.trim() });
}

export function cancelRequestSignature(expectedRevision: number): string {
  return JSON.stringify({ revision: expectedRevision });
}

export function finalizeDischargeSignature(expectedRevision: number): string {
  return JSON.stringify({ revision: expectedRevision });
}

export function dischargeRequestSignature(expectedRevision: number, summary: string): string {
  return JSON.stringify({ revision: expectedRevision, summary: summary.trim() });
}

/**
 * The token for this request: the pending one when this is a retry of the SAME
 * unresolved request, otherwise a fresh UUID.
 */
export function tokenFor(
  pending: PendingAdmissionOperation | null,
  kind: PendingAdmissionOperation["kind"],
  targetId: number,
  signature: string,
  mint: () => string,
): string {
  if (
    pending &&
    pending.kind === kind &&
    pending.targetId === targetId &&
    pending.signature === signature
  ) {
    return pending.token;
  }
  return mint();
}

/** A response was received AND parsed. Otherwise the outcome is unknown. */
export function isResolved(status: number | null, gotJson: boolean): boolean {
  return status !== null && gotJson;
}

/* ------------------------------------------------------------------ *
 * Wording
 * ------------------------------------------------------------------ */

export const UNKNOWN_OUTCOME_NOTICE =
  "The admissions service did not answer. Retry to finish the same request safely; it will not be applied twice.";

export const QUEUE_REFRESH_FAILED_NOTICE =
  "Admission updated. The census refresh failed; the displayed result is authoritative.";

/** Codes after which the operator must reload before trying again. */
export function needsReload(code: string | null): boolean {
  return (
    code === "admission_revision_conflict" ||
    code === "admission_invalid_state" ||
    code === "admission_not_found" ||
    // Slice 4: the discharge gates -- the financial / medical state shown is stale.
    code === "admission_not_medically_ready" ||
    code === "admission_settlement_required" ||
    code === "admission_financial_review_required"
  );
}

/** Codes after which the BED BOARD must be refreshed: the chosen bed is gone. */
export function needsBedRefresh(code: string | null): boolean {
  return code === "admission_bed_conflict" || code === "admission_bed_unavailable";
}
