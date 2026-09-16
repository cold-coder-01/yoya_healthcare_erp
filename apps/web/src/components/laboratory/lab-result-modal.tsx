"use client";

import type { KeyboardEvent as ReactKeyboardEvent } from "react";
import { useCallback, useEffect, useId, useRef, useState } from "react";

import {
  abnormalFlagHint,
  draftFromResult,
  draftStatusText,
  patchDraftLine,
  resultDraftChanged,
  resultModalSubtitle,
  resultPayload,
  sampleTypeLabel,
} from "@/lib/lab-result-format";
import type {
  LabResultDraft,
  LabResultOutcome,
} from "@/lib/lab-result-format";
import {
  closeIntent,
  escapeIntent,
  isSaveShortcut,
  shouldCloseAfterSave,
} from "@/lib/note-editor-format";
import type {
  LabRequestDetail,
  LabResult,
  LabResultWritePayload,
} from "@/types/lab-desk";

/**
 * THE LABORATORY RESULT SHEET, in a centred editor over the Laboratory Desk.
 *
 * LAB-OWNED, AND DELIBERATELY NOT THE DOCTOR'S SHELL. It follows the same
 * interaction pattern as the Doctor Desk's light order editor -- dimmed desk
 * behind, focus in and back, Tab trapped, Escape and every close path asking
 * before discarding typed work, Ctrl/Cmd+Enter for the primary act -- but it is
 * not built on ClinicalOrderModal. That shell has no busy state and no error
 * slot (its save only stages a draft locally), and it is held to the Doctor
 * Desk's own contract tests. The pure rules it uses ARE shared, from lib:
 * closeIntent, escapeIntent, isSaveShortcut and shouldCloseAfterSave, so the
 * keyboard and the buttons here cannot disagree about whether typing is safe.
 *
 * THE MODAL OWNS ITS DRAFT. It is handed the result as it stood when opened and
 * copies it once; nothing the desk does behind it -- a queue refresh, a
 * re-read of the request -- can replace what the technician is typing. The
 * workstation keys it on the result id, so a different result is a different
 * modal rather than a silent swap.
 *
 * NO COMPLETENESS RULE LIVES HERE. Mark entered is not disabled for a blank
 * value and nothing is trimmed: action_mark_entered() decides what a complete
 * result is, and its refusal -- which names the test missing a value -- is
 * shown inside the modal with every typed value intact.
 *
 * NO VALIDATE, NO RELEASE, NO CANCEL, NO RESET. Slice 3 stops at entered.
 */

const FIELD_CLASS =
  "h-9 w-full rounded-md border border-slate-300 bg-white px-2.5 cl-body text-slate-900 outline-none placeholder:text-slate-400 focus-visible:border-indigo-600 focus-visible:ring-2 focus-visible:ring-indigo-600/25 read-only:bg-slate-50 read-only:text-slate-700 disabled:cursor-not-allowed disabled:bg-slate-50 disabled:text-slate-700";

const TEXTAREA_CLASS =
  "w-full resize-y rounded-md border border-slate-300 bg-white px-2.5 py-2 cl-body leading-[1.5] text-slate-900 outline-none placeholder:text-slate-400 focus-visible:border-indigo-600 focus-visible:ring-2 focus-visible:ring-indigo-600/25 read-only:bg-slate-50 read-only:text-slate-700 disabled:cursor-not-allowed disabled:bg-slate-50";

const LABEL_CLASS = "cl-secondary font-semibold text-slate-700";

export type LabResultModalProps = {
  /** The request as it stood when the modal opened. Identity only. */
  request: LabRequestDetail;
  /** The result as it stood when the modal opened. Copied once, then owned. */
  result: LabResult;
  /** An entered (or later) result is shown, never edited. */
  readOnly: boolean;
  /** The button that opened this. Focus returns to it on close. */
  returnFocus: HTMLElement | null;
  /** POST /save. Resolves ok only when the server confirmed the write. */
  onSaveDraft: (payload: LabResultWritePayload) => Promise<LabResultOutcome>;
  /** POST /enter: save + action_mark_entered() in one savepoint. */
  onMarkEntered: (payload: LabResultWritePayload) => Promise<LabResultOutcome>;
  onClose: () => void;
};

