/**
 * UNSENT CLINICAL ORDER WORK, as a value.
 *
 * WHAT THIS EXISTS TO STOP. Each order panel used to own its own staged
 * selection, its own order form and -- for medication -- its own idempotency
 * token, in component state. The ORDERS tabs mount one panel at a time, so
 * moving from Medication to Laboratory unmounted the prescription a doctor had
 * half-built and React discarded it without a word. A staged Cetirizine, its
 * dose, its quantity and the note for the pharmacy all disappeared because of a
 * navigation click that was never meant to destroy anything.
 *
 * THE FIX IS A CHANGE OF OWNERSHIP, NOT A NEW PERSISTENCE MECHANISM. An unsent
 * order draft belongs to the ACTIVE CONSULTATION, not to whichever tab happens
 * to be mounted, so the state moves up to the consultation workspace and the
 * panels read and write it. Nothing here reaches localStorage, sessionStorage
 * or a server: a draft that outlived the browser tab would be a second clinical
 * record with no owner, and reconciling it against a consultation that may have
 * been completed meanwhile is a problem this desk does not need to have.
 *
 * PURE FUNCTIONS, and that is what makes the rules above testable. This project
 * ships no DOM test stack, so the decisions worth pinning -- what counts as
 * unsent work, what a tab indicator shows, what a successful submission clears
 * and what a patient change wipes -- live here rather than inside a component,
 * exactly as note-editor-format holds the note editor's decisions.
 *
 * NOTHING HERE IS A CONTRACT CHANGE. These are the same fields the three panels
 * already collected and the same payload builders already consume; only the
 * place they are held has moved.
 */
// TYPE-ONLY, and it has to stay that way -- TypeScript erases these, which is
// what lets node:test run order-draft-format.test.ts with no resolver and no
// transform.
import type { LabOrderForm, LabTestOption } from "@/types/doctor-laboratory";
import type {
  MedPrescriptionForm,
  StagedMedicine,
} from "@/types/doctor-medication";
import type { RadExamOption, RadOrderForm } from "@/types/doctor-radiology";

/**
 * The order kinds that can hold a draft.
 *
 * PROCEDURE IS ABSENT ON PURPOSE. It has no catalogue endpoint, no order
 * endpoint and no model behind it; giving it a draft slot would be scaffolding
 * for a workflow that does not exist, and a tab indicator that could never
 * light. When procedure ships it joins this list and inherits everything below.
 */
export const DRAFTABLE_ORDER_KINDS = [
  "laboratory",
  "radiology",
  "medication",
] as const;
export type DraftableOrderKind = (typeof DRAFTABLE_ORDER_KINDS)[number];

export function isDraftableOrderKind(key: string): key is DraftableOrderKind {
  return (DRAFTABLE_ORDER_KINDS as readonly string[]).includes(key);
}

/* ------------------------------------------------------------------ *
 * Shapes
 * ------------------------------------------------------------------ */

/**
 * A laboratory order being composed.
 *
 * ONE ORDER, N TESTS, ONE SHARED FORM -- the shape the API already takes.
 * Priority, indication and clinical notes belong to the REQUEST, not to a test,
 * so the editor collects them once and the staged row summarises one order
 * rather than one row per test.
 *
 * NO TOKEN IS HELD HERE. Laboratory mints its request token inside the
 * submission itself and has never retained one between attempts; moving that
 * into the draft would change idempotency behaviour this task is not allowed to
 * touch. See the note on MedOrderDraft, where the token genuinely was being
 * lost.
 */
export type LabOrderDraft = {
  tests: LabTestOption[];
  form: LabOrderForm;
};

/** A radiology order being composed. Same shape, plus the preparation field. */
export type RadOrderDraft = {
  exams: RadExamOption[];
  form: RadOrderForm;
};

/**
 * A prescription being composed.
 *
 * `staged` is a list of small forms rather than a list of ids, because a
 * prescription is a set of picked medicines EACH CARRYING ITS OWN dose, route,
 * frequency, duration, quantity and instructions.
 *
 * `token` IS THE ONE THAT HAD TO MOVE. Medication mints a token once per
 * submission and holds it until that submission definitively succeeds, so a
 * retry after a dropped response is deduplicated server-side instead of writing
 * a second prescription with a second pharmacy dispense behind it. Held in a
 * panel ref, that token died on every tab switch -- the exact case it exists
 * for. It now lives with the draft it identifies, and is still cleared only by
 * a definitive success or by the doctor discarding the draft.
 */
