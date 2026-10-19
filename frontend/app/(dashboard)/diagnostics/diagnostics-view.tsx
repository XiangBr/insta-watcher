"use client";

import { useState, type ReactNode } from "react";
import useSWR from "swr";
import { Button, Card, Notice, PageHeader, StatusBadge } from "@/components/ui";
import { ApiCallError } from "@/lib/api-error";
import { browserApi, unwrap } from "@/lib/api.client";
import { dateTime, relativeTime } from "@/lib/format";
import { messages } from "@/lib/messages";

const t = messages.diagnostics;
const LEVEL_TONE: Record<string, string> = {
  INFO: "text-muted",
  WARNING: "text-wait",
  ERROR: "text-fail",
};

type Change = {
  field?: string;
  kind?: string;
  payload?: string;
  comment_id?: string;
  from_username?: string;
  text?: string;
  is_echo?: boolean;
};

function Section({ title, aside, children }: { title: string; aside?: ReactNode; children: ReactNode }) {
  const id = `d-${title}`;
  return (
    <Card className="mt-6 p-5">
      <section aria-labelledby={id}>
        <div className="flex flex-wrap items-center justify-between gap-3">
          <h2 id={id} className="text-base font-bold">
            {title}
          </h2>
          {aside}
        </div>
        <div className="mt-3">{children}</div>
      </section>
    </Card>
  );
}

function QueueSection() {
  const { data } = useSWR("status", () => unwrap(browserApi.GET("/api/v1/status")), {
    refreshInterval: 10_000,
  });
  if (!data) return null;
  const rows: [string, number][] = [
    [t.queue.pending, data.jobs.pending],
    [t.queue.running, data.jobs.running],
    [t.queue.unconfirmed, data.jobs.unconfirmed],
    [t.queue.failed, data.jobs.failed],
    [t.queue.webhookBacklog, data.pending_webhook_events],
  ];
  return (
    <Section title={t.queue.title}>
      <dl className="grid gap-x-10 md:grid-cols-2">
        {rows.map(([label, value]) => (
          <div key={label} className="flex items-baseline justify-between border-b border-line py-2">
            <dt className="text-sm text-muted">{label}</dt>
            <dd className="font-medium tabular-nums">{value}</dd>
          </div>
        ))}
      </dl>
    </Section>
  );
}

function OpsSection() {
  const [onlyOpen, setOnlyOpen] = useState(true);
  const [flash, setFlash] = useState<string | null>(null);
  const { data, error, isLoading, mutate } = useSWR(
    ["ops-events", onlyOpen],
    () =>
      unwrap(
        browserApi.GET("/api/v1/ops-events", { params: { query: { limit: 50, unresolved: onlyOpen } } }),
      ),
    { refreshInterval: 10_000 },
  );

  async function resolve(id: number) {
    setFlash(null);
    try {
      await unwrap(
        browserApi.POST("/api/v1/ops-events/{event_id}/resolve", { params: { path: { event_id: id } } }),
      );
      await mutate();
    } catch (e) {
      setFlash(e instanceof ApiCallError ? e.message : messages.common.loadFailed);
    }
  }

  return (
    <Section
      title={t.ops.title}
      aside={
        <label className="flex items-center gap-2 text-sm">
          <input type="checkbox" checked={onlyOpen} onChange={(e) => setOnlyOpen(e.target.checked)} />
          {t.ops.onlyOpen}
        </label>
      }
    >
      {flash ? <Notice kind="error">{flash}</Notice> : null}
      {isLoading ? (
        <p className="text-muted">{messages.common.loading}</p>
      ) : error || !data ? (
        <p role="alert" className="text-fail">{messages.common.loadFailed}</p>
      ) : data.length === 0 ? (
        <p className="text-muted">{t.ops.empty}</p>
      ) : (
        <ul className="divide-y divide-line">
          {data.map((event) => (
            <li key={event.id} className="flex flex-wrap items-start justify-between gap-3 py-3">
              <div className="min-w-0 flex-1">
                <p className="text-sm text-muted">
                  <span className={`font-medium ${LEVEL_TONE[event.level] ?? ""}`}>
                    {t.ops.level[event.level] ?? event.level}
                  </span>{" "}
                  · {event.source} · {dateTime(event.created_at)}
                </p>
                <p className="mt-1 break-words">{event.message}</p>
              </div>
              {event.resolved_at ? (
                <span className="text-sm text-muted">{t.ops.resolved}</span>
              ) : (
                <Button onClick={() => resolve(event.id)}>{t.ops.resolve}</Button>
              )}
            </li>
          ))}
        </ul>
      )}
    </Section>
  );
}

function WebhooksSection() {
  const [withText, setWithText] = useState(false);
  const { data, error, isLoading } = useSWR(
    ["webhook-events", withText],
    () =>
      unwrap(
        browserApi.GET("/api/v1/webhook-events", { params: { query: { limit: 20, with_text: withText } } }),
      ),
    { refreshInterval: 10_000 },
  );
  return (
    <Section
      title={t.webhooks.title}
      aside={
        <label className="flex items-center gap-2 text-sm">
          <input type="checkbox" checked={withText} onChange={(e) => setWithText(e.target.checked)} />
          {t.webhooks.showText}
        </label>
      }
    >
      {isLoading ? (
        <p className="text-muted">{messages.common.loading}</p>
      ) : error || !data ? (
        <p role="alert" className="text-fail">{messages.common.loadFailed}</p>
      ) : data.length === 0 ? (
        <p className="text-muted">{t.webhooks.empty}</p>
      ) : (
        <ul className="divide-y divide-line">
          {data.map((event) => (
            <li key={event.id} className="py-3">
              <p className="flex flex-wrap items-center gap-x-3 text-sm text-muted">
                <span>{relativeTime(event.received_at)}</span>
                <StatusBadge
                  status={event.status === "PROCESSED" ? "SENT" : event.status === "PENDING" ? "PENDING" : "FAILED"}
                  label={t.webhooks.status[event.status] ?? event.status}
                />
              </p>
              {(event.changes as Change[]).map((change, i) => (
                <p key={i} className="mt-1 break-words">
                  {change.field === "comments"
                    ? t.webhooks.comment(change.from_username)
                    : change.kind === "story_reply"
                      ? t.webhooks.storyReply
                      : change.kind === "postback"
                        ? t.webhooks.postback
                        : change.kind === "echo"
                          ? t.webhooks.echo
                          : t.webhooks.message}
                  {change.comment_id ? (
                    <span className="text-muted"> · {change.comment_id}</span>
                  ) : null}
                  {change.payload ? <span className="text-muted"> · {change.payload}</span> : null}
                  {change.text ? <span className="block text-muted">{change.text}</span> : null}
                </p>
              ))}
              {event.error ? <p className="mt-1 break-words text-fail">{event.error}</p> : null}
            </li>
          ))}
        </ul>
      )}
    </Section>
  );
}

export function DiagnosticsView() {
  return (
    <>
      <PageHeader title={t.title} />
      <p className="mt-2 text-muted">{t.intro}</p>
      <QueueSection />
      <OpsSection />
      <WebhooksSection />
    </>
  );
}
