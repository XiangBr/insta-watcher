import { apiAdminToken, apiBaseUrl } from "@/lib/env";

const TIMEOUT_MS = 15_000;

/**
 * The only place that attaches API_ADMIN_TOKEN. Redirects are never followed, so the token
 * cannot be forwarded to another host by a misbehaving upstream.
 */
export function backendFetch(path: string, init: RequestInit = {}): Promise<Response> {
  const headers = new Headers(init.headers);
  headers.set("Authorization", `Bearer ${apiAdminToken()}`);
  return fetch(`${apiBaseUrl()}${path}`, {
    ...init,
    headers,
    cache: "no-store",
    redirect: "manual",
    signal: AbortSignal.timeout(TIMEOUT_MS),
  });
}
