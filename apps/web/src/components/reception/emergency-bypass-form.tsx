"use client";

/**
 * Emergency bypass authorization.
 *
 * Rendered only when permitted_actions.emergency_bypass is true, which the API
 * computes as (emergency_authorizer OR manager OR admin) AND an encounter
 * exists AND no bypass is already active. A plain receptionist never sees it.
 * Hiding is a UX courtesy; Odoo enforces the role on write regardless.
 *
 * DANGER SIGNS: there is no danger-sign reference/catalogue endpoint yet. The
 * visit-detail payload returns only signs ALREADY attached to the encounter,
 * which is empty before a bypass. Selection is therefore offered only when the
 * encounter already carries signs; otherwise the reason field carries the
 * clinical justification. See the report for the follow-up needed.
 */
import { useState } from "react";

import { formatSeverity } from "@/lib/reception-format";
import type { ReceptionDangerSign } from "@/types/reception";

export default function EmergencyBypassForm({
  availableSigns,
  submitting,
  onSubmit,
  onCancel,
}: {
  availableSigns: ReceptionDangerSign[];
  submitting: boolean;
  onSubmit: (reason: string, dangerSignIds: number[]) => void;
  onCancel: () => void;
}) {
  const [reason, setReason] = useState("");
  const [selected, setSelected] = useState<number[]>([]);
  const [confirmed, setConfirmed] = useState(false);

  const reasonValid = reason.trim().length > 0;
  const canSubmit = reasonValid && confirmed && !submitting;

  function toggleSign(id: number) {
    setSelected((current) =>
      current.includes(id)
        ? current.filter((value) => value !== id)
        : [...current, id],
    );
  }

  return (
    <form
      onSubmit={(event) => {
        event.preventDefault();
        if (!canSubmit) return;
        onSubmit(reason.trim(), selected);
      }}
      className="space-y-4 rounded-lg border border-red-200 bg-red-50 p-4"
    >
      <div>
        <h3 className="text-sm font-semibold text-red-900">
          Authorize emergency bypass
        </h3>
        <p className="mt-1 text-sm text-red-800">
          This releases the patient to triage before payment. Outstanding
          charges are <strong>not</strong> cancelled — they remain owed and
          visible on this visit.
        </p>
      </div>

      <label className="block text-sm font-medium text-red-900">
        Clinical justification <span className="text-red-700">*</span>
        <textarea
          value={reason}
          onChange={(event) => setReason(event.target.value)}
          rows={3}
          required
          className="mt-2 w-full rounded-md border border-red-300 bg-white p-3 text-sm text-slate-900"
          placeholder="Why care must proceed before payment"
        />
      </label>

      {availableSigns.length > 0 ? (
        <fieldset>
          <legend className="text-sm font-medium text-red-900">
            Danger signs
          </legend>
          <div className="mt-2 space-y-1.5">
            {availableSigns.map((sign) => (
              <label
                key={sign.id}
                className="flex items-center gap-2 text-sm text-red-900"
              >
                <input
                  type="checkbox"
                  checked={selected.includes(sign.id)}
                  onChange={() => toggleSign(sign.id)}
                  className="h-4 w-4 rounded border-red-300"
                />
                <span>
                  {sign.name}{" "}
                  <span className="text-xs text-red-700">
                    ({formatSeverity(sign.severity)})
                  </span>
                </span>
              </label>
            ))}
          </div>
        </fieldset>
      ) : (
        <p className="rounded-md border border-red-200 bg-white px-3 py-2 text-xs text-slate-600">
          No danger-sign catalogue is available to this screen yet. Record the
          presenting danger signs in the justification above.
        </p>
      )}

      <label className="flex items-start gap-2 text-sm text-red-900">
        <input
          type="checkbox"
          checked={confirmed}
          onChange={(event) => setConfirmed(event.target.checked)}
          className="mt-0.5 h-4 w-4 rounded border-red-300"
        />
        <span>
          I confirm I am authorized to waive the payment gate for this patient,
          and that this action is recorded against my name.
        </span>
      </label>

      <div className="flex flex-wrap gap-3">
        <button
          type="submit"
          disabled={!canSubmit}
          className="h-11 rounded-md bg-red-700 px-5 text-sm font-semibold text-white transition hover:bg-red-800 disabled:cursor-not-allowed disabled:bg-slate-400"
        >
          {submitting ? "Authorizing…" : "Authorize bypass & send to triage"}
        </button>
        <button
          type="button"
          onClick={onCancel}
          disabled={submitting}
          className="h-11 rounded-md border border-slate-300 bg-white px-5 text-sm font-semibold text-slate-700 transition hover:bg-slate-50 disabled:cursor-not-allowed"
        >
          Cancel
        </button>
      </div>
    </form>
  );
}
