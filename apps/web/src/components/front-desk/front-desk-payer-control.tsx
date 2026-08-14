"use client";

/**
 * Front Desk B2B -- who is responsible for this visit.
 *
 * IDENTITY ONLY. There is no amount on this control and none in its payload:
 * the type it renders (FrontDeskEligibility) has no monetary field, and the
 * server's serializer allowlist would not send one if it did. Money stays in
 * FinancialSummary, which reads the encounter's clearance figures and is
 * untouched by this phase.
 *
 * The control owns NO rule. Whether the payer may still be changed is
 * visit.payer_change, computed by the model
 * (hospital.encounter._payer_identity_freeze_reason) and re-enforced by
 * hospital.reception.workflow.set_visit_payer on every call -- so a client that
 * ignored the flag and posted anyway would simply be refused, with the same
 * message shown here.
 *
 * Eligibilities are fetched ON DEMAND, when the nurse opens the editor, and only
 * for this visit's patient. The panel therefore costs nothing extra to render
 * for the overwhelmingly common case where nobody touches the payer.
 */
import { useCallback, useEffect, useState } from "react";

import { messageFromPayload } from "@/lib/api-error";
import { eligibilityLabel } from "@/lib/front-desk-format";
import type {
  FrontDeskEligibility,
  FrontDeskEligibilityList,
  FrontDeskVisit,
} from "@/types/front-desk";
import type { ApiEnvelope } from "@/types/reception";

const SELECT =
  "h-7 w-full min-w-0 rounded border border-slate-300 bg-white px-1.5 text-[11px] outline-none focus:border-emerald-600 focus:ring-2 focus:ring-emerald-100";
const BUTTON =
  "h-7 shrink-0 rounded px-2 text-[11px] font-semibold outline-none transition focus-visible:ring-2 focus-visible:ring-emerald-600 disabled:cursor-not-allowed";

