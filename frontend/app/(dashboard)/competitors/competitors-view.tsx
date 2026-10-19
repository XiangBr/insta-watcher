"use client";

import { useState, type FormEvent } from "react";
import useSWR from "swr";
import { BarList } from "@/components/charts";
import { ImageIcon, PlayIcon, PlusIcon, RefreshIcon, TrashIcon } from "@/components/icons";
import {
  Button,
  Card,
  ConfirmDialog,
  IconButton,
  Notice,
  PageHeader,
  Pill,
  inputClass,
} from "@/components/ui";
import { ApiCallError } from "@/lib/api-error";
import { browserApi, unwrap } from "@/lib/api.client";
import {
  isConfident,
  labelText,
  mainOption,
  multiplier,
  question,
  topPosts,
  type Competitor,
  type CompetitorPost,
  type CompetitorsData,
  type Taxonomy,
} from "@/lib/competitors";
import { relativeTime } from "@/lib/format";
import { compact, percent } from "@/lib/insights-format";
import { messages } from "@/lib/messages";
import { Section } from "../insights/insights-sections";
import { ReportSection } from "./competitors-report";

const t = messages.competitors;
type Flash = { kind: "ok" | "error"; text: string } | null;

function errorText(error: unknown): string {
  return error instanceof ApiCallError ? error.message : messages.common.loadFailed;
}

/** External picture (Instagram CDN). Its signed URL expires, so a broken one shows the tile. */
function Picture({ url, className, video }: { url: string | null; className: string; video?: boolean }) {
  const [failed, setFailed] = useState(false);
  return (
    <span className={`relative grid shrink-0 place-items-center overflow-hidden bg-surface-2 text-muted ${className}`}>
      {video ? <PlayIcon size={18} /> : <ImageIcon size={18} />}
      {url && !failed ? (
        // eslint-disable-next-line @next/next/no-img-element
        <img
          src={url}
          alt=""
          loading="lazy"
          referrerPolicy="no-referrer"
          onError={() => setFailed(true)}
          className="absolute inset-0 size-full object-cover"
        />
      ) : null}
    </span>
  );
}

