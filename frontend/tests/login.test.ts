import { describe, expect, it, vi } from "vitest";
import { ConfigError } from "@/lib/env";
import {
  MAX_PASSWORD_LENGTH,
  ThrottleError,
  attemptLogin,
  describeFailure,
  type LoginDeps,
} from "@/lib/login";
import { throttleKey } from "@/lib/throttle-key";
import { setupTestEnv } from "./setup";

setupTestEnv();

const IP = "203.0.113.9";

function deps(options: { wait?: number; failureWait?: number; valid?: boolean } = {}) {
  const calls: { action: string; key: string }[] = [];
  const backend = vi.fn(async (path: string, init: RequestInit) => {
    const action = path.split("/").pop() ?? "";
    calls.push({ action, key: (JSON.parse(String(init.body)) as { key: string }).key });
    if (action === "success") return new Response(null, { status: 204 });
    const retry_after = action === "check" ? (options.wait ?? 0) : (options.failureWait ?? 0);
    return Response.json({ retry_after });
  });
  const verifyPassword = vi.fn(async () => options.valid ?? true);
  const value: LoginDeps = { backend, verifyPassword, hash: () => "$argon2id$stub" };
  return { value, calls, backend, verifyPassword };
}

describe("attemptLogin", () => {
  it("accepts the right password and clears the client's failures", async () => {
    const d = deps({ valid: true });
    expect(await attemptLogin("correct horse battery", IP, d.value)).toEqual({ ok: true });
    expect(d.calls.map((c) => c.action)).toEqual(["check", "success"]);
  });

  it("counts a wrong password and reports the wait", async () => {
    const d = deps({ valid: false, failureWait: 4 });
    expect(await attemptLogin("wrong password!!", IP, d.value)).toEqual({
      ok: false,
      reason: "invalid",
      retryAfter: 4,
    });
    expect(d.calls.map((c) => c.action)).toEqual(["check", "failure"]);
  });

  it("refuses without checking the password while the client is throttled", async () => {
    const d = deps({ wait: 30, valid: true });
    expect(await attemptLogin("correct horse battery", IP, d.value)).toEqual({
      ok: false,
      reason: "throttled",
      retryAfter: 30,
    });
    expect(d.verifyPassword).not.toHaveBeenCalled();
  });

  it("sends only the HMAC key to the backend, never the address", async () => {
    const d = deps({ valid: false });
    await attemptLogin("wrong password!!", IP, d.value);
    expect(new Set(d.calls.map((c) => c.key))).toEqual(new Set([throttleKey(IP)]));
    expect(JSON.stringify(d.backend.mock.calls)).not.toContain(IP);
  });

  it("fails closed when the throttle cannot be reached", async () => {
    const d = deps({ valid: true });
    d.backend.mockRejectedValueOnce(new Error("connect ECONNREFUSED"));
    vi.spyOn(console, "error").mockImplementation(() => undefined);
    expect(await attemptLogin("correct horse battery", IP, d.value)).toEqual({
      ok: false,
      reason: "unavailable",
    });
    expect(d.verifyPassword).not.toHaveBeenCalled();
  });

  it("fails closed when the backend answers with an error", async () => {
    const d = deps({ valid: true });
    d.backend.mockResolvedValueOnce(new Response("nope", { status: 401 }));
    vi.spyOn(console, "error").mockImplementation(() => undefined);
    expect((await attemptLogin("correct horse battery", IP, d.value)).ok).toBe(false);
    expect(d.verifyPassword).not.toHaveBeenCalled();
  });

  it("still logs in if clearing the counter fails afterwards", async () => {
    const d = deps({ valid: true });
    d.backend.mockImplementation(async (path: string) => {
      if (path.endsWith("/success")) throw new Error("boom");
      return Response.json({ retry_after: 0 });
    });
    expect(await attemptLogin("correct horse battery", IP, d.value)).toEqual({ ok: true });
  });

  it("does not hash empty or oversized passwords, and counts them as failures", async () => {
    for (const password of ["", "x".repeat(MAX_PASSWORD_LENGTH + 1)]) {
      const d = deps({ valid: true });
      const result = await attemptLogin(password, IP, d.value);
      expect(result).toMatchObject({ ok: false, reason: "invalid" });
      expect(d.verifyPassword).not.toHaveBeenCalled();
      expect(d.calls.map((c) => c.action)).toEqual(["check", "failure"]);
    }
  });

  it("reports a broken hash configuration as unavailable and logs only the variable", async () => {
    const d = deps();
    d.value.hash = () => {
      throw new ConfigError("ADMIN_PASSWORD_HASH is not set");
    };
    const log = vi.spyOn(console, "error").mockImplementation(() => undefined);
    expect(await attemptLogin("correct horse battery", IP, d.value)).toEqual({
      ok: false,
      reason: "unavailable",
    });
    expect(log).toHaveBeenCalledWith("login unavailable:", "ADMIN_PASSWORD_HASH is not set");
  });

  it("never logs the password, even when the check throws with it in the message", async () => {
    const d = deps();
    d.verifyPassword.mockRejectedValueOnce(new Error("bad hash for hunter2-password"));
    const log = vi.spyOn(console, "error").mockImplementation(() => undefined);
    await attemptLogin("hunter2-password", IP, d.value);
    expect(JSON.stringify(log.mock.calls)).not.toContain("hunter2-password");
  });
});

describe("describeFailure", () => {
  it("keeps configuration and throttle messages, which hold no secrets", () => {
    expect(describeFailure(new ConfigError("API_ADMIN_TOKEN is not set"))).toBe(
      "API_ADMIN_TOKEN is not set",
    );
    expect(describeFailure(new ThrottleError("check", 401))).toBe("throttle check answered 401");
  });

  it("adds the network code of a failed fetch", () => {
    const error = new TypeError("fetch failed", { cause: { code: "ECONNREFUSED" } });
    expect(describeFailure(error)).toBe("TypeError (ECONNREFUSED)");
  });

  it("keeps only the name of anything else, and ignores an odd code", () => {
    expect(describeFailure(new Error("bad hash for hunter2"))).toBe("Error");
    const odd = new Error("x", { cause: { code: "has spaces and 'quotes'" } });
    expect(describeFailure(odd)).toBe("Error");
    expect(describeFailure("a string")).toBe("unknown error");
  });
});

it("logs a 401 from the backend as a status, so a wrong API_ADMIN_TOKEN is easy to spot", async () => {
  const d = deps();
  d.backend.mockResolvedValueOnce(new Response("nope", { status: 401 }));
  const log = vi.spyOn(console, "error").mockImplementation(() => undefined);
  await attemptLogin("correct horse battery", IP, d.value);
  expect(log).toHaveBeenCalledWith("login unavailable:", "throttle check answered 401");
});
