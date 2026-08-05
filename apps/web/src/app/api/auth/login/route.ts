import { NextRequest, NextResponse } from "next/server";
import {
  authenticateOdoo,
  OdooClientError,
  YOYA_ODOO_SESSION_COOKIE,
} from "@/lib/odoo-client";

function errorResponse(code: string, message: string, status: number) {
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

export async function POST(request: NextRequest) {
  let body: unknown;

  try {
    body = await request.json();
  } catch {
    return errorResponse("invalid_json", "Request body must be valid JSON.", 400);
  }

  if (typeof body !== "object" || body === null) {
    return errorResponse("invalid_request", "Login and password are required.", 400);
  }

  const { login, password } = body as Record<string, unknown>;

  if (typeof login !== "string" || !login.trim()) {
    return errorResponse("invalid_login", "Login is required.", 400);
  }

  if (typeof password !== "string" || !password) {
    return errorResponse("invalid_password", "Password is required.", 400);
  }

  try {
    const auth = await authenticateOdoo(login.trim(), password);
    const response = NextResponse.json({
      success: true,
      data: {
        user: auth.user,
      },
    });

    response.cookies.set(YOYA_ODOO_SESSION_COOKIE, auth.sessionId, {
      httpOnly: true,
      sameSite: "lax",
      secure: process.env.NODE_ENV === "production",
      path: "/",
    });

    return response;
  } catch (error) {
    if (error instanceof OdooClientError) {
      return errorResponse(error.code, error.message, error.status);
    }

    return errorResponse("login_failed", "Unable to log in.", 500);
  }
}
