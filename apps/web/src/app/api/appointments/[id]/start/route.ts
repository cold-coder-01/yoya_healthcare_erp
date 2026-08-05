import { cookies } from "next/headers";
import { NextResponse } from "next/server";
import {
  OdooClientError,
  startConsultation,
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

export async function POST(
  _request: Request,
  context: { params: Promise<{ id: string }> },
) {
  const sessionId = (await cookies()).get(YOYA_ODOO_SESSION_COOKIE)?.value;

  if (!sessionId) {
    return errorResponse("unauthenticated", "Odoo session is missing.", 401);
  }

  const { id } = await context.params;
  const appointmentId = Number(id);

  if (!Number.isInteger(appointmentId) || appointmentId <= 0) {
    return errorResponse("invalid_appointment_id", "Appointment ID is invalid.", 400);
  }

  try {
    const result = await startConsultation(sessionId, appointmentId);
    return NextResponse.json(result.body, { status: result.status });
  } catch (error) {
    if (error instanceof OdooClientError) {
      return errorResponse(error.code, error.message, error.status);
    }

    return errorResponse(
      "start_consultation_failed",
      "Unable to start consultation.",
      500,
    );
  }
}
