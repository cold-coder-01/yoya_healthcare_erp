/**
 * Reading the stable API envelope on the client.
 *
 * Every BFF route returns { success: false, error: { code, message, ... } },
 * and normalizeError preserves any extra keys the Odoo layer attached -- which
 * is how a 409 keeps its clearance amounts.
 */
import type { ClearanceErrorDetails } from "@/types/reception";

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function errorOf(payload: unknown): Record<string, unknown> | null {
  if (!isRecord(payload) || !isRecord(payload.error)) {
    return null;
  }
  return payload.error;
}

export function messageFromPayload(payload: unknown, fallback: string): string {
  const error = errorOf(payload);
  if (error && typeof error.message === "string" && error.message.trim()) {
    return error.message;
  }
  return fallback;
}

export function codeFromPayload(payload: unknown): string | null {
  const error = errorOf(payload);
  return error && typeof error.code === "string" ? error.code : null;
}

/** Pulls the amounts off a 409 reception_clearance_required error. */
export function clearanceDetailsFromPayload(
  payload: unknown,
): ClearanceErrorDetails | null {
  const error = errorOf(payload);
  if (!error) {
    return null;
  }
  const num = (key: string) =>
    typeof error[key] === "number" ? (error[key] as number) : undefined;
  const str = (key: string) =>
    typeof error[key] === "string" ? (error[key] as string) : undefined;

  const details: ClearanceErrorDetails = {
    required_amount: num("required_amount"),
    received_amount: num("received_amount"),
    outstanding_amount: num("outstanding_amount"),
    clearance_state: str("clearance_state"),
    clearance_message: str("clearance_message"),
  };

  const hasAny = Object.values(details).some((value) => value !== undefined);
  return hasAny ? details : null;
}
