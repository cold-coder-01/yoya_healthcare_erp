/**
 * Admissions Quick Preview: the view model.
 *
 * A PURE PROJECTION of the AdmissionDetail the desk already holds. Nothing
 * here fetches, and nothing reads a field the detail payload does not carry,
 * so the preview can never show more than the detail pane the server
 * authorised for this caller:
 *
 *   * diagnosis `restricted: true` stays "Not visible to your role";
 *   * a restricted or legacy visit stays described, never revealed;
 *   * a counter the server sent as null stays "—", flagged as not visible;
 *   * the financial section is a STATE word -- the payload has no figure.
 *
 * Nothing here decides anything either: lane, readiness, blocking checks,
 * warnings and financial state are the server's verdicts, only named here.
 */
// TYPE-ONLY imports of the payload, so node:test runs this with no resolver.
import type {
  AdmissionDetail,
  AdmissionFinancial,
  CountAndLatest,
  NamedRef,
} from "@/types/admissions-desk";

import {
  ageSexLabel,
  bedStateLabel,
  encounterLabel,
  financialLabel,
  formatLengthOfStay,
  locationLabel,
  orDash,
} from "./admissions-desk-format.ts";
import { formatHospitalDateTime } from "./clinical-format.ts";

export type AdmissionPreviewTone = "neutral" | "ok" | "warn" | "danger" | "info";

export type AdmissionPreviewField = { label: string; value: string; muted?: boolean };
export type AdmissionPreviewMetric = { label: string; value: string; note: string | null; muted: boolean };
export type AdmissionPreviewReason = { key: string; text: string };
export type AdmissionPreviewStop = { place: string; at: string | null };

export type AdmissionPreview = {
  title: string;
  identifiers: { label: string; value: string }[];
  lane: AdmissionDetail["lane"];
  stateLabel: string;
  reviewReasons: AdmissionPreviewReason[];
  location: AdmissionPreviewField[];
  clinical: {
    physician: AdmissionPreviewField;
    diagnosis: AdmissionPreviewField;
    reason: AdmissionPreviewField;
  };
  work: AdmissionPreviewMetric[];
  discharge: {
    tone: AdmissionPreviewTone;
    label: string;
    text: string | null;
    blocking: AdmissionPreviewReason[];
    warnings: AdmissionPreviewReason[];
  };
  financial: {
    tone: AdmissionPreviewTone;
    label: string;
    text: string;
    reasons: AdmissionPreviewReason[];
  };
  transfer: {
    current: string;
    count: number;
    /** Oldest first: the admitting bed, then each bed moved to. */
    timeline: AdmissionPreviewStop[];
  };
};

export const NOT_VISIBLE = "Not visible to your role";

function ref(value: NamedRef | null | undefined): string | null {
  return value ? value.code || value.name || null : null;
}

function placeLabel(stop: { ward: NamedRef | null; room: NamedRef | null; bed: NamedRef | null }): string {
  const parts = [ref(stop.ward), ref(stop.room), ref(stop.bed)].filter((part): part is string => Boolean(part));
  return parts.length ? parts.join(" / ") : "—";
}

function metric(label: string, value: CountAndLatest | number | null): AdmissionPreviewMetric {
  if (value === null) return { label, value: "—", note: NOT_VISIBLE, muted: true };
  if (typeof value === "number") return { label, value: String(value), note: null, muted: value === 0 };
  return {
    label,
    value: String(value.count),
    note: value.count > 0 && value.latest_at ? `last ${formatHospitalDateTime(value.latest_at, "—")}` : null,
    muted: value.count === 0,
  };
}

/** The encounter as an identifier: the reference only when it may be read. */
export function encounterReference(detail: Pick<AdmissionDetail, "encounter">): string {
  const encounter = detail.encounter;
  if (!encounter || encounter.legacy) return "No linked visit";
  if (encounter.restricted) return NOT_VISIBLE;
  return encounter.reference ?? encounterLabel(encounter);
}

/** The diagnosis exactly as the detail pane shows it: redaction kept. */
export function diagnosisField(detail: Pick<AdmissionDetail, "diagnosis">): AdmissionPreviewField {
  const diagnosis = detail.diagnosis;
  if (diagnosis === null || diagnosis === undefined) return { label: "Primary diagnosis", value: "—", muted: true };
  if (diagnosis.restricted) return { label: "Primary diagnosis", value: NOT_VISIBLE, muted: true };
  const text = [diagnosis.code, diagnosis.name].filter(Boolean).join(" · ");
  return { label: "Primary diagnosis", value: text || "—", muted: !text };
}

const FINANCIAL_WORDS: Record<AdmissionFinancial["financial_state"], { label: string; tone: AdmissionPreviewTone }> = {
  covered: { label: "Covered", tone: "ok" },
  due: { label: "Payment required", tone: "warn" },
  refundable: { label: "Refund due", tone: "warn" },
  pending: { label: "Pending", tone: "neutral" },
  needs_review: { label: "Needs review", tone: "danger" },
  not_applicable: { label: "Not applicable", tone: "neutral" },
};

