"use client";

import { FormEvent, useState } from "react";
import { useRouter } from "next/navigation";

import {
  CLINICAL_ROUTE,
  landingRouteForRoles,
  parseReceptionRoles,
} from "@/lib/reception-roles";
import type { ApiEnvelope, ReceptionSession } from "@/types/reception";

function getErrorMessage(payload: unknown, fallback: string) {
  if (
    typeof payload === "object" &&
    payload !== null &&
    "error" in payload &&
    typeof payload.error === "object" &&
    payload.error !== null &&
    "message" in payload.error &&
    typeof payload.error.message === "string"
  ) {
    return payload.error.message;
  }

  return fallback;
}

export default function LoginPage() {
  const router = useRouter();
  const [login, setLogin] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSubmitting(true);
    setError(null);

    try {
      const loginResponse = await fetch("/api/auth/login", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ login, password }),
      });
      const loginPayload: unknown = await loginResponse.json();

      if (!loginResponse.ok) {
        setError(getErrorMessage(loginPayload, "Unable to sign in."));
        return;
      }

      setPassword("");

      // Authentication has already succeeded and the session cookie is set, so
      // a failed role lookup must never strand the user on the login screen.
      // It only costs us the ability to pick the better landing route, and we
      // fall back to the clinical workspace.
      //
      // NOTE: the reception session payload carries NO `nurse` flag. Routing is
      // therefore by elimination -- no reception-side role means clinical --
      // rather than by reading a field that does not exist. See
      // lib/reception-roles.ts.
      let destination = CLINICAL_ROUTE;
      try {
        const sessionResponse = await fetch("/api/reception/session", {
          method: "GET",
          cache: "no-store",
        });
        const sessionPayload = (await sessionResponse.json()) as
          | ApiEnvelope<ReceptionSession>
          | null;

        if (sessionResponse.ok && sessionPayload?.success) {
          destination = landingRouteForRoles(
            parseReceptionRoles(sessionPayload.data?.roles),
          );
        }
      } catch {
        // Keep the fallback destination; the user is signed in either way.
      }

      router.push(destination);
      router.refresh();
    } catch {
      setError("Unable to reach the YOYA EMR gateway.");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <main className="min-h-screen bg-slate-50 px-6 py-10 text-slate-950">
      <section className="mx-auto flex min-h-[calc(100vh-5rem)] w-full max-w-md flex-col justify-center">
        <div className="mb-8">
          <p className="text-sm font-semibold uppercase tracking-[0.18em] text-emerald-700">
            YOYA General Hospital
          </p>
          <h1 className="mt-3 text-3xl font-semibold tracking-tight text-slate-950">
            Sign in to EMR POC
          </h1>
          <p className="mt-3 text-sm leading-6 text-slate-600">
            Use your Odoo account to open the appointment bridge.
          </p>
        </div>

        <form
          onSubmit={handleSubmit}
          className="rounded-lg border border-slate-200 bg-white p-6 shadow-sm"
        >
          <label className="block text-sm font-medium text-slate-700" htmlFor="login">
            Username or email
          </label>
          <input
            id="login"
            name="login"
            type="text"
            autoComplete="username"
            value={login}
            onChange={(event) => setLogin(event.target.value)}
            className="mt-2 h-11 w-full rounded-md border border-slate-300 px-3 text-sm outline-none transition focus:border-emerald-600 focus:ring-2 focus:ring-emerald-100"
            required
          />

          <label
            className="mt-5 block text-sm font-medium text-slate-700"
            htmlFor="password"
          >
            Password
          </label>
          <input
            id="password"
            name="password"
            type="password"
            autoComplete="current-password"
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            className="mt-2 h-11 w-full rounded-md border border-slate-300 px-3 text-sm outline-none transition focus:border-emerald-600 focus:ring-2 focus:ring-emerald-100"
            required
          />

          {error ? (
            <div className="mt-5 rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">
              {error}
            </div>
          ) : null}

          <button
            type="submit"
            disabled={submitting}
            className="mt-6 h-11 w-full rounded-md bg-emerald-700 px-4 text-sm font-semibold text-white transition hover:bg-emerald-800 disabled:cursor-not-allowed disabled:bg-slate-400"
          >
            {submitting ? "Signing in..." : "Sign in"}
          </button>
        </form>
      </section>
    </main>
  );
}
