// Reads ODOO_BASE_URL and forwards the Odoo session cookie. `server-only`
// makes importing this from a client component a BUILD error, which is what
// keeps the gateway URL and session id out of the browser bundle. The shared
// lib/odoo-client.ts carries the same guard.
import "server-only";

import { cookies } from "next/headers";
import { NextResponse } from "next/server";
import { OdooClientError, YOYA_ODOO_SESSION_COOKIE } from "@/lib/odoo-client";
import type { ApiEnvelope } from "@/types/reception";

export function errorResponse(code: string, message: string, status: number) {
  return NextResponse.json(
    {
      success: false,
      error: {
        code,
        message,
      },
    },
    { status },
  );
}

export async function requireOdooSession() {
  const sessionId = (await cookies()).get(YOYA_ODOO_SESSION_COOKIE)?.value;

  if (!sessionId) {
    return {
      ok: false as const,
      response: errorResponse("unauthenticated", "Odoo session is missing.", 401),
    };
  }

  return {
    ok: true as const,
    sessionId,
  };
}

function getOdooBaseUrl() {
  const baseUrl = process.env.ODOO_BASE_URL?.replace(/\/+$/, "");
  if (!baseUrl) {
    throw new OdooClientError(
      "odoo_config_missing",
      "Odoo connection is not configured.",
      500,
    );
  }
  return baseUrl;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

async function readJson(response: Response): Promise<unknown> {
  try {
    return await response.json();
  } catch {
    return null;
  }
}

function normalizeError(value: unknown, fallbackStatus: number) {
  if (isRecord(value)) {
    const message =
      typeof value.message === "string" && value.message.trim()
        ? value.message
        : "The reception service returned an error.";
    const code =
      typeof value.code === "string" && value.code.trim()
        ? value.code
        : `odoo_http_${fallbackStatus}`;
    return { ...value, code, message };
  }

  return {
    code: `odoo_http_${fallbackStatus}`,
    message: "The reception service returned an error.",
  };
}

function normalizeEnvelope<T>(payload: unknown, status: number): ApiEnvelope<T> {
  if (isRecord(payload) && payload.success === true && "data" in payload) {
    return {
      success: true,
      data: payload.data as T,
    };
  }

  if (isRecord(payload) && payload.success === false) {
    return {
      success: false,
      error: normalizeError(payload.error, status),
    };
  }

  if (status >= 400) {
    return {
      success: false,
      error: normalizeError(isRecord(payload) ? payload.error : null, status),
    };
  }

  return {
    success: false,
    error: {
      code: "invalid_response",
      message: "The reception service returned an invalid response.",
      details: payload,
    },
  };
}

export async function forwardOdooResult<T>(result: {
  status: number;
  body: ApiEnvelope<T>;
}) {
  return NextResponse.json(result.body, { status: result.status });
}

export function handleRouteError(
  error: unknown,
  fallbackCode: string,
  fallbackMessage: string,
) {
  if (error instanceof OdooClientError) {
    return NextResponse.json(
      {
        success: false,
        error: error.toApiError(),
      } satisfies ApiEnvelope<never>,
      { status: error.status },
    );
  }

  return errorResponse(fallbackCode, fallbackMessage, 500);
}

export function withQuery(path: string, params: Record<string, string | undefined>) {
  const query = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value) {
      query.set(key, value);
    }
  }
  const queryString = query.toString();
  return queryString ? `${path}?${queryString}` : path;
}

export async function callOdooApi<T>(
  sessionId: string,
  path: string,
  method: "GET" | "POST",
  body?: unknown,
) {
  const baseUrl = getOdooBaseUrl();

  let response: Response;
  try {
    response = await fetch(`${baseUrl}${path}`, {
      method,
      headers: {
        Cookie: `session_id=${sessionId}`,
        ...(body === undefined ? {} : { "Content-Type": "application/json" }),
      },
      body: body === undefined ? undefined : JSON.stringify(body),
      cache: "no-store",
    });
  } catch {
    throw new OdooClientError(
      "odoo_unreachable",
      "Unable to reach Odoo.",
      502,
    );
  }

  return {
    status: response.status,
    body: normalizeEnvelope<T>(await readJson(response), response.status),
  };
}

export async function readJsonObject(request: Request) {
  let body: unknown;
  try {
    body = await request.json();
  } catch {
    return {
      ok: false as const,
      response: errorResponse("invalid_json", "Request body must be valid JSON.", 400),
    };
  }

  if (typeof body !== "object" || body === null || Array.isArray(body)) {
    return {
      ok: false as const,
      response: errorResponse("invalid_json", "Request body must be a JSON object.", 400),
    };
  }

  return {
    ok: true as const,
    body: body as Record<string, unknown>,
  };
}
