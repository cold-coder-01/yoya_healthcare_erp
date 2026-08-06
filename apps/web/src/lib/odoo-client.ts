import "server-only";

import type {
  ApiEnvelope,
  ApiErrorShape,
  ClinicalEvaluation,
  ClinicalSession,
  EvaluationDetail,
  EvaluationQueueResponse,
  EvaluationSavePayload,
} from "@/types/clinical";

const ODOO_BASE_URL = process.env.ODOO_BASE_URL?.replace(/\/+$/, "");
const ODOO_DATABASE = process.env.ODOO_DATABASE;

export const YOYA_ODOO_SESSION_COOKIE = "yoya_odoo_session";

export type OdooUser = {
  uid?: number;
  name?: string;
  username?: string;
  partnerId?: number;
};

export type OdooApiResult<T> = {
  status: number;
  body: ApiEnvelope<T>;
};

export class OdooClientError extends Error {
  status: number;
  code: string;
  details?: unknown;

  constructor(code: string, message: string, status = 500, details?: unknown) {
    super(message);
    this.name = "OdooClientError";
    this.code = code;
    this.status = status;
    this.details = details;
  }

  toApiError(): ApiErrorShape {
    return {
      code: this.code,
      message: this.message,
      ...(this.details === undefined ? {} : { details: this.details }),
    };
  }
}

function getOdooConfig() {
  if (!ODOO_BASE_URL || !ODOO_DATABASE) {
    throw new OdooClientError(
      "odoo_config_missing",
      "Odoo connection is not configured.",
      500,
    );
  }

  return {
    baseUrl: ODOO_BASE_URL,
    database: ODOO_DATABASE,
  };
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

function normalizeError(value: unknown, fallbackStatus: number): ApiErrorShape {
  if (isRecord(value)) {
    const message =
      typeof value.message === "string" && value.message.trim()
        ? value.message
        : "The clinical service returned an error.";
    const code =
      typeof value.code === "string" && value.code.trim()
        ? value.code
        : `odoo_http_${fallbackStatus}`;
    return { ...value, code, message };
  }

  return {
    code: `odoo_http_${fallbackStatus}`,
    message: "The clinical service returned an error.",
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
      message: "The clinical service returned an invalid response.",
      details: payload,
    },
  };
}

function extractSessionId(headers: Headers) {
  const headersWithCookies = headers as Headers & {
    getSetCookie?: () => string[];
  };
  const setCookieHeaders = headersWithCookies.getSetCookie?.() ?? [];
  const fallbackCookie = headers.get("set-cookie");
  const cookiesToParse = fallbackCookie
    ? [...setCookieHeaders, fallbackCookie]
    : setCookieHeaders;

  for (const cookie of cookiesToParse) {
    const match = cookie.match(/(?:^|,\s*|;\s*)session_id=([^;,]+)/);
    if (match?.[1]) {
      return match[1];
    }
  }

  return null;
}

function extractUser(payload: unknown): OdooUser | null {
  if (!isRecord(payload) || !isRecord(payload.result)) {
    return null;
  }

  const result = payload.result;
  return {
    uid: typeof result.uid === "number" ? result.uid : undefined,
    name: typeof result.name === "string" ? result.name : undefined,
    username: typeof result.username === "string" ? result.username : undefined,
    partnerId:
      typeof result.partner_id === "number" ? result.partner_id : undefined,
  };
}

export async function authenticateOdoo(login: string, password: string) {
  const { baseUrl, database } = getOdooConfig();

  let response: Response;
  try {
    response = await fetch(`${baseUrl}/web/session/authenticate`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        jsonrpc: "2.0",
        method: "call",
        params: {
          db: database,
          login,
          password,
        },
      }),
      cache: "no-store",
    });
  } catch {
    throw new OdooClientError(
      "odoo_unreachable",
      "Unable to reach Odoo.",
      502,
    );
  }

  const payload = await readJson(response);
  const hasRpcError = isRecord(payload) && "error" in payload;
  const sessionId = extractSessionId(response.headers);

  if (!response.ok || hasRpcError || !sessionId) {
    throw new OdooClientError(
      "invalid_credentials",
      "Invalid Odoo credentials.",
      401,
    );
  }

  return {
    sessionId,
    user: extractUser(payload),
  };
}

async function callOdooApi<T>(
  sessionId: string,
  path: string,
  method: "GET" | "POST",
  body?: unknown,
): Promise<OdooApiResult<T>> {
  const { baseUrl } = getOdooConfig();

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

function withQuery(path: string, params: Record<string, string | undefined>) {
  const query = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value) {
      query.set(key, value);
    }
  }
  const queryString = query.toString();
  return queryString ? `${path}?${queryString}` : path;
}

export type EvaluationQueueFilters = {
  date?: string;
  state?: string;
  department_id?: string;
  doctor_id?: string;
};

export function fetchClinicalSession(sessionId: string) {
  return callOdooApi<ClinicalSession>(
    sessionId,
    "/yoya-emr/api/v1/clinical/session",
    "GET",
  );
}

export function fetchEvaluationQueue(
  sessionId: string,
  filters: EvaluationQueueFilters = {},
) {
  return callOdooApi<EvaluationQueueResponse>(
    sessionId,
    withQuery("/yoya-emr/api/v1/clinical/evaluation-queue", filters),
    "GET",
  );
}

export function fetchEvaluationDetail(sessionId: string, appointmentId: number) {
  return callOdooApi<EvaluationDetail>(
    sessionId,
    `/yoya-emr/api/v1/clinical/evaluations/by-appointment/${appointmentId}`,
    "GET",
  );
}

export function saveEvaluation(
  sessionId: string,
  appointmentId: number,
  payload: EvaluationSavePayload,
) {
  return callOdooApi<{ evaluation: NonNullable<ClinicalEvaluation> }>(
    sessionId,
    `/yoya-emr/api/v1/clinical/evaluations/${appointmentId}/save`,
    "POST",
    payload,
  );
}

export function completeEvaluation(sessionId: string, evaluationId: number) {
  return callOdooApi<{ evaluation: NonNullable<ClinicalEvaluation> }>(
    sessionId,
    `/yoya-emr/api/v1/clinical/evaluations/${evaluationId}/complete`,
    "POST",
  );
}

export function startClinicalConsultation(
  sessionId: string,
  appointmentId: number,
) {
  return callOdooApi<{
    appointment: EvaluationDetail["appointment"];
    encounter: EvaluationDetail["encounter"];
  }>(
    sessionId,
    `/yoya-emr/api/v1/clinical/appointments/${appointmentId}/start-consultation`,
    "POST",
  );
}

/**
 * Reception session. Used server-side by the app shells to decide which
 * navigation items a user may see, so the sidebar is correct on first paint.
 */
export function fetchReceptionSession(sessionId: string) {
  return callOdooApi<{ roles: unknown }>(
    sessionId,
    "/yoya-emr/api/v1/reception/session",
    "GET",
  );
}

export function fetchAppointments(sessionId: string) {
  return callOdooApi<unknown>(sessionId, "/yoya-emr/api/v1/appointments", "GET");
}

export function startConsultation(sessionId: string, appointmentId: number) {
  return callOdooApi<unknown>(
    sessionId,
    `/yoya-emr/api/v1/appointments/${appointmentId}/start`,
    "POST",
  );
}
