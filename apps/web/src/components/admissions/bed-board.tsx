"use client";

import { useMemo } from "react";

import {
  bedStateLabel,
  bedTone,
  formatLengthOfStay,
  groupBeds,
  initials,
  orDash,
} from "@/lib/admissions-desk-format";
import type { BedBoardRow } from "@/types/admissions-desk";

/**
 * Ward > Room > BED cells. READ ONLY: a cell has no action of its own.
 *
 * An occupied bed shows its patient ONLY when the server sent the admission,
 * which it does only when the caller may read it. A bed whose admission is
 * hidden says "Occupied" and nothing more -- the redaction is the server's,
 * and this component has no other source of a name.
 *
 * Clicking a bed whose admission is visible PINS that admission in the detail
 * panel, whatever lane or filter the census is showing.
 */
function BedCell({
  bed,
  pinned,
  onPin,
}: {
  bed: BedBoardRow;
  pinned: boolean;
  onPin: (admissionId: number) => void;
}) {
  const admission = bed.admission;
  const clickable = Boolean(admission);
  const flagText = bed.flags.map((flag) => flag.message).join("\n");

  const body = (
    <>
      <span className="flex items-center gap-1">
        <span className="truncate font-mono cl-meta font-bold">{bed.code ?? bed.name}</span>
        {bed.needs_review ? (
          <span
            title={flagText}
            className="ml-auto shrink-0 rounded bg-red-600 px-1 cl-micro font-bold text-white"
          >
            !
          </span>
        ) : null}
      </span>
      {admission ? (
        <>
          <span className="truncate cl-meta font-bold text-slate-900" title={admission.patient?.name ?? ""}>
            <span className="mr-1 font-mono">{initials(admission.patient?.name)}</span>
            {admission.patient?.name ?? "—"}
          </span>
          <span className="flex items-center gap-1 truncate font-mono cl-micro text-slate-600">
            <span>{orDash(admission.patient?.mrn)}</span>
            <span aria-hidden>·</span>
            <span>{formatLengthOfStay(admission.length_of_stay)}</span>
            <span aria-hidden>·</span>
            <span className="truncate">{admission.state_label}</span>
          </span>
        </>
      ) : (
        <span className="cl-micro font-bold uppercase tracking-wide">
          {bed.active ? bedStateLabel(bed.state) : "Inactive"}
        </span>
      )}
    </>
  );

  const classes = `flex h-[64px] w-[150px] shrink-0 flex-col justify-center gap-0.5 rounded-md border px-2 text-left ${bedTone(bed)} ${
    pinned ? "ring-2 ring-sky-700 ring-offset-1" : ""
  } ${bed.needs_review ? "outline outline-2 outline-red-500" : ""}`;

  if (clickable && admission) {
    return (
      <button
        type="button"
        onClick={() => onPin(admission.id)}
        aria-pressed={pinned}
        aria-label={`Bed ${bed.code ?? bed.name}, ${admission.reference}`}
        title={bed.needs_review ? flagText : undefined}
        className={`${classes} hover:brightness-95`}
      >
        {body}
      </button>
    );
  }
  return (
    <div className={classes} title={bed.needs_review ? flagText : undefined} aria-label={`Bed ${bed.code ?? bed.name}, ${bedStateLabel(bed.state)}`}>
      {body}
    </div>
  );
}

export default function BedBoard({
  beds,
  loading,
  error,
  pinnedAdmissionId,
  onPin,
}: {
  beds: BedBoardRow[];
  loading: boolean;
  error: string | null;
  pinnedAdmissionId: number | null;
  onPin: (admissionId: number) => void;
}) {
  const wards = useMemo(() => groupBeds(beds), [beds]);

  return (
    <section
      aria-label="Bed board"
      className="flex min-h-0 flex-col overflow-hidden rounded-lg border border-slate-200 bg-white"
    >
      <header className="flex shrink-0 items-center gap-3 border-b border-slate-200 bg-slate-50 px-3 py-1">
        <span className="cl-micro font-bold uppercase tracking-wide text-slate-600">Bed board</span>
        <span className="cl-micro text-slate-500">Read only. Select an occupied bed to open its admission.</span>
      </header>
      <div className="min-h-0 flex-1 overflow-auto px-3 py-2">
        {error ? (
          <p className="cl-body text-red-700">{error}</p>
        ) : loading && beds.length === 0 ? (
          <p className="cl-body text-slate-400">Loading beds…</p>
        ) : beds.length === 0 ? (
          <p className="cl-body text-slate-500">No beds in this view.</p>
        ) : (
          <div className="flex flex-col gap-3">
            {wards.map((group) => (
              <div key={group.ward?.id ?? "none"} className="flex flex-col gap-1.5">
                <div className="flex items-center gap-2 cl-meta font-bold text-slate-800">
                  <span>{group.ward?.name ?? "No ward"}</span>
                  {group.ward?.code ? <span className="font-mono text-slate-500">{group.ward.code}</span> : null}
                </div>
                {group.rooms.map((room) => (
                  <div key={room.room?.id ?? "none"} className="flex items-start gap-2">
                    <div className="w-[88px] shrink-0 pt-1 font-mono cl-micro text-slate-600" title={room.room?.name ?? ""}>
                      {room.room?.code ?? room.room?.name ?? "No room"}
                    </div>
                    <div className="flex flex-wrap gap-1.5">
                      {room.beds.map((bed) => (
                        <BedCell
                          key={bed.id}
                          bed={bed}
                          pinned={bed.admission !== null && bed.admission.id === pinnedAdmissionId}
                          onPin={onPin}
                        />
                      ))}
                    </div>
                  </div>
                ))}
              </div>
            ))}
          </div>
        )}
      </div>
    </section>
  );
}
