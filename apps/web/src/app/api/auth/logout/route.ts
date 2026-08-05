import { NextResponse } from "next/server";
import { YOYA_ODOO_SESSION_COOKIE } from "@/lib/odoo-client";

export async function POST() {
  const response = NextResponse.json({ success: true });

  response.cookies.set(YOYA_ODOO_SESSION_COOKIE, "", {
    httpOnly: true,
    sameSite: "lax",
    secure: process.env.NODE_ENV === "production",
    path: "/",
    expires: new Date(0),
  });

  return response;
}
