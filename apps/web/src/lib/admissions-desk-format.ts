/**
 * Admissions Desk: display vocabulary, grouping and route construction.
 *
 * Pure functions. Nothing here fetches, nothing here writes, and nothing here
 * decides anything clinical, financial or about access.
 *
 * THE RULES THIS FILE KEEPS:
 *
 *   * The browser FORMATS a lane, it never DERIVES one. Every lane and every
 *     needs-review reason arrives decided by the server's classifier.
 *   * The browser renders a clearance VERDICT, never an amount -- there is none
 *     in the payload -- and says "not applicable" when the server says null
 *     rather than inventing a "cleared".
 *   * Every URL built here is a BFF path. The browser never addresses Odoo.
 */
// TYPE-ONLY, so node:test runs the format tests with no resolver.
import type {
  AdmissionClearance,
  AdmissionDeskScope,
  AdmissionDeskSession,
  AdmissionEncounter,
  AdmissionFinancial,
  AdmissionLocation,
  AdmissionPatient,
  BedBoardRow,
  BedState,
  LengthOfStay,
  NamedRef,
} from "@/types/admissions-desk";

/* ------------------------------------------------------------------ *
 * Vocabulary
 * ------------------------------------------------------------------ */

/** Runtime copies, DUPLICATED FROM the types file on purpose (no value
 *  imports); a test asserts they agree. */
export const ADMISSION_LANE_ORDER: readonly string[] = [
  "needs_review",
  "awaiting_bed",
  "draft",
  "discharge_pending",
  "admitted",
  "transferred",
  "discharged",
  "cancelled",
];

export const ADMISSION_ACTIVE_LANE_ORDER: readonly string[] = [
  "needs_review",
  "awaiting_bed",
  "draft",
  "discharge_pending",
  "admitted",
  "transferred",
];

/** Pseudo-lanes. `active` is the server default; `all` is sent as-is. */
export const ACTIVE_LANE_KEY = "active";
export const ALL_LANE_KEY = "all";

const LANE_LABELS: Record<string, string> = {
  needs_review: "Needs review",
  awaiting_bed: "Awaiting bed",
  draft: "Draft",
  discharge_pending: "Ready for discharge",
  admitted: "Admitted",
  transferred: "Transferred",
  discharged: "Discharged",
  cancelled: "Cancelled",
};

/** TEXT, NOT COLOUR: every lane carries a code as well as a tone. */
const LANE_CODES: Record<string, string> = {
  needs_review: "REV",
  awaiting_bed: "BED?",
  draft: "DRF",
  discharge_pending: "DSC?",
  admitted: "ADM",
  transferred: "TRF",
  discharged: "DIS",
  cancelled: "CAN",
};

const LANE_TONES: Record<string, string> = {
  needs_review: "border-red-300 bg-red-50 text-red-800",
  awaiting_bed: "border-amber-300 bg-amber-50 text-amber-900",
  draft: "border-slate-300 bg-slate-50 text-slate-700",
  discharge_pending: "border-violet-300 bg-violet-50 text-violet-900",
  admitted: "border-sky-300 bg-sky-50 text-sky-900",
  transferred: "border-indigo-300 bg-indigo-50 text-indigo-900",
  discharged: "border-emerald-300 bg-emerald-50 text-emerald-900",
  cancelled: "border-slate-200 bg-white text-slate-500",
};

export function laneLabel(lane: string | null | undefined): string {
  if (!lane) return "—";
  if (lane === ACTIVE_LANE_KEY) return "Open work";
  if (lane === ALL_LANE_KEY) return "All";
  return LANE_LABELS[lane] ?? lane;
}

export function laneCode(lane: string | null | undefined): string {
  if (!lane) return "—";
  if (lane === ACTIVE_LANE_KEY) return "OPEN";
  if (lane === ALL_LANE_KEY) return "ALL";
  return LANE_CODES[lane] ?? lane.slice(0, 4).toUpperCase();
}

export function laneTone(lane: string | null | undefined): string {
  return (lane && LANE_TONES[lane]) || "border-slate-200 bg-white text-slate-600";
}

/** What the worklist asks the server for. `active` sends nothing (the
 *  server's own default), so the browser never restates the lane list. */
export function laneParam(lane: string): string | null {
  if (lane === ACTIVE_LANE_KEY) return null;
  return lane;
}

