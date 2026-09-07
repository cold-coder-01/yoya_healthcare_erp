import Link from "next/link";

import {
  ClinicalDecoration,
  PublicPageFooter,
  YoyaLogo,
} from "@/components/public-page-chrome";

export default function Home() {
  return (
    <main className="min-h-screen bg-[#f6f9f8] px-3 py-3 font-sans text-slate-950 sm:px-5 sm:py-5 lg:px-7 lg:py-6">
      <section className="relative mx-auto flex min-h-[calc(100vh-1.5rem)] w-full max-w-[1240px] flex-col overflow-hidden rounded-[1.35rem] border border-white bg-white shadow-[0_18px_55px_rgba(15,50,55,0.09)] sm:min-h-[calc(100vh-2.5rem)] lg:min-h-[calc(100vh-3rem)]">
        <div className="absolute inset-y-0 right-0 w-[46%] bg-[radial-gradient(circle_at_75%_46%,rgba(22,188,218,0.22),rgba(209,250,244,0.38)_35%,rgba(255,255,255,0)_72%)]" />
        <ClinicalDecoration />

        <div className="relative z-10 px-7 pt-6 sm:px-12 sm:pt-8 lg:px-16">
          <YoyaLogo />
        </div>

        <div className="relative z-10 flex flex-1 items-center px-7 py-10 sm:px-12 lg:px-16 lg:pb-16">
          <div className="max-w-[660px]">
            <p className="text-xs font-bold uppercase tracking-[0.25em] text-emerald-700 sm:text-sm">
              YOYA General Hospital
            </p>
            <h1 className="mt-5 text-[2.35rem] font-semibold leading-[1.08] tracking-[-0.04em] text-[#07152f] sm:text-5xl lg:text-[3.25rem]">
              Clinical Evaluation UAT
            </h1>
            <p className="mt-6 max-w-[590px] text-base leading-7 text-slate-600 sm:text-lg sm:leading-8">
              Sign in with Odoo and open the real clinical evaluation queue for
              triage, vitals, billing clearance awareness, and consultation start.
            </p>
            <div className="mt-9 flex flex-col gap-3 sm:flex-row sm:gap-4">
              <Link
                href="/login"
                className="inline-flex h-13 min-w-36 items-center justify-center rounded-lg bg-emerald-700 px-7 text-base font-semibold text-white shadow-[0_8px_20px_rgba(4,120,87,0.18)] transition hover:bg-emerald-800 focus-visible:outline-2 focus-visible:outline-offset-3 focus-visible:outline-emerald-700"
              >
                Login
              </Link>
              <Link
                href="/triage"
                className="inline-flex h-13 min-w-52 items-center justify-center rounded-lg border border-emerald-700 bg-white/90 px-7 text-base font-semibold text-emerald-700 transition hover:bg-emerald-50 focus-visible:outline-2 focus-visible:outline-offset-3 focus-visible:outline-emerald-700"
              >
                Evaluation Queue
              </Link>
            </div>
          </div>
        </div>

        <PublicPageFooter />
      </section>
    </main>
  );
}
