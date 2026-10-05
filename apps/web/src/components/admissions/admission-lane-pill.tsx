import { laneCode, laneLabel, laneTone } from "@/lib/admissions-desk-format";

/**
 * A lane, as the SERVER decided it. Text AND tone: the code reads without
 * colour, the tone reads at a glance.
 */
export default function AdmissionLanePill({
  lane,
  compact = false,
}: {
  lane: string;
  compact?: boolean;
}) {
  return (
    <span
      title={laneLabel(lane)}
      className={`inline-flex shrink-0 items-center rounded border px-1.5 py-px font-mono cl-micro font-bold tracking-wide ${laneTone(lane)}`}
    >
      {compact ? laneCode(lane) : laneLabel(lane)}
    </span>
  );
}
