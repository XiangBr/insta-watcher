// Which backend paths the browser may reach through /api/bff. The browser is untrusted:
// it gets the admin API, minus the login throttle (which only this server may call).

const SEGMENT_FORBIDDEN = /[\\/\u0000-\u001f]/;
export const MAX_BODY_BYTES = 1024 * 1024;

/** Maps /api/bff/api/v1/x?y=1 to the backend path, or null if it must not be forwarded. */
export function backendPath(segments: string[], search: string): string | null {
  if (segments.length < 3 || segments[0] !== "api" || segments[1] !== "v1") return null;
  for (const segment of segments) {
    if (!segment || segment === "." || segment === ".." || SEGMENT_FORBIDDEN.test(segment)) {
      return null;
    }
  }
  if (segments[2] === "login-throttle") return null;
  return `/${segments.map(encodeURIComponent).join("/")}${search}`;
}

export function upstreamError(): Response {
  return Response.json(
    { error: { code: "upstream_error", message: "API unreachable", details: [] } },
    { status: 502, headers: { "Cache-Control": "no-store" } },
  );
}
