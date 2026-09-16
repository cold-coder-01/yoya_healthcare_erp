"use client";

import { formatHospitalDate, formatHospitalDateTime } from "@/lib/clinical-format";
import {
  ageSexLabel,
  canCollect,
  clearanceNotice,
  orDash,
  testCountLabel,
} from "@/lib/lab-desk-format";
import type { LabRequestDetail } from "@/types/lab-desk";

import { LabPriorityPill, LabStatusPill } from "./lab-status-pill";

/**
 * The request detail panel.
 *
 * ONE ACTION, AND ONLY ONE: Collect sample, offered for exactly one status.
 * Start Processing, result entry, validation and release are later slices and
 * have no control here -- not even a disabled one. A greyed-out button is a
 * promise about workflow that has not shipped, and the first thing a
 * technician does with one is click it.
 *
 * THE BUTTON IS AN AFFORDANCE, NOT A PERMISSION. It renders from the SERVER'S
 * derived status (`ready_for_collection`), never from a financial value, and
 * the server re-runs both gates -- clearance and the state machine -- inside
 * action_mark_sample_collected() on every call. A button rendered from stale
 * data still gets a clean refusal, which is why the refusal path below
 * reconciles rather than trusting the screen.
 *
 * NOTHING PRICED APPEARS HERE. For a request that is not financially cleared
 * the panel says the patient settles it at the cashier, and names no figure,
 * no payer and no invoice -- because none of that reaches the browser.
 * `billing_blocked` is the only billing-derived value in the payload, and it
 * is a boolean.
 */

function Field({
  label,
  value,
  mono = false,
}: {
  label: string;
  value: string;
  mono?: boolean;
}) {
  return (
    <div className="flex min-w-0 flex-col gap-0.5">
      <dt className="cl-micro font-bold uppercase tracking-[0.06em] text-slate-400">
        {label}
      </dt>
      <dd
        className={`truncate cl-secondary font-semibold text-slate-800 ${
          mono ? "font-mono" : ""
        }`}
      >
        {value}
      </dd>
    </div>
  );
}

function ClearanceBanner({ status }: { status: string }) {
  const notice = clearanceNotice(status);
  if (!notice) return null;

  const blocked = status === "awaiting_clearance";
  return (
    <div
      className={`flex items-start gap-2 rounded border px-3 py-2 cl-secondary ${
        blocked
          ? "border-amber-300 bg-amber-50 text-amber-900"
          : "border-indigo-200 bg-indigo-50 text-indigo-900"
      }`}
    >
      {/* Text carries the meaning; the tone only reinforces it. */}
      <span aria-hidden className="mt-px font-bold">
        {blocked ? "!" : "✓"}
      </span>
      <p className="font-semibold">{notice}</p>
    </div>
  );
}

