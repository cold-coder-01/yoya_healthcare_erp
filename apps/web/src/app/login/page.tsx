"use client";

import Image from "next/image";
import { FormEvent, useEffect, useState } from "react";
import { useRouter } from "next/navigation";

import { YoyaLogo } from "@/components/public-page-chrome";
import {
  greetingForHour,
  matchesRememberedIdentity,
  persistRememberedIdentity,
  readRememberedIdentity,
  type RememberedIdentity,
} from "@/lib/remembered-identity";
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

function text(value: unknown) {
  return typeof value === "string" && value.trim() ? value.trim() : null;
}

function authenticatedUserFromLoginPayload(payload: unknown) {
  if (
    typeof payload !== "object" ||
    payload === null ||
    !("data" in payload) ||
    typeof payload.data !== "object" ||
    payload.data === null ||
    !("user" in payload.data) ||
    typeof payload.data.user !== "object" ||
    payload.data.user === null
  ) {
    return null;
  }

  const user = payload.data.user as Record<string, unknown>;
  const displayName = text(user.name);

  return displayName
    ? { displayName, login: text(user.username) }
    : null;
}

function initials(displayName: string) {
  return displayName
    .split(/\s+/)
    .slice(0, 2)
    .map((part) => part.charAt(0).toUpperCase())
    .join("");
}

