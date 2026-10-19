import { SignJWT, jwtVerify } from "jose";
import { sessionSecret } from "@/lib/env";

// "__Host-" pins the cookie to this exact host over https. It needs Secure, so it is only
// used in production; plain http://localhost would drop it.
const production = process.env.NODE_ENV === "production";
export const SESSION_COOKIE = production ? "__Host-iw_session" : "iw_session";
export const SESSION_MAX_AGE_SECONDS = 7 * 24 * 60 * 60;

const key = () => new TextEncoder().encode(sessionSecret());

export async function createSessionToken(now = Date.now()): Promise<string> {
  const issuedAt = Math.floor(now / 1000);
  return new SignJWT({})
    .setProtectedHeader({ alg: "HS256" })
    .setSubject("admin")
    .setIssuedAt(issuedAt)
    .setExpirationTime(issuedAt + SESSION_MAX_AGE_SECONDS)
    .sign(key());
}

/** True only for an unexpired token signed with the current SESSION_SECRET. */
export async function isValidSessionToken(token: string | undefined | null): Promise<boolean> {
  if (!token) return false;
  try {
    await jwtVerify(token, key(), { algorithms: ["HS256"], subject: "admin" });
    return true;
  } catch {
    return false;
  }
}

export const sessionCookieOptions = {
  httpOnly: true,
  secure: production,
  sameSite: "lax" as const,
  path: "/",
  maxAge: SESSION_MAX_AGE_SECONDS,
};
