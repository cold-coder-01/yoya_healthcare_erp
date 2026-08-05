import Link from "next/link";

const items = [
  { label: "Dashboard", href: "#", disabled: true },
  { label: "Evaluation Queue", href: "/triage", disabled: false },
  { label: "Consultation", href: "#", disabled: true },
  { label: "Laboratory", href: "#", disabled: true },
  { label: "Radiology", href: "#", disabled: true },
  { label: "Pharmacy", href: "#", disabled: true },
  { label: "Inpatient", href: "#", disabled: true },
  { label: "Billing", href: "#", disabled: true },
  { label: "Reports", href: "#", disabled: true },
];

export default function ClinicalSidebar() {
  return (
    <aside className="hidden w-64 shrink-0 bg-emerald-950 text-white lg:flex lg:flex-col">
      <div className="border-b border-white/10 px-5 py-5">
        <div className="text-lg font-semibold tracking-tight">YOYA EMR</div>
        <div className="mt-1 text-xs text-emerald-100">Clinical UAT</div>
      </div>
      <nav className="flex-1 space-y-1 px-3 py-4">
        {items.map((item) =>
          item.disabled ? (
            <div
              key={item.label}
              className="flex items-center justify-between rounded-md px-3 py-2 text-sm text-emerald-100/55"
            >
              <span>{item.label}</span>
              <span className="text-[10px] uppercase tracking-wide">Soon</span>
            </div>
          ) : (
            <Link
              key={item.label}
              href={item.href}
              className="block rounded-md bg-white/10 px-3 py-2 text-sm font-medium text-white ring-1 ring-white/10"
            >
              {item.label}
            </Link>
          ),
        )}
      </nav>
      <div className="border-t border-white/10 px-5 py-4 text-xs text-emerald-100/75">
        Healthcare operations console
      </div>
    </aside>
  );
}
