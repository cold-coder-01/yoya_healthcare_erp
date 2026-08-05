import { cookies } from "next/headers";
import { NextResponse } from "next/server";
import {
  fetchAppointments,
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

export async function GET() {
  const sessionId = (await cookies()).get(YOYA_ODOO_SESSION_COOKIE)?.value;

  if (!sessionId) {
    return errorResponse("unauthenticated", "Odoo session is missing.", 401);
  }

  try {
    const result = await fetchAppointments(sessionId);
    return NextResponse.json(result.body, { status: result.status });
  } catch (error) {
    if (error instanceof OdooClientError) {
      return errorResponse(error.code, error.message, error.status);
    }

    return errorResponse("appointments_failed", "Unable to load appointments.", 500);
  }
}
