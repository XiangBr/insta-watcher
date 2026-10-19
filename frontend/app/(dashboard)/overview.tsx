"use client";

import Link from "next/link";
import useSWR from "swr";
import { AlertIcon, CheckIcon, PauseIcon } from "@/components/icons";
import { Card, PageHeader } from "@/components/ui";
import { browserApi, unwrap } from "@/lib/api.client";
import { attentionItems, type Status } from "@/lib/attention";
import { relativeTime } from "@/lib/format";
import { messages } from "@/lib/messages";

const t = messages.overview;

type State = { text: string; tone: "ok" | "wait" | "fail"; icon: "check" | "pause" | "alert" };

function stateSentence(status: Status): State {
  const account = status.account;
  if (!account) return { text: t.stateNoAccount, tone: "wait", icon: "alert" };
  if (account.status === "TOKEN_INVALID") return { text: t.stateInvalid, tone: "fail", icon: "alert" };
  if (account.sending_paused) return { text: t.statePaused, tone: "wait", icon: "pause" };
  return { text: t.stateOk, tone: "ok", icon: "check" };
}

const STATE_ICON = { check: CheckIcon, pause: PauseIcon, alert: AlertIcon } as const;

const TONE = {
  ok: "bg-ok-soft text-ok",
  wait: "bg-wait-soft text-wait",
  fail: "bg-fail-soft text-fail",
} as const;

function Measure({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="flex items-baseline justify-between gap-4 border-b border-line py-3">
      <dt className="text-sm text-muted">{label}</dt>
      <dd className="text-right font-semibold tabular-nums">{value}</dd>
    </div>
  );
}

export function Overview({ initial }: { initial: Status | null }) {
  const { data: status, error } = useSWR(
    "status",
    () => unwrap(browserApi.GET("/api/v1/status")),
    { fallbackData: initial ?? undefined, refreshInterval: 10_000 },
  );

  if (!status) {
    return (
      <>
        <PageHeader title={t.title} />
        <p
          role={error ? "alert" : "status"}
          className={`mt-6 ${error ? "text-fail" : "text-muted"}`}
        >
          {error ? t.unreachable : messages.common.loading}
        </p>
      </>
    );
  }

  const state = stateSentence(status);
  const items = attentionItems(status);
  const account = status.account;
  const StateIcon = STATE_ICON[state.icon];
  return (
    <>
      <PageHeader title={t.title} />

      <Card className="mt-6 flex items-center gap-4 p-5">
        <span className={`grid size-11 shrink-0 place-items-center rounded-full ${TONE[state.tone]}`}>
          <StateIcon size={22} />
        </span>
        <p className="text-lg font-bold">
          {account ? <span className="text-muted">@{account.username} </span> : null}
          {state.text}
        </p>
      </Card>

      <section aria-labelledby="attention" className="mt-8">
        <h2 id="attention" className="text-base font-bold">
          {items.length > 0 ? t.needsAttention : t.allClear}
        </h2>
        {items.length > 0 ? (
          <Card className="mt-3 overflow-hidden">
            <ul className="divide-y divide-line">
              {items.map((item) => (
                <li key={item.id}>
                  <Link
                    href={item.href}
                    className="flex items-start gap-3 px-5 py-3.5 transition-colors hover:bg-surface-2"
                  >
                    <span
                      aria-hidden
                      className={`mt-1.5 size-2 shrink-0 rounded-full ${
                        item.tone === "danger" ? "bg-fail" : "bg-wait"
                      }`}
                    />
                    <span className="text-sm">{item.text}</span>
                  </Link>
                </li>
              ))}
            </ul>
          </Card>
        ) : null}
      </section>

      <Card className="mt-8 px-5 py-2">
        <dl className="grid gap-x-10 md:grid-cols-2">
          <Measure
            label={t.measures.lastHour}
            value={t.ofLimit(status.rate_limit.used, status.rate_limit.limit)}
          />
          <Measure label={t.measures.queue} value={status.jobs.pending + status.jobs.running} />
          <Measure label={t.measures.failed} value={status.jobs.failed} />
          <Measure label={t.measures.unconfirmed} value={status.jobs.unconfirmed} />
          <Measure label={t.measures.campaigns} value={status.active_campaigns} />
          <Measure label={t.measures.webhook} value={relativeTime(status.last_webhook_at)} />
          <Measure
            label={t.measures.token}
            value={
              account?.token_days_left == null
                ? messages.common.none
                : t.tokenDays(account.token_days_left, account.token_expiry_confirmed)
            }
          />
        </dl>
      </Card>
    </>
  );
}