export default function LoginPage() {
  const router = useRouter();
  const [login, setLogin] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [showPassword, setShowPassword] = useState(false);
  const [rememberedIdentity, setRememberedIdentity] =
    useState<RememberedIdentity | null>(null);

  useEffect(() => {
    const timeout = window.setTimeout(() => {
      setRememberedIdentity(readRememberedIdentity(window.localStorage));
    }, 0);

    return () => window.clearTimeout(timeout);
  }, []);

  const recognizedIdentity = matchesRememberedIdentity(
    login,
    rememberedIdentity,
  )
    ? rememberedIdentity
    : null;

  function rememberSuccessfulIdentity(
    authenticatedLogin: unknown,
    displayName: unknown,
  ) {
    const identity = persistRememberedIdentity(
      window.localStorage,
      authenticatedLogin,
      displayName,
    );
    if (identity) {
      setRememberedIdentity(identity);
    }
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSubmitting(true);
    setError(null);

    try {
      const loginResponse = await fetch("/api/auth/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ login, password }),
      });
      const loginPayload: unknown = await loginResponse.json();

      if (!loginResponse.ok) {
        setError(getErrorMessage(loginPayload, "Unable to sign in."));
        return;
      }

      setPassword("");

      const authenticatedUser =
        authenticatedUserFromLoginPayload(loginPayload);
      if (authenticatedUser) {
        rememberSuccessfulIdentity(
          authenticatedUser.login ?? login,
          authenticatedUser.displayName,
        );
      }

      // Authentication has already succeeded and the session cookie is set, so
      // a failed role lookup must never strand the user on the login screen.
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
          const sessionUser = sessionPayload.data?.user;
          rememberSuccessfulIdentity(sessionUser?.login, sessionUser?.name);
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
    <main className="relative flex min-h-screen items-center justify-center overflow-hidden bg-[#eff7f7] px-3 py-4 font-sans text-slate-950 sm:px-6 sm:py-6">
      <div
        aria-hidden="true"
        className="absolute -left-32 -top-40 h-[34rem] w-[34rem] rounded-full bg-cyan-200/30 blur-3xl"
      />
      <div
        aria-hidden="true"
        className="absolute -bottom-44 -right-32 h-[34rem] w-[34rem] rounded-full bg-emerald-200/25 blur-3xl"
      />

      <section className="relative grid w-full max-w-[1040px] overflow-hidden rounded-[1.75rem] border border-white/80 bg-white shadow-[0_28px_80px_rgba(8,58,67,0.18)] lg:h-[680px] lg:grid-cols-[46%_54%]">
        <div className="relative z-20 min-h-[250px] overflow-hidden bg-[#057b83] text-white lg:min-h-0">
          <Image
            src="/images/yoya-login-welcome-3d.png"
            alt="Friendly YOYA hospital doctor"
            fill
            priority
            sizes="(min-width: 1024px) 478px, 100vw"
            className="pointer-events-none object-cover object-center select-none"
          />
          <div
            aria-hidden="true"
            className="absolute inset-0 bg-[linear-gradient(90deg,rgba(2,79,89,0.62)_0%,rgba(2,91,100,0.24)_52%,rgba(2,91,100,0)_78%)]"
          />

          <div className="relative z-30 max-w-[245px] px-8 pt-9 sm:px-11 sm:pt-11 lg:px-12 lg:pt-20">
            <p className="text-xs font-semibold uppercase tracking-[0.22em] text-cyan-50/90">
              YOYA clinical system
            </p>
            <h1 className="mt-4 text-4xl font-bold tracking-[-0.04em] sm:text-5xl">
              HELLO!
            </h1>
            <p className="mt-4 text-sm leading-6 text-cyan-50 sm:text-base">
              Welcome to your clinical workspace. Sign in to continue.
            </p>
          </div>
        </div>

        <div className="relative z-10 flex min-h-[560px] flex-col bg-white lg:min-h-0">
          <div className="flex flex-1 items-center justify-center px-6 py-8 sm:px-10 lg:px-14 lg:py-7">
            <div className="w-full max-w-[410px]">
              <div className="flex justify-center">
                <YoyaLogo compact />
              </div>
              <div className="mt-1 text-center">
                <p className="text-[0.7rem] font-bold uppercase tracking-[0.24em] text-emerald-700">
                  YOYA General Hospital
                </p>
                <h2 className="mt-3 text-3xl font-semibold tracking-[-0.035em] text-[#07152f]">
                  Sign in to EMR POC
                </h2>
                <p className="mt-2 text-sm leading-5 text-slate-500">
                  Use your Odoo account to open the appointment bridge.
                </p>
              </div>

              <form onSubmit={handleSubmit} className="mt-6">
                <label className="block text-sm font-medium text-slate-700" htmlFor="login">
                  Username or email
                </label>
                <div className="relative mt-2">
                  <input
                    id="login"
                    name="login"
                    type="text"
                    autoComplete="username"
                    value={login}
                    onChange={(event) => setLogin(event.target.value)}
                    className={`h-12 w-full rounded-lg border bg-white px-3.5 pr-11 text-sm text-slate-900 outline-none transition focus:ring-3 ${
                      recognizedIdentity
                        ? "border-emerald-600 focus:border-emerald-600 focus:ring-emerald-100"
                        : "border-slate-300 focus:border-emerald-600 focus:ring-emerald-100"
                    }`}
                    required
                  />
                  {recognizedIdentity ? (
                    <svg aria-hidden="true" viewBox="0 0 24 24" fill="none" className="absolute right-3.5 top-1/2 h-5 w-5 -translate-y-1/2 text-emerald-600">
                      <path d="m5 12 4 4L19 6" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
                    </svg>
                  ) : null}
                </div>

                <div
                  aria-live="polite"
                  aria-atomic="true"
                  aria-hidden={!recognizedIdentity}
                  className={`grid transition-[grid-template-rows,opacity,margin] duration-300 ease-out ${
                    recognizedIdentity
                      ? "mt-3 grid-rows-[1fr] opacity-100"
                      : "mt-0 grid-rows-[0fr] opacity-0"
                  }`}
                >
                  <div className="overflow-hidden">
                    <div className="flex items-center gap-3 rounded-lg border border-emerald-200 bg-emerald-50/90 px-3.5 py-3 text-emerald-950">
                      <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-emerald-700 text-xs font-bold text-white">
                        {recognizedIdentity
                          ? initials(recognizedIdentity.displayName)
                          : ""}
                      </span>
                      <span className="min-w-0 text-sm leading-5">
                        <span className="block font-semibold">
                          {recognizedIdentity
                            ? `${greetingForHour(new Date().getHours())}, ${recognizedIdentity.displayName}`
                            : ""}
                        </span>
                        <span className="block text-emerald-800">Welcome back</span>
                      </span>
                    </div>
                  </div>
                </div>

                <label className="mt-5 block text-sm font-medium text-slate-700" htmlFor="password">
                  Password
                </label>
                <div className="relative mt-2">
                  <input
                    id="password"
                    name="password"
                    type={showPassword ? "text" : "password"}
                    autoComplete="current-password"
                    value={password}
                    onChange={(event) => setPassword(event.target.value)}
                    className="h-12 w-full rounded-lg border border-slate-300 bg-white px-3.5 pr-12 text-sm text-slate-900 outline-none transition focus:border-emerald-600 focus:ring-3 focus:ring-emerald-100"
                    required
                  />
                  <button
                    type="button"
                    onClick={() => setShowPassword((visible) => !visible)}
                    className="absolute right-1.5 top-1/2 flex h-9 w-9 -translate-y-1/2 items-center justify-center rounded-md text-slate-500 transition hover:bg-slate-100 hover:text-slate-700 focus-visible:outline-2 focus-visible:outline-emerald-700"
                    aria-label={showPassword ? "Hide password" : "Show password"}
                    aria-pressed={showPassword}
                  >
                    {showPassword ? (
                      <svg aria-hidden="true" viewBox="0 0 24 24" fill="none" className="h-5 w-5">
                        <path d="m3 3 18 18M10.6 10.7a2 2 0 0 0 2.7 2.7M9.9 4.3A10 10 0 0 1 12 4c5.5 0 9 5 9 5a16 16 0 0 1-2.1 2.5M6.6 6.6C4.3 8.1 3 10 3 10s3.5 5 9 5c1 0 1.9-.2 2.7-.4" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
                      </svg>
                    ) : (
                      <svg aria-hidden="true" viewBox="0 0 24 24" fill="none" className="h-5 w-5">
                        <path d="M3 12s3.5-5 9-5 9 5 9 5-3.5 5-9 5-9-5-9-5Z" stroke="currentColor" strokeWidth="1.8" strokeLinejoin="round" />
                        <circle cx="12" cy="12" r="2.5" stroke="currentColor" strokeWidth="1.8" />
                      </svg>
                    )}
                  </button>
                </div>

                {error ? (
                  <div role="alert" className="mt-4 rounded-lg border border-red-200 bg-red-50 px-3.5 py-2.5 text-sm text-red-700">
                    {error}
                  </div>
                ) : null}

                <button
                  type="submit"
                  disabled={submitting}
                  className="mt-6 h-12 w-full rounded-lg bg-emerald-700 px-4 text-sm font-semibold text-white shadow-[0_8px_18px_rgba(4,120,87,0.16)] transition hover:bg-emerald-800 focus-visible:outline-2 focus-visible:outline-offset-3 focus-visible:outline-emerald-700 disabled:cursor-not-allowed disabled:bg-slate-400"
                >
                  {submitting ? "Signing in..." : "Sign in"}
                </button>
              </form>
            </div>
          </div>

          <footer className="border-t border-slate-200 px-6 py-4 text-center text-xs text-slate-500">
            Powered by Synergy Tech Solution
          </footer>
        </div>
      </section>
    </main>
  );
}