const BED_STATE_LABELS: Record<BedState, string> = {
  available: "Available",
  occupied: "Occupied",
  cleaning: "Cleaning",
  maintenance: "Maintenance",
  blocked: "Blocked",
};

const BED_STATE_TONES: Record<BedState, string> = {
  available: "border-emerald-300 bg-emerald-50 text-emerald-900",
  occupied: "border-sky-400 bg-sky-50 text-sky-950",
  cleaning: "border-cyan-300 bg-cyan-50 text-cyan-900",
  maintenance: "border-orange-300 bg-orange-50 text-orange-900",
  blocked: "border-slate-400 bg-slate-200 text-slate-800",
};

export function bedStateLabel(state: BedState | string): string {
  return BED_STATE_LABELS[state as BedState] ?? state;
}

export function bedTone(bed: Pick<BedBoardRow, "state" | "active">): string {
  if (!bed.active) return "border-dashed border-slate-300 bg-slate-50 text-slate-400";
  return BED_STATE_TONES[bed.state] ?? "border-slate-200 bg-white text-slate-600";
}

/* ------------------------------------------------------------------ *
 * Values
 * ------------------------------------------------------------------ */

export function orDash(value: string | number | null | undefined): string {
  if (value === null || value === undefined || value === "") return "—";
  return String(value);
}

/** "3d 4h", "0d 5h". Ongoing stays read as elapsed-so-far. */
export function formatLengthOfStay(los: LengthOfStay | null | undefined): string {
  if (!los) return "—";
  return `${los.days}d ${los.hours}h`;
}

export function ageSexLabel(patient: AdmissionPatient | null | undefined): string {
  if (!patient) return "—";
  const sex = patient.gender ? patient.gender.charAt(0).toUpperCase() : null;
  const age = patient.age ?? null;
  if (age === null && !sex) return "—";
  return [age === null ? null : `${age}y`, sex].filter(Boolean).join(" ");
}

function refLabel(ref: NamedRef | null | undefined): string | null {
  if (!ref) return null;
  return ref.code || ref.name || null;
}

/** "MED-WARD / R101 / BED-101A", or the parts that exist. */
export function locationLabel(location: AdmissionLocation | null | undefined): string {
  if (!location) return "—";
  const parts = [refLabel(location.ward), refLabel(location.room), refLabel(location.bed)];
  const present = parts.filter((part): part is string => Boolean(part));
  return present.length ? present.join(" / ") : "No location";
}

/** The visit line, honest about legacy and restricted visits. */
export function encounterLabel(encounter: AdmissionEncounter | null | undefined): string {
  if (!encounter || encounter.legacy) return "No linked visit (legacy admission)";
  if (encounter.restricted) return "Visit linked — not visible to your role";
  return [encounter.reference, encounter.type_label, encounter.state_label]
    .filter(Boolean)
    .join(" · ");
}

/**
 * The clearance line. NULL IS NOT FALSE: a null verdict is "cannot be
 * determined", never "cleared".
 */
export function clearanceLabel(clearance: AdmissionClearance | null | undefined): {
  text: string;
  tone: "neutral" | "ok" | "warn";
} {
  if (!clearance || clearance.billing_blocked === null) {
    return {
      text: clearance?.clearance_message ?? "Financial clearance cannot be determined.",
      tone: "neutral",
    };
  }
  if (clearance.billing_blocked) {
    return {
      text: clearance.clearance_message ?? "Financial clearance has not been confirmed.",
      tone: "warn",
    };
  }
  if (clearance.clearance_state === "not_required") {
    return { text: "No financial clearance required.", tone: "ok" };
  }
  return { text: "Financially cleared.", tone: "ok" };
}

/**
 * The inpatient financial STATE (Slice 3), in words. The server decides the
 * state; this only names it. No figure exists to show, by design.
 */
export function financialLabel(financial: AdmissionFinancial | null | undefined): {
  text: string;
  tone: "neutral" | "ok" | "warn";
} {
  if (!financial) return { text: "The inpatient financial state is not available.", tone: "neutral" };
  switch (financial.financial_state) {
    case "covered":
      return { text: "Covered. Care delivered so far is paid or authorized.", tone: "ok" };
    case "due":
      return { text: "Payment required at the cashier before discharge.", tone: "warn" };
    case "refundable":
      return { text: "Refund due: the patient paid more than the care delivered. The cashier returns it.", tone: "warn" };
    case "pending":
      return { text: "Nothing has been delivered or paid yet.", tone: "neutral" };
    case "needs_review":
      return { text: "The inpatient financial state needs review before billing.", tone: "warn" };
    default:
      return { text: "Not applicable: the patient has not been admitted.", tone: "neutral" };
  }
}

