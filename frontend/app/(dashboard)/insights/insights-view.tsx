"use client";

import { useMemo, useState } from "react";
import useSWR from "swr";
import { LineChart, Columns } from "@/components/charts";
import { RefreshIcon, SearchIcon } from "@/components/icons";
import { Button, Card, Notice, PageHeader, inputClass, selectClass } from "@/components/ui";
import { ApiCallError } from "@/lib/api-error";
import { browserApi, unwrap } from "@/lib/api.client";
import type { components } from "@/lib/api-types";
import { relativeTime } from "@/lib/format";
import {
  FORMATS,
  activityGrid,
  daySeries,
  daysInRange,
  filterPosts,
  followerSeries,
  groupStats,
  postsPerWeek,
  presetRange,
  previousRange,
  sumMetric,
  type Format,
  type Range,
} from "@/lib/insights";
import { buildAdvice } from "@/lib/insights-advice";
import { changePct, compact, percent, shortDay } from "@/lib/insights-format";
import { messages } from "@/lib/messages";
import {
  AudienceSection,
  ContentSection,
  FormatsSection,
  PostsSection,
  Section,
  TimingSection,
} from "./insights-sections";

const t = messages.insights;
type Insights = components["schemas"]["InsightsOut"];

const DAY_MS = 86_400_000;
const DAY_CHART_METRICS = [
  "reach",
  "views",
  "accounts_engaged",
  "total_interactions",
  "follows",
  "profile_links_taps",
] as const;

/** A custom range from two local dates (inclusive), in the account's time zone. */
function customRange(from: string, to: string, offset: number): Range | null {
  if (!from || !to) return null;
  const start = Date.parse(`${from}T00:00:00Z`) - offset * 60_000;
  const end = Date.parse(`${to}T00:00:00Z`) - offset * 60_000 + DAY_MS;
  if (!Number.isFinite(start) || !Number.isFinite(end) || end <= start) return null;
  return { from: new Date(start), to: new Date(end) };
}

function Kpi({
  label,
  value,
  sub,
  change,
}: {
  label: string;
  value: string;
  sub?: string;
  change?: number | null;
}) {
  return (
    <Card className="p-4">
      <p className="text-sm text-muted">{label}</p>
      <p className="mt-1 text-2xl font-bold tabular-nums">{value}</p>
      {sub ? <p className="mt-0.5 text-xs text-muted">{sub}</p> : null}
      {change != null ? (
        <p className={`mt-1 text-xs font-semibold ${change >= 0 ? "text-ok" : "text-fail"}`}>
          {t.kpi.vsPrevious(change)}
        </p>
      ) : null}
    </Card>
  );
}

