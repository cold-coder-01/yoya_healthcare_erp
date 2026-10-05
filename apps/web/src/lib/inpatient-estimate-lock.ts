/**
 * The inpatient estimate LOCK and its advance flag, as the Doctor Desk words
 * them.
 *
 * The server decides (hospital.admission._estimate_lock()) and sends only a
 * reason key and a flag; this names them. Never a figure: the Cashier owns
 * estimate / received / remaining.
 */
import type { EstimateLockReason } from "@/types/inpatient-settlement";

export const ESTIMATE_LOCK_LABELS: Record<EstimateLockReason, string> = {
  medically_ready: "Medical discharge has begun. Final settlement now uses actual delivered care.",
};

export const ADDITIONAL_ADVANCE_NOTICE = "Additional advance required at the Cashier.";

export function estimateLockLabel(reason: EstimateLockReason | null | undefined): string | null {
  return reason ? ESTIMATE_LOCK_LABELS[reason] ?? null : null;
}