export default function FrontDeskPayerControl({
  visit,
  onMutated,
}: {
  visit: FrontDeskVisit;
  onMutated: () => void;
}) {
  const current = visit.encounter?.patient_payer ?? null;
  const change = visit.payer_change;
  const patientId = visit.patient.id;
  const appointmentId = visit.visit.appointment_id;

  /**
   * Every piece of editor state is STAMPED WITH THE VISIT IT BELONGS TO, and
   * what is rendered is derived from that stamp. Switching patients therefore
   * closes the editor, drops a half-made choice and clears a stale error in the
   * SAME render that shows the new patient -- with no effect resetting anything
   * afterwards. An effect doing that work would leave one frame in which the
   * previous visit's pending payer choice sits under the new patient's name,
   * which at a shared front desk is the one mistake that must not be possible.
   */
  const [editingFor, setEditingFor] = useState<number | null>(null);
  const [choice, setChoice] = useState<{
    appointmentId: number;
    value: string;
  } | null>(null);
  const [loaded, setLoaded] = useState<{
    patientId: number;
    items: FrontDeskEligibility[];
    error: string | null;
  } | null>(null);
  const [saving, setSaving] = useState(false);
  const [saveFailure, setSaveFailure] = useState<{
    appointmentId: number;
    message: string;
  } | null>(null);

  const editing = editingFor === appointmentId;
  const ready = loaded?.patientId === patientId;
  const eligibilities = ready ? loaded.items : [];
  const loading = editing && !ready;
  const selected =
    choice?.appointmentId === appointmentId
      ? choice.value
      : current
        ? String(current.id)
        : "";
  const failure =
    (saveFailure?.appointmentId === appointmentId
      ? saveFailure.message
      : null) ?? (editing && ready ? loaded.error : null);

  useEffect(() => {
    if (!editing) return;

    const controller = new AbortController();

    // The id is a parameter rather than a capture, so a slow response can only
    // ever be stamped with the patient it was actually fetched for.
    async function load(id: number) {
      try {
        const response = await fetch(
          `/api/front-desk/patients/${id}/eligibilities`,
          { cache: "no-store", signal: controller.signal },
        );
        const payload =
          (await response.json()) as ApiEnvelope<FrontDeskEligibilityList>;
        if (controller.signal.aborted) return;

        setLoaded({
          patientId: id,
          items:
            response.ok && payload.success
              ? (payload.data.eligibilities ?? [])
              : [],
          error:
            response.ok && payload.success
              ? null
              : messageFromPayload(payload, "Unable to load payer identities."),
        });
      } catch {
        if (!controller.signal.aborted) {
          setLoaded({
            patientId: id,
            items: [],
            error: "Unable to reach the payer service.",
          });
        }
      }
    }

    void load(patientId);
    return () => controller.abort();
  }, [editing, patientId]);

  const save = useCallback(async () => {
    if (saving) return;
    setSaving(true);
    setSaveFailure(null);
    try {
      const response = await fetch(
        `/api/front-desk/visits/${appointmentId}/payer`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            patient_payer_id: selected ? Number(selected) : null,
          }),
        },
      );
      const payload = (await response.json()) as ApiEnvelope<FrontDeskVisit>;

      if (!response.ok || !payload.success) {
        // Odoo's message is the specific one ("payment RCP0042 has already been
        // received..."), so it is shown verbatim rather than flattened.
        setSaveFailure({
          appointmentId,
          message: messageFromPayload(payload, "Unable to update the payer."),
        });
        return;
      }
      setEditingFor(null);
      onMutated();
    } catch {
      setSaveFailure({
        appointmentId,
        message: "Unable to reach the payer service.",
      });
    } finally {
      setSaving(false);
    }
  }, [appointmentId, onMutated, saving, selected]);

  return (
    <section className="border-b border-slate-200 px-3 py-1.5">
      <div className="flex items-baseline justify-between gap-2">
        <h3 className="text-[10px] font-bold uppercase tracking-wide text-slate-600">
          Payer
        </h3>
        {!editing && change.allowed ? (
          <button
            type="button"
            onClick={() => setEditingFor(appointmentId)}
            className={`${BUTTON} border border-slate-300 bg-white text-slate-700 hover:bg-slate-50`}
          >
            Change
          </button>
        ) : null}
      </div>

      {editing ? (
        <div className="mt-1 flex items-center gap-1.5">
          <select
            value={selected}
            onChange={(event) =>
              setChoice({ appointmentId, value: event.target.value })
            }
            disabled={loading || saving}
            aria-label="Payer identity"
            className={SELECT}
          >
            <option value="">Self Pay / No sponsor</option>
            {eligibilities.map((eligibility) => (
              <option key={eligibility.id} value={eligibility.id}>
                {eligibilityLabel(eligibility)}
              </option>
            ))}
          </select>
          <button
            type="button"
            onClick={save}
            disabled={loading || saving}
            className={`${BUTTON} bg-emerald-700 text-white hover:bg-emerald-800 disabled:bg-slate-400`}
          >
            {saving ? "Saving…" : "Save"}
          </button>
          <button
            type="button"
            onClick={() => {
              setEditingFor(null);
              setChoice(null);
              setSaveFailure(null);
            }}
            disabled={saving}
            className={`${BUTTON} border border-slate-300 bg-white text-slate-700 hover:bg-slate-50`}
          >
            Cancel
          </button>
        </div>
      ) : (
        <p className="mt-0.5 truncate text-[12px] font-semibold text-slate-900">
          {eligibilityLabel(current)}
        </p>
      )}

      {!editing && current ? (
        <p className="mt-0.5 truncate text-[10px] text-slate-500">
          {current.agreement_number ?? current.agreement_name ?? "Agreement"}
          {current.relationship_to_principal
            ? ` · ${current.relationship_to_principal}`
            : ""}
        </p>
      ) : null}

      {!editing && change.frozen && change.reason ? (
        <p className="mt-0.5 text-[10px] text-slate-500">
          Locked: {change.reason}
        </p>
      ) : null}

      {failure ? (
        <p role="alert" className="mt-1 text-[11px] text-red-700">
          {failure}
        </p>
      ) : null}

      {editing && loading ? (
        <p className="mt-1 text-[10px] text-slate-500">
          Loading registered payer identities…
        </p>
      ) : null}
    </section>
  );
}
