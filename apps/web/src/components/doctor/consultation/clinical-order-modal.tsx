"use client";

import type { KeyboardEvent as ReactKeyboardEvent, ReactNode, RefObject } from "react";
import { useCallback, useEffect, useId, useRef, useState } from "react";

import {
  closeIntent,
  escapeIntent,
  isSaveShortcut,
} from "@/lib/note-editor-format";

/**
 * THE ONE ORDER-EDITOR SHELL, shared by every kind of clinical order.
 *
 * WHY IT IS SHARED. Laboratory, radiology and medication all now open a
 * centred editor over a still-visible Doctor Desk, and the parts that must not
 * differ between them are exactly the parts that are easy to get subtly wrong:
 * where focus lands, whether Tab can escape the dialog, what Escape does to
 * unsaved clinical text, whether a backdrop click discards silently. Four
 * copies of that would be four chances for one order type to lose a doctor's
 * typing in a way the others do not.
 *
 * WHAT IS NOT SHARED. Everything clinical. Each kind supplies its own fields as
 * children and its own validation before calling onSave, because a lab request,
 * an imaging request and a prescription line carry genuinely different
 * information and a parameterised form would need a union type at every input.
 *
 * THE UNSAVED-WORK RULES ARE NOT RESTATED HERE. closeIntent, escapeIntent and
 * isSaveShortcut are the same pure functions the focused note editor has used
 * since Slice 4, so the keyboard and the buttons cannot disagree about whether
 * clinical text is safe, and the rules are pinned by note-editor-format.test.
 *
 * READ-ONLY IS A STATE, NOT A SEPARATE COMPONENT. A completed consultation
 * closes these editors and clears the drafts behind them, so this mode is the
 * safety net for the frame in which that happens: the strip says READ ONLY, the
 * save action disappears, and closing never asks about work that can no longer
 * be sent.
 */

export type ClinicalOrderModalProps = {
  /** The dialog's accessible name -- the item being ordered, in the doctor's words. */
  title: string;
  /** Catalogue context under the title: form, strength, modality, code. */
  subtitle?: string | null;
  /** True when the editor holds changes the doctor has not committed. */
  changed: boolean;
  /** Blocks every mutating affordance. Closing is always allowed. */
  readOnly?: boolean;
  /** Disables the primary action without hiding it -- nothing valid to save yet. */
  saveDisabled?: boolean;
  /** Text of the primary action. "Add Medicine", "Save Changes", "Add to order". */
  saveLabel: string;
  /** The Ctrl+Enter reminder, phrased for this editor's primary action. */
  shortcutHint: string;
  /** The sentence the discard prompt asks. */
  discardPrompt: string;
  /** aria-label for the X. */
  closeLabel: string;
  /** Focused on open: the first field worth typing into. */
  initialFocusRef: RefObject<HTMLElement | null>;
  /** The result row or Edit button that opened this. Focus returns to it. */
  returnFocus: HTMLElement | null;
  onSave: () => void;
  onClose: () => void;
  children: ReactNode;
};

