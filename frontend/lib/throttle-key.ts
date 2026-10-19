import { createHmac } from "node:crypto";
import { sessionSecret } from "@/lib/env";

/** Vercel and the gateway (Caddy) overwrite x-forwarded-for with the real client address. */
export function clientIp(headers: Headers): string {
  const forwarded = headers.get("x-forwarded-for")?.split(",")[0]?.trim();
  return forwarded || headers.get("x-real-ip")?.trim() || "unknown";
}

/**
 * What the backend stores to count failed logins. The address itself never leaves this
 * server; the 43 base64url characters fit the backend's key pattern.
 */
export function throttleKey(ip: string): string {
  return createHmac("sha256", sessionSecret()).update(`login-ip:${ip}`).digest("base64url");
}
