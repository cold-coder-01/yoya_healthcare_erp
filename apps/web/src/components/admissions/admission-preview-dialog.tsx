"use client";

import { buildAdmissionPreview } from "@/lib/admission-preview";
import type { AdmissionDetail } from "@/types/admissions-desk";

import {
  PreviewField,
  PreviewHeader,
  PreviewMetric,
  PreviewSection,
  PreviewStatus,
  PreviewWarning,
  WorkstationPreviewDialog,
} from "@/components/workstation/workstation-preview";

import AdmissionLanePill from "./admission-lane-pill";

/**
 * ADMISSIONS QUICK PREVIEW: the whole operational picture in one viewport.
 *
 * READ ONLY. It is handed the AdmissionDetail the desk already loaded -- no
 * fetch, no form, no action -- so it shows exactly what the detail pane was
 * authorised to show and nothing more: redactions stay redacted, and the
 * financial section is a state word because the payload holds no figure.
 *
 * Layout: identity header; then two columns on a desktop -- Location and
 * Clinical left, Discharge and Financial right -- with Current work and
 * Transfer across the bottom. The detail pane remains the place to act.
 */
export default function AdmissionPreviewDialog({
  detail,
  onClose,
}: {
  detail: AdmissionDetail;
  onClose: () => void;
}) {
  const preview = buildAdmissionPreview(detail);
  const titleId = "admission-preview-title";

  return (
    <WorkstationPreviewDialog
      titleId={titleId}
      onClose={onClose}
      header={
        <PreviewHeader
          titleId={titleId}
          title={preview.title}
          identifiers={preview.identifiers}
          status={
            <>
              <AdmissionLanePill lane={preview.lane} />
              <span className="cl-micro text-slate-500">State: {preview.stateLabel}</span>
            </>
          }
        />
      }
    >
      {preview.reviewReasons.length ? (
        <div className="px-4 pt-2">
          <PreviewWarning tone="danger" title="Needs review" items={preview.reviewReasons} />
        </div>
      ) : null}

      <div className="grid min-[900px]:grid-cols-2 min-[900px]:divide-x min-[900px]:divide-slate-200">
        <div className="min-w-0">
          <PreviewSection title="Location" columns={3}>
            {preview.location.map((field) => (
              <PreviewField key={field.label} label={field.label}>
                {field.value}
              </PreviewField>
            ))}
          </PreviewSection>
          <PreviewSection title="Clinical" columns={2}>
            <PreviewField label={preview.clinical.physician.label}>{preview.clinical.physician.value}</PreviewField>
            <PreviewField label={preview.clinical.diagnosis.label} muted={preview.clinical.diagnosis.muted}>
              {preview.clinical.diagnosis.value}
            </PreviewField>
            <PreviewField label={preview.clinical.reason.label} muted={preview.clinical.reason.muted} wide>
              {preview.clinical.reason.value}
            </PreviewField>
          </PreviewSection>
        </div>

        <div className="min-w-0 border-t border-slate-200 min-[900px]:border-t-0">
          <PreviewSection title="Discharge">
            <PreviewStatus tone={preview.discharge.tone} label={preview.discharge.label}>
              {preview.discharge.text}
            </PreviewStatus>
            <PreviewWarning tone="danger" title="Blocking" items={preview.discharge.blocking} />
            <PreviewWarning tone="warn" title="Warnings" items={preview.discharge.warnings} />
          </PreviewSection>
          <PreviewSection title="Financial">
            <PreviewStatus tone={preview.financial.tone} label={preview.financial.label}>
              {preview.financial.text}
            </PreviewStatus>
            <PreviewWarning tone="warn" title="Review" items={preview.financial.reasons} />
          </PreviewSection>
        </div>
      </div>

      <PreviewSection title="Current work">
        <dl className="grid grid-cols-4 gap-1.5 min-[900px]:grid-cols-8">
          {preview.work.map((item) => (
            <PreviewMetric key={item.label} label={item.label} value={item.value} note={item.note} muted={item.muted} />
          ))}
        </dl>
      </PreviewSection>

      <PreviewSection
        title="Transfer"
        aside={<span className="cl-micro text-slate-500">{preview.transfer.count} transfer{preview.transfer.count === 1 ? "" : "s"}</span>}
      >
        <p className="cl-body">
          <span className="cl-micro font-bold uppercase tracking-wide text-slate-500">Current </span>
          <span className="font-mono text-slate-900">{preview.transfer.current}</span>
        </p>
        {preview.transfer.timeline.length ? (
          <ol className="mt-1 flex flex-wrap items-center gap-x-1.5 gap-y-1 font-mono cl-meta text-slate-800">
            {preview.transfer.timeline.map((stop, index) => (
              <li key={`${index}-${stop.place}`} className="flex items-center gap-1.5">
                {index > 0 ? <span aria-hidden className="text-slate-400">→</span> : null}
                <span className="rounded border border-slate-200 bg-slate-50 px-1.5 py-0.5">
                  {stop.place}
                  {stop.at ? <span className="ml-1.5 font-sans cl-micro text-slate-500">{stop.at}</span> : null}
                </span>
              </li>
            ))}
          </ol>
        ) : null}
      </PreviewSection>
    </WorkstationPreviewDialog>
  );
}
