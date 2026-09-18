"use client";

import type { KeyboardEvent as ReactKeyboardEvent } from "react";
import { useCallback, useEffect, useId, useRef, useState } from "react";

import { formatHospitalDateTime } from "@/lib/clinical-format";
import { closeIntent, escapeIntent } from "@/lib/note-editor-format";
import {
  ACCEPTED_IMAGE_LABEL,
  ACCEPTED_IMAGE_TYPES,
  IMAGE_REMOVE_SUPPORT_TEXT,
  IMAGE_TOO_LARGE_TEXT,
  MAX_IMAGE_LABEL,
  RELEASE_REVIEW_TEXT,
  REPORT_ENTER_SUPPORT_TEXT,
  VALIDATE_REVIEW_TEXT,
  VALIDATE_SUPPORT_TEXT,
  fileSizeLabel,
  imageKind,
  imagePath,
  imageRemoveConfirmText,
  imageTooLarge,
  imageTypeLabel,
  patchReportLine,
  reportContext,
  reportDraftChanged,
  reportDraftFromResult,
  reportEnterConfirmText,
  reportStatusText,
  releaseConfirmText,
  releaseIdentityText,
  releaseSupportText,
  validateConfirmText,
} from "@/lib/rad-desk-format";
import type { RadReportDraft } from "@/lib/rad-desk-format";
import type {
  RadImageMetadata,
  RadOperationalResult,
  RadRequestDetail,
  RadSignoffKind,
} from "@/types/rad-desk";

/**
 * THE RADIOLOGY REPORT, in a centred sheet over the Radiology Desk.
 *
 * RADIOLOGY-OWNED. It is not the Doctor Desk's consultation editor and not the
 * Laboratory result sheet; it shares only the pure close/escape rules from
 * lib/note-editor-format, so the buttons and the keyboard cannot disagree
 * about whether typed text is safe to throw away.
 *
 * THE SHEET OWNS ITS DRAFT. It copies the report once, when opened; a queue
 * refresh behind it can never replace what the radiologist is typing. The
 * workstation keys it on the report's id AND state, so the entered report the
 * server returns is a fresh, read-only sheet rather than a silent swap.
 *
 * WHO EDITS. `editable` is true only for a DRAFT report and a report-author
 * role (Radiologist, Manager, System Administrator). The technician opens the
 * same sheet read-only. Either way the server decides: its report routes
 * refuse a technician's write with 403 and any write to a non-draft with 409.
 *
 * NO COMPLETENESS RULE LIVES HERE. "Mark entered" is not disabled for an empty
 * narrative; action_mark_entered() decides what a complete report is, and its
 * refusal is shown inside the sheet with every typed word intact.
 *
 * MARK ENTERED TAKES TWO CLICKS. The first opens a confirmation naming the
 * report; only its "Mark entered" sends anything, with the text on screen, in
 * one request. Focus lands on its Cancel, Escape backs out of it, and NO
 * keyboard shortcut performs it -- Ctrl/Cmd+Enter does nothing on this sheet.
 *
 * IMAGES (Slice 4) ARE A SEPARATE MUTABILITY. The narrative freezes when the
 * report is entered; the image set freezes only at validation. So an entered
 * report opens with READ-ONLY text and a WORKING Images section, and the sheet
 * is never disabled as a whole because one half of it is frozen. Whether the
 * images may change is the SERVER's images_mutable, never read from the state
 * here. Previews and Open links go through the desk's own BFF byte route only.
 * Removing a file takes a second click, focused on Cancel, with no shortcut.
 *
 * VALIDATE AND RELEASE (Slice 5) ARE A MODE OF THE SAME SHEET. Given
 * `signoff`, the sheet is read-only -- text AND images -- and its footer is a
 * TWO-STEP confirmation: "Validate report…" / "Release report…" only moves to
 * a second step naming the report, the patient and the study (and, for
 * release, the clinician who will see it); only "Confirm validation" /
 * "Confirm release" sends anything. Focus lands on "Go back", Escape goes
 * back, and no keyboard shortcut performs either act. On success the
 * workstation closes the sheet; a refusal stays in it, back at the review step.
 *
 * NO AMENDMENT, NO RETRACTION, NO CANCEL.
 */

const TEXTAREA_CLASS =
  "w-full resize-y rounded-md border border-slate-300 bg-white px-2.5 py-2 cl-body leading-[1.5] text-slate-900 outline-none placeholder:text-slate-400 focus-visible:border-teal-700 focus-visible:ring-2 focus-visible:ring-teal-700/25 read-only:bg-slate-50 read-only:text-slate-700 disabled:cursor-not-allowed disabled:bg-slate-50";

