"use client";

import { useEffect, useState, type FormEvent } from "react";
import { Button, inputClass } from "@/components/ui";
import { messages } from "@/lib/messages";

export function LoginForm() {
  const [password, setPassword] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [waitSeconds, setWaitSeconds] = useState(0);

  useEffect(() => {
    if (waitSeconds <= 0) return;
    const timer = setTimeout(() => setWaitSeconds((s) => s - 1), 1000);
    return () => clearTimeout(timer);
  }, [waitSeconds]);

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setPending(true);
    setError(null);
    try {
      const response = await fetch("/api/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ password }),
      });
      if (response.ok) {
        // A full navigation, so the proxy and the server components see the new cookie.
        window.location.replace("/");
        return;
      }
      const body = (await response.json().catch(() => ({}))) as { retry_after?: number };
      const retryAfter = Number(body.retry_after ?? 0);
      if (response.status === 429) {
        setWaitSeconds(retryAfter);
        setError(messages.login.throttled(retryAfter));
      } else if (response.status === 401) {
        setWaitSeconds(retryAfter);
        setError(messages.login.invalid);
      } else if (response.status === 403) {
        setError(messages.login.forbidden);
      } else {
        setError(messages.login.unavailable);
      }
      setPassword("");
    } catch {
      setError(messages.login.unavailable);
    } finally {
      setPending(false);
    }
  }

  const blocked = pending || waitSeconds > 0;
  // method and action are the safety net before hydration: a native submit must never put the
  // password in the URL.
  return (
    <form onSubmit={onSubmit} method="post" action="/api/login" className="mt-6 space-y-4">
      <div>
        <label htmlFor="password" className="block text-sm font-semibold">
          {messages.login.password}
        </label>
        <input
          id="password"
          name="password"
          type="password"
          autoComplete="current-password"
          autoFocus
          required
          maxLength={256}
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          className={`mt-1.5 ${inputClass}`}
        />
      </div>
      <p role="alert" aria-live="polite" className="min-h-5 text-sm text-fail">
        {waitSeconds > 0 ? messages.login.throttled(waitSeconds) : error}
      </p>
      <Button type="submit" variant="primary" disabled={blocked} className="w-full">
        {pending ? messages.login.submitting : messages.login.submit}
      </Button>
    </form>
  );
}
