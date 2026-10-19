import { SignJWT } from "jose";
import { describe, expect, it } from "vitest";
import {
  SESSION_MAX_AGE_SECONDS,
  createSessionToken,
  isValidSessionToken,
} from "@/lib/session";
import { SESSION_SECRET, setupTestEnv } from "./setup";

setupTestEnv();

const sign = (secret: string, claims: { sub?: string; exp?: number } = {}) =>
  new SignJWT({})
    .setProtectedHeader({ alg: "HS256" })
    .setSubject(claims.sub ?? "admin")
    .setIssuedAt()
    .setExpirationTime(claims.exp ?? Math.floor(Date.now() / 1000) + 600)
    .sign(new TextEncoder().encode(secret));

describe("session token", () => {
  it("accepts a token it just created", async () => {
    expect(await isValidSessionToken(await createSessionToken())).toBe(true);
  });

  it.each([undefined, null, "", "garbage", "a.b.c"])("rejects %j", async (token) => {
    expect(await isValidSessionToken(token)).toBe(false);
  });

  it("rejects a token past its seven days", async () => {
    const eightDaysAgo = Date.now() - (SESSION_MAX_AGE_SECONDS + 86_400) * 1000;
    expect(await isValidSessionToken(await createSessionToken(eightDaysAgo))).toBe(false);
  });

  it("rejects a token signed with another secret", async () => {
    expect(await isValidSessionToken(await sign("x".repeat(40)))).toBe(false);
    expect(await isValidSessionToken(await sign(SESSION_SECRET))).toBe(true);
  });

  it("rejects a token for another subject", async () => {
    expect(await isValidSessionToken(await sign(SESSION_SECRET, { sub: "someone" }))).toBe(false);
  });

  it("rejects a token with a tampered payload", async () => {
    const [header, , signature] = (await createSessionToken()).split(".");
    const forged = Buffer.from(JSON.stringify({ sub: "admin", exp: 9_999_999_999 })).toString(
      "base64url",
    );
    expect(await isValidSessionToken(`${header}.${forged}.${signature}`)).toBe(false);
  });

  it("rejects an unsigned (alg none) token", async () => {
    const part = (value: object) => Buffer.from(JSON.stringify(value)).toString("base64url");
    const token = `${part({ alg: "none", typ: "JWT" })}.${part({ sub: "admin", exp: 9_999_999_999 })}.`;
    expect(await isValidSessionToken(token)).toBe(false);
  });

  it("treats a missing SESSION_SECRET as logged out instead of crashing", async () => {
    const token = await createSessionToken();
    delete process.env.SESSION_SECRET;
    expect(await isValidSessionToken(token)).toBe(false);
  });
});
