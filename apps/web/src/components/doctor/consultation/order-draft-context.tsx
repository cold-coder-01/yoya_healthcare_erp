"use client";

import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useState,
} from "react";
import type { ReactNode } from "react";

import {
  EMPTY_ORDER_DRAFTS,
  clearAllDrafts,
  clearDraft,
  draftIndicator,
  putDraft,
} from "@/lib/order-draft-format";
import type {
  ConsultationOrderDrafts,
  DraftIndicator,
  DraftableOrderKind,
  LabOrderDraft,
  MedOrderDraft,
  RadOrderDraft,
} from "@/lib/order-draft-format";

/**
 * WHERE UNSENT ORDER WORK LIVES.
 *
 * ABOVE THE TABS, AND ABOVE THE SECTIONS. The ORDERS tabs mount one panel at a
 * time and the consultation sections mount one workspace at a time, so any
 * draft held inside a panel is destroyed by a navigation click. This provider
 * sits at the top of the consultation workspace, outside every one of those
 * conditionals, which is what lets a doctor stage a medicine, check a lab
 * result, glance at the diagnosis list and come back to the exact prescription
 * they left.
 *
 * SCOPED TO ONE CONSULTATION, TWICE OVER. The workstation already mounts the
 * consultation workspace with key={appointment_id}, so a patient change
 * unmounts this provider and its state with it. The render-time guard below is
 * the second lock: if this provider is ever mounted without that key, an
 * appointment change still wipes every draft before the children render, so
 * patient A's staged prescription cannot appear on patient B's screen. Two
 * cheap mechanisms for the most dangerous thing this screen could do is the
 * right trade.
 *
 * NOTHING LEAVES THE BROWSER TAB. No localStorage, no sessionStorage, no
 * autosave endpoint. A draft that outlived the tab would be an unowned clinical
 * record needing reconciliation against a consultation that may since have been
 * completed by someone else.
 */

type OrderDraftStore = {
  drafts: ConsultationOrderDrafts;
  setLabDraft: (next: LabOrderDraft) => void;
  setRadDraft: (next: RadOrderDraft) => void;
  setMedDraft: (next: MedOrderDraft | ((current: MedOrderDraft) => MedOrderDraft)) => void;
  /** Discard one kind. Submission success, explicit Remove, or read-only. */
  discardDraft: (kind: DraftableOrderKind) => void;
  indicatorFor: (kind: DraftableOrderKind) => DraftIndicator;
};

const OrderDraftContext = createContext<OrderDraftStore | null>(null);

export function ConsultationOrderDraftProvider({
  appointmentId,
  children,
}: {
  appointmentId: number;
  children: ReactNode;
}) {
  const [drafts, setDrafts] = useState<ConsultationOrderDrafts>(
    EMPTY_ORDER_DRAFTS,
  );
  const [scope, setScope] = useState(appointmentId);

  /*
    Adjusting state during render, deliberately and by the book: React discards
    this render and re-runs it with the new state before anything commits, so no
    child ever sees the previous consultation's drafts -- not even for a frame,
    which an effect could not promise.
  */
  if (scope !== appointmentId) {
    setScope(appointmentId);
    setDrafts(clearAllDrafts());
  }

  const setLabDraft = useCallback((next: LabOrderDraft) => {
    setDrafts((current) => putDraft(current, "laboratory", next));
  }, []);

  const setRadDraft = useCallback((next: RadOrderDraft) => {
    setDrafts((current) => putDraft(current, "radiology", next));
  }, []);

  const setMedDraft = useCallback(
    (next: MedOrderDraft | ((current: MedOrderDraft) => MedOrderDraft)) => {
      setDrafts((current) =>
        putDraft(
          current,
          "medication",
          typeof next === "function" ? next(current.medication) : next,
        ),
      );
    },
    [],
  );

  const discardDraft = useCallback((kind: DraftableOrderKind) => {
    setDrafts((current) => clearDraft(current, kind));
  }, []);

  const indicatorFor = useCallback(
    (kind: DraftableOrderKind) => draftIndicator(drafts, kind),
    [drafts],
  );

  const store = useMemo<OrderDraftStore>(
    () => ({
      drafts,
      setLabDraft,
      setRadDraft,
      setMedDraft,
      discardDraft,
      indicatorFor,
    }),
    [drafts, setLabDraft, setRadDraft, setMedDraft, discardDraft, indicatorFor],
  );

  return (
    <OrderDraftContext.Provider value={store}>
      {children}
    </OrderDraftContext.Provider>
  );
}

/**
 * The store, or a loud failure.
 *
 * A missing provider means a panel is mounted somewhere that cannot hold its
 * draft, and the symptom would be the exact bug this whole mechanism exists to
 * fix -- work silently vanishing on navigation. Better a crash in development
 * than a clinician losing a prescription in production.
 */
export function useOrderDraftStore(): OrderDraftStore {
  const store = useContext(OrderDraftContext);
  if (!store) {
    throw new Error(
      "useOrderDraftStore must be used inside a ConsultationOrderDraftProvider.",
    );
  }
  return store;
}

export function useLabOrderDraft() {
  const { drafts, setLabDraft, discardDraft } = useOrderDraftStore();
  const discard = useCallback(
    () => discardDraft("laboratory"),
    [discardDraft],
  );
  return { draft: drafts.laboratory, setDraft: setLabDraft, discard };
}

export function useRadOrderDraft() {
  const { drafts, setRadDraft, discardDraft } = useOrderDraftStore();
  const discard = useCallback(() => discardDraft("radiology"), [discardDraft]);
  return { draft: drafts.radiology, setDraft: setRadDraft, discard };
}

export function useMedOrderDraft() {
  const { drafts, setMedDraft, discardDraft } = useOrderDraftStore();
  const discard = useCallback(() => discardDraft("medication"), [discardDraft]);
  return { draft: drafts.medication, setDraft: setMedDraft, discard };
}
