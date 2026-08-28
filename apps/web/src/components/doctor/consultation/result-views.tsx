"use client";

import {
  abnormalFlagText,
  abnormalTone,
  abnormalToneClass,
  clinicalValue,
  isDocumented,
  reportPreview,
} from "@/lib/results-format";
import type { LaboratoryReview, RadiologyReview } from "@/types/doctor-results";

/**
 * The two documents a released result can be, rendered for the viewer canvas.
 *
 * THESE ARE BODIES, NOT MODALS. The centered shell, the focus trap, the close
 * routes and the read-only treatment all belong to
 * ClinicalResultViewerModal; these components only know how to lay out a
 * laboratory panel and a radiology report. That split is what lets the two
 * documents look like themselves without either growing its own dialog.
 *
 * THEY RECEIVE DATA AND NOTHING ELSE. No fetch, no state, no callbacks that
 * write. Everything here was already loaded by the Results workspace when the
 * worklist rendered, so opening a viewer costs a state change and no request.
 *
 * NOTHING IS DERIVED. Values, units and reference ranges are printed exactly as
 * the laboratory recorded them, and abnormality is the laboratory's own flag.
 * A component that recomputed either would be a second clinical opinion with no
 * clinician behind it.
 */

/** A section heading inside the canvas. Quiet, and consistent between bodies. */
function ViewerSection({
  title,
  children,
}: {
  title: string;
  children: React.ReactNode;
}) {
  return (
    <section className="border-t border-slate-200 pt-3 first:border-0 first:pt-0">
      <h3 className="cl-micro font-bold uppercase tracking-[0.07em] text-slate-400">
        {title}
      </h3>
      <div className="mt-1">{children}</div>
    </section>
  );
}

/* ------------------------------------------------------------------ *
 * Laboratory
 * ------------------------------------------------------------------ */

