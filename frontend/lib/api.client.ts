import createClient from "openapi-fetch";
import { ApiCallError, parseApiError } from "@/lib/api-error";
import type { paths } from "@/lib/api-types";

// The browser talks to the BFF route (app/api/bff), which checks the session and adds the
// admin token. Paths keep their /api/v1 prefix: /api/bff/api/v1/campaigns.
export const browserApi = createClient<paths>({ baseUrl: "/api/bff" });

type Result<T> = { data?: T; error?: unknown; response: Response };

/** Returns the body, or throws an ApiCallError whose message is safe to show. */
export async function unwrap<T>(call: Promise<Result<T>>): Promise<T> {
  let result: Result<T>;
  try {
    result = await call;
  } catch {
    throw new ApiCallError(
      { code: "network", message: "Sem conexão com o painel. Tente de novo.", fields: {} },
      0,
    );
  }
  if (!result.response.ok) {
    if (result.response.status === 401) {
      // The session ended: a full navigation lets the proxy send the person to /login.
      // eslint-disable-next-line @next/next/no-location-assign-relative-destination -- full page load on purpose
      window.location.assign("/login");
    }
    throw new ApiCallError(parseApiError(result.error), result.response.status);
  }
  return result.data as T;
}
