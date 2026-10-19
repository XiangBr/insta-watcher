"use client";

import { useState } from "react";
import useSWR from "swr";
import { Button, Card, ConfirmDialog, Notice, PageHeader, StatusBadge, selectClass } from "@/components/ui";
import { ApiCallError } from "@/lib/api-error";
import { browserApi, unwrap } from "@/lib/api.client";
import type { components, paths } from "@/lib/api-types";
import { dmSummary, gateTone } from "@/lib/delivery-view";
import { dateTime, relativeTime } from "@/lib/format";
import { messages } from "@/lib/messages";

const t = messages.deliveries;
type Delivery = components["schemas"]["DeliveryOut"];
type Period = "day" | "week" | "month" | "all";

const PERIOD_MS: Record<Exclude<Period, "all">, number> = {
  day: 24 * 3600_000,
  week: 7 * 24 * 3600_000,
  month: 30 * 24 * 3600_000,
};
const STATUSES = ["SENT", "PENDING", "FAILED", "UNCONFIRMED"] as const;
const STATE_TEXT: Record<string, string> = {
  SENT: "bg-ok",
  PENDING: "bg-wait",
  FAILED: "bg-fail",
  UNCONFIRMED: "bg-unconf",
};

type Filters = { campaign: string; status: string; period: Period };
type Query = NonNullable<paths["/api/v1/deliveries"]["get"]["parameters"]["query"]>;

function deliveriesQuery(filters: Filters, before?: number): Query {
  return {
    limit: 30,
    before,
    campaign_id: filters.campaign ? Number(filters.campaign) : undefined,
    status: (filters.status || undefined) as Query["status"],
    since:
      filters.period === "all"
        ? undefined
        : new Date(Date.now() - PERIOD_MS[filters.period]).toISOString(),
  };
}

function Step({ tone, title, children }: { tone: string; title: string; children: React.ReactNode }) {
  return (
    <li className="relative">
      <span aria-hidden className={`absolute top-1.5 -left-[1.6rem] size-2.5 rounded-full ${tone}`} />
      <p className="text-sm font-medium">{title}</p>
      <div className="mt-1 space-y-1 text-sm text-muted">{children}</div>
    </li>
  );
}

/** The journey of one trigger: the comment, the DM that answered it, the public reply. */
function whoOf(d: Delivery): string {
  if (d.actor_username) return `@${d.actor_username}`;
  // Story replies carry no @ until the worker looks it up, so "no name" is not "anonymized".
  return d.anonymized ? t.anonymized : t.unknownUser;
}

function Trail({ delivery: d }: { delivery: Delivery }) {
  const who = whoOf(d);
  const isStory = d.trigger === "STORY_REPLY";
  const gated = d.gate_state !== null && d.gate_state !== undefined;
  return (
    <ol className="space-y-5 border-l border-line pl-5">
      <Step tone="bg-muted" title={isStory ? t.trail.storyReply : t.trail.comment}>
        <p>
          {who} · {dateTime(d.created_at)} · {t.trail.origin[d.origin] ?? d.origin}
        </p>
        {d.text ? (
          <blockquote className="whitespace-pre-wrap break-words border-l-2 border-line pl-3 text-fg">
            {d.text}
          </blockquote>
        ) : (
          <p>{t.trail.textRemoved}</p>
        )}
        {d.matched_keyword ? <p>{t.trail.keyword(d.matched_keyword)}</p> : null}
      </Step>
      <Step tone={STATE_TEXT[d.status] ?? "bg-muted"} title={gated ? t.trail.invite : t.trail.dm}>
        <p className="flex flex-wrap items-center gap-x-3">
          <StatusBadge status={d.status} />
          <span>{t.trail.attempts(d.attempts)}</span>
        </p>
        {d.dm_sent_at ? <p>{t.trail.sentAt(relativeTime(d.dm_sent_at))}</p> : null}
        {d.status === "PENDING" ? <p>{t.trail.queued}</p> : null}
        {d.error_message ? <p className="break-words text-fail">{d.error_message}</p> : null}
      </Step>
      {d.gate_state ? (
        <>
          <Step tone={gateTone(d.gate_state)} title={t.trail.gate}>
            <p>{t.gate.state[d.gate_state] ?? d.gate_state}</p>
            {d.gate_taps > 0 ? <p>{t.gate.taps(d.gate_taps)}</p> : null}
          </Step>
          <Step
            tone={d.followup_status ? (STATE_TEXT[d.followup_status] ?? "bg-muted") : "bg-line"}
            title={t.trail.link}
          >
            {d.followup_status ? (
              <>
                <StatusBadge status={d.followup_status} />
                {d.followup_sent_at ? <p>{t.trail.sentAt(relativeTime(d.followup_sent_at))}</p> : null}
                {d.followup_error ? <p className="break-words text-fail">{d.followup_error}</p> : null}
              </>
            ) : (
              <p>{t.trail.linkWaiting}</p>
            )}
          </Step>
        </>
      ) : null}
      <Step tone={d.public_reply_status ? (STATE_TEXT[d.public_reply_status] ?? "bg-muted") : "bg-line"} title={t.trail.reply}>
        {d.public_reply_status ? (
          <>
            <StatusBadge status={d.public_reply_status} />
            {d.public_reply_sent_at ? <p>{t.trail.sentAt(relativeTime(d.public_reply_sent_at))}</p> : null}
            {d.public_reply_error ? <p className="break-words text-fail">{d.public_reply_error}</p> : null}
          </>
        ) : (
          <p>{isStory ? t.trail.replyNotApplicable : t.trail.replyOff}</p>
        )}
      </Step>
    </ol>
  );
}

