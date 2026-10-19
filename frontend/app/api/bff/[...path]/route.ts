import type { NextRequest } from "next/server";
import { MAX_BODY_BYTES, backendPath, upstreamError } from "@/lib/bff";
import { backendFetch } from "@/lib/backend";
import { isSameOrigin } from "@/lib/origin";
import { SESSION_COOKIE, isValidSessionToken } from "@/lib/session";

type Context = { params: Promise<{ path: string[] }> };

function reject(status: number, code: string, message: string): Response {
  return Response.json(
    { error: { code, message, details: [] } },
    { status, headers: { "Cache-Control": "no-store" } },
  );
}

async function handle(request: NextRequest, { params }: Context): Promise<Response> {
  if (!(await isValidSessionToken(request.cookies.get(SESSION_COOKIE)?.value))) {
    return reject(401, "unauthorized", "login required");
  }
  if (!isSameOrigin(request)) return reject(403, "forbidden", "cross-origin request refused");

  const target = backendPath((await params).path, request.nextUrl.search);
  if (!target) return reject(404, "not_found", "unknown route");

  let body: ArrayBuffer | undefined;
  if (request.method !== "GET") {
    body = await request.arrayBuffer();
    if (body.byteLength > MAX_BODY_BYTES) return reject(413, "validation_failed", "body too large");
  }

  try {
    const upstream = await backendFetch(target, {
      method: request.method,
      // Only what the API needs. Cookies and the rest of the browser headers stay here.
      headers: body?.byteLength ? { "Content-Type": "application/json" } : {},
      body: body?.byteLength ? body : undefined,
    });
    return new Response(upstream.body, {
      status: upstream.status,
      headers: {
        "Content-Type": upstream.headers.get("content-type") ?? "application/json",
        "Cache-Control": "no-store",
      },
    });
  } catch (error) {
    console.error("bff upstream failed:", error instanceof Error ? error.name : "unknown error");
    return upstreamError();
  }
}

export const GET = handle;
export const POST = handle;
export const PUT = handle;
export const PATCH = handle;
export const DELETE = handle;
