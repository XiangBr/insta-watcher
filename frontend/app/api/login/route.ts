import type { NextRequest } from "next/server";
import { NextResponse } from "next/server";
import { clientIp } from "@/lib/throttle-key";
import { isSameOrigin } from "@/lib/origin";
import { attemptLogin } from "@/lib/login";
import { SESSION_COOKIE, createSessionToken, sessionCookieOptions } from "@/lib/session";

const MAX_BODY_CHARS = 2048;
const noStore = { "Cache-Control": "no-store" };

export async function POST(request: NextRequest) {
  if (!isSameOrigin(request)) {
    return NextResponse.json({ error: "forbidden" }, { status: 403, headers: noStore });
  }
  const raw = await request.text();
  let password: unknown;
  try {
    if (raw.length > MAX_BODY_CHARS) throw new Error("too large");
    password = (JSON.parse(raw) as { password?: unknown }).password;
  } catch {
    return NextResponse.json({ error: "bad_request" }, { status: 400, headers: noStore });
  }
  if (typeof password !== "string") {
    return NextResponse.json({ error: "bad_request" }, { status: 400, headers: noStore });
  }

  const result = await attemptLogin(password, clientIp(request.headers));
  if (result.ok) {
    const response = NextResponse.json({ ok: true }, { headers: noStore });
    response.cookies.set(SESSION_COOKIE, await createSessionToken(), sessionCookieOptions);
    return response;
  }
  if (result.reason === "throttled") {
    const retryAfter = result.retryAfter ?? 1;
    return NextResponse.json(
      { error: "throttled", retry_after: retryAfter },
      { status: 429, headers: { ...noStore, "Retry-After": String(retryAfter) } },
    );
  }
  if (result.reason === "unavailable") {
    return NextResponse.json({ error: "unavailable" }, { status: 503, headers: noStore });
  }
  return NextResponse.json(
    { error: "invalid", retry_after: result.retryAfter ?? 0 },
    { status: 401, headers: noStore },
  );
}