function StatusBar({ data, onChange }: { data: CompetitorsData; onChange: () => void }) {
  const [busy, setBusy] = useState(false);
  const [flash, setFlash] = useState<Flash>(null);
  const scan = data.last_scan;
  const running = data.running || busy;

  async function scanNow() {
    setBusy(true);
    setFlash(null);
    try {
      const result = await unwrap(browserApi.POST("/api/v1/competitors/scan"));
      setFlash({ kind: "ok", text: result.started ? t.scanStarted : t.scanBusy });
      onChange();
    } catch (error) {
      setFlash({ kind: "error", text: errorText(error) });
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="mt-4 space-y-3">
      {!data.collect_enabled ? (
        <p className="rounded-lg bg-wait-soft px-4 py-3 text-sm text-wait">{t.setupApify}</p>
      ) : null}
      {!data.classify_enabled ? (
        <p className="rounded-lg bg-wait-soft px-4 py-3 text-sm text-wait">{t.setupOpenRouter}</p>
      ) : null}
      <div className="flex flex-wrap items-center gap-3 text-sm text-muted">
        <span>
          {running
            ? t.scanRunning
            : scan?.finished_at
              ? t.lastScan(relativeTime(scan.finished_at))
              : t.neverScanned}
        </span>
        {data.collect_enabled ? (
          <Button onClick={scanNow} disabled={running}>
            <RefreshIcon size={16} className={running ? "animate-spin" : ""} />
            {running ? t.scanning : t.scanNow}
          </Button>
        ) : null}
      </div>
      <p className="text-xs text-muted">
        {t.refreshEvery(data.refresh_hours)} {t.costs(t.usd(scan?.cost_usd), t.usd(data.classify_cost_usd))}
        {data.pending_classification > 0 ? ` · ${t.pending(data.pending_classification)}` : ""}
      </p>
      {scan?.status === "FAILED" && scan.error ? (
        <Notice kind="error">{t.scanFailed(scan.error)}</Notice>
      ) : null}
      {flash ? <Notice kind={flash.kind}>{flash.text}</Notice> : null}
    </div>
  );
}

function ProfilesSection({ data, onChange }: { data: CompetitorsData; onChange: () => void }) {
  const [value, setValue] = useState("");
  const [busy, setBusy] = useState(false);
  const [flash, setFlash] = useState<Flash>(null);
  const [removing, setRemoving] = useState<Competitor | null>(null);
  const others = data.competitors.filter((c) => !c.is_self);

  async function add(event: FormEvent) {
    event.preventDefault();
    if (!value.trim()) return;
    setBusy(true);
    setFlash(null);
    try {
      await unwrap(browserApi.POST("/api/v1/competitors", { body: { username: value } }));
      setFlash({ kind: "ok", text: t.add.added(value.replace(/^@/, "").trim()) });
      setValue("");
      onChange();
    } catch (error) {
      setFlash({ kind: "error", text: errorText(error) });
    } finally {
      setBusy(false);
    }
  }

  async function remove() {
    if (!removing) return;
    setBusy(true);
    try {
      await unwrap(
        browserApi.DELETE("/api/v1/competitors/{competitor_id}", {
          params: { path: { competitor_id: removing.id } },
        }),
      );
      setFlash({ kind: "ok", text: t.add.removed });
      onChange();
    } catch (error) {
      setFlash({ kind: "error", text: errorText(error) });
    } finally {
      setBusy(false);
      setRemoving(null);
    }
  }

  return (
    <Section title={t.add.title} hint={t.add.hint(others.length, data.max_competitors)}>
      <Card className="space-y-4 p-4">
        <form onSubmit={add} className="flex flex-wrap gap-2">
          <label className="min-w-56 flex-1">
            <span className="sr-only">{t.add.placeholder}</span>
            <input
              value={value}
              onChange={(e) => setValue(e.target.value)}
              placeholder={t.add.placeholder}
              className={inputClass}
              autoComplete="off"
            />
          </label>
          <Button type="submit" variant="primary" disabled={busy || !value.trim()}>
            <PlusIcon size={16} />
            {busy ? t.add.adding : t.add.submit}
          </Button>
        </form>
        {flash ? <Notice kind={flash.kind}>{flash.text}</Notice> : null}
        {data.competitors.length > 0 ? (
          <ul className="flex flex-wrap gap-2">
            {data.competitors.map((c) => (
              <li
                key={c.id}
                className="flex items-center gap-2 rounded-full border border-line bg-surface py-1 pr-1 pl-1 text-sm"
              >
                <Picture url={c.profile_pic_url} className="size-7 rounded-full" />
                <span className="font-medium">@{c.username}</span>
                {c.is_self ? <Pill tone="accent">{t.add.you}</Pill> : null}
                {!c.scraped_at ? <Pill tone="wait">{t.add.waiting}</Pill> : null}
                {c.scrape_error ? <Pill tone="fail">{c.scrape_error}</Pill> : null}
                {!c.is_self ? (
                  <IconButton
                    label={t.add.remove(c.username)}
                    className="size-7"
                    onClick={() => setRemoving(c)}
                  >
                    <TrashIcon size={15} />
                  </IconButton>
                ) : (
                  <span className="w-1" />
                )}
              </li>
            ))}
          </ul>
        ) : null}
      </Card>
      <ConfirmDialog
        open={removing !== null}
        title={t.add.removeTitle}
        confirmLabel={messages.common.delete}
        pending={busy}
        onConfirm={remove}
        onCancel={() => setRemoving(null)}
      >
        <p>{removing ? t.add.removeBody(removing.username) : null}</p>
      </ConfirmDialog>
    </Section>
  );
}

function ProfileLabels({ competitor, taxonomy }: { competitor: Competitor; taxonomy: Taxonomy }) {
  const chips = taxonomy.profile
    .map((q) => ({ q, label: competitor.labels[q.key] }))
    .map(({ q, label }) => ({ key: q.key, name: q.label, text: labelText(q, label), sure: isConfident(label) }))
    .filter((c) => c.text);
  if (chips.length === 0) return null;
  return (
    <div className="mt-2 flex flex-wrap gap-1.5">
      {chips.map((c) => (
        <span
          key={c.key}
          title={c.sure ? c.name : `${c.name}. ${t.lowConfidence}`}
          className={`rounded-md bg-surface-2 px-2 py-0.5 text-xs ${c.sure ? "text-fg" : "text-muted italic"}`}
        >
          <span className="text-muted">{c.name}:</span> {c.text}
        </span>
      ))}
    </div>
  );
}

function CompareSection({ data }: { data: CompetitorsData }) {
  const rows = data.competitors.filter((c) => c.scraped_at && !c.scrape_error);
  const format = question(data.taxonomy, "post", "format");
  return (
    <Section title={t.compare.title} hint={t.compare.hint}>
      {rows.length === 0 ? (
        <Card className="p-6 text-sm text-muted">{t.compare.empty}</Card>
      ) : (
        <Card className="overflow-x-auto">
          <table className="w-full min-w-[46rem] text-sm">
            <thead>
              <tr className="border-b border-line text-left text-xs tracking-wide text-muted uppercase">
                <th className="px-4 py-3 font-semibold">{t.compare.profile}</th>
                <th className="px-3 py-3 text-right font-semibold">{t.compare.followers}</th>
                <th className="px-3 py-3 text-right font-semibold">{t.compare.engagement}</th>
                <th className="px-3 py-3 text-right font-semibold">{t.compare.perWeek}</th>
                <th className="px-3 py-3 text-right font-semibold">{t.compare.videos}</th>
                <th className="px-4 py-3 font-semibold">{t.compare.mainFormat}</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((c) => {
                const main = mainOption(c.posts, "format");
                return (
                  <tr
                    key={c.id}
                    className={`border-b border-line align-top last:border-0 ${c.is_self ? "bg-accent-soft/40" : ""}`}
                  >
                    <td className="px-4 py-3">
                      <div className="flex items-start gap-3">
                        <Picture url={c.profile_pic_url} className="size-10 rounded-full" />
                        <div className="min-w-0">
                          <p className="font-semibold">
                            @{c.username} {c.is_self ? <Pill tone="accent">{t.add.you}</Pill> : null}
                          </p>
                          {c.category ? <p className="text-xs text-muted">{c.category}</p> : null}
                          <ProfileLabels competitor={c} taxonomy={data.taxonomy} />
                        </div>
                      </div>
                    </td>
                    <td className="px-3 py-3 text-right tabular-nums">{compact(c.followers)}</td>
                    <td className="px-3 py-3 text-right font-semibold tabular-nums">
                      {percent(c.stats.median_engagement_rate, 2)}
                    </td>
                    <td className="px-3 py-3 text-right tabular-nums">
                      {c.stats.posts_per_week?.toLocaleString("pt-BR") ?? "—"}
                    </td>
                    <td className="px-3 py-3 text-right tabular-nums">{percent(c.stats.video_share, 0)}</td>
                    <td className="px-4 py-3">
                      {main ? format?.options.find((o) => o.key === main)?.label : "—"}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </Card>
      )}
    </Section>
  );
}

function NicheSection({ data }: { data: CompetitorsData }) {
  const groups = data.taxonomy.post
    .filter((q) => q.type === "choice")
    .map((q) => ({ q, rows: data.niche[q.key] ?? [] }))
    // A question where every post landed in the same option compares nothing.
    .filter(({ rows }) => rows.length > 1);
  return (
    <Section title={t.niche.title} hint={t.niche.hint}>
      {groups.length === 0 ? (
        <Card className="p-6 text-sm text-muted">{t.niche.empty}</Card>
      ) : (
        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
          {groups.map(({ q, rows }) => (
            <Card key={q.key} className="p-4">
              <h3 className="mb-3 font-semibold">{q.label}</h3>
              <BarList
                format={multiplier}
                items={rows.map((r) => ({
                  key: r.option,
                  label: q.options.find((o) => o.key === r.option)?.label ?? r.option,
                  value: r.median_performance,
                  sub: t.niche.posts(r.posts),
                }))}
              />
            </Card>
          ))}
        </div>
      )}
    </Section>
  );
}

function PostChips({ post, taxonomy }: { post: CompetitorPost; taxonomy: Taxonomy }) {
  const chips = ["format", "hook", "cta"]
    .map((key) => {
      const q = question(taxonomy, "post", key);
      const label = post.labels[key];
      return { key, text: labelText(q, label), sure: isConfident(label), name: q?.label ?? key };
    })
    .filter((c) => c.text);
  return (
    <div className="flex flex-wrap gap-1.5">
      {chips.map((c) => (
        <span
          key={c.key}
          title={c.sure ? c.name : `${c.name}. ${t.lowConfidence}`}
          className={`rounded-md bg-surface-2 px-2 py-0.5 text-xs ${c.sure ? "" : "text-muted italic"}`}
        >
          {c.text}
        </span>
      ))}
    </div>
  );
}

function TopPostsSection({ data }: { data: CompetitorsData }) {
  const ranked = topPosts(data.competitors, 12);
  return (
    <Section title={t.top.title} hint={t.top.hint}>
      {ranked.length === 0 ? (
        <Card className="p-6 text-sm text-muted">{t.top.empty}</Card>
      ) : (
        <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
          {ranked.map(({ post, competitor }) => (
            <Card key={post.id} className="flex gap-3 p-3">
              <Picture
                url={post.display_url}
                video={post.media_type?.toLowerCase() === "video"}
                className="size-24 rounded-lg"
              />
              <div className="min-w-0 flex-1 space-y-1.5">
                <div className="flex items-baseline justify-between gap-2">
                  <span className="truncate text-sm font-semibold">@{competitor.username}</span>
                  <span className="shrink-0 text-lg font-bold text-accent tabular-nums">
                    {multiplier(post.performance)}
                  </span>
                </div>
                <p className="text-xs text-muted">
                  {t.top.engagement(percent(post.engagement_rate, 2))} · {t.top.postedAt(relativeTime(post.posted_at))}
                </p>
                <PostChips post={post} taxonomy={data.taxonomy} />
                {post.caption ? <p className="line-clamp-2 text-xs">{post.caption}</p> : null}
                {post.url ? (
                  <a
                    href={post.url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="text-xs font-semibold text-accent hover:underline"
                  >
                    {t.top.open}
                  </a>
                ) : null}
              </div>
            </Card>
          ))}
        </div>
      )}
    </Section>
  );
}

export function CompetitorsView() {
  const { data, error, mutate } = useSWR(
    "competitors",
    () => unwrap(browserApi.GET("/api/v1/competitors")),
    {
      revalidateOnFocus: false,
      // While Apify collects or the model classifies, look again often.
      refreshInterval: (latest) =>
        latest?.running || (latest?.pending_classification ?? 0) > 0 ? 10_000 : 60_000,
    },
  );
  const refresh = () => void mutate();

  return (
    <div>
      <PageHeader title={t.title} />
      {error ? (
        <div className="mt-6">
          <Notice kind="error">{errorText(error)}</Notice>
        </div>
      ) : !data ? (
        <p className="mt-6 text-sm text-muted">{messages.common.loading}</p>
      ) : (
        <>
          <StatusBar data={data} onChange={refresh} />
          <ProfilesSection data={data} onChange={refresh} />
          <CompareSection data={data} />
          <NicheSection data={data} />
          <TopPostsSection data={data} />
          <ReportSection />
        </>
      )}
    </div>
  );
}
