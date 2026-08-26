"use client";

import type { KeyboardEvent as ReactKeyboardEvent } from "react";
import { useCallback, useEffect, useRef, useState } from "react";

import {
  closeIntent,
  escapeIntent,
  isSaveShortcut,
  shouldCloseAfterSave,
} from "@/lib/note-editor-format";

/**
 * The focused writing surface for ONE consultation section.
 *
 * WHY A MODAL AND NOT A BIGGER TEXTAREA. Writing a history is a different act
 * from reviewing a note, and it wants the opposite layout: one thing on screen,
 * a wide measure, generous leading, and nothing competing for the eye. The desk
 * stays visible behind a dimmed backdrop so the doctor never loses their place,
 * and the modal is deliberately NOT full-screen -- a clinician mid-consultation
 * should still see the patient they are documenting.
 *
 * IT OWNS NO PERSISTENCE. `onSave` is the workspace's existing save path, with
 * its version token and its conflict handling untouched. This component holds a
 * local buffer, hands the text over, and closes only if the caller reports the
 * save succeeded. There is no second write path and no autosave: a save can be
 * REFUSED by the version check, and a modal that closed on a refused write
 * would tell the doctor their paragraph was stored when it was not.
 *
 * CLINICAL TEXT IS NEVER DISCARDED SILENTLY. Cancel, the X and Escape all take
 * the same route: unchanged closes immediately, changed asks first. The
 * confirmation is inline rather than a second dialog, so it cannot end up
 * behind this one.
 */

export type NoteEditorModalProps = {
  /** Section title, e.g. "History of Presenting Illness". */
  label: string;
  hint: string;
  /** The draft text this section currently holds. */
  value: string;
  /** Patient/visit context line. Identity only -- never clinical content. */
  context?: string | null;
  readOnly: boolean;
  /** A save is in flight, or the note is reloading. */
  busy: boolean;
  /** The workspace's save error / conflict message, shown verbatim. */
  errorMessage?: string | null;
  /** Push a keystroke into the workspace draft. */
  onChange: (value: string) => void;
  /** The existing save path. Resolves true only when the write landed. */
  onSave: () => Promise<boolean>;
  onClose: () => void;
};