export function LaboratoryResultView({ row }: { row: LaboratoryReview }) {
  const result = row.result;
  if (!result) return null;
  const noted = result.lines.filter((line) => isDocumented(line.notes));

  return (
    <div className="flex flex-col gap-3">
      {isDocumented(row.clinical_indication) ? (
        <ViewerSection title="Clinical indication">
          <p className="whitespace-pre-wrap break-words cl-secondary leading-relaxed text-slate-700">
            {row.clinical_indication}
          </p>
        </ViewerSection>
      ) : null}

      <ViewerSection title="Results">
        {/* Wide content scrolls inside its own container, so the dialog never
            scrolls horizontally. */}
        <div className="overflow-x-auto">
          <table className="w-full min-w-[560px] border-collapse cl-secondary">
            <thead>
              <tr className="border-b border-slate-300 text-left">
                <th scope="col" className="py-1.5 pr-3 cl-micro font-bold uppercase tracking-wide text-slate-500">
                  Test
                </th>
                <th scope="col" className="py-1.5 pr-3 cl-micro font-bold uppercase tracking-wide text-slate-500">
                  Result
                </th>
                <th scope="col" className="py-1.5 pr-3 cl-micro font-bold uppercase tracking-wide text-slate-500">
                  Unit
                </th>
                <th scope="col" className="py-1.5 pr-3 cl-micro font-bold uppercase tracking-wide text-slate-500">
                  Reference
                </th>
                <th scope="col" className="py-1.5 cl-micro font-bold uppercase tracking-wide text-slate-500">
                  Flag
                </th>
              </tr>
            </thead>
            <tbody>
              {result.lines.map((line) => {
                const tone = abnormalTone(line.abnormal_flag);
                const word = abnormalFlagText(
                  line.abnormal_flag,
                  line.abnormal_flag_label,
                );
                return (
                  <tr
                    key={line.id}
                    className="border-b border-slate-100 align-top last:border-0"
                  >
                    <th
                      scope="row"
                      className="py-2 pr-3 text-left font-semibold text-slate-800"
                    >
                      {line.name}
                      {line.code ? (
                        <span className="ml-1 font-mono cl-micro font-normal text-slate-400">
                          {line.code}
                        </span>
                      ) : null}
                      {/* The linkage the server refused to guess at. Stated,
                          because a value whose ordered test is uncertain is
                          still a value the doctor should see. */}
                      {!line.ordered ? (
                        <span className="ml-1 cl-micro font-normal italic text-slate-400">
                          unmatched order line
                        </span>
                      ) : null}
                    </th>
                    <td
                      className={`py-2 pr-3 font-semibold tabular-nums ${
                        tone === "critical"
                          ? "text-red-900"
                          : tone === "warn"
                            ? "text-amber-900"
                            : "text-slate-900"
                      }`}
                    >
                      {clinicalValue(line.value)}
                    </td>
                    <td className="py-2 pr-3 text-slate-600">
                      {clinicalValue(line.unit)}
                    </td>
                    <td className="py-2 pr-3 text-slate-600">
                      {clinicalValue(line.reference_range)}
                    </td>
                    <td className="py-2">
                      {/* THE WORD, ALWAYS. Never colour alone. */}
                      {word && tone !== "neutral" ? (
                        <span
                          className={`inline-flex rounded border px-1.5 py-px cl-micro font-bold uppercase tracking-wide ${abnormalToneClass(tone)}`}
                        >
                          {word}
                        </span>
                      ) : word ? (
                        /* A normal row states its judgement without a chip: a
                           table where every row is boxed makes the rows that
                           matter harder, not easier, to find. */
                        <span className="cl-micro text-slate-500">{word}</span>
                      ) : (
                        <span className="cl-micro text-slate-400">—</span>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </ViewerSection>

      {isDocumented(result.interpretation) ? (
        <ViewerSection title="Interpretation">
          <p className="whitespace-pre-wrap break-words cl-prose leading-relaxed text-slate-800">
            {result.interpretation}
          </p>
        </ViewerSection>
      ) : null}

      {isDocumented(result.remarks) ? (
        <ViewerSection title="Remarks">
          <p className="whitespace-pre-wrap break-words cl-secondary leading-relaxed text-slate-700">
            {result.remarks}
          </p>
        </ViewerSection>
      ) : null}

      {noted.length ? (
        <ViewerSection title="Line notes">
          <ul className="space-y-1">
            {noted.map((line) => (
              <li
                key={line.id}
                className="cl-secondary leading-relaxed text-slate-700"
              >
                <span className="font-semibold text-slate-800">
                  {line.name}:{" "}
                </span>
                {line.notes}
              </li>
            ))}
          </ul>
        </ViewerSection>
      ) : null}
    </div>
  );
}

/* ------------------------------------------------------------------ *
 * Radiology
 * ------------------------------------------------------------------ */

export function RadiologyResultView({ row }: { row: RadiologyReview }) {
  const result = row.result;
  if (!result) return null;
  const detailed = result.exams.filter(
    (exam) => isDocumented(exam.summary) || isDocumented(exam.notes),
  );

  return (
    <div className="flex flex-col gap-3">
      {isDocumented(row.clinical_indication) ? (
        <ViewerSection title="Clinical indication">
          <p className="whitespace-pre-wrap break-words cl-secondary leading-relaxed text-slate-700">
            {row.clinical_indication}
          </p>
        </ViewerSection>
      ) : null}

      {result.has_report ? (
        <>
          {/* IMPRESSION LEADS. It is the conclusion a clinician acts on;
              findings are the evidence behind it. */}
          {isDocumented(result.impression) ? (
            <ViewerSection title="Impression">
              <p className="whitespace-pre-wrap break-words cl-prose leading-relaxed text-slate-900">
                {result.impression}
              </p>
            </ViewerSection>
          ) : null}
          {isDocumented(result.findings) ? (
            <ViewerSection title="Findings">
              <p className="whitespace-pre-wrap break-words cl-prose leading-relaxed text-slate-800">
                {result.findings}
              </p>
            </ViewerSection>
          ) : null}
          {isDocumented(result.recommendations) ? (
            <ViewerSection title="Recommendations">
              <p className="whitespace-pre-wrap break-words cl-prose leading-relaxed text-slate-800">
                {result.recommendations}
              </p>
            </ViewerSection>
          ) : null}
        </>
      ) : (
        <ViewerSection title="Report">
          {/* A REACHABLE STATE: radiology has no completeness constraint, so a
              report can be released with no narrative at all. Blank space would
              read as a broken screen; this reads as a fact about the record. */}
          <p className="cl-secondary italic leading-relaxed text-slate-500">
            {reportPreview(result)}
          </p>
        </ViewerSection>
      )}

      <ViewerSection title="Examinations">
        <ul className="space-y-1">
          {result.exams.map((exam) => (
            <li key={exam.id} className="cl-secondary leading-relaxed text-slate-700">
              <span className="font-semibold text-slate-800">{exam.name}</span>
              {exam.modality_label ? ` · ${exam.modality_label}` : ""}
              {exam.body_part ? ` · ${exam.body_part}` : ""}
              {exam.contrast_used ? (
                <span className="ml-1.5 rounded border border-violet-300 bg-violet-50 px-1 py-px cl-micro font-bold uppercase tracking-wide text-violet-900">
                  contrast
                </span>
              ) : null}
            </li>
          ))}
        </ul>
      </ViewerSection>

      {detailed.length ? (
        <ViewerSection title="Per-study notes">
          <ul className="space-y-1">
            {detailed.map((exam) => (
              <li key={exam.id} className="cl-secondary leading-relaxed text-slate-700">
                <span className="font-semibold text-slate-800">{exam.name}: </span>
                {[exam.summary, exam.notes].filter(Boolean).join(" — ")}
              </li>
            ))}
          </ul>
        </ViewerSection>
      ) : null}
    </div>
  );
}
