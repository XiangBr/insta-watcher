import { NextRequest } from "next/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { GET, POST } from "@/app/api/bff/[...path]/route";
import { MAX_BODY_BYTES } from "@/lib/bff";
import { SESSION_COOKIE, createSessionToken } from "@/lib/session";
import { API_TOKEN, setupTestEnv } from "./setup";

setupTestEnv();

const HOST = "app.example.test";
const fetchMock = vi.fn();

beforeEach(() => {
  fetchMock.mockReset();
  fetchMock.mockImplementation(async () => Response.json({ upstream: true }));
  vi.stubGlobal("fetch", fetchMock);
});
afterEach(() => vi.unstubAllGlobals());

async function call(
  handler: typeof GET,
  path: string,
  options: { method?: string; headers?: Record<string, string>; body?: BodyInit; session?: boolean } = {},
) {
  const headers = new Headers({ host: HOST, ...options.headers });
  if (options.session !== false) {
    headers.set("cookie", `${SESSION_COOKIE}=${await createSessionToken()}`);
  }
  const request = new NextRequest(`https://${HOST}/api/bff/${path}`, {
    method: options.method ?? "GET",
    headers,
    body: options.body,
  });
  return handler(request, { params: Promise.resolve({ path: path.split("?")[0]!.split("/") }) });
}

const sameOrigin = { origin: `https://${HOST}`, "content-type": "application/json" };

describe("BFF route", () => {
  it("refuses a request without a session and never reaches the backend", async () => {
    const response = await call(GET, "api/v1/campaigns", { session: false });
    expect(response.status).toBe(401);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("refuses a forged session cookie", async () => {
    const response = await call(GET, "api/v1/campaigns", {
      session: false,
      headers: { cookie: `${SESSION_COOKIE}=not.a.jwt` },
    });
    expect(response.status).toBe(401);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("refuses a cross-site write", async () => {
    const response = await call(POST, "api/v1/campaigns", {
      method: "POST",
      headers: { origin: "https://evil.test", "content-type": "application/json" },
      body: "{}",
    });
    expect(response.status).toBe(403);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("does not expose the login throttle or non-admin paths", async () => {
    for (const path of ["api/v1/login-throttle/failure", "webhook", "api/v1/../../webhook"]) {
      const response = await call(POST, path, { method: "POST", headers: sameOrigin, body: "{}" });
      expect(response.status).toBe(404);
    }
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("forwards a read with the admin token and the query string", async () => {
    const response = await call(GET, "api/v1/deliveries?status=FAILED");
    expect(response.status).toBe(200);
    expect(await response.json()).toEqual({ upstream: true });
    expect(response.headers.get("cache-control")).toBe("no-store");

    const [url, init] = fetchMock.mock.calls[0]!;
    expect(url).toBe("https://api.example.test/api/v1/deliveries?status=FAILED");
    expect(new Headers(init.headers).get("authorization")).toBe(`Bearer ${API_TOKEN}`);
    expect(init.redirect).toBe("manual");
  });

  it("forwards a write's JSON body but nothing else from the browser", async () => {
    await call(POST, "api/v1/campaigns", {
      method: "POST",
      headers: { ...sameOrigin, authorization: "Bearer attacker", "x-forwarded-for": "1.2.3.4" },
      body: JSON.stringify({ name: "x" }),
    });
    const [, init] = fetchMock.mock.calls[0]!;
    const sent = new Headers(init.headers);
    expect(sent.get("authorization")).toBe(`Bearer ${API_TOKEN}`);
    expect(sent.get("content-type")).toBe("application/json");
    expect(sent.get("cookie")).toBeNull();
    expect(sent.get("x-forwarded-for")).toBeNull();
    expect(Buffer.from(init.body).toString()).toBe('{"name":"x"}');
  });

  it("passes the backend's error status and body through", async () => {
    fetchMock.mockResolvedValueOnce(
      Response.json({ error: { code: "validation_failed" } }, { status: 422 }),
    );
    const response = await call(POST, "api/v1/campaigns", {
      method: "POST",
      headers: sameOrigin,
      body: "{}",
    });
    expect(response.status).toBe(422);
    expect((await response.json()).error.code).toBe("validation_failed");
  });

  it("rejects an oversized body", async () => {
    const response = await call(POST, "api/v1/campaigns", {
      method: "POST",
      headers: sameOrigin,
      body: "x".repeat(MAX_BODY_BYTES + 1),
    });
    expect(response.status).toBe(413);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("answers 502 without leaking the token when the backend is down", async () => {
    fetchMock.mockRejectedValueOnce(new Error(`connect failed, sent Bearer ${API_TOKEN}`));
    vi.spyOn(console, "error").mockImplementation(() => undefined);
    const response = await call(GET, "api/v1/status");
    expect(response.status).toBe(502);
    expect(await response.text()).not.toContain(API_TOKEN);
  });
});