export default function LabRequestPanel({
  detail,
  loading,
  error,
  empty,
  onCollect,
  collecting,
  collectError,
}: {
  detail: LabRequestDetail | null;
  loading: boolean;
  error: string | null;
  /** No request is selected because the queue itself is empty. */
  empty: boolean;
  /** Runs the collection through the BFF. The panel performs no fetch itself. */
  onCollect: () => void;
  /** A collection is in flight for THIS request. */
  collecting: boolean;
  /** The server's own refusal sentence, already sanitised by the Lab API. */
  collectError: string | null;
}) {
  if (error) {
    return (
      <section className="flex min-h-[340px] flex-col overflow-hidden rounded-lg border border-slate-200 bg-white shadow-sm min-[1100px]:min-h-0">
        <div className="m-3 rounded border border-red-200 bg-red-50 px-3 py-2 cl-body text-red-800">
          {error}
        </div>
      </section>
    );
  }

  if (!detail) {
    return (
      <section className="flex min-h-[340px] flex-col items-center justify-center gap-1 overflow-hidden rounded-lg border border-slate-200 bg-white px-6 text-center shadow-sm min-[1100px]:min-h-0">
        <p className="cl-body font-semibold text-slate-600">
          {loading
            ? "Loading request…"
            : empty
              ? "Nothing to show"
              : "Select a request"}
        </p>
        <p className="cl-secondary text-slate-500">
          {empty
            ? "There is no laboratory work in this lane."
            : "Choose a request from the bench queue."}
        </p>
      </section>
    );
  }

  const patient = detail.patient;

  return (
    <section className="flex min-h-[340px] min-w-0 flex-col overflow-hidden rounded-lg border border-slate-200 bg-white shadow-sm min-[1100px]:min-h-0">
      {/*
        PATIENT IDENTITY IS PERSISTENTLY VISIBLE, pinned above the scroll area.
        A specimen is labelled from this header, so it must never scroll out of
        sight while the technician reads the ordered tests below it.
      */}
      <header className="shrink-0 border-b border-slate-200 bg-slate-50 px-3 py-2">
        <div className="flex flex-wrap items-start justify-between gap-2">
          <div className="flex min-w-0 flex-col gap-0.5">
            <h2 className="truncate text-[15px] font-bold leading-tight text-slate-900">
              {patient?.name ?? "—"}
            </h2>
            <p className="flex flex-wrap items-center gap-1.5 cl-meta text-slate-500">
              <span className="font-mono font-semibold text-slate-700">
                {patient?.mrn ?? "—"}
              </span>
              <span aria-hidden className="text-slate-300">
                ·
              </span>
              <span className="tabular-nums">{ageSexLabel(patient)}</span>
              <span aria-hidden className="text-slate-300">
                ·
              </span>
              <span className="font-mono">{detail.request_code}</span>
            </p>
          </div>
          <div className="flex shrink-0 items-center gap-1.5">
            <LabPriorityPill
              priority={detail.priority}
              label={detail.priority_label}
            />
            <LabStatusPill status={detail.status} label={detail.status_label} />
          </div>
        </div>
        {loading ? (
          <p className="mt-1 cl-meta text-slate-500">Updating…</p>
        ) : null}
      </header>

      <div className="min-h-0 flex-1 space-y-3 overflow-y-auto p-3">
        <ClearanceBanner status={detail.status} />

        {/*
          THE COLLECT ACTION. Rendered for exactly one status and no other, so
          `awaiting_clearance`, draft, sample_collected, in_progress, completed
          and cancelled all show nothing here -- not a disabled control.
        */}
        {canCollect(detail.status) ? (
          <div className="flex flex-wrap items-center gap-2">
            <button
              type="button"
              onClick={onCollect}
              /*
                DISABLED THE MOMENT IT IS PRESSED. The state machine is the real
                protection -- a replayed POST is refused because the request is
                no longer `requested` -- but disabling stops the technician
                firing a second call that can only come back as an error they
                then have to interpret.
              */
              disabled={collecting}
              aria-busy={collecting}
              className="inline-flex h-9 items-center gap-2 rounded-md bg-indigo-700 px-3.5 cl-body font-semibold text-white shadow-sm transition hover:bg-indigo-800 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-indigo-700 disabled:cursor-not-allowed disabled:bg-slate-400"
            >
              {collecting ? "Collecting…" : "Collect sample"}
            </button>
            <span className="cl-meta text-slate-500">
              {collecting
                ? "Marking the sample collected…"
                : "Marks the sample collected for every ordered test on this request."}
            </span>
          </div>
        ) : null}

        {/*
          The refusal. The server's own sentence is shown because it is the only
          thing that says WHY -- and the Laboratory API has already replaced the
          one refusal whose wording could carry an amount with a fixed,
          role-independent message.
        */}
        {collectError ? (
          <div
            role="alert"
            className="flex items-start gap-2 rounded border border-red-200 bg-red-50 px-3 py-2 cl-secondary text-red-800"
          >
            <span aria-hidden className="mt-px font-bold">
              !
            </span>
            <p className="whitespace-pre-wrap font-semibold">{collectError}</p>
          </div>
        ) : null}

        <dl className="grid grid-cols-2 gap-x-4 gap-y-2.5 min-[720px]:grid-cols-3">
          <Field
            label="Ordered by"
            value={orDash(detail.ordering_physician?.name)}
          />
          <Field label="Department" value={orDash(detail.department?.name)} />
          {/* The visit, reached through the ENCOUNTER. A lab technician holds
              no ACL on hospital.appointment, so the encounter is the anchor. */}
          <Field label="Encounter" value={orDash(detail.encounter_code)} mono />
          <Field
            label="Ordered on"
            value={formatHospitalDate(detail.request_date, "—")}
          />
          <Field
            label="Received"
            value={formatHospitalDateTime(detail.created_at, "—")}
          />
          <Field label="Workflow state" value={orDash(detail.state)} mono />
        </dl>

        {detail.clinical_notes || detail.instructions ? (
          <div className="space-y-2 rounded border border-slate-200 bg-slate-50 px-3 py-2">
            {detail.clinical_notes ? (
              <div>
                <h3 className="cl-micro font-bold uppercase tracking-[0.06em] text-slate-400">
                  Clinical indication
                </h3>
                <p className="mt-0.5 whitespace-pre-wrap cl-secondary text-slate-800">
                  {detail.clinical_notes}
                </p>
              </div>
            ) : null}
            {detail.instructions ? (
              <div>
                <h3 className="cl-micro font-bold uppercase tracking-[0.06em] text-slate-400">
                  Instructions to the laboratory
                </h3>
                <p className="mt-0.5 whitespace-pre-wrap cl-secondary text-slate-800">
                  {detail.instructions}
                </p>
              </div>
            ) : null}
          </div>
        ) : null}

        <div className="overflow-hidden rounded border border-slate-200">
          <div className="flex items-baseline justify-between border-b border-slate-200 bg-slate-50 px-3 py-1.5">
            <h3 className="cl-secondary font-bold uppercase tracking-[0.08em] text-slate-700">
              Ordered tests
            </h3>
            <span className="cl-meta tabular-nums text-slate-500">
              {testCountLabel(detail.test_count)}
            </span>
          </div>

          {detail.tests.length === 0 ? (
            <p className="px-3 py-3 cl-secondary text-slate-500">
              No tests are recorded on this request.
            </p>
          ) : (
            /* Wide content scrolls inside its own container so the panel body
               never scrolls horizontally. */
            <div className="overflow-x-auto">
              <table className="w-full min-w-[420px] border-collapse">
                <thead>
                  <tr className="border-b border-slate-200 bg-white text-left cl-micro font-bold uppercase tracking-[0.06em] text-slate-400">
                    <th className="px-3 py-1.5">Test</th>
                    <th className="px-3 py-1.5">Code</th>
                    <th className="px-3 py-1.5">Sample</th>
                    <th className="px-3 py-1.5">Instruction</th>
                  </tr>
                </thead>
                <tbody>
                  {detail.tests.map((test) => (
                    <tr
                      key={test.id}
                      className="border-b border-slate-100 last:border-b-0"
                    >
                      <td className="px-3 py-1.5 cl-secondary font-semibold text-slate-900">
                        {test.name}
                      </td>
                      <td className="px-3 py-1.5 font-mono cl-meta text-slate-600">
                        {orDash(test.code)}
                      </td>
                      <td className="px-3 py-1.5 cl-secondary capitalize text-slate-700">
                        {orDash(test.sample_type)}
                      </td>
                      <td className="px-3 py-1.5 cl-secondary text-slate-600">
                        {orDash(test.special_instruction)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>

        {detail.result_count > 0 ? (
          <p className="cl-secondary text-slate-600">
            <span className="font-semibold tabular-nums">
              {detail.result_count}
            </span>{" "}
            result {detail.result_count === 1 ? "record" : "records"} on this
            request
            {detail.released_count > 0
              ? `, ${detail.released_count} released`
              : ""}
            .
          </p>
        ) : null}
      </div>

      {/*
        The scope marker. Stated once, plainly, instead of rendering disabled
        buttons for workflow that has not shipped.
      */}
      <footer className="flex h-7 shrink-0 items-center gap-2 border-t border-slate-200 bg-slate-50 px-3 cl-meta text-slate-500">
        Sample collection only. Result entry, validation and release are not
        available yet.
      </footer>
    </section>
  );
}