const INPUT_CLASS =
  "h-9 w-full rounded-md border border-slate-300 bg-white px-2.5 cl-body text-slate-900 outline-none placeholder:text-slate-400 focus-visible:border-teal-700 focus-visible:ring-2 focus-visible:ring-teal-700/25 read-only:bg-slate-50 read-only:text-slate-700 disabled:cursor-not-allowed disabled:bg-slate-50";

const LABEL_CLASS = "cl-secondary font-semibold text-slate-700";

export type RadReportOutcome =
  | { ok: true; result: RadOperationalResult }
  | { ok: false; message: string };

export type RadImageOutcome = { ok: true } | { ok: false; message: string };

export type RadReportModalProps = {
  /** The request, for the header context. Identity only. */
  request: RadRequestDetail;
  /** The report as it stood when the sheet opened. Copied once, then owned. */
  result: RadOperationalResult;
  /** A draft AND a report-author role. Otherwise the sheet is read-only. */
  editable: boolean;
  /** The ROLE may author reports (edit_report). Chooses the read-only notice. */
  canAuthor: boolean;
  /** The server's images_mutable AND manage_images. Independent of `editable`. */
  imagesEditable: boolean;
  /** POST .../images with one file. Resolves ok only on the server's word. */
  onUploadImage: (file: File, caption: string) => Promise<RadImageOutcome>;
  /** POST .../images/<id>/remove. */
  onRemoveImage: (imageId: number) => Promise<RadImageOutcome>;
  /** The button that opened this. Focus returns to it on close. */
  returnFocus: HTMLElement | null;
  /** POST /save. Resolves ok only when the server confirmed the write. */
  onSaveDraft: (draft: RadReportDraft) => Promise<RadReportOutcome>;
  /** POST /enter: save + action_mark_entered() in one savepoint. */
  onMarkEntered: (draft: RadReportDraft) => Promise<RadReportOutcome>;
  /** Slice 5: the sheet was opened to validate or release. */
  signoff?: RadSignoffKind | null;
  /** POST .../validate or .../release. Resolves ok only on the server's word. */
  onSignoff?: () => Promise<RadImageOutcome>;
  onClose: () => void;
};

