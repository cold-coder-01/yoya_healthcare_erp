const STYLE_BY_STATUS: Record<string, string> = {
  not_started: "bg-slate-100 text-slate-700 ring-slate-200",
  waiting: "bg-cyan-50 text-cyan-700 ring-cyan-100",
  in_progress: "bg-amber-50 text-amber-800 ring-amber-100",
  completed: "bg-emerald-50 text-emerald-800 ring-emerald-100",
  done: "bg-emerald-50 text-emerald-800 ring-emerald-100",
  cancelled: "bg-red-50 text-red-700 ring-red-100",
  routine: "bg-slate-100 text-slate-700 ring-slate-200",
  urgent: "bg-amber-50 text-amber-800 ring-amber-100",
  emergency: "bg-red-50 text-red-700 ring-red-100",
};

function label(value: string | null | undefined) {
  if (!value) {
    return "Not set";
  }
  return value
    .split("_")
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(" ");
}

export default function StatusBadge({ value }: { value?: string | null }) {
  const key = value ?? "not_started";
  const style = STYLE_BY_STATUS[key] ?? "bg-slate-100 text-slate-700 ring-slate-200";

  return (
    <span className={`inline-flex rounded-full px-2.5 py-1 text-xs font-semibold ring-1 ${style}`}>
      {label(value)}
    </span>
  );
}
