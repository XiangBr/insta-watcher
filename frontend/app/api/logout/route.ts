import type { NextRequest } from "next/server";
import { NextResponse } from "next/server";
import { isSameOrigin } from "@/lib/origin";
import { SESSION_COOKIE, sessionCookieOptions } from "@/lib/session";

export async function POST(request: NextRequest) {
  if (!isSameOrigin(request)) {
    return NextResponse.json({ error: "forbidden" }, { status: 403 });
  }
  // 303 turns the form POST into a GET of /login. The Location is relative on purpose:
  // building it from request.url would trust whatever host a proxy in front reports.
  const response = new NextResponse(null, { status: 303, headers: { Location: "/login" } });
  response.cookies.set(SESSION_COOKIE, "", { ...sessionCookieOptions, maxAge: 0 });
  return response;
}