export default function RadReportModal({
  request,
  result,
  editable,
  canAuthor,
  imagesEditable,
  returnFocus,
  onSaveDraft,
  onMarkEntered,
  onUploadImage,
  onRemoveImage,
  signoff = null,
  onSignoff,
  onClose,
}: RadReportModalProps) {
  const readOnly = !editable;
  /*
    `baseline` is the last text the SERVER confirmed; `draft` is what is on
    screen. Both start from the report the sheet opened with, exactly once.
  */
  const [baseline, setBaseline] = useState<RadReportDraft>(() =>
    reportDraftFromResult(result),
  );
  const [draft, setDraft] = useState<RadReportDraft>(() => reportDraftFromResult(result));
  const [busy, setBusy] = useState<"save" | "enter" | "upload" | "remove" | "sign" | null>(null);
  /* Slice 5: "review" the report, then "confirm" the act. */
  const [signStep, setSignStep] = useState<"review" | "confirm">("review");
  const goBackRef = useRef<HTMLButtonElement>(null);
  const [error, setError] = useState<string | null>(null);
  const [savedSinceOpen, setSavedSinceOpen] = useState(false);
  const [confirmDiscard, setConfirmDiscard] = useState(false);
  const [confirmEnter, setConfirmEnter] = useState(false);
  /* ---- images (Slice 4) ---- */
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [caption, setCaption] = useState("");
  const [imageError, setImageError] = useState<string | null>(null);
  const [removeFor, setRemoveFor] = useState<number | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const removeCancelRef = useRef<HTMLButtonElement>(null);

  const panelRef = useRef<HTMLDivElement>(null);
  const findingsRef = useRef<HTMLTextAreaElement>(null);
  const closeRef = useRef<HTMLButtonElement>(null);
  const enterCancelRef = useRef<HTMLButtonElement>(null);
  const baseId = useId();
  const titleId = `${baseId}-title`;
  const contextId = `${baseId}-context`;

  const changed = !readOnly && reportDraftChanged(baseline, draft);
  const isBusy = busy !== null;

  /* Focus in on open, back to the opening button on close. */
  useEffect(() => {
    const target = readOnly ? closeRef.current : findingsRef.current;
    target?.focus();
    return () => returnFocus?.focus();
  }, [readOnly, returnFocus]);

  /* The entry confirmation focuses its CANCEL, never the final button. */
  useEffect(() => {
    if (confirmEnter) enterCancelRef.current?.focus();
  }, [confirmEnter]);

  /* The sign-off confirmation focuses GO BACK, never the final button. */
  useEffect(() => {
    if (signStep === "confirm") goBackRef.current?.focus();
  }, [signStep]);

  /* So does the remove confirmation. */
  useEffect(() => {
    if (removeFor !== null) removeCancelRef.current?.focus();
  }, [removeFor]);

  /* ---------------- validate / release (Slice 5) ---------------- */
  /** Step one's button. Moves to the confirmation; NEVER sends anything. */
  const askToSign = useCallback(() => {
    if (!signoff || busy !== null) return;
    setError(null);
    setSignStep("confirm");
  }, [busy, signoff]);

  /** Step two's button, and the ONLY path that validates or releases. */
  const confirmSign = useCallback(async () => {
    if (!signoff || !onSignoff || busy !== null || signStep !== "confirm") return;
    setBusy("sign");
    setError(null);
    try {
      const outcome = await onSignoff();
      if (!outcome.ok) {
        // Back to review with the server's (money-free) reason; nothing changed.
        setError(outcome.message);
        setSignStep("review");
      }
      // On success the workstation closes the sheet.
    } finally {
      setBusy(null);
    }
  }, [busy, onSignoff, signStep, signoff]);

  /* ---------------- images (Slice 4) ---------------- */
  const chooseFile = useCallback((file: File | null) => {
    setImageError(null);
    if (!file) {
      setSelectedFile(null);
      return;
    }
    if (imageTooLarge(file)) {
      // Advisory only: the server refuses it too. Not sent at all.
      setImageError(IMAGE_TOO_LARGE_TEXT);
      setSelectedFile(null);
      if (fileInputRef.current) fileInputRef.current.value = "";
      return;
    }
    setSelectedFile(file);
  }, []);

  const uploadImage = useCallback(async () => {
    if (!imagesEditable || !selectedFile || busy !== null) return;
    setBusy("upload");
    setImageError(null);
    try {
      const outcome = await onUploadImage(selectedFile, caption);
      if (outcome.ok) {
        setSelectedFile(null);
        setCaption("");
        if (fileInputRef.current) fileInputRef.current.value = "";
      } else {
        // The list on screen is the server's last confirmed one; nothing is
        // removed from it on a refusal.
        setImageError(outcome.message);
      }
    } finally {
      setBusy(null);
    }
  }, [busy, caption, imagesEditable, onUploadImage, selectedFile]);

  /** The remove confirmation's final button: the ONLY path that removes. */
  const removeImage = useCallback(async () => {
    if (!imagesEditable || removeFor === null || busy !== null) return;
    setBusy("remove");
    setImageError(null);
    try {
      const outcome = await onRemoveImage(removeFor);
      if (!outcome.ok) setImageError(outcome.message);
      setRemoveFor(null);
    } finally {
      setBusy(null);
    }
  }, [busy, imagesEditable, onRemoveImage, removeFor]);

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
      const outcome = await onSaveDraft(draft);
      if (outcome.ok) {
        // STAYS OPEN. The confirmed text becomes the new baseline; what is on
        // screen is left exactly as typed.
        setBaseline(reportDraftFromResult(outcome.result));
        setSavedSinceOpen(true);
      } else {
        setError(outcome.message);
      }
    } finally {
      setBusy(null);
    }
  }, [busy, draft, onSaveDraft, readOnly]);

  /* ---------------- mark entered ---------------- */
  /** Step one. Opens the confirmation; NEVER sends anything. */
  const askToEnter = useCallback(() => {
    if (readOnly || busy !== null) return;
    setError(null);
    setConfirmEnter(true);
  }, [busy, readOnly]);

  /** Step two's button, and the ONLY path that marks the report entered. */
  const markEntered = useCallback(async () => {
    if (readOnly || busy !== null || !confirmEnter) return;
    setBusy("enter");
    setError(null);
    try {
      const outcome = await onMarkEntered(draft);
      if (!outcome.ok) {
        // A refusal -- an empty narrative, a report that moved on elsewhere,
        // a lost connection -- keeps every typed word and says why.
        setError(outcome.message);
        setConfirmEnter(false);
      }
      // On success the workstation replaces this sheet with the entered,
      // read-only report the server returned.
    } finally {
      setBusy(null);
    }
  }, [busy, confirmEnter, draft, onMarkEntered, readOnly]);

  /* ---------------- keyboard ---------------- */
  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") {
        // Captured and stopped: an Escape meant for this sheet must not also
        // reach the desk behind it.
        event.preventDefault();
        event.stopPropagation();
        if (signStep === "confirm") {
          if (!isBusy) setSignStep("review");
          return;
        }
        if (confirmEnter) {
          if (!isBusy) setConfirmEnter(false);
          return;
        }
        if (removeFor !== null) {
          if (!isBusy) setRemoveFor(null);
          return;
        }
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
      // NO SHORTCUT PERFORMS AN ACT HERE. Ctrl/Cmd+Enter is swallowed so it can
      // neither save nor mark entered; bare Enter stays the newline key.
      if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) {
        event.preventDefault();
      }
    }
    document.addEventListener("keydown", onKeyDown, true);
    return () => document.removeEventListener("keydown", onKeyDown, true);
  }, [changed, confirmDiscard, confirmEnter, isBusy, readOnly, removeFor, requestClose, signStep]);

  const trapFocus = useCallback((event: ReactKeyboardEvent) => {
    if (event.key !== "Tab") return;
    const focusable = panelRef.current?.querySelectorAll<HTMLElement>(
      'button:not([disabled]), input:not([disabled]), textarea:not([disabled]), [href]',
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
  const status = reportStatusText({
    readOnly,
    busy: busy === "save" || busy === "enter" ? busy : null,
    changed,
    savedSinceOpen,
  });
  const context = reportContext(request, result);
  const linesById = new Map(result.lines.map((line) => [line.id, line]));

  return (
    <div className="fixed inset-0 z-[110] flex items-center justify-center p-4">
      {/* Dimmed, not opaque. Clicking it takes the same guarded route as Cancel. */}
      <div aria-hidden onClick={requestClose} className="absolute inset-0 bg-slate-900/45" />
      <div
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        aria-describedby={contextId}
        onKeyDown={trapFocus}
        className="relative flex max-h-[88vh] w-[900px] max-w-[94vw] flex-col overflow-hidden rounded-xl border border-slate-300 bg-white shadow-[0_20px_50px_rgba(15,23,42,0.28)]"
      >
        {/* ---- Header ---- */}
        <header className="flex shrink-0 items-start gap-3 border-b border-slate-200 bg-slate-50 px-5 py-3.5">
          <div className="min-w-0 flex-1">
            <div className="flex min-w-0 flex-wrap items-center gap-2">
              <h2 id={titleId} className="min-w-0 truncate cl-head font-semibold text-slate-900">
                {signoff === "validate"
                  ? "Validate radiology report"
                  : signoff === "release"
                    ? "Release radiology report"
                    : "Radiology report"}
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
            <dl
              id={contextId}
              className="mt-1.5 grid grid-cols-2 gap-x-4 gap-y-1 min-[700px]:grid-cols-4"
            >
              {context.map((item) => (
                <div key={item.label} className="flex min-w-0 flex-col">
                  <dt className="cl-micro font-bold uppercase tracking-[0.06em] text-slate-400">
                    {item.label}
                  </dt>
                  <dd className="truncate cl-secondary font-semibold text-slate-800">
                    {item.value}
                  </dd>
                </div>
              ))}
            </dl>
          </div>
          <button
            ref={closeRef}
            type="button"
            onClick={requestClose}
            disabled={isBusy}
            aria-label="Close radiology report"
            className="shrink-0 rounded-md border border-slate-300 bg-white px-2 py-1 cl-body font-semibold leading-none text-slate-600 outline-none hover:border-slate-400 hover:text-slate-900 focus-visible:ring-2 focus-visible:ring-teal-700 disabled:opacity-50"
          >
            ✕
          </button>
        </header>

        {/* ---- Body ---- */}
        <div className="flex min-h-0 flex-1 flex-col gap-4 overflow-y-auto px-5 py-4">
          {!canAuthor ? (
            <p className="rounded border border-slate-300 bg-slate-50 px-3 py-2 cl-secondary text-slate-700">
              Report text is written and entered by a radiologist. You can read it here.
            </p>
          ) : null}

          <label className="flex flex-col gap-1">
            <span className={LABEL_CLASS}>Findings</span>
            <textarea
              ref={findingsRef}
              rows={6}
              value={draft.findings}
              readOnly={readOnly}
              disabled={fieldsDisabled}
              onChange={(event) => setDraft({ ...draft, findings: event.target.value })}
              className={TEXTAREA_CLASS}
            />
          </label>
          <label className="flex flex-col gap-1">
            <span className={LABEL_CLASS}>Impression</span>
            <textarea
              rows={3}
              value={draft.impression}
              readOnly={readOnly}
              disabled={fieldsDisabled}
              onChange={(event) => setDraft({ ...draft, impression: event.target.value })}
              className={TEXTAREA_CLASS}
            />
          </label>
          <label className="flex flex-col gap-1">
            <span className={LABEL_CLASS}>Recommendations</span>
            <textarea
              rows={2}
              value={draft.recommendations}
              readOnly={readOnly}
              disabled={fieldsDisabled}
              onChange={(event) =>
                setDraft({ ...draft, recommendations: event.target.value })
              }
              className={TEXTAREA_CLASS}
            />
          </label>

          <section className="flex flex-col gap-2">
            <h3 className="cl-micro font-bold uppercase tracking-[0.06em] text-slate-500">
              Studies
            </h3>
            {draft.lines.map((line) => {
              const source = linesById.get(line.id);
              return (
                <div
                  key={line.id}
                  className="grid gap-2 rounded-md border border-slate-200 px-3 py-2 min-[700px]:grid-cols-[minmax(0,1fr)_minmax(0,2fr)]"
                >
                  <div className="flex min-w-0 flex-col">
                    <span className="truncate cl-body font-semibold text-slate-900">
                      {source?.exam.name ?? "Study"}
                    </span>
                    <span className="cl-secondary text-slate-600">
                      Body part: {source?.body_part ?? "—"}
                    </span>
                  </div>
                  <div className="flex min-w-0 flex-col gap-2">
                    <label className="flex flex-col gap-1">
                      <span className={LABEL_CLASS}>Result summary</span>
                      <input
                        type="text"
                        value={line.result_summary}
                        readOnly={readOnly}
                        disabled={fieldsDisabled}
                        onChange={(event) =>
                          setDraft(
                            patchReportLine(draft, line.id, {
                              result_summary: event.target.value,
                            }),
                          )
                        }
                        className={INPUT_CLASS}
                      />
                    </label>
                    <label className="flex flex-col gap-1">
                      <span className={LABEL_CLASS}>Notes</span>
                      <textarea
                        rows={2}
                        value={line.notes}
                        readOnly={readOnly}
                        disabled={fieldsDisabled}
                        onChange={(event) =>
                          setDraft(patchReportLine(draft, line.id, { notes: event.target.value }))
                        }
                        className={TEXTAREA_CLASS}
                      />
                    </label>
                  </div>
                </div>
              );
            })}
          </section>

          <ImagesSection
            resultId={result.id}
            images={result.images}
            editable={imagesEditable}
            busy={busy}
            selectedFile={selectedFile}
            caption={caption}
            error={imageError}
            removeFor={removeFor}
            fileInputRef={fileInputRef}
            removeCancelRef={removeCancelRef}
            onChooseFile={chooseFile}
            onCaptionChange={setCaption}
            onUpload={() => void uploadImage()}
            onAskRemove={(imageId) => {
              if (busy === null) {
                setImageError(null);
                setRemoveFor(imageId);
              }
            }}
            onCancelRemove={() => setRemoveFor(null)}
            onConfirmRemove={() => void removeImage()}
          />
        </div>

        {/* ---- Footer ---- */}
        <footer className="flex shrink-0 flex-col gap-2 border-t border-slate-200 bg-slate-50 px-5 py-3">
          {error ? (
            <p
              role="alert"
              className="rounded border border-red-300 bg-red-50 px-3 py-2 cl-secondary font-semibold text-red-900"
            >
              {error}
            </p>
          ) : null}

          {confirmDiscard ? (
            <div
              role="alertdialog"
              aria-label="Discard unsaved changes"
              className="flex flex-wrap items-center justify-between gap-2 rounded border border-amber-300 bg-amber-50 px-3 py-2"
            >
              <span className="cl-secondary font-semibold text-amber-900">
                Discard the changes you have not saved?
              </span>
              <div className="flex gap-2">
                <button
                  type="button"
                  onClick={() => setConfirmDiscard(false)}
                  className="rounded border border-slate-300 bg-white px-3 py-1.5 cl-secondary font-bold text-slate-700 hover:bg-slate-50"
                >
                  Keep editing
                </button>
                <button
                  type="button"
                  onClick={onClose}
                  className="rounded border border-amber-700 bg-amber-700 px-3 py-1.5 cl-secondary font-bold text-white hover:bg-amber-800"
                >
                  Discard
                </button>
              </div>
            </div>
          ) : null}

          {signoff ? (
            <SignoffStep
              kind={signoff}
              request={request}
              result={result}
              step={signStep}
              busy={busy === "sign"}
              disabled={isBusy}
              goBackRef={goBackRef}
              onAsk={askToSign}
              onGoBack={() => setSignStep("review")}
              onConfirm={() => void confirmSign()}
            />
          ) : null}

          {confirmEnter ? (
            <div
              role="alertdialog"
              aria-label="Mark report entered"
              aria-describedby={`${baseId}-enter-question`}
              className="flex flex-col gap-2 rounded border border-teal-300 bg-teal-50 px-3 py-2"
            >
              <p id={`${baseId}-enter-question`} className="cl-body font-semibold text-slate-900">
                {reportEnterConfirmText(result)}
              </p>
              <p className="cl-secondary text-slate-600">{REPORT_ENTER_SUPPORT_TEXT}</p>
              <div className="flex items-center gap-2">
                <button
                  ref={enterCancelRef}
                  type="button"
                  onClick={() => setConfirmEnter(false)}
                  disabled={isBusy}
                  className="rounded border border-slate-300 bg-white px-3 py-1.5 cl-secondary font-bold text-slate-700 hover:bg-slate-50 disabled:opacity-50"
                >
                  Cancel
                </button>
                <button
                  type="button"
                  onClick={() => void markEntered()}
                  disabled={isBusy}
                  className="rounded border border-teal-700 bg-teal-700 px-3 py-1.5 cl-secondary font-bold text-white hover:bg-teal-800 disabled:opacity-50"
                >
                  {busy === "enter" ? "Working…" : "Mark entered"}
                </button>
              </div>
            </div>
          ) : null}

          <div className="flex flex-wrap items-center justify-between gap-2">
            <span role="status" className="cl-meta text-slate-500">
              {status}
            </span>
            {readOnly ? (
              <button
                type="button"
                onClick={onClose}
                className="rounded border border-slate-300 bg-white px-3 py-1.5 cl-secondary font-bold text-slate-700 hover:bg-slate-50"
              >
                Close
              </button>
            ) : (
              <div className="flex items-center gap-2">
                <button
                  type="button"
                  onClick={requestClose}
                  disabled={isBusy}
                  className="rounded border border-slate-300 bg-white px-3 py-1.5 cl-secondary font-bold text-slate-700 hover:bg-slate-50 disabled:opacity-50"
                >
                  Cancel
                </button>
                <button
                  type="button"
                  onClick={() => void saveDraft()}
                  disabled={isBusy || confirmEnter}
                  className="rounded border border-slate-400 bg-white px-3 py-1.5 cl-secondary font-bold text-slate-800 hover:bg-slate-50 disabled:opacity-50"
                >
                  {busy === "save" ? "Saving…" : "Save draft"}
                </button>
                <button
                  type="button"
                  onClick={askToEnter}
                  disabled={isBusy || confirmEnter}
                  className="rounded border border-teal-700 bg-teal-700 px-3 py-1.5 cl-secondary font-bold text-white hover:bg-teal-800 disabled:opacity-50"
                >
                  Mark entered
                </button>
              </div>
            )}
          </div>
        </footer>
      </div>
    </div>
  );
}

/**
 * The report's files. EVERY URL IS THE DESK'S BFF BYTE ROUTE (imagePath): a
 * JPEG or PNG previews through it, a PDF is an item with an Open link through
 * it, and nothing is embedded from anywhere else. Upload and remove render
 * only when the server says the image set may still change.
 */
function ImagesSection({
  resultId,
  images,
  editable,
  busy,
  selectedFile,
  caption,
  error,
  removeFor,
  fileInputRef,
  removeCancelRef,
  onChooseFile,
  onCaptionChange,
  onUpload,
  onAskRemove,
  onCancelRemove,
  onConfirmRemove,
}: {
  resultId: number;
  images: RadImageMetadata[];
  editable: boolean;
  busy: "save" | "enter" | "upload" | "remove" | "sign" | null;
  selectedFile: File | null;
  caption: string;
  error: string | null;
  removeFor: number | null;
  fileInputRef: React.RefObject<HTMLInputElement | null>;
  removeCancelRef: React.RefObject<HTMLButtonElement | null>;
  onChooseFile: (file: File | null) => void;
  onCaptionChange: (value: string) => void;
  onUpload: () => void;
  onAskRemove: (imageId: number) => void;
  onCancelRemove: () => void;
  onConfirmRemove: () => void;
}) {
  const isBusy = busy !== null;
  return (
    <section aria-label="Images" className="flex flex-col gap-2">
      <h3 className="cl-micro font-bold uppercase tracking-[0.06em] text-slate-500">Images</h3>

      {editable ? (
        <div className="flex flex-col gap-2 rounded-md border border-dashed border-slate-300 px-3 py-2">
          <div className="flex flex-wrap items-center gap-2">
            <input
              ref={fileInputRef}
              type="file"
              accept={ACCEPTED_IMAGE_TYPES}
              className="hidden"
              onChange={(event) => onChooseFile(event.target.files?.[0] ?? null)}
            />
            <button
              type="button"
              onClick={() => fileInputRef.current?.click()}
              disabled={isBusy}
              className="rounded border border-teal-700 bg-white px-3 py-1.5 cl-secondary font-bold text-teal-800 hover:bg-teal-50 disabled:opacity-50"
            >
              Upload image/report
            </button>
            <span className="cl-meta text-slate-500">
              {ACCEPTED_IMAGE_LABEL} · {MAX_IMAGE_LABEL}
            </span>
          </div>
          {selectedFile ? (
            <div className="flex flex-wrap items-center gap-2">
              <span className="min-w-0 truncate cl-secondary font-semibold text-slate-800">
                {selectedFile.name} · {fileSizeLabel(selectedFile.size)}
              </span>
              <input
                type="text"
                value={caption}
                maxLength={200}
                placeholder="Caption (optional)"
                disabled={isBusy}
                onChange={(event) => onCaptionChange(event.target.value)}
                className="h-8 min-w-[12rem] flex-1 rounded-md border border-slate-300 px-2 cl-secondary"
              />
              <button
                type="button"
                onClick={onUpload}
                disabled={isBusy}
                className="rounded border border-teal-700 bg-teal-700 px-3 py-1.5 cl-secondary font-bold text-white hover:bg-teal-800 disabled:opacity-50"
              >
                {busy === "upload" ? "Uploading…" : "Upload"}
              </button>
              <button
                type="button"
                onClick={() => onChooseFile(null)}
                disabled={isBusy}
                className="rounded border border-slate-300 bg-white px-3 py-1.5 cl-secondary font-bold text-slate-700 hover:bg-slate-50 disabled:opacity-50"
              >
                Clear
              </button>
            </div>
          ) : null}
        </div>
      ) : null}

      {error ? (
        <p role="alert" className="rounded border border-red-300 bg-red-50 px-3 py-2 cl-secondary font-semibold text-red-900">
          {error}
        </p>
      ) : null}

      {images.length === 0 ? (
        <p className="cl-secondary text-slate-500">No files are attached to this report.</p>
      ) : (
        <ul className="flex flex-col gap-2">
          {images.map((image) => {
            const kind = imageKind(image);
            const href = imagePath(resultId, image.id);
            return (
              <li key={image.id} className="flex flex-col gap-2 rounded-md border border-slate-200 px-3 py-2">
                <div className="flex items-start gap-3">
                  {kind === "image" ? (
                    // eslint-disable-next-line @next/next/no-img-element -- a private, uncached BFF stream; next/image would re-host it
                    <img
                      src={href}
                      alt={image.name}
                      className="h-16 w-16 shrink-0 rounded border border-slate-200 object-cover"
                    />
                  ) : (
                    <span className="flex h-16 w-16 shrink-0 items-center justify-center rounded border border-slate-200 bg-slate-50 cl-secondary font-bold text-slate-600">
                      {imageTypeLabel(image)}
                    </span>
                  )}
                  <div className="flex min-w-0 flex-1 flex-col">
                    <span className="truncate cl-body font-semibold text-slate-900">{image.filename}</span>
                    {image.caption ? (
                      <span className="truncate cl-secondary text-slate-700">{image.caption}</span>
                    ) : null}
                    <span className="cl-meta text-slate-500">
                      {imageTypeLabel(image)} · {fileSizeLabel(image.file_size)} ·{" "}
                      {image.uploaded_by ?? "—"} · {formatHospitalDateTime(image.uploaded_at, "—")}
                    </span>
                  </div>
                  <div className="flex shrink-0 items-center gap-2">
                    <a
                      href={href}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="rounded border border-slate-300 bg-white px-3 py-1.5 cl-secondary font-bold text-slate-700 hover:bg-slate-50"
                    >
                      Open
                    </a>
                    {editable ? (
                      <button
                        type="button"
                        onClick={() => onAskRemove(image.id)}
                        disabled={isBusy || removeFor !== null}
                        className="rounded border border-red-300 bg-white px-3 py-1.5 cl-secondary font-bold text-red-800 hover:bg-red-50 disabled:opacity-50"
                      >
                        Remove
                      </button>
                    ) : null}
                  </div>
                </div>
                {editable && removeFor === image.id ? (
                  <div
                    role="alertdialog"
                    aria-label="Remove file"
                    className="flex flex-col gap-2 rounded border border-red-300 bg-red-50 px-3 py-2"
                  >
                    <p className="cl-body font-semibold text-slate-900">{imageRemoveConfirmText(image)}</p>
                    <p className="cl-secondary text-slate-600">{IMAGE_REMOVE_SUPPORT_TEXT}</p>
                    <div className="flex items-center gap-2">
                      <button
                        ref={removeCancelRef}
                        type="button"
                        onClick={onCancelRemove}
                        disabled={isBusy}
                        className="rounded border border-slate-300 bg-white px-3 py-1.5 cl-secondary font-bold text-slate-700 hover:bg-slate-50 disabled:opacity-50"
                      >
                        Cancel
                      </button>
                      <button
                        type="button"
                        onClick={onConfirmRemove}
                        disabled={isBusy}
                        className="rounded border border-red-700 bg-red-700 px-3 py-1.5 cl-secondary font-bold text-white hover:bg-red-800 disabled:opacity-50"
                      >
                        {busy === "remove" ? "Removing…" : "Remove file"}
                      </button>
                    </div>
                  </div>
                ) : null}
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}

/**
 * The two-step validate / release footer. Step one reviews and only moves on;
 * step two names what will happen and is the ONLY place the act is sent from.
 */
function SignoffStep({
  kind,
  request,
  result,
  step,
  busy,
  disabled,
  goBackRef,
  onAsk,
  onGoBack,
  onConfirm,
}: {
  kind: RadSignoffKind;
  request: RadRequestDetail;
  result: RadOperationalResult;
  step: "review" | "confirm";
  busy: boolean;
  disabled: boolean;
  goBackRef: React.RefObject<HTMLButtonElement | null>;
  onAsk: () => void;
  onGoBack: () => void;
  onConfirm: () => void;
}) {
  const validate = kind === "validate";
  if (step === "review") {
    return (
      <div className="flex flex-wrap items-center justify-between gap-2 rounded border border-teal-300 bg-teal-50 px-3 py-2">
        <p className="min-w-0 flex-1 cl-secondary text-slate-700">
          {validate ? VALIDATE_REVIEW_TEXT : RELEASE_REVIEW_TEXT}
        </p>
        <button
          type="button"
          onClick={onAsk}
          disabled={disabled}
          className="rounded border border-teal-700 bg-teal-700 px-3 py-1.5 cl-secondary font-bold text-white hover:bg-teal-800 disabled:opacity-50"
        >
          {validate ? "Validate report…" : "Release report…"}
        </button>
      </div>
    );
  }
  return (
    <div
      role="alertdialog"
      aria-label={validate ? "Confirm validation" : "Confirm release"}
      className="flex flex-col gap-2 rounded border border-teal-300 bg-teal-50 px-3 py-2"
    >
      <p className="cl-body font-semibold text-slate-900">
        {validate ? validateConfirmText(request, result) : releaseConfirmText(request, result)}
      </p>
      {validate ? null : (
        <p className="cl-secondary font-semibold text-slate-800">{releaseIdentityText(request)}</p>
      )}
      <p className="cl-secondary text-slate-600">
        {validate ? VALIDATE_SUPPORT_TEXT : releaseSupportText(request)}
      </p>
      <div className="flex items-center gap-2">
        <button
          ref={goBackRef}
          type="button"
          onClick={onGoBack}
          disabled={disabled}
          className="rounded border border-slate-300 bg-white px-3 py-1.5 cl-secondary font-bold text-slate-700 hover:bg-slate-50 disabled:opacity-50"
        >
          Go back
        </button>
        <button
          type="button"
          onClick={onConfirm}
          disabled={disabled}
          className="rounded border border-teal-700 bg-teal-700 px-3 py-1.5 cl-secondary font-bold text-white hover:bg-teal-800 disabled:opacity-50"
        >
          {busy ? "Working…" : validate ? "Confirm validation" : "Confirm release"}
        </button>
      </div>
    </div>
  );
}
