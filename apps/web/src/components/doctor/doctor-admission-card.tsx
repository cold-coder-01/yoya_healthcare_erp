"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import {
  ADMISSION_REASON_MAX,
  UNKNOWN_OUTCOME_NOTICE,
  admissionRequestBody,
  admissionRequestPath,
  cancelRequestBody,
  cancelRequestPath,
  cancelRequestSignature,
  cleanReason,
  dischargeRequestBody,
  dischargeRequestPath,
  dischargeRequestSignature,
  isResolved,
  requestSignature,
  tokenFor,
  type PendingAdmissionOperation,
} from "@/lib/admissions-desk-actions";
import { formatLengthOfStay } from "@/lib/admissions-desk-format";
import { buildDischargeReview } from "@/lib/doctor-discharge-review";
import DoctorDischargeReviewDialog from "./doctor-discharge-review-dialog";
import DoctorInpatientEstimate from "./doctor-inpatient-estimate";
import { messageFromPayload } from "@/lib/api-error";
import { formatHospitalDateTime } from "@/lib/clinical-format";
import type {
  AdmissionCancelRequestResponse,
  ApiEnvelope,
  DoctorAdmissionRequestResponse,
  DoctorDischargeRequestResponse,
  DoctorAdmissionSummary,
} from "@/types/admissions-desk";

type Notice = { tone: "red" | "amber" | "green"; text: string };

const STATUS_TONE: Record<DoctorAdmissionSummary["status"], string> = {
  none: "border-slate-200 bg-slate-50 text-slate-700",
  requested: "border-amber-300 bg-amber-50 text-amber-900",
  admitted: "border-sky-300 bg-sky-50 text-sky-900",
  transferred: "border-sky-300 bg-sky-50 text-sky-900",
  discharge_pending: "border-violet-300 bg-violet-50 text-violet-900",
  discharged: "border-slate-300 bg-slate-100 text-slate-700",
  cancelled: "border-slate-300 bg-slate-100 text-slate-600",
};

/**
 * ADMISSION on the Doctor Desk (Admissions Slices 2-4).
 *
 * The doctor REQUESTS an admission; the Admissions Desk chooses the bed and
 * admits. So this card offers "Request admission" -- never a ward, room or bed
 * -- and, while their own request is still a draft, "Cancel request" (Slice
 * 3), offered only on the server's `can_cancel_request`. A transferred patient
 * is shown at their CURRENT ward / room / bed.
 *
 * SLICE 4: "Request discharge…" declares the inpatient MEDICALLY ready, offered
 * only on the server's `can_request_discharge`. Write the summary, then
 * "Review discharge" opens a modal wizard (DoctorDischargeReviewDialog:
 * patient, admission, location, summary, warnings, revision) -- never an
 * inline card, which a long summary overflowed -- and confirm with a pointer
 * click. It frees no bed and shows no money: the Admissions Desk finalizes the
 * discharge. Transfer is never here.
 *
 * Whether the request is offered is the SERVER's `can_request`; when it is
 * not, the server's reason is shown instead. The card keeps the summary the
 * server last returned, so a successful request shows its status at once
 * without refetching the visit.
 *
 * Two steps: write the reason, then confirm. One operation token per intended
 * request; an unknown outcome keeps it, so a retry replays instead of creating
 * a second request.
 */
