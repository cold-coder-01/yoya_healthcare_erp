"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import ErrorBanner from "@/components/clinical/error-banner";
import ChargeLinesTable from "@/components/reception/charge-lines-table";
import ConsultationClearanceCard from "@/components/reception/consultation-clearance-card";
import EmergencyBypassForm from "@/components/reception/emergency-bypass-form";
import ReceptionClearanceCard from "@/components/reception/reception-clearance-card";
import {
  ClearanceBadge,
  EmergencyTag,
  QueueStageBadge,
} from "@/components/reception/reception-badges";
import {
  clearanceDetailsFromPayload,
  messageFromPayload,
} from "@/lib/api-error";
import { formatHospitalDateTime } from "@/lib/clinical-format";
import {
  formatCardStatus,
  formatEncounterState,
  formatEtb,
  formatGender,
  formatSeverity,
  formatVisitType,
} from "@/lib/reception-format";
import type {
  ApiEnvelope,
  ClearanceErrorDetails,
  ReceptionSession,
  ReceptionVisitDetail,
} from "@/types/reception";

function Field({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div>
      <dt className="text-xs font-semibold uppercase tracking-wide text-slate-500">
        {label}
      </dt>
      <dd className="mt-1 text-sm text-slate-900">{children}</dd>
    </div>
  );
}

