import Image from "next/image";

export function YoyaLogo({ compact = false }: { compact?: boolean }) {
  const size = compact ? 136 : 172;

  return (
    <Image
      src="/images/yoya-hospital-logo.png"
      alt="YOYA Hospital"
      width={size}
      height={size}
      priority
      className={
        compact
          ? "h-auto w-[7rem] object-contain sm:w-[7.5rem] lg:w-[8rem]"
          : "h-auto w-[8.5rem] object-contain sm:w-[9.5rem] lg:w-[10.75rem]"
      }
    />
  );
}

export function ClinicalDecoration({ centered = false }: { centered?: boolean }) {
  return (
    <div
      aria-hidden="true"
      className={`pointer-events-none absolute inset-0 overflow-hidden ${
        centered ? "opacity-65" : "opacity-90"
      }`}
    >
      <div className="absolute -right-32 top-[-14rem] h-[42rem] w-[42rem] rounded-full border border-cyan-200/50" />
      <div className="absolute -right-20 top-[-10rem] h-[38rem] w-[38rem] rounded-full border border-emerald-100/70" />
      <div className="absolute -bottom-64 right-8 h-[34rem] w-[34rem] rounded-full border border-cyan-200/45" />
      <div className="absolute right-[16%] top-[22%] h-3 w-3 rounded-full border border-cyan-300/60" />
      <div className="absolute right-[7%] top-[51%] h-2.5 w-2.5 rounded-full border border-emerald-300/55" />
      <div className="absolute bottom-[17%] right-[20%] h-4 w-4 rounded-full border border-cyan-200/70" />
      <svg
        viewBox="0 0 520 720"
        fill="none"
        className="absolute -right-24 top-0 hidden h-full w-[32rem] text-cyan-300/50 sm:block"
      >
        <path d="M505 -30C302 115 253 254 353 411c55 85 83 165 39 329" stroke="currentColor" />
        <path d="M540 9C330 154 286 292 378 433c47 72 77 145 58 268" stroke="currentColor" />
        <path d="M456 28C271 183 237 327 337 462c49 66 67 135 41 247" stroke="currentColor" />
      </svg>
      <svg
        viewBox="0 0 420 100"
        fill="none"
        className={`absolute text-white/90 ${
          centered
            ? "bottom-[13%] left-1/2 hidden w-[28rem] -translate-x-1/2 sm:block"
            : "bottom-[29%] right-[-1rem] hidden w-[28rem] sm:block"
        }`}
      >
        <path
          d="M0 51h118l12-34 17 72 18-53 17 33 13-18h225"
          stroke="currentColor"
          strokeWidth="2"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
      </svg>
    </div>
  );
}

export function PublicPageFooter() {
  return (
    <footer className="relative z-10 flex min-h-15 items-center justify-center border-t border-slate-200/80 px-6 text-center text-xs text-slate-500 sm:text-sm">
      Powered by Synergy Tech Solution
    </footer>
  );
}
