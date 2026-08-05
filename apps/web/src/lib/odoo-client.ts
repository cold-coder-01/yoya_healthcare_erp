import "server-only";

const ODOO_BASE_URL = process.env.ODOO_BASE_URL?.replace(/\/+$/, "");
const ODOO_DATABASE = process.env.ODOO_DATABASE;

export const YOYA_ODOO_SESSION_COOKIE = "yoya_odoo_session";

export type OdooUser = {
  uid?: number;
  name?: string;
  username?: string;
  partnerId?: number;
};

export type OdooApiResult = {
  status: number;
  body: unknown;
};

export class OdooClientError extends Error {
  status: number;
  code: string;

  constructor(code: string, message: string, status = 500) {
    super(message);
    this.name = "OdooClientError";
    this.code = code;
    this.status = status;
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

async function callOdooApi(
  sessionId: string,
  path: string,
  method: "GET" | "POST",
): Promise<OdooApiResult> {
  const { baseUrl } = getOdooConfig();

  let response: Response;
  try {
    response = await fetch(`${baseUrl}${path}`, {
      method,
      headers: {
        Cookie: `session_id=${sessionId}`,
      },
      cache: "no-store",
    });
  } catch {
    throw new OdooClientError(
      "odoo_unreachable",
      "Unable to reach Odoo.",
      502,
    );
  }

  const body = await readJson(response);
  return {
    status: response.status,
    body,
  };
}

export function fetchAppointments(sessionId: string) {
  return callOdooApi(sessionId, "/yoya-emr/api/v1/appointments", "GET");
}

export function startConsultation(sessionId: string, appointmentId: number) {
  return callOdooApi(
    sessionId,
    `/yoya-emr/api/v1/appointments/${appointmentId}/start`,
    "POST",
  );
}