/** The row's compact clearance marker. */
export function billingMarker(blocked: boolean | null): "CLEARANCE" | "N/A" | null {
  if (blocked === null) return "N/A";
  return blocked ? "CLEARANCE" : null;
}

/* ------------------------------------------------------------------ *
 * Session
 * ------------------------------------------------------------------ */

const SCOPE_LABELS: Record<AdmissionDeskScope, string> = {
  all_wards: "All wards",
  own_patients: "Your patients",
  permitted_departments: "Your wards",
  own_patients_and_permitted_departments: "Your patients and wards",
  none: "No scope",
};

export function scopeLabel(scope: AdmissionDeskScope | null | undefined): string {
  return scope ? SCOPE_LABELS[scope] ?? scope : "—";
}

export function deskRoleLabel(session: AdmissionDeskSession | null): string | null {
  return session?.desk_role ?? null;
}

/** The empty queue, in words that fit who is looking. */
export function emptyQueueMessage(scope: AdmissionDeskScope | null | undefined, lane: string): string {
  const where =
    scope === "own_patients"
      ? "for your patients"
      : scope === "permitted_departments"
        ? "on your wards"
        : "";
  return `No ${laneLabel(lane).toLowerCase()} admissions ${where}`.replace(/\s+$/, "") + ".";
}

/* ------------------------------------------------------------------ *
 * Selection and grouping
 * ------------------------------------------------------------------ */

/** Keep the selection if it is still listed, else the first row, else none. */
export function resolveSelection<T extends { id: number }>(rows: T[], selectedId: number | null): number | null {
  if (selectedId !== null && rows.some((row) => row.id === selectedId)) return selectedId;
  return rows[0]?.id ?? null;
}

export type BedBoardRoom = { room: NamedRef | null; beds: BedBoardRow[] };
export type BedBoardWard = { ward: NamedRef | null; rooms: BedBoardRoom[] };

/** Ward -> Room -> Bed, preserving the server's order. */
export function groupBeds(beds: BedBoardRow[]): BedBoardWard[] {
  const wards: BedBoardWard[] = [];
  const wardIndex = new Map<string, BedBoardWard>();
  const roomIndex = new Map<string, BedBoardRoom>();
  for (const bed of beds) {
    const wardKey = String(bed.ward?.id ?? "none");
    let ward = wardIndex.get(wardKey);
    if (!ward) {
      ward = { ward: bed.ward, rooms: [] };
      wardIndex.set(wardKey, ward);
      wards.push(ward);
    }
    const roomKey = `${wardKey}:${bed.room?.id ?? "none"}`;
    let room = roomIndex.get(roomKey);
    if (!room) {
      room = { room: bed.room, beds: [] };
      roomIndex.set(roomKey, room);
      ward.rooms.push(room);
    }
    room.beds.push(bed);
  }
  return wards;
}

/** Initials for a dense bed cell. Only ever given a name the server sent. */
export function initials(name: string | null | undefined): string {
  if (!name) return "";
  return name
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 3)
    .map((part) => part.charAt(0).toUpperCase())
    .join("");
}

/* ------------------------------------------------------------------ *
 * BFF paths -- the ONLY addresses the browser uses
 * ------------------------------------------------------------------ */

export const ADMISSIONS_SESSION_PATH = "/api/admissions/session";
export const ADMISSIONS_WARDS_PATH = "/api/admissions/wards";

function withQuery(path: string, params: Record<string, string | number | null | undefined>): string {
  const query = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value === null || value === undefined || value === "") continue;
    query.set(key, String(value));
  }
  const text = query.toString();
  return text ? `${path}?${text}` : path;
}

export function worklistPath(params: { lane: string; wardId: number | null; q: string | null }): string {
  return withQuery("/api/admissions/worklist", {
    lane: laneParam(params.lane),
    ward_id: params.wardId,
    q: params.q,
  });
}

export function admissionPath(admissionId: number): string {
  return `/api/admissions/${admissionId}`;
}

export function bedsPath(params: { wardId: number | null; state?: string | null; q?: string | null }): string {
  return withQuery("/api/admissions/beds", {
    ward_id: params.wardId,
    state: params.state ?? null,
    q: params.q ?? null,
  });
}
