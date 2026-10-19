import createClient from "openapi-fetch";
import type { paths } from "@/lib/api-types";
import { apiAdminToken, apiBaseUrl } from "@/lib/env";

/** Typed client for server components. Never import it from a client component. */
export function serverApi() {
  return createClient<paths>({
    baseUrl: apiBaseUrl(),
    headers: { Authorization: `Bearer ${apiAdminToken()}` },
    fetch: (request) =>
      fetch(request, { cache: "no-store", redirect: "manual", signal: AbortSignal.timeout(15_000) }),
  });
}
