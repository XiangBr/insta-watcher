import { NextRequest } from "next/server";
import { describe, expect, it } from "vitest";
import { proxy } from "@/proxy";
import { SESSION_COOKIE, createSessionToken } from "@/lib/session";
import { setupTestEnv } from "./setup";

setupTestEnv();

async function call(path: string, options: { session?: boolean } = {}) {
  const headers = new Headers();
  if (options.session) headers.set("cookie", `${SESSION_COOKIE}=${await createSessionToken()}`);
  return proxy(new NextRequest(`https://app.example.test${path}`, { headers }));
}

const csp = (response: Response) => response.headers.get("content-security-policy") ?? "";

describe("proxy", () => {
  it("sends a page request without a session to /login", async () => {
    const response = await call("/campaigns");
    expect(response.status).toBe(307);
    expect(new URL(response.headers.get("location")!).pathname).toBe("/login");
  });

  it("answers an API request without a session with 401 JSON, not a redirect", async () => {
    const response = await call("/api/bff/api/v1/campaigns");
    expect(response.status).toBe(401);
    expect((await response.json()).error.code).toBe("unauthorized");
  });

  it("lets the login page and login endpoint through without a session", async () => {
    for (const path of ["/login", "/api/login"]) {
      expect((await call(path)).status).toBe(200);
    }
  });

  it("sends a signed-in visitor away from /login", async () => {
    const response = await call("/login", { session: true });
    expect(response.status).toBe(307);
    expect(new URL(response.headers.get("location")!).pathname).toBe("/");
  });

  it("does not open /api/logout or the BFF to visitors without a session", async () => {
    expect((await call("/api/logout")).status).toBe(401);
    expect((await call("/api/bff/api/v1/status")).status).toBe(401);
  });

  it("sets a strict CSP with a fresh nonce on every page", async () => {
    const first = csp(await call("/campaigns", { session: true }));
    const second = csp(await call("/campaigns", { session: true }));
    const nonce = (policy: string) => /'nonce-([^']+)'/.exec(policy)?.[1];
    expect(nonce(first)).toBeTruthy();
    expect(nonce(first)).not.toBe(nonce(second));
    expect(first).toContain("default-src 'self'");
    expect(first).toContain("object-src 'none'");
    expect(first).toContain("frame-ancestors 'none'");
    expect(first).toContain("form-action 'self'");
    expect(first).not.toContain("unsafe-eval");
    expect(first).not.toContain("unsafe-inline");
  });

  it("allows images only from self and Instagram's CDN", async () => {
    const policy = csp(await call("/campaigns", { session: true }));
    const images = /img-src ([^;]+)/.exec(policy)?.[1] ?? "";
    expect(images.split(" ").sort()).toEqual(
      ["'self'", "blob:", "data:", "https://*.cdninstagram.com", "https://*.fbcdn.net"].sort(),
    );
  });

  it("only lets the page talk to its own origin", async () => {
    expect(csp(await call("/", { session: true }))).toContain("connect-src 'self'");
  });

  it("rejects a session cookie signed with another secret", async () => {
    const forged = new NextRequest("https://app.example.test/campaigns", {
      headers: { cookie: `${SESSION_COOKIE}=${"a".repeat(20)}.${"b".repeat(20)}.${"c".repeat(20)}` },
    });
    expect((await proxy(forged)).status).toBe(307);
  });
});