function DeliveryList({ filters }: { filters: Filters }) {
  const { data, error, isLoading, mutate } = useSWR(
    ["deliveries", filters.campaign, filters.status, filters.period],
    () =>
      unwrap(
        browserApi.GET("/api/v1/deliveries", {
          params: { query: deliveriesQuery(filters) },
        }),
      ),
    { refreshInterval: 10_000 },
  );
  const [extra, setExtra] = useState<Delivery[]>([]);
  const [cursor, setCursor] = useState<number | null | undefined>(undefined);
  const [loadingMore, setLoadingMore] = useState(false);
  const [flash, setFlash] = useState<{ kind: "ok" | "error"; text: string } | null>(null);
  const [busyId, setBusyId] = useState<number | null>(null);
  const [resending, setResending] = useState<Delivery | null>(null);
  const [confirmed, setConfirmed] = useState(false);

  const nextCursor = cursor === undefined ? (data?.next_cursor ?? null) : cursor;
  const firstIds = new Set(data?.items.map((i) => i.id));
  const items = data ? [...data.items, ...extra.filter((i) => !firstIds.has(i.id))] : [];

  async function loadMore() {
    if (nextCursor === null) return;
    setLoadingMore(true);
    try {
      const page = await unwrap(
        browserApi.GET("/api/v1/deliveries", {
          params: { query: deliveriesQuery(filters, nextCursor) },
        }),
      );
      setExtra((current) => [...current, ...page.items]);
      setCursor(page.next_cursor ?? null);
    } catch (e) {
      setFlash({ kind: "error", text: e instanceof ApiCallError ? e.message : t.title });
    } finally {
      setLoadingMore(false);
    }
  }

  async function retry(delivery: Delivery, confirmNotDelivered: boolean) {
    setBusyId(delivery.id);
    setFlash(null);
    try {
      await unwrap(
        browserApi.POST("/api/v1/deliveries/{delivery_id}/retry", {
          params: { path: { delivery_id: delivery.id } },
          body: { confirm_not_delivered: confirmNotDelivered },
        }),
      );
      setFlash({ kind: "ok", text: t.retried });
      setExtra([]);
      setCursor(undefined);
      await mutate();
    } catch (e) {
      setFlash({ kind: "error", text: e instanceof ApiCallError ? e.message : messages.common.loadFailed });
    } finally {
      setBusyId(null);
    }
  }

  function closeResend() {
    setResending(null);
    setConfirmed(false);
  }
  async function confirmResend() {
    const delivery = resending;
    closeResend();
    if (delivery) await retry(delivery, true);
  }

  return (
    <>
      <div className="mt-4 min-h-10" aria-live="polite">
        {flash ? <Notice kind={flash.kind}>{flash.text}</Notice> : null}
      </div>

      {isLoading ? (
        <p className="text-muted">{messages.common.loading}</p>
      ) : error || !data ? (
        <p role="alert" className="text-fail">{messages.common.loadFailed}</p>
      ) : items.length === 0 ? (
        <Card className="px-6 py-12 text-center">
          <h2 className="text-lg font-bold">{t.emptyTitle}</h2>
          <p className="mx-auto mt-1 max-w-sm text-muted">{t.emptyBody}</p>
        </Card>
      ) : (
        <>
          <Card className="overflow-hidden">
          <div
            aria-hidden
            className="hidden grid-cols-[8rem_1fr_1fr_9rem_9rem] gap-4 border-b border-line bg-surface-2 px-5 py-2.5 text-xs font-semibold text-muted md:grid"
          >
            <span>{t.columns.when}</span>
            <span>{t.columns.who}</span>
            <span>{t.columns.campaign}</span>
            <span>{t.columns.dm}</span>
            <span>{t.columns.reply}</span>
          </div>
          <ul className="divide-y divide-line">
            {items.map((d) => (
              <li key={d.id}>
                <details className="group">
                  <summary className="grid cursor-pointer list-none grid-cols-2 items-center gap-x-4 gap-y-1 px-5 py-3.5 transition-colors hover:bg-surface-2 md:grid-cols-[8rem_1fr_1fr_9rem_9rem] [&::-webkit-details-marker]:hidden">
                    <span className="text-sm text-muted">{relativeTime(d.created_at)}</span>
                    <span className="truncate font-semibold">
                      {whoOf(d)}
                    </span>
                    <span className="col-span-2 truncate text-sm text-muted md:col-span-1">{d.campaign_name}</span>
                    <StatusBadge status={dmSummary(d).status} label={dmSummary(d).label} />
                    {d.public_reply_status ? (
                      <StatusBadge status={d.public_reply_status} />
                    ) : (
                      <span className="text-sm text-muted">{messages.common.none}</span>
                    )}
                  </summary>
                  <div className="space-y-5 px-5 pb-6 md:pl-[10.5rem]">
                    <Trail delivery={d} />
                    {!d.anonymized && d.status === "FAILED" ? (
                      <Button disabled={busyId === d.id} onClick={() => retry(d, false)}>
                        {busyId === d.id ? t.retrying : t.retry}
                      </Button>
                    ) : null}
                    {!d.anonymized && d.status === "UNCONFIRMED" ? (
                      <Button disabled={busyId === d.id} onClick={() => setResending(d)}>
                        {t.resend}
                      </Button>
                    ) : null}
                  </div>
                </details>
              </li>
            ))}
          </ul>
          </Card>
          {nextCursor !== null ? (
            <div className="mt-6">
              <Button onClick={loadMore} disabled={loadingMore}>
                {loadingMore ? messages.common.loadingMore : messages.common.loadMore}
              </Button>
            </div>
          ) : null}
        </>
      )}

      <ConfirmDialog
        open={resending !== null}
        title={t.resendTitle}
        confirmLabel={t.resendAction}
        confirmVariant="primary"
        confirmDisabled={!confirmed}
        onConfirm={confirmResend}
        onCancel={closeResend}
      >
        <p>{t.resendBody}</p>
        <label className="flex items-start gap-2 text-fg">
          <input
            type="checkbox"
            className="mt-1"
            checked={confirmed}
            onChange={(e) => setConfirmed(e.target.checked)}
          />
          <span>{t.resendCheck}</span>
        </label>
      </ConfirmDialog>
    </>
  );
}

