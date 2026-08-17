"use client";

import { DOCTOR_BUCKETS, type DoctorBucket } from "@/lib/doctor-format";

/**
 * The All / Wait / Review / Finished strip.
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
  { chip: string; bar: string; label: string }
> = {
  wait: {
    chip: "border-amber-400 bg-amber-50 text-amber-900",
    bar: "bg-amber-400",
    label: "Awaiting triage",
  },
  review: {
    chip: "border-cyan-400 bg-cyan-50 text-cyan-900",
    bar: "bg-cyan-400",
    label: "Ready to review",
  },
  finished: {
    chip: "border-slate-300 bg-slate-100 text-slate-700",
    bar: "bg-slate-400",
    label: "Seen today",
  },
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
    <section className="flex shrink-0 flex-wrap items-center gap-2 rounded border border-slate-200 bg-white px-2 py-1.5 shadow-sm">
      <div className="flex items-center gap-1" role="group" aria-label="Queue filter">
        {DOCTOR_BUCKETS.map((bucket) => {
          const selected = active === bucket.key;
          const count = counts[bucket.key];
          const tone =
            bucket.key === "all"
              ? { chip: "border-slate-300 bg-slate-100 text-slate-800" }
              : TONE[bucket.key];

          return (
            <button
              key={bucket.key}
              type="button"
              onClick={() => onChange(bucket.key)}
              aria-pressed={selected}
              className={`inline-flex h-7 items-center gap-1.5 rounded border px-2 text-[11px] font-bold uppercase tracking-wide outline-none transition-colors focus-visible:ring-2 focus-visible:ring-emerald-600 ${
                selected
                  ? `${tone.chip} shadow-[inset_0_-2px_0_rgba(0,0,0,0.18)]`
                  : "border-slate-200 bg-white text-slate-600 hover:bg-slate-50"
              }`}
            >
              {bucket.label}
              <span className="tabular-nums opacity-70">{count}</span>
            </button>
          );
        })}
      </div>

      {total > 0 ? (
        <div
          className="flex h-2 min-w-[140px] flex-1 overflow-hidden rounded-full bg-slate-100"
          role="img"
          aria-label={`${counts.wait} awaiting triage, ${counts.review} ready to review, ${counts.finished} seen today`}
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
      ) : null}
    </section>
  );
}
