import { describe, expect, it } from "vitest";
import { backendPath } from "@/lib/bff";
import { isSameOrigin } from "@/lib/origin";
import { clientIp, throttleKey } from "@/lib/throttle-key";
import { setupTestEnv } from "./setup";

setupTestEnv();

const request = (method: string, headers: Record<string, string>) =>
  new Request("https://app.example.test/api/x", { method, headers });

describe("isSameOrigin", () => {
  it("lets reads through without an Origin", () => {
    expect(isSameOrigin(request("GET", {}))).toBe(true);
  });

  it("accepts a write from the same host", () => {
    const headers = { origin: "https://app.example.test", host: "app.example.test" };
    expect(isSameOrigin(request("POST", headers))).toBe(true);
  });

  it.each([
    ["another site", { origin: "https://evil.test", host: "app.example.test" }],
    ["a look-alike host", { origin: "https://app.example.test.evil.test", host: "app.example.test" }],
    ["no Origin", { host: "app.example.test" }],
    ["no Host", { origin: "https://app.example.test" }],
    ["a malformed Origin", { origin: "not a url", host: "app.example.test" }],
    ["the null origin", { origin: "null", host: "app.example.test" }],
  ])("refuses a write with %s", (_name, headers) => {
    for (const method of ["POST", "PUT", "PATCH", "DELETE"]) {
      expect(isSameOrigin(request(method, headers))).toBe(false);
    }
  });
});

describe("backendPath", () => {
  it("maps admin API paths and keeps the query string", () => {
    expect(backendPath(["api", "v1", "campaigns"], "")).toBe("/api/v1/campaigns");
    expect(backendPath(["api", "v1", "deliveries"], "?status=FAILED&limit=5")).toBe(
      "/api/v1/deliveries?status=FAILED&limit=5",
    );
    expect(backendPath(["api", "v1", "campaigns", "7", "pause"], "")).toBe(
      "/api/v1/campaigns/7/pause",
    );
  });

  it.each([
    ["the login throttle", ["api", "v1", "login-throttle", "check"]],
    ["the public webhook", ["webhook"]],
    ["the health route", ["health"]],
    ["a parent segment", ["api", "v1", "campaigns", "..", "..", "webhook"]],
    ["a dot segment", ["api", "v1", ".", "campaigns"]],
    ["an encoded slash", ["api", "v1", "campaigns", "1/../../webhook"]],
    ["a backslash", ["api", "v1", "campaigns\\x"]],
    ["a control character", ["api", "v1", "campaigns\u0000"]],
    ["an empty segment", ["api", "v1", "", "campaigns"]],
    ["only the prefix", ["api", "v1"]],
    ["another API version", ["api", "v2", "campaigns"]],
  ])("refuses %s", (_name, segments) => {
    expect(backendPath(segments, "")).toBeNull();
  });

  it("encodes what it forwards", () => {
    expect(backendPath(["api", "v1", "campaigns", "a b"], "")).toBe("/api/v1/campaigns/a%20b");
  });
});

describe("throttle key", () => {
  it("is stable, never contains the address and fits the backend's key pattern", () => {
    const key = throttleKey("203.0.113.9");
    expect(key).toBe(throttleKey("203.0.113.9"));
    expect(key).not.toContain("203.0.113.9");
    expect(key).toMatch(/^[A-Za-z0-9_-]{16,128}$/);
    expect(key).not.toBe(throttleKey("203.0.113.10"));
  });

  it("changes with the session secret", () => {
    const before = throttleKey("203.0.113.9");
    process.env.SESSION_SECRET = "z".repeat(40);
    expect(throttleKey("203.0.113.9")).not.toBe(before);
  });

  it("reads the client address from the forwarding headers", () => {
    expect(clientIp(new Headers({ "x-forwarded-for": "198.51.100.4, 10.0.0.1" }))).toBe(
      "198.51.100.4",
    );
    expect(clientIp(new Headers({ "x-real-ip": "198.51.100.5" }))).toBe("198.51.100.5");
    expect(clientIp(new Headers())).toBe("unknown");
  });
});