export function LogsView({
  initialStatus,
  initialCampaign,
}: {
  initialStatus: string;
  initialCampaign: string;
}) {
  const [filters, setFilters] = useState<Filters>({
    campaign: initialCampaign,
    status: initialStatus,
    period: "week",
  });
  const { data: campaigns } = useSWR("campaigns", () => unwrap(browserApi.GET("/api/v1/campaigns")));
  const set = <K extends keyof Filters>(key: K, value: Filters[K]) =>
    setFilters((current) => ({ ...current, [key]: value }));

  return (
    <>
      <PageHeader title={t.title} />
      <div className="mt-5 flex flex-wrap gap-4">
        <label className="text-sm">
          <span className="block font-semibold">{t.filters.campaign}</span>
          <select
            className={`${selectClass} mt-1.5 min-w-40`}
            value={filters.campaign}
            onChange={(e) => set("campaign", e.target.value)}
          >
            <option value="">{t.filters.allCampaigns}</option>
            {campaigns?.map((c) => (
              <option key={c.id} value={String(c.id)}>
                {c.name}
              </option>
            ))}
          </select>
        </label>
        <label className="text-sm">
          <span className="block font-semibold">{t.filters.status}</span>
          <select
            className={`${selectClass} mt-1.5 min-w-40`}
            value={filters.status}
            onChange={(e) => set("status", e.target.value)}
          >
            <option value="">{t.filters.allStatuses}</option>
            {STATUSES.map((s) => (
              <option key={s} value={s}>
                {t.status[s]}
              </option>
            ))}
          </select>
        </label>
        <label className="text-sm">
          <span className="block font-semibold">{t.filters.period}</span>
          <select
            className={`${selectClass} mt-1.5 min-w-40`}
            value={filters.period}
            onChange={(e) => set("period", e.target.value as Period)}
          >
            <option value="day">{t.filters.day}</option>
            <option value="week">{t.filters.week}</option>
            <option value="month">{t.filters.month}</option>
            <option value="all">{t.filters.all}</option>
          </select>
        </label>
      </div>
      {/* Remounted per filter set, so loaded pages and the cursor never leak across filters. */}
      <DeliveryList key={`${filters.campaign}|${filters.status}|${filters.period}`} filters={filters} />
    </>
  );
}
