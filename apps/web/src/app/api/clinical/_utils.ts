import { cookies } from "next/headers";
import { NextResponse } from "next/server";
import {
  OdooClientError,
  YOYA_ODOO_SESSION_COOKIE,
  type OdooApiResult,
} from "@/lib/odoo-client";
import type { ApiEnvelope } from "@/types/clinical";

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

export function parsePositiveInteger(value: string, label: string) {
  const parsed = Number(value);
  if (!Number.isInteger(parsed) || parsed <= 0) {
    return {
      ok: false as const,
      response: errorResponse("invalid_id", `${label} is invalid.`, 400),
    };
  }

  return {
    ok: true as const,
    value: parsed,
  };
}

export function forwardOdooResult<T>(result: OdooApiResult<T>) {
  return NextResponse.json(result.body, { status: result.status });
}

export function handleRouteError(error: unknown, fallbackCode: string, fallbackMessage: string) {
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
