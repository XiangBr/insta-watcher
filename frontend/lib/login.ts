import { verify } from "@node-rs/argon2";
import { backendFetch } from "@/lib/backend";
import { ConfigError, adminPasswordHash } from "@/lib/env";
import { throttleKey } from "@/lib/throttle-key";

export type LoginResult =
  | { ok: true }
  | { ok: false; reason: "invalid" | "throttled" | "unavailable"; retryAfter?: number };

// Longer inputs are never a real password, and hashing them is wasted work.
export const MAX_PASSWORD_LENGTH = 256;

/** The throttle endpoint answered with an error. The message is only an action and a status. */
export class ThrottleError extends Error {
  constructor(action: string, status: number) {
    super(`throttle ${action} answered ${status}`);
    this.name = "ThrottleError";
  }
}

export type LoginDeps = {
  backend: (path: string, init: RequestInit) => Promise<Response>;
  verifyPassword: (hash: string, password: string) => Promise<boolean>;
  hash: () => string;
};

const defaultDeps: LoginDeps = {
  backend: backendFetch,
  verifyPassword: verify,
  hash: adminPasswordHash,
};

async function throttle(
  deps: LoginDeps,
  action: "check" | "failure" | "success",
  key: string,
): Promise<number> {
  const response = await deps.backend(`/api/v1/login-throttle/${action}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ key }),
  });
  if (!response.ok) throw new ThrottleError(action, response.status);
  if (response.status === 204) return 0;
  const body = (await response.json()) as { retry_after?: number };
  return Number(body.retry_after ?? 0);
}

/**
 * What is safe to log about a failure: configuration messages (they name a variable, never
 * its value), a throttle status, or an error name plus its network code (ECONNREFUSED,
 * ENOTFOUND). Anything else might carry a secret, so only its name is kept.
 */
export function describeFailure(error: unknown): string {
  if (error instanceof ConfigError || error instanceof ThrottleError) return error.message;
  if (!(error instanceof Error)) return "unknown error";
  const code = (error.cause as { code?: unknown } | undefined)?.code;
  return typeof code === "string" && /^[A-Z0-9_]+$/.test(code) ? `${error.name} (${code})` : error.name;
}

/**
 * The backend only counts failures; the password is checked here. If the backend cannot be
 * reached the login is refused: without the counter, guesses would be unlimited.
 */
export async function attemptLogin(
  password: string,
  ip: string,
  deps: LoginDeps = defaultDeps,
): Promise<LoginResult> {
  const key = throttleKey(ip);
  try {
    const wait = await throttle(deps, "check", key);
    if (wait > 0) return { ok: false, reason: "throttled", retryAfter: wait };

    const valid =
      password.length > 0 &&
      password.length <= MAX_PASSWORD_LENGTH &&
      (await deps.verifyPassword(deps.hash(), password));
    if (valid) {
      await throttle(deps, "success", key).catch(() => undefined);
      return { ok: true };
    }
    const retryAfter = await throttle(deps, "failure", key).catch(() => 0);
    return { ok: false, reason: "invalid", retryAfter };
  } catch (error) {
    // ConfigError messages name a variable, never its value. Any other error is logged by
    // name only, because backend and hash-check errors might carry secrets.
    console.error("login unavailable:", describeFailure(error));
    return { ok: false, reason: "unavailable" };
  }
}
