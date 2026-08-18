"use client";

import { DOCTOR_BUCKETS, type DoctorBucket } from "@/lib/doctor-format";

/**
 * The All / Wait / Review / Finished strip.
 *
 * Read as a SEGMENTED CONTROL rather than four loose boxes: one enclosing
 * track, the active segment lifted onto white with a coloured underline. That
 * is the affordance a doctor already knows from every worklist tool, and it
 * makes "which filter am I on" answerable at a glance instead of by comparing
 * border tints.
 *
 * The count is the thing being scanned, so it is the larger of the two numbers
 * in each segment and sits on its own line under the label.
 *
 * The vendor OPD screen puts a proportional colour bar directly under these
 * four choices, and it is the first thing a doctor looks at: it answers "how
 * much work is left" without reading a single row. That is genuinely useful
 * information design, so it is kept -- rendered from the real counts rather
 * than as decoration, and hidden entirely when the queue is empty so an empty
 * clinic does not draw a meaningless bar.
 */

const TONE: Record<
  Exclude<DoctorBucket, "all">,
  { text: string; rule: string; bar: string; label: string }
> = {
  wait: {
    text: "text-amber-800",
    rule: "bg-amber-500",
    bar: "bg-amber-400",
    label: "Still waiting",
  },
  review: {
    // Emerald, matching the Ready badge and the primary action. Review IS the
    // workable set, and it is the only bucket allowed to use the accent.
    text: "text-emerald-800",
    rule: "bg-emerald-600",
    bar: "bg-emerald-500",
    label: "Ready to review",
  },
  finished: {
    text: "text-slate-600",
    rule: "bg-slate-400",
    bar: "bg-slate-300",
    label: "Seen today",
  },
};

const ALL_TONE = {
  text: "text-slate-800",
  rule: "bg-slate-500",
};

export default function DoctorBucketBar({
  counts,
  active,
  onChange,
}: {
  counts: { all: number; wait: number; review: number; finished: number };
  active: DoctorBucket;
  onChange: (bucket: DoctorBucket) => void;
}) {
  const total = counts.all;

  return (
    <section className="flex shrink-0 flex-wrap items-center gap-3 rounded-lg border border-slate-200 bg-white px-2 py-1.5 shadow-sm">
      <div
        className="flex items-stretch gap-0.5 rounded-md bg-slate-100 p-0.5"
        role="group"
        aria-label="Queue filter"
      >
        {DOCTOR_BUCKETS.map((bucket) => {
          const selected = active === bucket.key;
          const count = counts[bucket.key];
          const tone = bucket.key === "all" ? ALL_TONE : TONE[bucket.key];

          return (
            <button
              key={bucket.key}
              type="button"
              onClick={() => onChange(bucket.key)}
              aria-pressed={selected}
              className={`relative flex min-w-[62px] flex-col items-center justify-center rounded px-2.5 py-1 outline-none transition-colors focus-visible:ring-2 focus-visible:ring-emerald-600 ${
                selected
                  ? "bg-white shadow-sm"
                  : "text-slate-500 hover:bg-white/60 hover:text-slate-700"
              }`}
            >
              <span
                className={`text-[9px] font-bold uppercase tracking-[0.08em] ${
                  selected ? tone.text : ""
                }`}
              >
                {bucket.label}
              </span>
              <span
                className={`text-[15px] font-bold leading-none tabular-nums ${
                  selected ? tone.text : "text-slate-600"
                }`}
              >
                {count}
              </span>
              {/* The active underline. Colour carries the bucket's meaning. */}
              {selected ? (
                <span
                  aria-hidden
                  className={`absolute inset-x-1.5 bottom-0 h-0.5 rounded-full ${tone.rule}`}
                />
              ) : null}
            </button>
          );
        })}
      </div>

      {total > 0 ? (
        <div className="flex min-w-[160px] flex-1 flex-col gap-1">
          <div
            className="flex h-1.5 overflow-hidden rounded-full bg-slate-100"
            role="img"
            aria-label={`${counts.wait} still waiting, ${counts.review} ready to review, ${counts.finished} seen today`}
          >
            {(["wait", "review", "finished"] as const).map((key) =>
              counts[key] > 0 ? (
                <span
                  key={key}
                  className={TONE[key].bar}
                  style={{ width: `${(counts[key] / total) * 100}%` }}
                  title={`${TONE[key].label}: ${counts[key]}`}
                />
              ) : null,
            )}
          </div>
          {/* A legend, so the bar is readable without hovering it. */}
          <div className="flex flex-wrap items-center gap-x-3 gap-y-0.5">
            {(["wait", "review", "finished"] as const).map((key) => (
              <span
                key={key}
                className="flex items-center gap-1 text-[9px] font-semibold uppercase tracking-wide text-slate-500"
              >
                <span
                  aria-hidden
                  className={`h-1.5 w-1.5 rounded-full ${TONE[key].bar}`}
                />
                {TONE[key].label}
              </span>
            ))}
          </div>
        </div>
      ) : null}
    </section>
  );
}