export default function LabResultModal({
  request,
  result,
  readOnly,
  returnFocus,
  onSaveDraft,
  onMarkEntered,
  onClose,
}: LabResultModalProps) {
  /*
    `baseline` is the last state the SERVER confirmed; `draft` is what is on
    screen. Both start from the result the modal was opened with, captured by
    state initializers exactly once.
  */
  const [baseline, setBaseline] = useState<LabResultDraft>(() =>
    draftFromResult(result),
  );
  const [draft, setDraft] = useState<LabResultDraft>(() =>
    draftFromResult(result),
  );
  const [busy, setBusy] = useState<"save" | "enter" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [savedSinceOpen, setSavedSinceOpen] = useState(false);
  const [confirmDiscard, setConfirmDiscard] = useState(false);

  const panelRef = useRef<HTMLDivElement>(null);
  const firstValueRef = useRef<HTMLInputElement>(null);
  const closeRef = useRef<HTMLButtonElement>(null);
  const baseId = useId();
  const titleId = `${baseId}-title`;
  const subtitleId = `${baseId}-subtitle`;

  const changed = !readOnly && resultDraftChanged(baseline, draft);
  const isBusy = busy !== null;

  /*
    Focus in on open, focus back on close. A read-only sheet focuses its close
    button: there is no field to type into. The cleanup runs however the modal
    closes, so a keyboard user resumes at the button they came from.
  */
  useEffect(() => {
    const target = readOnly ? closeRef.current : firstValueRef.current;
    target?.focus();
    return () => returnFocus?.focus();
  }, [readOnly, returnFocus]);

  /* ---------------- close paths ---------------- */
  const requestClose = useCallback(() => {
    const intent = closeIntent({ readOnly, changed, busy: isBusy });
    if (intent === "close") onClose();
    else if (intent === "confirm") setConfirmDiscard(true);
    // "blocked": a request is in flight; closing now would hide its outcome.
  }, [changed, isBusy, onClose, readOnly]);

  /* ---------------- save draft ---------------- */
  const saveDraft = useCallback(async () => {
    if (readOnly || busy !== null) return;
    setBusy("save");
    setError(null);
    try {
      const outcome = await onSaveDraft(resultPayload(draft));
      if (outcome.ok) {
        // STAYS OPEN. The confirmed values become the new baseline; the text
        // on screen is left exactly as typed.
        setBaseline(draftFromResult(outcome.result));
        setSavedSinceOpen(true);
      } else {
        setError(outcome.message);
      }
    } finally {
      setBusy(null);
    }
  }, [busy, draft, onSaveDraft, readOnly]);

  /* ---------------- mark entered ---------------- */
  const markEntered = useCallback(async () => {
    if (readOnly || busy !== null) return;
    setBusy("enter");
    setError(null);
    let ok = false;
    try {
      const outcome = await onMarkEntered(resultPayload(draft));
      ok = outcome.ok;
      if (!outcome.ok) setError(outcome.message);
    } finally {
      setBusy(null);
    }
    // CLOSE ONLY ON SUCCESS. A refusal -- a blank value, a result that moved
    // on elsewhere, a lost connection -- keeps the modal open with every typed
    // value and the reason above the buttons.
    if (shouldCloseAfterSave(ok)) onClose();
  }, [busy, draft, onClose, onMarkEntered, readOnly]);

  /* ---------------- keyboard ---------------- */
  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") {
        // Captured and stopped: an Escape meant for this dialog must not also
        // reach the desk behind it.
        event.preventDefault();
        event.stopPropagation();
        const intent = escapeIntent({
          readOnly,
          changed,
          busy: isBusy,
          confirmingDiscard: confirmDiscard,
        });
        if (intent === "dismiss-confirm") setConfirmDiscard(false);
        else requestClose();
        return;
      }
      // Ctrl/Cmd+Enter marks entered. Bare Enter is left alone: it is the
      // newline key in the notes and interpretation.
      if (isSaveShortcut(event, { readOnly, confirmingDiscard: confirmDiscard })) {
        event.preventDefault();
        void markEntered();
      }
    }
    document.addEventListener("keydown", onKeyDown, true);
    return () => document.removeEventListener("keydown", onKeyDown, true);
  }, [changed, confirmDiscard, isBusy, markEntered, readOnly, requestClose]);

  const trapFocus = useCallback((event: ReactKeyboardEvent) => {
    if (event.key !== "Tab") return;
    const focusable = panelRef.current?.querySelectorAll<HTMLElement>(
      'button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [href]',
    );
    if (!focusable?.length) return;
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  }, []);

  const fieldsDisabled = !readOnly && isBusy;
  const status = draftStatusText({ readOnly, busy, changed, savedSinceOpen });

  return (
    <div className="fixed inset-0 z-[110] flex items-center justify-center p-4">
      {/* Dimmed, not opaque: the Laboratory Desk stays identifiable behind the
          sheet. Clicking it takes the same guarded route as Cancel. */}
      <div
        aria-hidden
        onClick={requestClose}
        className="absolute inset-0 bg-slate-900/45"
      />
      <div
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        aria-describedby={subtitleId}
        onKeyDown={trapFocus}
        className="relative flex max-h-[88vh] w-[900px] max-w-[94vw] flex-col overflow-hidden rounded-xl border border-slate-300 bg-white shadow-[0_20px_50px_rgba(15,23,42,0.28)]"
      >
        {/* ---- Header ---- */}
        <header className="flex shrink-0 items-start gap-3 border-b border-slate-200 bg-slate-50 px-5 py-3.5">
          <div className="min-w-0 flex-1">
            <div className="flex min-w-0 flex-wrap items-center gap-2">
              <h2
                id={titleId}
                className="min-w-0 truncate cl-head font-semibold text-slate-900"
              >
                {readOnly ? "Laboratory result" : "Enter laboratory results"}
              </h2>
              <span className="inline-flex shrink-0 items-center rounded border border-slate-300 bg-white px-1.5 py-px font-mono cl-micro font-bold uppercase tracking-wide text-slate-700">
                {result.name} · {result.state_label ?? result.state}
              </span>
              {readOnly ? (
                <span className="inline-flex shrink-0 items-center rounded border border-slate-400 bg-white px-1.5 py-px cl-micro font-bold uppercase tracking-wide text-slate-700">
                  Read only
                </span>
              ) : changed ? (
                <span className="inline-flex shrink-0 items-center gap-1 cl-micro font-bold uppercase tracking-wide text-amber-700">
                  <span aria-hidden className="h-1.5 w-1.5 rounded-full bg-amber-500" />
                  Unsaved
                </span>
              ) : null}
            </div>
            <p
              id={subtitleId}
              className="mt-0.5 truncate cl-secondary text-slate-600"
            >
              {resultModalSubtitle(request)}
            </p>
          </div>
          <button
            ref={closeRef}
            type="button"
            onClick={requestClose}
            disabled={isBusy}
            aria-label="Close laboratory result sheet"
            className="shrink-0 rounded-md border border-slate-300 bg-white px-2 py-1 cl-body font-semibold leading-none text-slate-600 outline-none hover:border-slate-400 hover:text-slate-900 focus-visible:ring-2 focus-visible:ring-indigo-600 disabled:opacity-50"
          >
            ✕
          </button>
        </header>

        {/* ---- Body ---- */}
        <div className="min-h-0 space-y-3 overflow-y-auto px-5 py-4">
          {draft.lines.length === 0 ? (
            <p className="rounded-md border border-amber-300 bg-amber-50 px-2.5 py-1.5 cl-secondary text-amber-900">
              This result has no lines. The line structure comes from the
              laboratory request and cannot be added here.
            </p>
          ) : null}

          {draft.lines.map((line, index) => {
            const source = result.lines.find((entry) => entry.id === line.id);
            const lineId = `${baseId}-line-${line.id}`;
            const hint = readOnly ? null : abnormalFlagHint(line.abnormal_flag);
            return (
              <section
                key={line.id}
                aria-labelledby={`${lineId}-test`}
                className="rounded-lg border border-slate-200"
              >
                {/* The ordered test and its specimen: shown, never edited. */}
                <div className="flex flex-wrap items-baseline justify-between gap-2 border-b border-slate-200 bg-slate-50 px-3 py-2">
                  <h3
                    id={`${lineId}-test`}
                    className="cl-body font-semibold text-slate-900"
                  >
                    {source?.test.name ?? "Ordered test"}
                    {source?.test.code ? (
                      <span className="ml-2 font-mono cl-meta font-semibold text-slate-600">
                        {source.test.code}
                      </span>
                    ) : null}
                  </h3>
                  <p className="cl-meta text-slate-600">
                    Specimen:{" "}
                    <span className="font-semibold text-slate-800">
                      {sampleTypeLabel(source?.sample_type)}
                    </span>
                    <span className="sr-only"> (read-only)</span>
                  </p>
                </div>

                <div className="grid grid-cols-1 gap-x-4 gap-y-3 p-3 sm:grid-cols-2">
                  <label className="flex min-w-0 flex-col gap-1 sm:col-span-2">
                    <span className={LABEL_CLASS}>Result value</span>
                    <input
                      ref={index === 0 ? firstValueRef : undefined}
                      type="text"
                      value={line.result_value}
                      readOnly={readOnly}
                      disabled={fieldsDisabled}
                      autoComplete="off"
                      spellCheck={false}
                      placeholder={readOnly ? undefined : "e.g. WBC 11.8, Hgb 14.2, Plt 265"}
                      onChange={(event) =>
                        setDraft((current) =>
                          patchDraftLine(current, line.id, {
                            result_value: event.target.value,
                          }),
                        )
                      }
                      className={FIELD_CLASS}
                    />
                  </label>

                  <label className="flex min-w-0 flex-col gap-1">
                    <span className={LABEL_CLASS}>Unit</span>
                    <input
                      type="text"
                      value={line.unit}
                      readOnly={readOnly}
                      disabled={fieldsDisabled}
                      autoComplete="off"
                      onChange={(event) =>
                        setDraft((current) =>
                          patchDraftLine(current, line.id, { unit: event.target.value }),
                        )
                      }
                      className={FIELD_CLASS}
                    />
                  </label>

                  <label className="flex min-w-0 flex-col gap-1">
                    <span className={LABEL_CLASS}>Reference range</span>
                    <input
                      type="text"
                      value={line.reference_range}
                      readOnly={readOnly}
                      disabled={fieldsDisabled}
                      autoComplete="off"
                      onChange={(event) =>
                        setDraft((current) =>
                          patchDraftLine(current, line.id, {
                            reference_range: event.target.value,
                          }),
                        )
                      }
                      className={FIELD_CLASS}
                    />
                  </label>

                  <div className="flex min-w-0 flex-col gap-1">
                    <label htmlFor={`${lineId}-flag`} className={LABEL_CLASS}>
                      Abnormal flag
                    </label>
                    <select
                      id={`${lineId}-flag`}
                      value={line.abnormal_flag}
                      // A select has no readOnly; disabled is the read-only form.
                      disabled={readOnly || fieldsDisabled}
                      aria-describedby={hint ? `${lineId}-flag-hint` : undefined}
                      onChange={(event) =>
                        setDraft((current) =>
                          patchDraftLine(current, line.id, {
                            abnormal_flag: event.target.value,
                          }),
                        )
                      }
                      className={`${FIELD_CLASS} font-semibold`}
                    >
                      {result.abnormal_flag_options.map((option) => (
                        <option key={option.value} value={option.value}>
                          {option.label}
                        </option>
                      ))}
                    </select>
                    {hint ? (
                      <p id={`${lineId}-flag-hint`} className="cl-meta text-slate-500">
                        {hint}
                      </p>
                    ) : null}
                  </div>

                  <label className="flex min-w-0 flex-col gap-1">
                    <span className={LABEL_CLASS}>Notes</span>
                    <textarea
                      value={line.notes}
                      rows={2}
                      readOnly={readOnly}
                      disabled={fieldsDisabled}
                      onChange={(event) =>
                        setDraft((current) =>
                          patchDraftLine(current, line.id, { notes: event.target.value }),
                        )
                      }
                      className={TEXTAREA_CLASS}
                    />
                  </label>
                </div>
              </section>
            );
          })}

          <div className="grid grid-cols-1 gap-x-4 gap-y-3 sm:grid-cols-2">
            <label className="flex min-w-0 flex-col gap-1">
              <span className={LABEL_CLASS}>Interpretation</span>
              <textarea
                value={draft.interpretation}
                rows={3}
                readOnly={readOnly}
                disabled={fieldsDisabled}
                placeholder={readOnly ? undefined : "Optional"}
                onChange={(event) =>
                  setDraft((current) => ({
                    ...current,
                    interpretation: event.target.value,
                  }))
                }
                className={TEXTAREA_CLASS}
              />
            </label>
            <label className="flex min-w-0 flex-col gap-1">
              <span className={LABEL_CLASS}>Remarks</span>
              <textarea
                value={draft.remarks}
                rows={3}
                readOnly={readOnly}
                disabled={fieldsDisabled}
                placeholder={readOnly ? undefined : "Optional"}
                onChange={(event) =>
                  setDraft((current) => ({ ...current, remarks: event.target.value }))
                }
                className={TEXTAREA_CLASS}
              />
            </label>
          </div>
        </div>

        {/* ---- Footer ---- */}
        <footer className="shrink-0 border-t border-slate-200 bg-slate-50 px-5 py-3">
          {error ? (
            <p
              role="alert"
              className="mb-2 whitespace-pre-wrap rounded-md border border-red-300 bg-white px-2.5 py-1.5 cl-secondary leading-snug text-red-900"
            >
              {error}
            </p>
          ) : null}

          {confirmDiscard ? (
            <div className="flex flex-wrap items-center justify-between gap-2">
              <p className="cl-secondary font-semibold text-amber-900">
                These results have unsaved changes. Discard them?
              </p>
              <div className="flex items-center gap-2">
                <button
                  type="button"
                  onClick={() => setConfirmDiscard(false)}
                  className="rounded-md border border-slate-300 bg-white px-3 py-1.5 cl-body font-semibold text-slate-700 outline-none hover:bg-slate-100 focus-visible:ring-2 focus-visible:ring-indigo-600"
                >
                  Keep editing
                </button>
                <button
                  type="button"
                  onClick={onClose}
                  className="rounded-md border border-red-400 bg-white px-3 py-1.5 cl-body font-semibold text-red-800 outline-none hover:bg-red-50 focus-visible:ring-2 focus-visible:ring-red-600"
                >
                  Discard changes
                </button>
              </div>
            </div>
          ) : (
            <div className="flex flex-wrap items-center justify-end gap-2">
              <span
                aria-live="polite"
                className="mr-auto cl-meta text-slate-500"
              >
                {status}
              </span>
              <button
                type="button"
                onClick={requestClose}
                disabled={isBusy}
                className="rounded-md border border-slate-300 bg-white px-3.5 py-1.5 cl-body font-semibold text-slate-700 outline-none hover:bg-slate-100 focus-visible:ring-2 focus-visible:ring-indigo-600 disabled:opacity-50"
              >
                {readOnly ? "Close" : "Cancel"}
              </button>
              {readOnly ? null : (
                <>
                  <button
                    type="button"
                    onClick={() => void saveDraft()}
                    disabled={isBusy || !changed}
                    aria-busy={busy === "save"}
                    className="rounded-md border border-indigo-300 bg-white px-3.5 py-1.5 cl-body font-semibold text-indigo-800 outline-none hover:bg-indigo-50 focus-visible:ring-2 focus-visible:ring-indigo-600 disabled:border-slate-300 disabled:text-slate-500"
                  >
                    {busy === "save" ? "Saving…" : "Save draft"}
                  </button>
                  <button
                    type="button"
                    onClick={() => void markEntered()}
                    disabled={isBusy}
                    aria-busy={busy === "enter"}
                    className="rounded-md bg-indigo-700 px-4 py-1.5 cl-body font-semibold text-white shadow-sm outline-none hover:bg-indigo-800 focus-visible:ring-2 focus-visible:ring-indigo-700 disabled:bg-slate-300 disabled:text-slate-600 disabled:shadow-none"
                  >
                    {busy === "enter" ? "Marking entered…" : "Mark entered"}
                  </button>
                </>
              )}
            </div>
          )}
        </footer>
      </div>
    </div>
  );
}
