"use client";

import { useState, type FormEvent, type ReactNode } from "react";
import useSWR from "swr";
import { Button, Card, Field, Notice, PageHeader, inputClass } from "@/components/ui";
import { ApiCallError } from "@/lib/api-error";
import { browserApi, unwrap } from "@/lib/api.client";
import { relativeTime } from "@/lib/format";
import { messages } from "@/lib/messages";

const t = messages.settings;
type Flash = { kind: "ok" | "error"; text: string } | null;

function Section({ title, children }: { title: string; children: ReactNode }) {
  const id = `s-${title}`;
  return (
    <Card className="p-5">
      <section aria-labelledby={id}>
        <h2 id={id} className="text-base font-bold">
          {title}
        </h2>
        <div className="mt-3 space-y-3">{children}</div>
      </section>
    </Card>
  );
}

export function SettingsView() {
  const account = useSWR("account", () => unwrap(browserApi.GET("/api/v1/account")), {
    refreshInterval: 10_000,
  });
  const status = useSWR("status", () => unwrap(browserApi.GET("/api/v1/status")), {
    refreshInterval: 10_000,
  });
  const [flash, setFlash] = useState<Flash>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [token, setToken] = useState("");

  async function act(name: string, work: () => Promise<string>) {
    setBusy(name);
    setFlash(null);
    try {
      setFlash({ kind: "ok", text: await work() });
      await Promise.all([account.mutate(), status.mutate()]);
    } catch (e) {
      setFlash({
        kind: "error",
        text: e instanceof ApiCallError ? e.message : messages.common.loadFailed,
      });
    } finally {
      setBusy(null);
    }
  }

  function saveToken(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const value = token;
    return act("token", async () => {
      const saved = await unwrap(
        browserApi.PUT("/api/v1/account/token", { body: { token: value } }),
      );
      setToken("");
      return t.token.saved(saved.username);
    });
  }

  if (account.isLoading || status.isLoading) {
    return (
      <>
        <PageHeader title={t.title} />
        <p className="mt-6 text-muted">{messages.common.loading}</p>
      </>
    );
  }
  if (account.data === undefined || status.data === undefined) {
    return (
      <>
        <PageHeader title={t.title} />
        <p role="alert" className="mt-6 text-fail">
          {messages.common.loadFailed}
        </p>
      </>
    );
  }

  const acc = account.data;
  const live = status.data.account;
  const paused = acc?.sending_paused ?? false;

  return (
    <>
      <PageHeader title={t.title} />
      <div className="mt-4 min-h-10" aria-live="polite">
        {flash ? <Notice kind={flash.kind}>{flash.text}</Notice> : null}
      </div>

      <div className="max-w-2xl space-y-4">
        <Section title={t.account.title}>
          {acc === null ? (
            <p>{t.account.none}</p>
          ) : acc.status === "TOKEN_INVALID" ? (
            <p className="text-fail">{t.account.invalid}</p>
          ) : (
            <p>{t.account.connected(acc.username)}</p>
          )}
        </Section>

        <Section title={t.token.title}>
          {live ? (
            <p className="text-muted">
              {t.token.validity(live.token_days_left ?? null, live.token_expiry_confirmed)}
            </p>
          ) : null}
          <form onSubmit={saveToken} className="space-y-3">
            <Field label={t.token.label} hint={t.token.hint}>
              {(p) => (
                <input
                  {...p}
                  type="password"
                  autoComplete="off"
                  spellCheck={false}
                  value={token}
                  onChange={(e) => setToken(e.target.value)}
                  className={inputClass}
                />
              )}
            </Field>
            <Button type="submit" variant="primary" disabled={busy !== null || token.trim().length < 10}>
              {busy === "token" ? t.token.saving : t.token.save}
            </Button>
          </form>
        </Section>

        {acc ? (
          <>
            <Section title={t.webhook.title}>
              <p className="text-muted">
                {acc.webhook_subscribed_at
                  ? t.webhook.subscribed(relativeTime(acc.webhook_subscribed_at))
                  : t.webhook.notSubscribed}
              </p>
              <Button
                disabled={busy !== null}
                onClick={() =>
                  act("subscribe", async () => {
                    await unwrap(browserApi.POST("/api/v1/account/subscribe"));
                    return t.webhook.done;
                  })
                }
              >
                {busy === "subscribe" ? t.webhook.acting : t.webhook.action}
              </Button>
            </Section>

            <Section title={t.sending.title}>
              <p className={paused ? "text-wait" : "text-muted"}>
                {paused ? t.sending.offBody : t.sending.onBody}
              </p>
              <Button
                variant={paused ? "primary" : "secondary"}
                disabled={busy !== null}
                onClick={() =>
                  act("pause", async () => {
                    await unwrap(
                      paused
                        ? browserApi.POST("/api/v1/account/resume")
                        : browserApi.POST("/api/v1/account/pause"),
                    );
                    return paused ? t.sending.resumed : t.sending.paused;
                  })
                }
              >
                {paused ? t.sending.resume : t.sending.pause}
              </Button>
            </Section>

            <Section title={t.reconcile.title}>
              <p className="text-muted">{t.reconcile.body}</p>
              <Button
                disabled={busy !== null || acc.status !== "ACTIVE"}
                onClick={() =>
                  act("reconcile", async () => {
                    const result = await unwrap(browserApi.POST("/api/v1/reconcile"));
                    return t.reconcile.done(result.queued);
                  })
                }
              >
                {busy === "reconcile" ? t.reconcile.acting : t.reconcile.action}
              </Button>
            </Section>
          </>
        ) : null}

        <Section title={t.params.title}>
          <p className="text-muted">{t.params.body}</p>
          <p>{t.params.hourly(status.data.rate_limit.limit)}</p>
        </Section>
      </div>
    </>
  );
}
