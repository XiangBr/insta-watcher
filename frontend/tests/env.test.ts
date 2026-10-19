import { afterEach, describe, expect, it, vi } from "vitest";
import { apiBaseUrl } from "@/lib/env";

afterEach(() => {
  vi.unstubAllEnvs();
});

function withUrl(url: string, nodeEnv = "production") {
  vi.stubEnv("API_BASE_URL", url);
  vi.stubEnv("NODE_ENV", nodeEnv);
  return () => apiBaseUrl();
}

describe("API_BASE_URL", () => {
  it("accepts https anywhere and plain http only where traffic stays on the machine", () => {
    expect(withUrl("https://insta.example.com/")()).toBe("https://insta.example.com");
    expect(withUrl("http://api:8000")()).toBe("http://api:8000"); // Docker service name
    expect(withUrl("http://localhost:8000", "development")()).toBe("http://localhost:8000");
  });

  it("refuses public http, localhost http in production and embedded credentials", () => {
    expect(withUrl("http://insta.example.com")).toThrow("https");
    expect(withUrl("http://localhost:8000")).toThrow("https");
    expect(withUrl("https://user:pw@insta.example.com")).toThrow("credentials");
  });
});
