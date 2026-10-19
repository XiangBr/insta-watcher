// Server-only configuration. Every value is read when used, never at import, so a missing
// variable fails the request that needs it instead of the build. Messages name the variable
// and never its value.

export class ConfigError extends Error {}

function required(name: string): string {
  const value = process.env[name];
  if (!value) throw new ConfigError(`${name} is not set`);
  return value;
}

export function apiBaseUrl(): string {
  const raw = required("API_BASE_URL").replace(/\/+$/, "");
  let url: URL;
  try {
    url = new URL(raw);
  } catch {
    throw new ConfigError("API_BASE_URL is not a valid URL");
  }
  const local = url.hostname === "localhost" || url.hostname === "127.0.0.1";
  // A name without a dot (http://api:8000) is a service on the private Docker network, where
  // the traffic never leaves the machine. Anything public must be https.
  const privateService = !url.hostname.includes(".") && url.hostname !== "localhost";
  if (
    url.protocol !== "https:" &&
    !privateService &&
    !(local && process.env.NODE_ENV !== "production")
  ) {
    throw new ConfigError("API_BASE_URL must use https");
  }
  if (url.username || url.password) throw new ConfigError("API_BASE_URL must not embed credentials");
  return raw;
}

export function apiAdminToken(): string {
  const token = required("API_ADMIN_TOKEN");
  if (token.length < 32) throw new ConfigError("API_ADMIN_TOKEN must have at least 32 characters");
  return token;
}

export function adminPasswordHash(): string {
  const hash = required("ADMIN_PASSWORD_HASH");
  if (!hash.startsWith("$argon2")) {
    // In .env files, every "$" must be written "\$" or the hash is silently truncated.
    throw new ConfigError("ADMIN_PASSWORD_HASH is not an argon2 hash (escape $ as \$ in .env files)");
  }
  return hash;
}

export function sessionSecret(): string {
  const secret = required("SESSION_SECRET");
  if (secret.length < 32) throw new ConfigError("SESSION_SECRET must have at least 32 characters");
  return secret;
}