export type MedOrderDraft = {
  staged: StagedMedicine[];
  form: MedPrescriptionForm;
  token: string | null;
};

export type ConsultationOrderDrafts = {
  laboratory: LabOrderDraft;
  radiology: RadOrderDraft;
  medication: MedOrderDraft;
};

/* ------------------------------------------------------------------ *
 * Empty values
 * ------------------------------------------------------------------ */

/*
  RESTATED, NOT IMPORTED, from laboratory-format, radiology-format and
  medication-format.

  Those three modules are the runtime home of EMPTY_ORDER_FORM /
  EMPTY_PRESCRIPTION_FORM and must stay free of cross-module value imports so
  node:test can run every format test with no resolver and no transform. This is
  the same trade the ORDER_KINDS list already makes between types and lib, and
  it is held the same way: order-draft-format.test.ts asserts these agree with
  the originals field for field, so changing one without the other fails rather
  than drifts.
*/
export const EMPTY_LAB_DRAFT: LabOrderDraft = {
  tests: [],
  form: { priority: "routine", clinical_notes: "", diagnosis_id: null },
};

export const EMPTY_RAD_DRAFT: RadOrderDraft = {
  exams: [],
  form: {
    priority: "routine",
    clinical_indication: "",
    instructions: "",
    diagnosis_id: null,
  },
};

export const EMPTY_MED_DRAFT: MedOrderDraft = {
  staged: [],
  form: { diagnosis_id: null, notes: "" },
  token: null,
};

export const EMPTY_ORDER_DRAFTS: ConsultationOrderDrafts = {
  laboratory: EMPTY_LAB_DRAFT,
  radiology: EMPTY_RAD_DRAFT,
  medication: EMPTY_MED_DRAFT,
};

/* ------------------------------------------------------------------ *
 * Reading
 * ------------------------------------------------------------------ */

/** How many orderable items are staged in this kind's draft. */
export function draftItemCount(
  drafts: ConsultationOrderDrafts,
  kind: DraftableOrderKind,
): number {
  if (kind === "laboratory") return drafts.laboratory.tests.length;
  if (kind === "radiology") return drafts.radiology.exams.length;
  return drafts.medication.staged.length;
}

/**
 * Whether the doctor has typed or chosen something on this kind's ORDER FORM,
 * separately from having staged an item.
 *
 * A typed indication with no test selected is still unsent work and still worth
 * a mark on the tab, even though it could not be submitted. Whitespace does not
 * count: a field holding a stray space is an empty field that happens to have
 * been touched.
 */
export function draftFormTouched(
  drafts: ConsultationOrderDrafts,
  kind: DraftableOrderKind,
): boolean {
  if (kind === "laboratory") {
    const form = drafts.laboratory.form;
    return (
      form.priority !== "routine" ||
      form.clinical_notes.trim().length > 0 ||
      form.diagnosis_id !== null
    );
  }
  if (kind === "radiology") {
    const form = drafts.radiology.form;
    return (
      form.priority !== "routine" ||
      form.clinical_indication.trim().length > 0 ||
      form.instructions.trim().length > 0 ||
      form.diagnosis_id !== null
    );
  }
  const form = drafts.medication.form;
  return form.notes.trim().length > 0 || form.diagnosis_id !== null;
}

/** True when this kind holds anything the doctor has not sent yet. */
export function hasDraftWork(
  drafts: ConsultationOrderDrafts,
  kind: DraftableOrderKind,
): boolean {
  return draftItemCount(drafts, kind) > 0 || draftFormTouched(drafts, kind);
}

