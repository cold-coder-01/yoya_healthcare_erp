import Link from "next/link";

export default function Home() {
  return (
    <main className="min-h-screen bg-slate-50 px-6 py-10 text-slate-950">
      <section className="mx-auto flex min-h-[calc(100vh-5rem)] w-full max-w-3xl flex-col justify-center">
        <p className="text-sm font-semibold uppercase tracking-[0.18em] text-emerald-700">
          YOYA General Hospital
        </p>
        <h1 className="mt-4 text-4xl font-semibold tracking-tight">
          EMR integration proof of concept
        </h1>
        <p className="mt-4 max-w-2xl text-base leading-7 text-slate-600">
          Sign in with Odoo, view demo appointments, and start a consultation
          through the Next.js server-side bridge.
        </p>
        <div className="mt-8 flex flex-col gap-3 sm:flex-row">
          <Link
            href="/login"
            className="inline-flex h-11 items-center justify-center rounded-md bg-emerald-700 px-5 text-sm font-semibold text-white transition hover:bg-emerald-800"
          >
            Login
          </Link>
          <Link
            href="/appointments"
            className="inline-flex h-11 items-center justify-center rounded-md border border-slate-300 bg-white px-5 text-sm font-semibold text-slate-700 transition hover:bg-slate-100"
          >
            Appointments
          </Link>
        </div>
      </section>
    </main>
  );
}