function SyncBar({ data, onSynced }: { data: Insights; onSynced: () => void }) {
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<{ kind: "ok" | "error"; text: string } | null>(null);
  const running = data.sync.running || busy;

  async function sync() {
    setBusy(true);
    setNotice(null);
    try {
      const result = await unwrap(browserApi.POST("/api/v1/insights/sync"));
      setNotice({ kind: "ok", text: result.started ? t.syncStarted : t.syncBusy });
      onSynced();
    } catch (error) {
      setNotice({
        kind: "error",
        text: error instanceof ApiCallError ? error.message : messages.common.loadFailed,
      });
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-3 text-sm text-muted">
        <span>
          {data.sync.finished_at
            ? t.lastSync(relativeTime(data.sync.finished_at))
            : t.neverSynced}
        </span>
        <Button onClick={sync} disabled={running}>
          <RefreshIcon size={16} className={running ? "animate-spin" : ""} />
          {running ? t.syncing : t.syncNow}
        </Button>
      </div>
      {notice ? <Notice kind={notice.kind}>{notice.text}</Notice> : null}
      {data.sync.missing_permission ? (
        <p className="rounded-lg bg-wait-soft px-4 py-3 text-sm text-wait">{t.missingPermission}</p>
      ) : null}
      {data.sync.partial ? <p className="text-sm text-muted">{t.partial}</p> : null}
      {data.sync.errors.length > 0 ? (
        <details className="text-sm text-muted">
          <summary className="cursor-pointer">{t.syncErrors}</summary>
          <ul className="mt-2 list-disc space-y-1 pl-5 break-words">
            {data.sync.errors.map((e) => (
              <li key={e}>{e}</li>
            ))}
          </ul>
        </details>
      ) : null}
    </div>
  );
}

type Preset = number | null | "custom";

function Filters({
  preset,
  setPreset,
  from,
  to,
  setFrom,
  setTo,
  formats,
  setFormats,
  search,
  setSearch,
}: {
  preset: Preset;
  setPreset: (p: Preset) => void;
  from: string;
  to: string;
  setFrom: (v: string) => void;
  setTo: (v: string) => void;
  formats: Format[];
  setFormats: (f: Format[]) => void;
  search: string;
  setSearch: (v: string) => void;
}) {
  const chip = (on: boolean) =>
    `rounded-full px-3 py-1.5 text-sm font-medium transition-colors ${
      on ? "bg-accent text-accent-fg" : "bg-surface-2 text-muted hover:text-fg"
    }`;
  const filtered = preset !== 30 || formats.length > 0 || search !== "";
  return (
    <Card className="space-y-4 p-4">
      <fieldset>
        <legend className="text-xs font-semibold tracking-wide text-muted uppercase">
          {t.filters.period}
        </legend>
        <div className="mt-2 flex flex-wrap gap-2">
          {t.filters.presets.map((p) => (
            <button
              key={p.label}
              type="button"
              aria-pressed={preset === p.days}
              className={chip(preset === p.days)}
              onClick={() => setPreset(p.days)}
            >
              {p.label}
            </button>
          ))}
          <button
            type="button"
            aria-pressed={preset === "custom"}
            className={chip(preset === "custom")}
            onClick={() => setPreset("custom")}
          >
            {t.filters.custom}
          </button>
        </div>
        {preset === "custom" ? (
          <div className="mt-3 flex flex-wrap items-center gap-3 text-sm">
            <label className="flex items-center gap-2">
              {t.filters.from}
              <input type="date" value={from} onChange={(e) => setFrom(e.target.value)} className={selectClass} />
            </label>
            <label className="flex items-center gap-2">
              {t.filters.to}
              <input type="date" value={to} onChange={(e) => setTo(e.target.value)} className={selectClass} />
            </label>
          </div>
        ) : null}
      </fieldset>
      <div className="flex flex-wrap items-end gap-4">
        <fieldset>
          <legend className="text-xs font-semibold tracking-wide text-muted uppercase">
            {t.filters.formats}
          </legend>
          <div className="mt-2 flex flex-wrap gap-2">
            <button
              type="button"
              aria-pressed={formats.length === 0}
              className={chip(formats.length === 0)}
              onClick={() => setFormats([])}
            >
              {t.filters.allFormats}
            </button>
            {FORMATS.map((f) => {
              const on = formats.includes(f);
              return (
                <button
                  key={f}
                  type="button"
                  aria-pressed={on}
                  className={chip(on)}
                  onClick={() => setFormats(on ? formats.filter((x) => x !== f) : [...formats, f])}
                >
                  {t.formats[f]}
                </button>
              );
            })}
          </div>
        </fieldset>
        <label className="relative min-w-56 flex-1">
          <span className="sr-only">{t.filters.search}</span>
          <SearchIcon size={16} className="absolute top-1/2 left-3 -translate-y-1/2 text-muted" />
          <input
            type="search"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder={t.filters.search}
            className={`${inputClass} pl-9`}
          />
        </label>
        {filtered ? (
          <Button
            variant="ghost"
            onClick={() => {
              setPreset(30);
              setFormats([]);
              setSearch("");
            }}
          >
            {t.filters.clear}
          </Button>
        ) : null}
      </div>
    </Card>
  );
}

export function InsightsView() {
  const { data, error, mutate } = useSWR(
    "insights",
    () => unwrap(browserApi.GET("/api/v1/insights")),
    {
      revalidateOnFocus: false,
      // While a sync runs, look again every few seconds; otherwise once a minute is plenty.
      refreshInterval: (latest) => (latest?.sync.running ? 5_000 : 60_000),
    },
  );
  const [preset, setPreset] = useState<Preset>(30);
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [formats, setFormats] = useState<Format[]>([]);
  const [search, setSearch] = useState("");
  const [dayMetric, setDayMetric] = useState<string>("reach");
  // "Now" is fixed when the page opens (rounded to the hour), so ranges and the activity
  // request stay stable across renders.
  const [now] = useState(() => Math.ceil(Date.now() / 3_600_000) * 3_600_000);

  const offset = data?.utc_offset_minutes ?? -180;
  // The range is kept at day precision so the activity request is not refetched every render.
  const range = useMemo<Range | null>(() => {
    if (preset === "custom") return customRange(from, to, offset);
    return presetRange(preset, new Date(now));
  }, [preset, from, to, offset, now]);

  const activityRange = useMemo<Range>(() => {
    return range ?? { from: new Date(now - 730 * DAY_MS), to: new Date(now) };
  }, [range, now]);
  const { data: activity } = useSWR(
    ["insights-activity", activityRange.from.toISOString(), activityRange.to.toISOString()],
    () =>
      unwrap(
        browserApi.GET("/api/v1/insights/activity", {
          params: {
            query: {
              since: activityRange.from.toISOString(),
              until: activityRange.to.toISOString(),
            },
          },
        }),
      ),
    { revalidateOnFocus: false },
  );

  const view = useMemo(() => {
    if (!data) return null;
    const followers = data.profile?.followers_count ?? null;
    const posts = filterPosts(data.posts, { range, formats, search });
    const days = daysInRange(data.days, range, offset);
    const previousDays = range ? daysInRange(data.days, previousRange(range), offset) : [];
    const previousPosts = range
      ? filterPosts(data.posts, { range: previousRange(range), formats, search })
      : [];
    const followerPoints = followerSeries(data.days).filter((p) => days.some((d) => d.day === p.day));
    const grid = activity ? activityGrid(activity.hours, offset) : null;
    const perWeek = postsPerWeek(posts.length, range, posts);
    return {
      followers,
      posts,
      days,
      previousDays,
      engagement: groupStats("all", posts, followers).engagement,
      previousEngagement: previousPosts.length
        ? groupStats("prev", previousPosts, followers).engagement
        : null,
      followerPoints,
      grid,
      perWeek,
      advice: buildAdvice({
        posts,
        followers,
        offset,
        postsPerWeek: perWeek,
        activity: grid,
        online: data.audience?.online_followers,
        reachByFollowType: data.audience?.reach_by_follow_type,
        keywords: activity?.keywords ?? [],
      }),
    };
  }, [data, range, formats, search, offset, activity]);

  if (!data || !view) {
    return (
      <>
        <PageHeader title={t.title} />
        <p role={error ? "alert" : "status"} className={`mt-6 ${error ? "text-fail" : "text-muted"}`}>
          {error ? (error instanceof ApiCallError ? error.message : messages.common.loadFailed) : messages.common.loading}
        </p>
      </>
    );
  }

  const profile = data.profile;
  const sumWithChange = (metric: string) => {
    const current = sumMetric(view.days, metric);
    return { value: current, change: range ? changePct(current, sumMetric(view.previousDays, metric)) : null };
  };
  const reach = sumWithChange("reach");
  const views = sumWithChange("views");
  const interactions = sumWithChange("total_interactions");
  const firstFollowers = view.followerPoints[0]?.value;
  const lastFollowers = view.followerPoints[view.followerPoints.length - 1]?.value;
  const dayPoints = daySeries(view.days, dayMetric).map((p) => ({ label: shortDay(p.day), value: p.value }));
  const usesReach = view.posts.some((p) => (p.metrics.reach ?? 0) > 0);

  return (
    <>
      <PageHeader title={t.title} />

      <div className="mt-6 flex flex-wrap items-start justify-between gap-4">
        {profile ? (
          <div className="flex items-center gap-3">
            {profile.profile_picture_url?.startsWith("https://") ? (
              // Instagram CDN avatar: external and already sized.
              // eslint-disable-next-line @next/next/no-img-element
              <img src={profile.profile_picture_url} alt="" className="size-12 rounded-full bg-surface-2 object-cover" />
            ) : null}
            <div>
              <p className="font-bold">@{profile.username}</p>
              <p className="text-sm text-muted">
                {compact(profile.followers_count)} {t.kpi.followers.toLowerCase()} ·{" "}
                {compact(profile.media_count)} posts
              </p>
            </div>
          </div>
        ) : null}
        <SyncBar data={data} onSynced={() => void mutate()} />
      </div>

      <div className="mt-6">
        <Filters
          preset={preset}
          setPreset={setPreset}
          from={from}
          to={to}
          setFrom={setFrom}
          setTo={setTo}
          formats={formats}
          setFormats={setFormats}
          search={search}
          setSearch={setSearch}
        />
      </div>

      <div className="mt-6 grid grid-cols-2 gap-3 lg:grid-cols-3">
        <Kpi
          label={t.kpi.followers}
          value={compact(profile?.followers_count)}
          sub={
            firstFollowers != null && lastFollowers != null && view.followerPoints.length > 1
              ? t.kpi.followersDelta(lastFollowers - firstFollowers)
              : undefined
          }
        />
        <Kpi label={t.kpi.reach} value={compact(reach.value)} change={reach.change} />
        <Kpi label={t.kpi.views} value={compact(views.value)} change={views.change} />
        <Kpi label={t.kpi.interactions} value={compact(interactions.value)} change={interactions.change} />
        <Kpi
          label={t.kpi.engagement}
          value={percent(view.engagement)}
          sub={usesReach ? t.kpi.engagementHint : t.kpi.engagementHintFollowers}
          change={changePct(view.engagement, view.previousEngagement)}
        />
        <Kpi
          label={t.kpi.posts}
          value={String(view.posts.length)}
          sub={view.perWeek != null ? t.kpi.perWeek(view.perWeek) : undefined}
        />
      </div>

      <Section title={t.sections.advice}>
        {view.advice.length > 0 ? (
          <Card className="divide-y divide-line">
            {view.advice.map((a) => (
              <p key={a.id} className="flex gap-3 px-5 py-3.5 text-sm">
                <span aria-hidden className="mt-1.5 size-2 shrink-0 rounded-full bg-accent" />
                <span>{a.text}</span>
              </p>
            ))}
          </Card>
        ) : (
          <p className="text-sm text-muted">{t.sections.adviceEmpty}</p>
        )}
      </Section>

      <Section title={t.sections.growth} hint={t.delayNote}>
        {view.days.some((d) => Object.keys(d.metrics).length > 0) || view.followerPoints.length > 1 ? (
          <div className="grid gap-4 lg:grid-cols-2">
            <Card className="p-5">
              <h3 className="text-sm font-semibold">{t.sections.followersChart}</h3>
              <LineChart
                label={t.sections.followersChart}
                points={view.followerPoints.map((p) => ({ label: shortDay(p.day), value: p.value }))}
                format={compact}
              />
              <p className="mt-2 text-xs text-muted">{t.sections.followersRebuilt}</p>
            </Card>
            <Card className="p-5">
              <label className="flex items-center justify-between gap-3">
                <span className="sr-only">{t.sections.rankBy}</span>
                <select value={dayMetric} onChange={(e) => setDayMetric(e.target.value)} className={selectClass}>
                  {DAY_CHART_METRICS.map((m) => (
                    <option key={m} value={m}>
                      {t.dayMetrics[m]}
                    </option>
                  ))}
                </select>
              </label>
              <div className="mt-2">
                {dayMetric === "follows" ? (
                  <Columns
                    label={t.dayMetrics.follows ?? ""}
                    items={view.days.map((d) => ({
                      label: shortDay(d.day),
                      value: d.metrics.follows ?? 0,
                      hint:
                        d.metrics.unfollows != null
                          ? `${t.dayMetrics.unfollows}: ${compact(d.metrics.unfollows)}`
                          : undefined,
                    }))}
                    format={compact}
                    tick={Math.max(1, Math.ceil(view.days.length / 8))}
                  />
                ) : (
                  <LineChart label={t.dayMetrics[dayMetric] ?? dayMetric} points={dayPoints} format={compact} />
                )}
              </div>
            </Card>
          </div>
        ) : (
          <p className="text-sm text-muted">{t.sections.growthEmpty}</p>
        )}
      </Section>

      <PostsSection posts={view.posts} followers={view.followers} />
      <FormatsSection posts={view.posts} followers={view.followers} />
      <TimingSection
        posts={view.posts}
        followers={view.followers}
        offset={offset}
        grid={view.grid}
        online={data.audience?.online_followers ?? null}
      />
      <AudienceSection audience={data.audience} activity={activity ?? null} posts={view.posts} />
      <ContentSection posts={view.posts} followers={view.followers} />
    </>
  );
}