export default function ClinicalOrderModal({
  title,
  subtitle,
  changed,
  readOnly = false,
  saveDisabled = false,
  saveLabel,
  shortcutHint,
  discardPrompt,
  closeLabel,
  initialFocusRef,
  returnFocus,
  onSave,
  onClose,
  children,
}: ClinicalOrderModalProps) {
  const [confirmDiscard, setConfirmDiscard] = useState(false);
  const panelRef = useRef<HTMLDivElement>(null);
  const baseId = useId();
  const titleId = `${baseId}-title`;
  const subtitleId = `${baseId}-subtitle`;

  /*
    Focus in on open, focus back on close. The cleanup runs whether the editor
    was saved, cancelled or dismissed, so a keyboard user always resumes at the
    search hit or the Edit button they came from rather than at the top of the
    document.
  */
  useEffect(() => {
    initialFocusRef.current?.focus();
    return () => returnFocus?.focus();
  }, [initialFocusRef, returnFocus]);

  const requestClose = useCallback(() => {
    const intent = closeIntent({ readOnly, changed, busy: false });
    if (intent === "close") onClose();
    else if (intent === "confirm") setConfirmDiscard(true);
  }, [changed, onClose, readOnly]);

  const save = useCallback(() => {
    if (readOnly || saveDisabled) return;
    onSave();
  }, [onSave, readOnly, saveDisabled]);

  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") {
        // Captured and stopped: an Escape meant for this dialog must not also
        // reach a picker or a section behind it.
        event.preventDefault();
        event.stopPropagation();
        const intent = escapeIntent({
          readOnly,
          changed,
          busy: false,
          confirmingDiscard: confirmDiscard,
        });
        if (intent === "dismiss-confirm") setConfirmDiscard(false);
        else requestClose();
        return;
      }
      if (isSaveShortcut(event, { readOnly, confirmingDiscard: confirmDiscard })) {
        event.preventDefault();
        save();
      }
    }
    document.addEventListener("keydown", onKeyDown, true);
    return () => document.removeEventListener("keydown", onKeyDown, true);
  }, [changed, confirmDiscard, readOnly, requestClose, save]);

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

  return (
    <div className="fixed inset-0 z-[110] flex items-center justify-center p-4">
      {/* Dimmed, not opaque: the Doctor Desk stays identifiable underneath, so
          the editor reads as a step in the consultation rather than a screen
          the doctor has navigated to. */}
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
        aria-describedby={subtitle ? subtitleId : undefined}
        onKeyDown={trapFocus}
        className="relative flex max-h-[85vh] w-[830px] max-w-[92vw] flex-col overflow-hidden rounded-xl border border-slate-300 bg-white shadow-[0_20px_50px_rgba(15,23,42,0.28)]"
      >
        <header className="flex shrink-0 items-start gap-3 border-b border-slate-200 bg-slate-50 px-5 py-3.5">
          <div className="min-w-0 flex-1">
            <div className="flex min-w-0 items-center gap-2">
              <h2
                id={titleId}
                className="min-w-0 truncate cl-head font-semibold text-slate-900"
              >
                {title}
              </h2>
              {readOnly ? (
                <span className="inline-flex shrink-0 items-center rounded border border-red-300 bg-white px-1.5 py-px cl-micro font-bold uppercase tracking-wide text-red-800">
                  Read only
                </span>
              ) : changed ? (
                <span className="inline-flex shrink-0 items-center gap-1 cl-micro font-bold uppercase tracking-wide text-amber-700">
                  <span
                    aria-hidden
                    className="h-1.5 w-1.5 rounded-full bg-amber-500"
                  />
                  Unsaved
                </span>
              ) : null}
            </div>
            {subtitle ? (
              <p
                id={subtitleId}
                className="mt-0.5 truncate cl-secondary text-slate-600"
              >
                {subtitle}
              </p>
            ) : null}
          </div>
          <button
            type="button"
            onClick={requestClose}
            aria-label={closeLabel}
            className="shrink-0 rounded-md border border-slate-300 bg-white px-2 py-1 cl-body font-semibold leading-none text-slate-600 outline-none hover:border-slate-400 hover:text-slate-900 focus-visible:ring-2 focus-visible:ring-emerald-600"
          >
            x
          </button>
        </header>

        <div className="min-h-0 overflow-y-auto px-5 py-4">{children}</div>

        <footer className="shrink-0 border-t border-slate-200 bg-slate-50 px-5 py-3">
          {confirmDiscard ? (
            <div className="flex flex-wrap items-center justify-between gap-2">
              <p className="cl-secondary font-semibold text-amber-900">
                {discardPrompt}
              </p>
              <div className="flex items-center gap-2">
                <button
                  type="button"
                  onClick={() => setConfirmDiscard(false)}
                  className="rounded-md border border-slate-300 bg-white px-3 py-1.5 cl-body font-semibold text-slate-700 outline-none hover:bg-slate-100 focus-visible:ring-2 focus-visible:ring-emerald-600"
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
              {readOnly ? null : (
                <span className="mr-auto hidden cl-meta text-slate-500 sm:inline">
                  {shortcutHint}
                </span>
              )}
              <button
                type="button"
                onClick={requestClose}
                className="rounded-md border border-slate-300 bg-white px-3.5 py-1.5 cl-body font-semibold text-slate-700 outline-none hover:bg-slate-100 focus-visible:ring-2 focus-visible:ring-emerald-600"
              >
                {readOnly ? "Close" : "Cancel"}
              </button>
              {readOnly ? null : (
                <button
                  type="button"
                  onClick={save}
                  disabled={saveDisabled}
                  className="rounded-md bg-emerald-700 px-4 py-1.5 cl-body font-semibold text-white shadow-sm outline-none hover:bg-emerald-800 focus-visible:ring-2 focus-visible:ring-emerald-700 disabled:bg-slate-300 disabled:text-slate-600 disabled:shadow-none"
                >
                  {saveLabel}
                </button>
              )}
            </div>
          )}
        </footer>
      </div>
    </div>
  );
}
