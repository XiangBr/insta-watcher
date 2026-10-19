import { beforeEach } from "vitest";

export const SESSION_SECRET = "s".repeat(16) + "e".repeat(16) + "cret-for-tests-only-0123456789";
export const API_TOKEN = "t".repeat(16) + "k".repeat(16) + "en-for-tests-only-0123456789ab";

/** Fresh, valid configuration before every test. Tests override single variables as needed. */
export function setupTestEnv(): void {
  beforeEach(() => {
    process.env.SESSION_SECRET = SESSION_SECRET;
    process.env.API_ADMIN_TOKEN = API_TOKEN;
    process.env.API_BASE_URL = "https://api.example.test";
    process.env.ADMIN_PASSWORD_HASH = "$argon2id$v=19$m=19456,t=2,p=1$c29tZXNhbHQ$aGFzaGhhc2hoYXNo";
  });
}