/**
 * What the ORDERS tab should mark.
 *
 * `count` is the number of staged items and is shown as a figure; `dirty` is
 * true whenever there is unsent work at all, including a form typed into with
 * nothing staged, and is shown as a bare dot.
 *
 * SUBMITTED ORDERS ARE NEVER COUNTED. The indicator answers "is there work
 * here you have not sent", not "does this patient have laboratory orders" --
 * counting placed orders would light every tab of every consultation and the
 * mark would stop meaning anything.
 */
export type DraftIndicator = { dirty: boolean; count: number };

export function draftIndicator(
  drafts: ConsultationOrderDrafts,
  kind: DraftableOrderKind,
): DraftIndicator {
  return {
    dirty: hasDraftWork(drafts, kind),
    count: draftItemCount(drafts, kind),
  };
}

/** Every kind currently holding unsent work, in tab order. */
export function unfinishedDraftKinds(
  drafts: ConsultationOrderDrafts,
): DraftableOrderKind[] {
  return DRAFTABLE_ORDER_KINDS.filter((kind) => hasDraftWork(drafts, kind));
}

/* ------------------------------------------------------------------ *
 * Writing
 * ------------------------------------------------------------------ */

/** Replace one kind's draft, leaving the other kinds exactly as they were. */
export function putDraft<K extends DraftableOrderKind>(
  drafts: ConsultationOrderDrafts,
  kind: K,
  draft: ConsultationOrderDrafts[K],
): ConsultationOrderDrafts {
  return { ...drafts, [kind]: draft };
}

/**
 * Discard one kind's draft.
 *
 * THE ONLY THINGS ALLOWED TO CALL THIS are a definitive submission success, an
 * explicit Remove by the doctor, and a consultation that has become read-only.
 * Tab navigation is not on that list and never reaches here.
 */
export function clearDraft(
  drafts: ConsultationOrderDrafts,
  kind: DraftableOrderKind,
): ConsultationOrderDrafts {
  if (kind === "laboratory") return { ...drafts, laboratory: EMPTY_LAB_DRAFT };
  if (kind === "radiology") return { ...drafts, radiology: EMPTY_RAD_DRAFT };
  return { ...drafts, medication: EMPTY_MED_DRAFT };
}

/** Everything unsent, gone. A patient change and a completion both land here. */
export function clearAllDrafts(): ConsultationOrderDrafts {
  return EMPTY_ORDER_DRAFTS;
}

/* ------------------------------------------------------------------ *
 * Editor dirty checks
 * ------------------------------------------------------------------ */

/*
  WHAT "CHANGED" MEANS TO AN ORDER EDITOR.

  The editors hold a local copy of the draft and commit it on Save, so these
  answer the question the close protection asks: would closing now throw away
  something the doctor did? Item IDENTITY is compared, not the catalogue objects
  themselves -- the same test re-read from the catalogue is the same test, and a
  reference comparison would report a change nobody made.
*/
function sameIds(left: { id: number }[], right: { id: number }[]): boolean {
  if (left.length !== right.length) return false;
  return left.every((entry, index) => entry.id === right[index].id);
}

export function labDraftChanged(
  openedWith: LabOrderDraft,
  current: LabOrderDraft,
): boolean {
  return (
    !sameIds(openedWith.tests, current.tests) ||
    openedWith.form.priority !== current.form.priority ||
    openedWith.form.clinical_notes !== current.form.clinical_notes ||
    openedWith.form.diagnosis_id !== current.form.diagnosis_id
  );
}

export function radDraftChanged(
  openedWith: RadOrderDraft,
  current: RadOrderDraft,
): boolean {
  return (
    !sameIds(openedWith.exams, current.exams) ||
    openedWith.form.priority !== current.form.priority ||
    openedWith.form.clinical_indication !== current.form.clinical_indication ||
    openedWith.form.instructions !== current.form.instructions ||
    openedWith.form.diagnosis_id !== current.form.diagnosis_id
  );
}

/**
 * The token this prescription submission should carry.
 *
 * Minted once and then returned unchanged for every later attempt, so a retry
 * after a dropped response is the SAME submission as far as the server is
 * concerned. `mint` is passed in rather than called here because a pure
 * function may not read the clock or the crypto module.
 */
export function prescriptionToken(
  draft: MedOrderDraft,
  mint: () => string,
): string {
  return draft.token ?? mint();
}