export default function VisitDetailClient({
  appointmentId,
}: {
  appointmentId: string;
}) {
  const [detail, setDetail] = useState<ReceptionVisitDetail | null>(null);
  const [session, setSession] = useState<ReceptionSession | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [refreshToken, setRefreshToken] = useState(0);

  const [actionError, setActionError] = useState<string | null>(null);
  const [clearanceBlock, setClearanceBlock] =
    useState<ClearanceErrorDetails | null>(null);
  const [sendingToTriage, setSendingToTriage] = useState(false);
  const [bypassOpen, setBypassOpen] = useState(false);
  const [bypassing, setBypassing] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);

  const numericId = Number(appointmentId);
  const validId = Number.isInteger(numericId) && numericId > 0;

  // An invalid ID is handled entirely in the render path below, so the effect
  // simply does nothing. Setting error/loading from the effect body instead
  // would trigger a cascading render.
  useEffect(() => {
    if (!validId) {
      return;
    }

    const controller = new AbortController();

    async function run() {
      try {
        const [detailResponse, sessionResponse] = await Promise.all([
          fetch(`/api/reception/visits/${numericId}`, {
            cache: "no-store",
            signal: controller.signal,
          }),
          fetch("/api/reception/session", {
            cache: "no-store",
            signal: controller.signal,
          }),
        ]);

        const detailPayload =
          (await detailResponse.json()) as ApiEnvelope<ReceptionVisitDetail>;
        if (controller.signal.aborted) return;

        if (!detailResponse.ok || !detailPayload.success) {
          setError(messageFromPayload(detailPayload, "Unable to load the visit."));
          setDetail(null);
          return;
        }
        setError(null);
        setDetail(detailPayload.data);

        const sessionPayload =
          (await sessionResponse.json()) as ApiEnvelope<ReceptionSession>;
        if (sessionResponse.ok && sessionPayload.success) {
          setSession(sessionPayload.data);
        }
      } catch {
        if (controller.signal.aborted) return;
        setError("Unable to reach the reception service.");
        setDetail(null);
      } finally {
        if (!controller.signal.aborted) setLoading(false);
      }
    }

    void run();
    return () => controller.abort();
  }, [numericId, validId, refreshToken]);

  const refresh = useCallback(() => {
    setLoading(true);
    setRefreshToken((token) => token + 1);
  }, []);

  async function handleSendToTriage() {
    if (sendingToTriage) return;
    setSendingToTriage(true);
    setActionError(null);
    setClearanceBlock(null);
    setNotice(null);

    try {
      const response = await fetch(
        `/api/reception/visits/${numericId}/send-to-triage`,
        { method: "POST" },
      );
      const payload =
        (await response.json()) as ApiEnvelope<ReceptionVisitDetail>;

      if (!response.ok || !payload.success) {
        // 409 carries the clearance amounts; show them rather than a bare string.
        if (response.status === 409) {
          setClearanceBlock(clearanceDetailsFromPayload(payload));
        }
        setActionError(
          messageFromPayload(payload, "Unable to send the visit to triage."),
        );
        return;
      }
      setDetail(payload.data);
      setNotice("Patient sent to nursing triage.");
    } catch {
      setActionError("Unable to reach the reception service.");
    } finally {
      setSendingToTriage(false);
    }
  }

  async function handleBypass(reason: string, dangerSignIds: number[]) {
    if (bypassing) return;
    setBypassing(true);
    setActionError(null);
    setClearanceBlock(null);
    setNotice(null);

    try {
      const response = await fetch(
        `/api/reception/visits/${numericId}/emergency-bypass`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            reason,
            ...(dangerSignIds.length
              ? { danger_sign_ids: dangerSignIds }
              : {}),
          }),
        },
      );
      const payload =
        (await response.json()) as ApiEnvelope<ReceptionVisitDetail>;

      if (!response.ok || !payload.success) {
        setActionError(
          messageFromPayload(payload, "Unable to authorize the bypass."),
        );
        return;
      }
      setDetail(payload.data);
      setBypassOpen(false);
      setNotice(
        "Emergency bypass authorized. Outstanding charges remain owed and are still listed below.",
      );
    } catch {
      setActionError("Unable to reach the reception service.");
    } finally {
      setBypassing(false);
    }
  }

  if (!validId) {
    return (
      <div className="rounded-xl border border-slate-200 bg-white p-6">
        <ErrorBanner message="Visit ID is invalid." title="Unable to load visit" />
        <Link
          href="/reception"
          className="mt-4 inline-block text-sm font-semibold text-emerald-700"
        >
          Back to reception queue
        </Link>
      </div>
    );
  }

  if (loading) {
    return (
      <div className="rounded-xl border border-slate-200 bg-white p-6 text-sm text-slate-600">
        Loading visit…
      </div>
    );
  }

  if (!detail) {
    return (
      <div className="rounded-xl border border-slate-200 bg-white p-6">
        <ErrorBanner message={error} title="Unable to load visit" />
        <Link
          href="/reception"
          className="mt-4 inline-block text-sm font-semibold text-emerald-700"
        >
          Back to reception queue
        </Link>
      </div>
    );
  }

  const { appointment, patient, encounter, card_issue, emergency } = detail;
  const actions = detail.permitted_actions;
  const paymentApiEnabled =
    session?.capabilities.record_payment_api_enabled ?? false;

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <Link
          href="/reception"
          className="text-sm font-semibold text-emerald-700 hover:text-emerald-900"
        >
          ← Back to reception queue
        </Link>
        <div className="flex flex-wrap items-center gap-2">
          <QueueStageBadge stage={appointment.clinical_queue_stage} />
          {emergency?.emergency_bypass ? <EmergencyTag /> : null}
        </div>
      </div>

      {notice ? (
        <p
          role="status"
          className="rounded-md border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm font-medium text-emerald-900"
        >
          {notice}
        </p>
      ) : null}

      <ErrorBanner message={actionError} title="Action could not be completed" />

      {clearanceBlock ? (
        <div className="rounded-md border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-900">
          <p className="font-semibold">Reception clearance outstanding</p>
          <dl className="mt-2 grid grid-cols-1 gap-1 sm:grid-cols-3">
            <div>
              <dt className="inline text-amber-800">Required: </dt>
              <dd className="inline font-semibold tabular-nums">
                {formatEtb(clearanceBlock.required_amount ?? null)}
              </dd>
            </div>
            <div>
              <dt className="inline text-amber-800">Received: </dt>
              <dd className="inline font-semibold tabular-nums">
                {formatEtb(clearanceBlock.received_amount ?? null)}
              </dd>
            </div>
            <div>
              <dt className="inline text-amber-800">Outstanding: </dt>
              <dd className="inline font-semibold tabular-nums">
                {formatEtb(clearanceBlock.outstanding_amount ?? null)}
              </dd>
            </div>
          </dl>
          {clearanceBlock.clearance_message ? (
            <p className="mt-2">{clearanceBlock.clearance_message}</p>
          ) : null}
        </div>
      ) : null}

      {/* Patient + visit identity */}
      <section className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div>
            <h2 className="text-2xl font-semibold text-slate-950">
              {patient.name}
            </h2>
            <p className="mt-1 text-sm text-slate-600">
              {patient.identification_code ?? "No MRN"} · {patient.age ?? "—"}{" "}
              yrs · {formatGender(patient.gender)}
              {patient.mobile || patient.phone
                ? ` · ${patient.mobile ?? patient.phone}`
                : ""}
            </p>
          </div>
          <ClearanceBadge
            state={detail.reception_clearance.state}
            ok={detail.reception_clearance.ok}
          />
        </div>

        <dl className="mt-5 grid grid-cols-2 gap-4 border-t border-slate-100 pt-4 md:grid-cols-4">
          <Field label="Appointment">
            {appointment.appointment_code ?? `#${appointment.id}`}
          </Field>
          <Field label="Encounter">
            {encounter ? (
              <>
                {encounter.name}
                <span className="ml-1 text-xs text-slate-500">
                  ({formatEncounterState(encounter.state)})
                </span>
              </>
            ) : (
              "Not opened"
            )}
          </Field>
          <Field label="Visit type">
            {formatVisitType(appointment.visit_type)}
          </Field>
          <Field label="Scheduled">
            {formatHospitalDateTime(appointment.appointment_date)}
          </Field>
          <Field label="Department">
            {appointment.department?.name ?? "—"}
          </Field>
          <Field label="Doctor">{appointment.doctor?.name ?? "Unassigned"}</Field>
          <Field label="Triage destination">
            {appointment.triage_destination?.name ?? "—"}
          </Field>
          <Field label="Patient card">
            {card_issue ? (
              <>
                {formatCardStatus(card_issue.state)}
                <span className="ml-1 text-xs text-slate-500">
                  ({card_issue.name})
                </span>
              </>
            ) : (
              "No card issuance"
            )}
          </Field>
        </dl>

        {appointment.reason ? (
          <p className="mt-4 border-t border-slate-100 pt-4 text-sm text-slate-700">
            <span className="font-semibold">Reason: </span>
            {appointment.reason}
          </p>
        ) : null}
      </section>

      <ReceptionClearanceCard clearance={detail.reception_clearance} />

      {/* Payment: no form, because the API is not enabled. */}
      <section className="rounded-lg border border-slate-200 bg-white p-4">
        <h2 className="text-sm font-semibold text-slate-900">Payment</h2>
        <p className="mt-2 text-sm text-slate-700">
          {paymentApiEnabled
            ? "Payment can be recorded from this screen."
            : "Payment must be recorded in Odoo by an authorized user."}
        </p>
        <button
          type="button"
          onClick={refresh}
          className="mt-3 h-10 rounded-md border border-slate-300 bg-white px-4 text-sm font-semibold text-slate-700 transition hover:bg-slate-50"
        >
          Refresh Payment Status
        </button>
      </section>

      <ChargeLinesTable lines={detail.charge_lines} />

      <ConsultationClearanceCard clearance={detail.consultation_clearance} />

      {/* Emergency */}
      <section className="rounded-lg border border-slate-200 bg-white p-4">
        <h2 className="text-sm font-semibold text-slate-900">Emergency</h2>
        {emergency?.emergency_bypass ? (
          <dl className="mt-3 grid grid-cols-1 gap-3 sm:grid-cols-2">
            <Field label="Bypass reason">{emergency.reason ?? "—"}</Field>
            <Field label="Authorized by">
              {emergency.authorized_by?.name ?? "—"}
              {emergency.authorized_at
                ? ` · ${formatHospitalDateTime(emergency.authorized_at)}`
                : ""}
            </Field>
            <Field label="Screened by">
              {emergency.screened_by?.name ?? "—"}
            </Field>
            <Field label="Highest danger severity">
              {formatSeverity(emergency.highest_danger_severity)}
            </Field>
          </dl>
        ) : (
          <p className="mt-2 text-sm text-slate-600">
            No emergency bypass is active for this visit.
          </p>
        )}

        {detail.danger_signs.length > 0 ? (
          <ul className="mt-3 flex flex-wrap gap-2">
            {detail.danger_signs.map((sign) => (
              <li
                key={sign.id}
                className="rounded-full bg-red-50 px-2.5 py-1 text-xs font-semibold text-red-800 ring-1 ring-red-100"
              >
                {sign.name} · {formatSeverity(sign.severity)}
              </li>
            ))}
          </ul>
        ) : null}
      </section>

      {/* Actions */}
      <section className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
        <h2 className="text-sm font-semibold text-slate-900">Actions</h2>

        {!actions.send_to_triage && actions.blocked_reason ? (
          <p className="mt-2 text-sm text-slate-600">{actions.blocked_reason}</p>
        ) : null}

        <div className="mt-3 flex flex-wrap gap-3">
          {actions.send_to_triage ? (
            <button
              type="button"
              onClick={handleSendToTriage}
              disabled={sendingToTriage}
              className="h-11 rounded-md bg-emerald-700 px-5 text-sm font-semibold text-white transition hover:bg-emerald-800 disabled:cursor-not-allowed disabled:bg-slate-400"
            >
              {sendingToTriage ? "Sending…" : "Send to Triage"}
            </button>
          ) : null}

          {actions.emergency_bypass && !bypassOpen ? (
            <button
              type="button"
              onClick={() => setBypassOpen(true)}
              className="h-11 rounded-md border border-red-300 bg-white px-5 text-sm font-semibold text-red-800 transition hover:bg-red-50"
            >
              Emergency Bypass
            </button>
          ) : null}
        </div>

        {actions.emergency_bypass && bypassOpen ? (
          <div className="mt-4">
            <EmergencyBypassForm
              availableSigns={detail.danger_signs}
              submitting={bypassing}
              onSubmit={handleBypass}
              onCancel={() => setBypassOpen(false)}
            />
          </div>
        ) : null}
      </section>
    </div>
  );
}
