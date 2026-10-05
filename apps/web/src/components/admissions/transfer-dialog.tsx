"use client";

import { useEffect, useMemo, useRef, useState } from "react";

import { ADMISSION_REASON_MAX, cleanReason } from "@/lib/admissions-desk-actions";
import { groupBeds } from "@/lib/admissions-desk-format";
import type { AdmissionDetail, BedBoardRow, NamedRef } from "@/types/admissions-desk";

type ActionMessage = { tone: "red" | "amber" | "green"; text: string };

function place(ref: NamedRef | null | undefined) {
  return ref ? (ref.code ?? ref.name ?? "—") : "—";
}

/**
 * TRANSFER PATIENT (Admissions Slice 3): choose the destination bed and write
 * the reason, then review and confirm. One atomic server act.
 *
 * TWO DELIBERATE STEPS, the Admit dialog's rule exactly. Choosing changes
 * nothing; only "Transfer patient" on the review step sends the request, and
 * it is a pointer click: a keyboard-activated click (detail === 0) is ignored.
 * Focus starts on "Go back".
 *
 * The bed list is the server's bed board filtered to AVAILABLE beds, minus
 * the patient's own bed; the browser decides nothing about availability. If
 * the bed is taken between choosing and confirming, the server refuses with
 * admission_bed_conflict and the workstation reloads the list. The review
 * shows places only -- never a rate.
 */