export default function DoctorAdmissionCard({
  appointmentId,
  summary: initial,
  compact = false,
  patientName = null,
  patientMrn = null,
  encounterName = null,
  physicianName = null,
}: {
  appointmentId: number;
  summary: DoctorAdmissionSummary | undefined;
  /** For the discharge review: the patient this visit is for. */
  patientName?: string | null;
  patientMrn?: string | null;
  encounterName?: string | null;
  /** The visit's doctor, shown as the responsible physician. */
  physicianName?: string | null;
  /** One-line strip for the consultation workspace. */
  compact?: boolean;
}) {
  /** The server's answer to OUR request, valid only while the visit summary it
   *  replaced is still the one passed in; a visit reload supersedes it. */
  const [returned, setReturned] = useState<{ basis: DoctorAdmissionSummary | undefined; value: DoctorAdmissionSummary } | null>(null);
  const summary = returned && returned.basis === initial ? returned.value : initial;
  const [step, setStep] = useState<
    "idle" | "write" | "confirm" | "cancel" | "discharge_write" | "discharge_confirm"
  >("idle");
  const [dischargeSummary, setDischargeSummary] = useState("");
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<Notice | null>(null);
  const pendingRef = useRef<PendingAdmissionOperation | null>(null);
  const backRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (step === "confirm" || step === "cancel") backRef.current?.focus();
  }, [step]);

  /** Back to the summary editor; the typed summary is kept. */
  const closeDischargeReview = useCallback(() => setStep("discharge_write"), []);

  if (!summary) return null;

  const cleaned = cleanReason(reason);
  const admission = summary.admission;

  async function submit() {
    if (busy || !cleaned) return;
    const signature = requestSignature(cleaned);
    const token = tokenFor(pendingRef.current, "request", appointmentId, signature, () => crypto.randomUUID());
    pendingRef.current = { kind: "request", targetId: appointmentId, signature, token };

    setBusy(true);
    let status: number | null = null;
    let payload: ApiEnvelope<DoctorAdmissionRequestResponse> | null = null;
    try {
      const response = await fetch(admissionRequestPath(appointmentId), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(admissionRequestBody(cleaned, token)),
        cache: "no-store",
      });
      status = response.status;
      payload = (await response.json()) as ApiEnvelope<DoctorAdmissionRequestResponse>;
    } catch {
      payload = null;
    } finally {
      setBusy(false);
    }

    if (!isResolved(status, payload !== null)) {
      setNotice({ tone: "amber", text: UNKNOWN_OUTCOME_NOTICE });
      return;
    }
    pendingRef.current = null;

    if (payload && payload.success) {
      setReturned({ basis: initial, value: payload.data.admission });
      setStep("idle");
      setReason("");
      setNotice({
        tone: "green",
        text: payload.data.operation.replayed
          ? "The admission request was already sent."
          : "Admission requested. The Admissions Desk will assign the bed.",
      });
      return;
    }
    setNotice({
      tone: "red",
      text: messageFromPayload(payload, "The admission request could not be sent. Nothing was changed."),
    });
    setStep("write");
  }

  const cleanedDischarge = cleanReason(dischargeSummary);

  async function requestDischarge() {
    if (busy || !admission || !cleanedDischarge || !summary?.can_request_discharge) return;
    const signature = dischargeRequestSignature(admission.workflow_revision, cleanedDischarge);
    const token = tokenFor(pendingRef.current, "medical_discharge", admission.id, signature, () => crypto.randomUUID());
    pendingRef.current = { kind: "medical_discharge", targetId: admission.id, signature, token };

    setBusy(true);
    let status: number | null = null;
    let payload: ApiEnvelope<DoctorDischargeRequestResponse> | null = null;
    try {
      const response = await fetch(dischargeRequestPath(appointmentId), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(dischargeRequestBody(admission.workflow_revision, cleanedDischarge, token)),
        cache: "no-store",
      });
      status = response.status;
      payload = (await response.json()) as ApiEnvelope<DoctorDischargeRequestResponse>;
    } catch {
      payload = null;
    } finally {
      setBusy(false);
    }

    if (!isResolved(status, payload !== null)) {
      setNotice({ tone: "amber", text: UNKNOWN_OUTCOME_NOTICE });
      return;
    }
    pendingRef.current = null;

    if (payload && payload.success) {
      setReturned({ basis: initial, value: payload.data.admission });
      setStep("idle");
      setDischargeSummary("");
      setNotice({
        tone: "green",
        text: payload.data.operation.replayed
          ? "The discharge request was already sent."
          : "Discharge requested. The Admissions Desk will finalize it.",
      });
      return;
    }
    setNotice({
      tone: "red",
      text: messageFromPayload(payload, "The discharge request could not be sent. Nothing was changed."),
    });
    setStep("discharge_write");
  }

  async function cancelRequest() {
    if (busy || !admission || !summary?.can_cancel_request) return;
    const signature = cancelRequestSignature(admission.workflow_revision);
    const token = tokenFor(pendingRef.current, "cancel_request", admission.id, signature, () => crypto.randomUUID());
    pendingRef.current = { kind: "cancel_request", targetId: admission.id, signature, token };

    setBusy(true);
    let status: number | null = null;
    let payload: ApiEnvelope<AdmissionCancelRequestResponse> | null = null;
    try {
      const response = await fetch(cancelRequestPath(admission.id), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(cancelRequestBody(admission.workflow_revision, token)),
        cache: "no-store",
      });
      status = response.status;
      payload = (await response.json()) as ApiEnvelope<AdmissionCancelRequestResponse>;
    } catch {
      payload = null;
    } finally {
      setBusy(false);
    }

    if (!isResolved(status, payload !== null)) {
      setNotice({ tone: "amber", text: UNKNOWN_OUTCOME_NOTICE });
      return;
    }
    pendingRef.current = null;

    if (payload && payload.success) {
      if (payload.data.doctor_admission) {
        setReturned({ basis: initial, value: payload.data.doctor_admission });
      }
      setStep("idle");
      setNotice({
        tone: "green",
        text: payload.data.operation.replayed
          ? "The admission request was already cancelled."
          : "Admission request cancelled.",
      });
      return;
    }
    setNotice({
      tone: "red",
      text: messageFromPayload(payload, "The request could not be cancelled. Nothing was changed."),
    });
    setStep("idle");
  }

  const statusLine = (
    <div className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-0.5">
      <span className="cl-micro font-bold uppercase tracking-[0.07em] text-slate-500">Admission</span>
      <span className={`rounded border px-1.5 py-0.5 cl-meta font-bold ${STATUS_TONE[summary.status]}`}>
        {summary.status_label}
      </span>
      {admission ? (
        <span className="min-w-0 truncate font-mono cl-meta text-slate-600">
          {admission.reference}
          {admission.location.bed
            ? ` · ${[admission.location.ward?.name, admission.location.room?.name, admission.location.bed.code ?? admission.location.bed.name]
                .filter(Boolean)
                .join(" / ")}`
            : ""}
          {admission.admitted_at
            ? ` · admitted ${formatHospitalDateTime(admission.admitted_at, "—")} · LOS ${formatLengthOfStay(admission.length_of_stay)}`
            : admission.requested_at
              ? ` · requested ${formatHospitalDateTime(admission.requested_at, "—")}`
              : ""}
        </span>
      ) : null}
    </div>
  );

  return (
    <section
      aria-label="Admission"
      className={compact ? "flex flex-col gap-1.5" : "flex flex-col gap-2 rounded-md border border-slate-200 bg-white px-3 py-2"}
    >
      <div className="flex items-center justify-between gap-2">
        {statusLine}
        {summary.can_request && step === "idle" ? (
          <button
            type="button"
            onClick={() => {
              setNotice(null);
              setStep("write");
            }}
            className="h-7 shrink-0 rounded-md border border-sky-700 bg-white px-2.5 cl-meta font-bold text-sky-800 hover:bg-sky-50"
          >
            Request admission…
          </button>
        ) : null}
        {summary.can_request_discharge && step === "idle" ? (
          <button
            type="button"
            onClick={() => {
              setNotice(null);
              setStep("discharge_write");
            }}
            className="h-7 shrink-0 rounded-md border border-violet-700 bg-white px-2.5 cl-meta font-bold text-violet-800 hover:bg-violet-50"
          >
            Request discharge…
          </button>
        ) : null}
        {summary.can_cancel_request && step === "idle" ? (
          <button
            type="button"
            onClick={() => {
              setNotice(null);
              setStep("cancel");
            }}
            className="h-7 shrink-0 rounded-md border border-red-300 bg-white px-2.5 cl-meta font-bold text-red-800 hover:bg-red-50"
          >
            Cancel request…
          </button>
        ) : null}
      </div>

      {step === "discharge_write" && admission ? (
        <div className="flex flex-col gap-1.5">
          <label htmlFor={`discharge-summary-${appointmentId}`} className="cl-meta font-bold text-slate-700">
            Discharge summary
          </label>
          <textarea
            id={`discharge-summary-${appointmentId}`}
            value={dischargeSummary}
            maxLength={ADMISSION_REASON_MAX}
            rows={3}
            onChange={(event) => setDischargeSummary(event.target.value)}
            className="w-full rounded-md border border-slate-300 px-2 py-1 cl-body text-slate-900"
          />
          <p className="cl-micro text-slate-500">
            Declares the patient medically ready. The Admissions Desk releases the bed.
          </p>
          <div className="flex justify-end gap-2">
            <button
              type="button"
              onClick={() => {
                setStep("idle");
                setNotice(null);
              }}
              className="h-7 rounded-md border border-slate-300 bg-white px-2.5 cl-meta font-bold text-slate-700 hover:bg-slate-50"
            >
              Cancel
            </button>
            <button
              type="button"
              disabled={!cleanedDischarge}
              onClick={() => {
                setNotice(null);
                setStep("discharge_confirm");
              }}
              className="h-7 rounded-md bg-violet-700 px-2.5 cl-meta font-bold text-white hover:bg-violet-800 disabled:opacity-40"
            >
              Review discharge
            </button>
          </div>
        </div>
      ) : null}

      {step === "discharge_confirm" && admission && cleanedDischarge ? (
        <DoctorDischargeReviewDialog
          review={buildDischargeReview(
            admission,
            summary.discharge_warnings,
            { patientName, patientMrn, encounterName, physicianName },
            cleanedDischarge,
          )}
          busy={busy}
          notice={notice}
          onConfirm={() => void requestDischarge()}
          onClose={closeDischargeReview}
        />
      ) : null}

      {step === "cancel" && admission ? (
        <div role="group" aria-label="Confirm cancelling the admission request" className="flex flex-col gap-1.5 rounded-md border border-red-200 bg-red-50/60 px-2 py-1.5">
          <p className="cl-body text-slate-800">
            Cancel this admission request? The patient has not been admitted and no bed is occupied.
          </p>
          <div className="flex justify-end gap-2">
            <button
              ref={backRef}
              type="button"
              disabled={busy}
              onClick={() => setStep("idle")}
              className="h-7 rounded-md border border-slate-300 bg-white px-2.5 cl-meta font-bold text-slate-700 hover:bg-slate-50 disabled:opacity-40"
            >
              Keep request
            </button>
            <button
              type="button"
              disabled={busy}
              onClick={(event) => {
                // Pointer only: a keyboard-activated click reports detail 0.
                if (event.detail === 0) return;
                void cancelRequest();
              }}
              className="h-7 rounded-md bg-red-700 px-2.5 cl-meta font-bold text-white hover:bg-red-800 disabled:opacity-40"
            >
              {busy ? "Cancelling…" : "Cancel request"}
            </button>
          </div>
        </div>
      ) : null}

      {!summary.can_request && summary.request_blocked_message && summary.status === "none" ? (
        <p className="cl-meta text-slate-500">{summary.request_blocked_message}</p>
      ) : null}

      {step === "write" ? (
        <div className="flex flex-col gap-1.5">
          <label htmlFor={`admission-reason-${appointmentId}`} className="cl-meta font-bold text-slate-700">
            Reason for admission
          </label>
          <textarea
            id={`admission-reason-${appointmentId}`}
            value={reason}
            maxLength={ADMISSION_REASON_MAX}
            rows={3}
            onChange={(event) => setReason(event.target.value)}
            className="w-full rounded-md border border-slate-300 px-2 py-1 cl-body text-slate-900"
          />
          <p className="cl-micro text-slate-500">The Admissions Desk chooses the ward and bed.</p>
          <div className="flex justify-end gap-2">
            <button
              type="button"
              onClick={() => {
                setStep("idle");
                setNotice(null);
              }}
              className="h-7 rounded-md border border-slate-300 bg-white px-2.5 cl-meta font-bold text-slate-700 hover:bg-slate-50"
            >
              Cancel
            </button>
            <button
              type="button"
              disabled={!cleaned}
              onClick={() => setStep("confirm")}
              className="h-7 rounded-md bg-sky-700 px-2.5 cl-meta font-bold text-white hover:bg-sky-800 disabled:opacity-40"
            >
              Review request
            </button>
          </div>
        </div>
      ) : null}

      {step === "confirm" && cleaned ? (
        <div role="group" aria-label="Confirm admission request" className="flex flex-col gap-1.5 rounded-md border border-sky-200 bg-sky-50/60 px-2 py-1.5">
          <p className="cl-body text-slate-800">Request inpatient admission for this patient?</p>
          <p className="whitespace-pre-wrap cl-secondary text-slate-700">{cleaned}</p>
          <div className="flex justify-end gap-2">
            <button
              ref={backRef}
              type="button"
              disabled={busy}
              onClick={() => setStep("write")}
              className="h-7 rounded-md border border-slate-300 bg-white px-2.5 cl-meta font-bold text-slate-700 hover:bg-slate-50 disabled:opacity-40"
            >
              Go back
            </button>
            <button
              type="button"
              disabled={busy}
              onClick={(event) => {
                // Pointer only: a keyboard-activated click reports detail 0.
                if (event.detail === 0) return;
                void submit();
              }}
              className="h-7 rounded-md bg-sky-700 px-2.5 cl-meta font-bold text-white hover:bg-sky-800 disabled:opacity-40"
            >
              {busy ? "Requesting…" : "Request admission"}
            </button>
          </div>
        </div>
      ) : null}

      {/* While the review is open its notice is shown inside the dialog. */}
      {notice && step !== "discharge_confirm" ? (
        <p
          role="status"
          className={`rounded border px-2 py-1 cl-meta ${
            notice.tone === "red"
              ? "border-red-300 bg-red-50 text-red-900"
              : notice.tone === "amber"
                ? "border-amber-300 bg-amber-50 text-amber-900"
                : "border-emerald-300 bg-emerald-50 text-emerald-900"
          }`}
        >
          {notice.text}
        </p>
      ) : null}

      {/* The inpatient estimate: its own fetch and route, so this card's visit
          payload stays amount-free. PRE-ADMISSION FIRST -- shown the moment a
          request exists (status requested), in the consultation strip as well
          as the patient panel, and kept visible through the stay. */}
      {(summary.status === "requested" ||
        summary.status === "admitted" ||
        summary.status === "transferred" ||
        summary.status === "discharge_pending") ? (
        <DoctorInpatientEstimate
          key={`${admission?.id ?? "none"}:${summary.status}`}
          appointmentId={appointmentId}
          compact={compact}
        />
      ) : null}
    </section>
  );
}