/** A STATE word and its sentence. Never a figure: the payload carries none. */
export function financialState(financial: AdmissionFinancial | null | undefined): AdmissionPreview["financial"] {
  const words = (financial && FINANCIAL_WORDS[financial.financial_state]) || FINANCIAL_WORDS.not_applicable;
  return {
    ...words,
    text: financialLabel(financial).text,
    reasons: (financial?.review_reasons ?? []).map((reason) => ({ key: reason.code, text: reason.message })),
  };
}

export function dischargeState(detail: Pick<AdmissionDetail, "discharge" | "state" | "discharged_at">): AdmissionPreview["discharge"] {
  const discharge = detail.discharge;
  const checks = (list: { code: string; message: string }[] | undefined) =>
    (list ?? []).map((check) => ({ key: check.code, text: check.message }));
  if (!discharge) {
    if (detail.state === "discharged") {
      return {
        tone: "ok",
        label: "Discharged",
        text: formatHospitalDateTime(detail.discharged_at, "—"),
        blocking: [],
        warnings: [],
      };
    }
    return {
      tone: "neutral",
      label: "Not applicable",
      text: detail.state === "cancelled" ? "The request was cancelled." : "The patient is not in a bed yet.",
      blocking: [],
      warnings: [],
    };
  }
  return {
    tone: discharge.medical_ready ? "info" : "neutral",
    label: discharge.medical_ready ? "Medically ready — awaiting administrative discharge" : "Not medically ready",
    text: discharge.medical_ready
      ? [discharge.medical_ready_by, formatHospitalDateTime(discharge.medical_ready_at, "—")].filter(Boolean).join(" · ")
      : null,
    blocking: checks(discharge.blocking),
    warnings: checks(discharge.warnings),
  };
}

export function transferTimeline(transfers: AdmissionDetail["transfers"]): AdmissionPreviewStop[] {
  if (!transfers.length) return [];
  // The server sends newest first; a timeline reads oldest first.
  const ordered = [...transfers].sort((a, b) =>
    (a.transferred_at ?? "").localeCompare(b.transferred_at ?? "") || a.id - b.id,
  );
  return [
    { place: placeLabel(ordered[0].from), at: null },
    ...ordered.map((transfer) => ({
      place: placeLabel(transfer.to),
      at: transfer.transferred_at ? formatHospitalDateTime(transfer.transferred_at, "—") : null,
    })),
  ];
}

export function buildAdmissionPreview(detail: AdmissionDetail): AdmissionPreview {
  const bed = detail.bed_ownership;
  return {
    title: detail.patient?.name ?? "—",
    identifiers: [
      { label: "MRN", value: orDash(detail.patient?.mrn) },
      { label: "Admission", value: detail.reference },
      { label: "Encounter", value: encounterReference(detail) },
      { label: "Age / sex", value: ageSexLabel(detail.patient) },
    ],
    lane: detail.lane,
    stateLabel: detail.state_label,
    reviewReasons: detail.review_reasons.map((reason) => ({ key: reason.code, text: reason.message })),
    location: [
      { label: "Ward", value: ref(detail.location.ward) ?? "—" },
      { label: "Room", value: ref(detail.location.room) ?? "—" },
      { label: "Bed", value: ref(detail.location.bed) ?? "—" },
      {
        label: "Bed state",
        value: bed
          ? `${bedStateLabel(bed.bed_state)}${bed.consistent === false ? " — does not match" : ""}`
          : "—",
      },
      { label: "Admitted", value: formatHospitalDateTime(detail.admitted_at, "—") },
      { label: "Length of stay", value: formatLengthOfStay(detail.length_of_stay) },
    ],
    clinical: {
      physician: { label: "Responsible physician", value: detail.physician?.name ?? "—" },
      diagnosis: diagnosisField(detail),
      reason: { label: "Admission reason", value: detail.admission_reason ?? "—", muted: !detail.admission_reason },
    },
    work: [
      metric("Nursing rounds", detail.nursing.rounds),
      metric("Nursing notes", detail.nursing.notes),
      metric("Care plans", detail.nursing.care_plans),
      metric("Medication admin.", detail.nursing.medication_administrations),
      metric("Procedures", detail.linkages.procedure_requests),
      metric("Pending lab", detail.linkages.pending_laboratory_requests),
      metric("Pending imaging", detail.linkages.pending_radiology_requests),
      metric("Transfers", detail.transfer_count),
    ],
    discharge: dischargeState(detail),
    financial: financialState(detail.financial),
    transfer: {
      current: locationLabel(detail.location),
      count: detail.transfer_count,
      timeline: transferTimeline(detail.transfers),
    },
  };
}