export default function NoteEditorModal({
  label,
  hint,
  value,
  context,
  readOnly,
  busy,
  errorMessage,
  onChange,
  onSave,
  onClose,
}: NoteEditorModalProps) {
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const closeRef = useRef<HTMLButtonElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);
  /*
    The text as it stood when this modal opened, captured ONCE by the state
    initializer and never updated. Comparing the live value against it is what
    decides whether closing needs a confirmation.

    State rather than a ref because this is read during render, and a ref read
    during render is not safe under concurrent rendering. The initializer runs
    on mount only, and the parent keys this component on the section, so
    switching sections remounts it and re-captures correctly.
  */
  const [openedWith] = useState(value);
  const [confirmDiscard, setConfirmDiscard] = useState(false);

  const changed = value !== openedWith;

  /* ---------------- focus ---------------- */
  useEffect(() => {
    // A read-only viewer focuses its close button instead: autofocusing a
    // disabled textarea would leave the dialog with no focused control at all.
    const target = readOnly ? closeRef.current : textareaRef.current;
    target?.focus();
    if (!readOnly && textareaRef.current) {
      // Caret at the END of existing text, so continuing a paragraph does not
      // require a click first.
      const end = textareaRef.current.value.length;
      textareaRef.current.setSelectionRange(end, end);
    }
  }, [readOnly]);

  /* ---------------- close paths ---------------- */
  const requestClose = useCallback(() => {
    // The rule itself lives in note-editor-format, where it is tested.
    const intent = closeIntent({ readOnly, changed, busy });
    if (intent === "blocked") return;
    if (intent === "close") {
      onClose();
      return;
    }
    setConfirmDiscard(true);
  }, [busy, changed, onClose, readOnly]);

  const save = useCallback(async () => {
    if (readOnly || busy) return;
    // CLOSE ONLY ON SUCCESS. A refused save leaves the modal open with the
    // doctor's text intact and the server's own sentence above it.
    const ok = await onSave();
    if (shouldCloseAfterSave(ok)) onClose();
  }, [busy, onClose, onSave, readOnly]);

  /* ---------------- keyboard ---------------- */
  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") {
        event.stopPropagation();
        const intent = escapeIntent({
          readOnly,
          changed,
          busy,
          confirmingDiscard: confirmDiscard,
        });
        // Escape out of the discard prompt returns to the editor rather than
        // leaving: the doctor answered "no" to discarding.
        if (intent === "dismiss-confirm") {
          setConfirmDiscard(false);
          return;
        }
        requestClose();
        return;
      }

      /*
        Ctrl/Cmd+Enter saves. Bare Enter is left alone: it is the newline key
        in a clinical narrative, and stealing it would make paragraphs
        impossible to write.
      */
      if (
        isSaveShortcut(event, { readOnly, confirmingDiscard: confirmDiscard })
      ) {
        event.preventDefault();
        void save();
      }
    }

    document.addEventListener("keydown", onKeyDown, true);
    return () => document.removeEventListener("keydown", onKeyDown, true);
  }, [busy, changed, confirmDiscard, readOnly, requestClose, save]);

  /*
    A minimal focus trap: Tab cycling is left to the browser, but focus is
    prevented from escaping the panel entirely. Deliberately not a full
    focus-management library -- this dialog has four controls.
  */
  const onKeyDownPanel = useCallback((event: ReactKeyboardEvent) => {
    if (event.key !== "Tab") return;
    const panel = panelRef.current;
    if (!panel) return;
    const focusable = panel.querySelectorAll<HTMLElement>(
      'button:not([disabled]), textarea:not([disabled]), [href], input:not([disabled])',
    );
    if (!focusable.length) return;
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

  const titleId = "note-editor-title";
  const hintId = "note-editor-hint";

  return (
    <div
      className="fixed inset-0 z-[100] flex items-center justify-center p-4"
      /* The backdrop is a sibling concern: clicking it takes the same guarded
         route as Cancel, so a stray click cannot discard a paragraph. */
    >
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
        aria-describedby={hintId}
        onKeyDown={onKeyDownPanel}
        /*
          SIZED FOR WRITING, NOT FOR TAKING OVER THE SCREEN. A fixed comfortable
          measure on a wide monitor, and vw/vh caps so it never overflows a
          narrow window -- the body scrolls internally instead.
        */
        className="relative flex max-h-[85vh] h-[70vh] w-[820px] max-w-[92vw] flex-col overflow-hidden rounded-xl border border-slate-300 bg-white shadow-[0_20px_50px_rgba(15,23,42,0.28)]"
      >
        {/* ---- Header ---- */}
        <header className="flex shrink-0 items-start gap-3 border-b border-slate-200 bg-slate-50 px-4 py-3">
          <div className="min-w-0 flex-1">
            <div className="flex min-w-0 items-center gap-2">
              <h2
                id={titleId}
                className="min-w-0 truncate cl-head font-semibold text-slate-900"
              >
                {label}
              </h2>
              {readOnly ? (
                <span className="shrink-0 rounded border border-slate-400 bg-white px-1.5 py-px cl-micro font-bold uppercase tracking-wide text-slate-700">
                  Read only
                </span>
              ) : changed ? (
                <span className="inline-flex shrink-0 items-center gap-1 cl-micro font-bold uppercase tracking-wide text-amber-700">
                  <span aria-hidden className="h-1.5 w-1.5 rounded-full bg-amber-500" />
                  Unsaved
                </span>
              ) : null}
            </div>
            <p id={hintId} className="mt-0.5 truncate cl-meta text-slate-600">
              {hint}
              {context ? ` · ${context}` : ""}
            </p>
          </div>

          <button
            ref={closeRef}
            type="button"
            onClick={requestClose}
            disabled={busy}
            aria-label="Close editor"
            className="shrink-0 rounded-md border border-slate-300 bg-white px-2 py-1 cl-body font-semibold leading-none text-slate-600 outline-none transition-colors hover:border-slate-400 hover:text-slate-900 focus-visible:ring-2 focus-visible:ring-emerald-600 disabled:opacity-50"
          >
            ✕
          </button>
        </header>

        {/* ---- Writing surface ---- */}
        <div className="min-h-0 flex-1 overflow-y-auto p-4">
          <textarea
            ref={textareaRef}
            value={value}
            readOnly={readOnly}
            disabled={busy && !readOnly}
            spellCheck
            onChange={(event) => onChange(event.target.value)}
            placeholder={readOnly ? undefined : hint}
            /*
              NO BOX, ON PURPOSE. The border and the small padding of an
              ordinary form field make a paragraph feel like a value being
              entered; a page-like surface makes it feel like a note being
              written. The ring appears on focus so the surface is still
              obviously an input.
            */
            className="h-full min-h-[220px] w-full resize-none rounded-lg border border-transparent bg-white px-4 py-3 cl-prose text-slate-900 caret-emerald-700 outline-none transition-colors placeholder:text-slate-400 focus-visible:border-emerald-200 focus-visible:bg-emerald-50/20 focus-visible:ring-2 focus-visible:ring-emerald-600/25 read-only:cursor-default read-only:text-slate-700 disabled:cursor-not-allowed"
          />
        </div>

        {/* ---- Footer ---- */}
        <footer className="shrink-0 border-t border-slate-200 bg-slate-50 px-4 py-2.5">
          {errorMessage ? (
            <p
              role="alert"
              className="mb-2 rounded-md border border-red-300 bg-white px-2.5 py-1.5 cl-secondary leading-snug text-red-900"
            >
              {errorMessage}
            </p>
          ) : null}

          {confirmDiscard ? (
            <div className="flex flex-wrap items-center justify-between gap-2">
              <p className="cl-secondary font-semibold leading-snug text-amber-900">
                This section has unsaved changes. Discard them?
              </p>
              <div className="flex shrink-0 items-center gap-2">
                <button
                  type="button"
                  onClick={() => setConfirmDiscard(false)}
                  className="rounded-md border border-slate-300 bg-white px-3 py-1.5 cl-secondary font-semibold text-slate-700 outline-none transition-colors hover:bg-slate-100 focus-visible:ring-2 focus-visible:ring-emerald-600"
                >
                  Keep writing
                </button>
                <button
                  type="button"
                  onClick={onClose}
                  className="rounded-md border border-red-400 bg-white px-3 py-1.5 cl-secondary font-semibold text-red-800 outline-none transition-colors hover:bg-red-50 focus-visible:ring-2 focus-visible:ring-red-600"
                >
                  Discard changes
                </button>
              </div>
            </div>
          ) : (
            <div className="flex flex-wrap items-center justify-end gap-2">
              {!readOnly ? (
                <span className="mr-auto hidden cl-meta text-slate-500 sm:inline">
                  Ctrl+Enter saves
                </span>
              ) : null}
              <button
                type="button"
                onClick={requestClose}
                disabled={busy}
                className="rounded-md border border-slate-300 bg-white px-3.5 py-1.5 cl-body font-semibold text-slate-700 outline-none transition-colors hover:bg-slate-100 focus-visible:ring-2 focus-visible:ring-emerald-600 disabled:opacity-50"
              >
                {readOnly ? "Close" : "Cancel"}
              </button>
              {!readOnly ? (
                <button
                  type="button"
                  onClick={() => void save()}
                  disabled={busy}
                  className="rounded-md bg-emerald-700 px-4 py-1.5 cl-body font-semibold text-white shadow-sm outline-none transition-colors hover:bg-emerald-800 focus-visible:ring-2 focus-visible:ring-emerald-700 disabled:bg-slate-300 disabled:text-slate-600"
                >
                  {busy ? "Saving…" : "Save & close"}
                </button>
              ) : null}
            </div>
          )}
        </footer>
      </div>
    </div>
  );
}