export default function TransferDialog({
  detail,
  beds,
  bedsLoading,
  bedsError,
  busy,
  message,
  onConfirm,
  onClose,
}: {
  detail: AdmissionDetail;
  beds: BedBoardRow[];
  bedsLoading: boolean;
  bedsError: string | null;
  busy: boolean;
  message: ActionMessage | null;
  onConfirm: (bedId: number, reason: string) => void;
  onClose: () => void;
}) {
  const currentBedId = detail.location.bed?.id ?? null;
  const available = useMemo(
    () =>
      beds.filter(
        (bed) => bed.active && bed.state === "available" && !bed.needs_review && bed.id !== currentBedId,
      ),
    [beds, currentBedId],
  );
  const [chosenId, setChosenId] = useState<number | null>(null);
  const [reason, setReason] = useState("");
  const [step, setStep] = useState<"choose" | "confirm">("choose");
  const backRef = useRef<HTMLButtonElement>(null);

  const selected = available.find((bed) => bed.id === chosenId) ?? null;
  const cleaned = cleanReason(reason);
  // A chosen bed that disappeared from the list sends the operator back.
  const effectiveStep = step === "confirm" && (!selected || !cleaned) ? "choose" : step;

  useEffect(() => {
    if (effectiveStep === "confirm") backRef.current?.focus();
  }, [effectiveStep]);

  const grouped = useMemo(() => groupBeds(available), [available]);

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby="transfer-dialog-title"
      className="fixed inset-0 z-40 flex items-center justify-center bg-slate-950/40 p-4"
    >
      <div className="flex max-h-[85vh] w-full max-w-[640px] flex-col overflow-hidden rounded-lg border border-slate-300 bg-white shadow-xl">
        <header className="shrink-0 border-b border-slate-200 px-4 py-3">
          <h2 id="transfer-dialog-title" className="cl-head font-bold text-slate-950">
            Transfer patient
          </h2>
          <p className="font-mono cl-meta text-slate-600">
            {detail.patient?.name ?? "—"} · {detail.patient?.mrn ?? "No chart no."} · {detail.reference} · rev{" "}
            {detail.workflow_revision}
          </p>
        </header>

        {effectiveStep === "choose" ? (
          <div className="min-h-0 flex-1 overflow-y-auto px-4 py-3">
            <p className="mb-2 cl-meta text-slate-600">
              Now in {place(detail.location.ward)} / {place(detail.location.room)} / {place(detail.location.bed)}
            </p>
            {bedsError ? (
              <p className="cl-body text-red-700">{bedsError}</p>
            ) : bedsLoading && available.length === 0 ? (
              <p className="cl-body text-slate-500">Loading available beds…</p>
            ) : available.length === 0 ? (
              <p className="cl-body text-slate-600">No other bed is available right now.</p>
            ) : (
              <div className="flex flex-col gap-3" role="radiogroup" aria-label="Destination beds">
                {grouped.map((ward) => (
                  <div key={ward.ward?.id ?? "none"} className="flex flex-col gap-1">
                    <div className="cl-meta font-bold text-slate-800">{ward.ward?.name ?? "No ward"}</div>
                    {ward.rooms.map((room) => (
                      <div key={room.room?.id ?? "none"} className="flex items-start gap-2">
                        <span className="w-[88px] shrink-0 pt-1 font-mono cl-micro text-slate-600">
                          {room.room?.code ?? room.room?.name ?? "No room"}
                        </span>
                        <div className="flex flex-wrap gap-1.5">
                          {room.beds.map((bed) => {
                            const checked = bed.id === chosenId;
                            return (
                              <button
                                key={bed.id}
                                type="button"
                                role="radio"
                                aria-checked={checked}
                                onClick={() => setChosenId(bed.id)}
                                className={`rounded-md border px-2 py-1 font-mono cl-meta font-bold ${
                                  checked
                                    ? "border-sky-700 bg-sky-700 text-white"
                                    : "border-emerald-300 bg-emerald-50 text-emerald-900 hover:bg-emerald-100"
                                }`}
                              >
                                {bed.code ?? bed.name}
                              </button>
                            );
                          })}
                        </div>
                      </div>
                    ))}
                  </div>
                ))}
              </div>
            )}
            <label htmlFor="transfer-reason" className="mt-3 block cl-meta font-bold text-slate-700">
              Reason for transfer
            </label>
            <textarea
              id="transfer-reason"
              value={reason}
              maxLength={ADMISSION_REASON_MAX}
              rows={2}
              onChange={(event) => setReason(event.target.value)}
              className="mt-1 w-full rounded-md border border-slate-300 px-2 py-1 cl-body text-slate-900"
            />
          </div>
        ) : (
          <div className="min-h-0 flex-1 overflow-y-auto px-4 py-3">
            <p className="cl-body text-slate-800">
              Transfer this patient now? The old bed is released and the new bed becomes occupied immediately.
            </p>
            <dl className="mt-3 grid grid-cols-[120px_minmax(0,1fr)] gap-y-1 cl-body">
              <dt className="text-slate-500">Patient</dt>
              <dd className="font-bold text-slate-900">{detail.patient?.name ?? "—"}</dd>
              <dt className="text-slate-500">Admission</dt>
              <dd className="font-mono">{detail.reference}</dd>
              <dt className="text-slate-500">From</dt>
              <dd>
                {place(detail.location.ward)} / {place(detail.location.room)} /{" "}
                <span className="font-mono font-bold">{place(detail.location.bed)}</span>
              </dd>
              <dt className="text-slate-500">To</dt>
              <dd>
                {place(selected?.ward)} / {place(selected?.room)} /{" "}
                <span className="font-mono font-bold">{selected?.code ?? selected?.name ?? "—"}</span>
              </dd>
              <dt className="text-slate-500">Reason</dt>
              <dd className="whitespace-pre-wrap">{cleaned ?? "—"}</dd>
              <dt className="text-slate-500">Revision</dt>
              <dd className="font-mono">{detail.workflow_revision}</dd>
            </dl>
          </div>
        )}

        {message ? (
          <p
            role="status"
            className={`mx-4 mb-2 rounded border px-2 py-1 cl-meta ${
              message.tone === "red"
                ? "border-red-300 bg-red-50 text-red-900"
                : message.tone === "amber"
                  ? "border-amber-300 bg-amber-50 text-amber-900"
                  : "border-emerald-300 bg-emerald-50 text-emerald-900"
            }`}
          >
            {message.text}
          </p>
        ) : null}

        <footer className="flex shrink-0 items-center justify-end gap-2 border-t border-slate-200 px-4 py-3">
          {effectiveStep === "choose" ? (
            <>
              <button
                type="button"
                onClick={onClose}
                className="h-8 rounded-md border border-slate-300 bg-white px-3 cl-meta font-bold text-slate-700 hover:bg-slate-50"
              >
                Close
              </button>
              <button
                type="button"
                disabled={!selected || !cleaned}
                onClick={() => setStep("confirm")}
                className="h-8 rounded-md bg-sky-700 px-3 cl-meta font-bold text-white hover:bg-sky-800 disabled:opacity-40"
              >
                Review move
              </button>
            </>
          ) : (
            <>
              <button
                ref={backRef}
                type="button"
                disabled={busy}
                onClick={() => setStep("choose")}
                className="h-8 rounded-md border border-slate-300 bg-white px-3 cl-meta font-bold text-slate-700 hover:bg-slate-50 disabled:opacity-40"
              >
                Go back
              </button>
              <button
                type="button"
                disabled={busy || !selected || !cleaned}
                onClick={(event) => {
                  // Pointer only: a keyboard-activated click reports detail 0.
                  if (event.detail === 0 || !selected || !cleaned) return;
                  onConfirm(selected.id, cleaned);
                }}
                className="h-8 rounded-md bg-sky-700 px-3 cl-meta font-bold text-white hover:bg-sky-800 disabled:opacity-40"
              >
                {busy ? "Moving…" : "Transfer patient"}
              </button>
            </>
          )}
        </footer>
      </div>
    </div>
  );
}
