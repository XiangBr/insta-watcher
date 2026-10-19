"use client";

import { useState, type ReactNode } from "react";
import { BarList, Columns, Heatmap, SplitBar } from "@/components/charts";
import { PostThumb } from "@/components/post-thumb";
import { Button, Card, Pill, selectClass } from "@/components/ui";
import type { components } from "@/lib/api-types";
import { dateTime } from "@/lib/format";
import {
  CAPTION_BUCKETS,
  HASHTAG_BUCKETS,
  MIN_GROUP,
  SORT_METRICS,
  bestGroup,
  bucketStats,
  byFormat,
  formatOf,
  hashtagStats,
  metricValue,
  postsByTimeBlock,
  postsByWeekday,
  rankPosts,
  sortedEntries,
  topTerms,
  type GroupStats,
  type InsightPost,
  type SortMetric,
} from "@/lib/insights";
import { compact, countryName, formatMetric, percent, seconds } from "@/lib/insights-format";
import { messages } from "@/lib/messages";
import { captionLine } from "@/lib/posts";

const t = messages.insights;
const s = t.sections;
type Audience = components["schemas"]["InsightsAudience"];
type Activity = components["schemas"]["InsightsActivityOut"];

export function Section({
  title,
  hint,
  actions,
  children,
}: {
  title: string;
  hint?: string;
  actions?: ReactNode;
  children: ReactNode;
}) {
  return (
    <section className="mt-10">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h2 className="text-lg font-bold">{title}</h2>
          {hint ? <p className="mt-0.5 text-sm text-muted">{hint}</p> : null}
        </div>
        {actions}
      </div>
      <div className="mt-3">{children}</div>
    </section>
  );
}

function CardTitle({ children }: { children: ReactNode }) {
  return <h3 className="mb-3 text-sm font-semibold">{children}</h3>;
}

// ---- posts -------------------------------------------------------------------------------

const ROW_METRICS: readonly SortMetric[] = ["reach", "interactions", "saved", "shares"];

/** One ranked post: position, picture, caption, the ranking value and a few others. */
function PostRow({
  post,
  metric,
  followers,
  rank,
}: {
  post: InsightPost;
  metric: SortMetric;
  followers: number | null;
  rank: number;
}) {
  const caption = captionLine({ id: post.id, caption: post.caption }, 80);
  const others = ROW_METRICS.filter((m) => m !== metric)
    .map((m) => `${t.metrics[m]} ${formatMetric(m, metricValue(post, m))}`)
    .join(" · ");
  const body = (
    <>
      <span className="w-6 shrink-0 text-right text-xs font-bold text-muted tabular-nums">{rank}</span>
      <PostThumb post={post} className="w-12 rounded-md" />
      <span className="min-w-0 flex-1">
        <span className="block truncate text-sm font-medium">
          {caption || t.formatOne[formatOf(post)]}
        </span>
        <span className="block truncate text-xs text-muted">
          {t.formatOne[formatOf(post)]} · {dateTime(post.posted_at)}
        </span>
        <span className="block truncate text-xs text-muted">{others}</span>
      </span>
      <span className="shrink-0 text-right">
        <span className="block font-bold tabular-nums">
          {formatMetric(metric, metricValue(post, metric, followers))}
        </span>
        <span className="block text-xs text-muted">{t.metrics[metric]}</span>
      </span>
    </>
  );
  const row = "flex items-center gap-3 px-4 py-2.5";
  return (
    <li>
      {post.permalink?.startsWith("https://") ? (
        <a
          href={post.permalink}
          target="_blank"
          rel="noreferrer"
          title={s.openOnInstagram}
          className={`${row} transition-colors hover:bg-surface-2`}
        >
          {body}
        </a>
      ) : (
        <div className={row}>{body}</div>
      )}
    </li>
  );
}

const TABLE_METRICS: readonly SortMetric[] = [
  "engagement",
  "reach",
  "views",
  "likes",
  "comments",
  "saved",
  "shares",
  "follows",
];

export function PostsSection({
  posts,
  followers,
}: {
  posts: readonly InsightPost[];
  followers: number | null;
}) {
  const [metric, setMetric] = useState<SortMetric>("engagement");
  const [showAll, setShowAll] = useState(false);
  const ranked = rankPosts(posts, metric, followers);
  const top = ranked.slice(0, 5);
  // The bottom list never repeats a post that is already in the top list.
  const bottom = ranked.length > 5 ? ranked.slice(-Math.min(5, ranked.length - 5)).reverse() : [];
  const unranked = posts.filter((p) => !ranked.includes(p));
  const table = [...ranked, ...unranked];
  const visible = showAll ? table : table.slice(0, 15);

  return (
    <Section
      title={s.posts}
      actions={
        <label className="flex items-center gap-2 text-sm">
          <span className="text-muted">{s.rankBy}</span>
          <select
            value={metric}
            onChange={(e) => setMetric(e.target.value as SortMetric)}
            className={selectClass}
          >
            {SORT_METRICS.map((m) => (
              <option key={m} value={m}>
                {t.metrics[m]}
              </option>
            ))}
          </select>
        </label>
      }
    >
      {posts.length === 0 ? (
        <p className="text-sm text-muted">{s.noPosts}</p>
      ) : (
        <div className="space-y-4">
          <div className="grid gap-4 lg:grid-cols-2">
            {[
              { title: s.top, list: top, from: 1 },
              { title: s.bottom, list: bottom, from: 0 },
            ].map(({ title, list, from }) =>
              list.length > 0 ? (
                <Card key={title} className="overflow-hidden">
                  <h3 className="px-4 pt-4 pb-1 text-sm font-semibold">{title}</h3>
                  <ul className="divide-y divide-line">
                    {list.map((post, i) => (
                      <PostRow
                        key={post.id}
                        post={post}
                        metric={metric}
                        followers={followers}
                        rank={from ? from + i : ranked.length - i}
                      />
                    ))}
                  </ul>
                </Card>
              ) : null,
            )}
          </div>

          <Card className="overflow-hidden">
            <h3 className="px-5 pt-4 text-sm font-semibold">{s.allPosts}</h3>
            <div className="mt-2 overflow-x-auto">
              <table className="w-full min-w-[52rem] text-sm">
                <thead>
                  <tr className="border-b border-line text-left text-xs text-muted">
                    <th className="px-5 py-2 font-medium">{t.columns.post}</th>
                    <th className="px-2 py-2 font-medium">{t.columns.date}</th>
                    {TABLE_METRICS.map((m) => (
                      <th key={m} className="px-2 py-2 text-right font-medium">
                        <button
                          type="button"
                          onClick={() => setMetric(m)}
                          aria-pressed={metric === m}
                          className={`hover:text-fg ${metric === m ? "font-bold text-fg" : ""}`}
                        >
                          {t.metrics[m]}
                          {metric === m ? " ↓" : ""}
                        </button>
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody className="divide-y divide-line">
                  {visible.map((post) => (
                    <tr key={post.id} className="hover:bg-surface-2">
                      <td className="px-5 py-2">
                        <div className="flex max-w-72 items-center gap-3">
                          <PostThumb post={post} className="w-10 rounded-md" />
                          <div className="min-w-0">
                            {post.permalink?.startsWith("https://") ? (
                              <a
                                href={post.permalink}
                                target="_blank"
                                rel="noreferrer"
                                className="block truncate hover:underline"
                              >
                                {captionLine({ id: post.id, caption: post.caption }, 60) ||
                                  t.formatOne[formatOf(post)]}
                              </a>
                            ) : (
                              <span className="block truncate">
                                {captionLine({ id: post.id, caption: post.caption }, 60)}
                              </span>
                            )}
                            <span className="text-xs text-muted">
                              {t.formatOne[formatOf(post)]}
                              {post.insights_error ? ` · ${s.noInsights}` : ""}
                            </span>
                          </div>
                        </div>
                      </td>
                      <td className="px-2 py-2 whitespace-nowrap text-muted">
                        {dateTime(post.posted_at)}
                      </td>
                      {TABLE_METRICS.map((m) => (
                        <td
                          key={m}
                          className={`px-2 py-2 text-right tabular-nums ${metric === m ? "font-semibold" : ""}`}
                        >
                          {formatMetric(m, metricValue(post, m, followers))}
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {table.length > 15 ? (
              <div className="border-t border-line px-5 py-3">
                <Button variant="ghost" onClick={() => setShowAll(!showAll)}>
                  {showAll ? s.showLess : s.showAll(table.length)}
                </Button>
              </div>
            ) : null}
          </Card>
        </div>
      )}
    </Section>
  );
}

// ---- formats -----------------------------------------------------------------------------

export function FormatsSection({
  posts,
  followers,
}: {
  posts: readonly InsightPost[];
  followers: number | null;
}) {
  const groups = byFormat(posts, followers);
  if (groups.length === 0) return null;
  const reels = posts.filter((p) => formatOf(p) === "REELS");
  const watch = reels
    .map((p) => p.metrics.ig_reels_avg_watch_time)
    .filter((v): v is number => v != null);
  const rows: { key: keyof GroupStats; label: string; format: (v: number | null) => string }[] = [
    { key: "engagement", label: t.metrics.engagement ?? "", format: (v) => percent(v) },
    { key: "reach", label: t.metrics.reach ?? "", format: compact },
    { key: "comments", label: t.metrics.comments ?? "", format: compact },
    { key: "saved", label: t.metrics.saved ?? "", format: compact },
    { key: "shares", label: t.metrics.shares ?? "", format: compact },
  ];
  const best = (key: keyof GroupStats) => {
    const eligible = groups.filter((g) => g.posts >= MIN_GROUP && g[key] != null);
    if (eligible.length < 2) return null;
    return eligible.reduce((a, b) => ((b[key] as number) > (a[key] as number) ? b : a)).key;
  };
  return (
    <Section title={s.formats} hint={s.formatsHint}>
      <Card className="overflow-x-auto">
        <table className="w-full min-w-[28rem] text-sm">
          <thead>
            <tr className="border-b border-line text-left text-xs text-muted">
              <th className="px-5 py-2 font-medium" />
              {groups.map((g) => (
                <th key={g.key} className="px-3 py-2 text-right font-semibold text-fg">
                  {t.formats[g.key]}
                  <span className="block text-xs font-normal text-muted">{s.groupPosts(g.posts)}</span>
                </th>
              ))}
            </tr>
          </thead>
          <tbody className="divide-y divide-line">
            {rows.map((row) => {
              const winner = best(row.key);
              return (
                <tr key={row.key}>
                  <th className="px-5 py-2.5 text-left font-normal text-muted">{row.label}</th>
                  {groups.map((g) => (
                    <td key={g.key} className="px-3 py-2.5 text-right tabular-nums">
                      {winner === g.key ? (
                        <Pill tone="accent">{row.format(g[row.key] as number | null)}</Pill>
                      ) : (
                        row.format(g[row.key] as number | null)
                      )}
                    </td>
                  ))}
                </tr>
              );
            })}
            {watch.length > 0 ? (
              <tr>
                <th className="px-5 py-2.5 text-left font-normal text-muted">{s.watchTime}</th>
                {groups.map((g) => (
                  <td key={g.key} className="px-3 py-2.5 text-right tabular-nums">
                    {g.key === "REELS"
                      ? seconds(watch.reduce((a, b) => a + b, 0) / watch.length)
                      : "—"}
                  </td>
                ))}
              </tr>
            ) : null}
          </tbody>
        </table>
      </Card>
    </Section>
  );
}

// ---- timing ------------------------------------------------------------------------------

export function TimingSection({
  posts,
  followers,
  offset,
  grid,
  online,
}: {
  posts: readonly InsightPost[];
  followers: number | null;
  offset: number;
  grid: number[][] | null;
  online: Record<string, number> | null;
}) {
  const weekdays = postsByWeekday(posts, offset, followers);
  const blocks = postsByTimeBlock(posts, offset, followers);
  const bestDay = bestGroup(weekdays)?.key;
  const bestBlock = bestGroup(blocks)?.key;
  const onlineItems = online
    ? Array.from({ length: 24 }, (_, h) => ({ label: `${h}h`, value: online[String(h)] ?? 0 }))
    : [];
  const onlineMax = Math.max(0, ...onlineItems.map((i) => i.value));
  return (
    <Section title={s.timing}>
      <div className="grid gap-4 lg:grid-cols-2">
        <Card className="p-5">
          <CardTitle>{s.timingWeekday}</CardTitle>
          <Columns
            label={s.timingWeekday}
            items={weekdays.map((g, i) => ({
              label: t.weekdays[i] ?? g.key,
              value: (g.engagement ?? 0) * 100,
              hint: s.groupPosts(g.posts),
            }))}
            format={(v) => `${v.toLocaleString("pt-BR", { maximumFractionDigits: 1 })}%`}
            strong={(i) => String(i) === bestDay}
            faint={(i) => (weekdays[i]?.posts ?? 0) < MIN_GROUP}
          />
          <p className="mt-2 text-xs text-muted">{s.fewPosts(MIN_GROUP)}</p>
        </Card>
        <Card className="p-5">
          <CardTitle>{s.timingBlock}</CardTitle>
          <Columns
            label={s.timingBlock}
            items={blocks.map((g) => ({
              label: `${g.key}h`,
              value: (g.engagement ?? 0) * 100,
              hint: s.groupPosts(g.posts),
            }))}
            format={(v) => `${v.toLocaleString("pt-BR", { maximumFractionDigits: 1 })}%`}
            strong={(i) => String(i * 3) === bestBlock}
            faint={(i) => (blocks[i]?.posts ?? 0) < MIN_GROUP}
          />
          <p className="mt-2 text-xs text-muted">{s.fewPosts(MIN_GROUP)}</p>
        </Card>
        <Card className="p-5 lg:col-span-2">
          <CardTitle>{s.activity}</CardTitle>
          <p className="-mt-2 text-xs text-muted">{s.activityHint}</p>
          {grid ? (
            <Heatmap
              grid={grid}
              rows={t.weekdays}
              label={s.activity}
              format={(v) => `${compact(v)} comentário${v === 1 ? "" : "s"}`}
            />
          ) : (
            <p className="text-sm text-muted">{messages.common.loading}</p>
          )}
        </Card>
        {onlineItems.length > 0 ? (
          <Card className="p-5 lg:col-span-2">
            <CardTitle>{s.online}</CardTitle>
            <Columns
              label={s.online}
              items={onlineItems}
              format={compact}
              tick={3}
              strong={(i) => (onlineItems[i]?.value ?? 0) >= onlineMax * 0.9}
            />
            <p className="mt-2 text-xs text-muted">{s.onlineHint}</p>
          </Card>
        ) : null}
      </div>
    </Section>
  );
}

// ---- audience ----------------------------------------------------------------------------

const AGE_ORDER = ["13-17", "18-24", "25-34", "35-44", "45-54", "55-64", "65+"];

function shareItems(
  values: Record<string, number> | null | undefined,
  label: (key: string) => string,
  limit?: number,
  order?: readonly string[],
) {
  const total = Object.values(values ?? {}).reduce((a, b) => a + b, 0);
  let entries = sortedEntries(values, limit);
  if (order) entries = [...entries].sort((a, b) => order.indexOf(a.key) - order.indexOf(b.key));
  return entries.map((e) => ({ key: e.key, label: label(e.key), value: total ? e.value / total : 0 }));
}

export function AudienceSection({
  audience,
  activity,
  posts,
}: {
  audience: Audience | null | undefined;
  activity: Activity | null;
  posts: readonly InsightPost[];
}) {
  const [who, setWho] = useState<"followers" | "engaged">("followers");
  const groups = audience?.[who] ?? null;
  const pct = (v: number) => percent(v, 0);
  const reach = audience?.reach_by_follow_type;
  const terms = topTerms(posts, 15);
  return (
    <Section
      title={s.audience}
      actions={
        <div className="flex gap-1 rounded-lg bg-surface-2 p-1 text-sm" role="group">
          {(["followers", "engaged"] as const).map((key) => (
            <button
              key={key}
              type="button"
              aria-pressed={who === key}
              onClick={() => setWho(key)}
              className={`rounded-md px-3 py-1.5 font-medium ${
                who === key ? "bg-surface text-fg shadow-card" : "text-muted hover:text-fg"
              }`}
            >
              {key === "followers" ? s.audienceFollowers : s.audienceEngaged}
            </button>
          ))}
        </div>
      }
    >
      <div className="grid gap-4 lg:grid-cols-2">
        {groups ? (
          <>
            <Card className="p-5">
              <CardTitle>{s.age}</CardTitle>
              <BarList items={shareItems(groups.age, (k) => k, undefined, AGE_ORDER)} format={pct} />
            </Card>
            <Card className="p-5">
              <CardTitle>{s.gender}</CardTitle>
              <BarList items={shareItems(groups.gender, (k) => s.genders[k] ?? k)} format={pct} />
            </Card>
            <Card className="p-5">
              <CardTitle>{s.cities}</CardTitle>
              <BarList items={shareItems(groups.city, (k) => k, 8)} format={pct} />
            </Card>
            <Card className="p-5">
              <CardTitle>{s.countries}</CardTitle>
              <BarList items={shareItems(groups.country, countryName, 8)} format={pct} />
            </Card>
          </>
        ) : (
          <p className="text-sm text-muted lg:col-span-2">{s.audienceEmpty}</p>
        )}

        {reach && Object.keys(reach).length > 0 ? (
          <Card className="p-5">
            <CardTitle>{s.discovery}</CardTitle>
            <SplitBar
              left={{ label: s.discoveryFollowers, value: reach.FOLLOWER ?? 0 }}
              right={{ label: s.discoveryNonFollowers, value: reach.NON_FOLLOWER ?? 0 }}
            />
            <p className="mt-3 text-xs text-muted">{s.discoveryHint(audience?.window_days ?? 28)}</p>
            {audience?.reach_by_product ? (
              <div className="mt-5">
                <CardTitle>{s.byProduct}</CardTitle>
                <BarList
                  items={sortedEntries(audience.reach_by_product).map((e) => ({
                    key: e.key,
                    label: s.products[e.key] ?? e.key,
                    value: e.value,
                  }))}
                  format={compact}
                />
              </div>
            ) : null}
          </Card>
        ) : null}

        <Card className="p-5">
          <CardTitle>{s.commenters}</CardTitle>
          {activity ? (
            <>
              <p className="text-sm">{s.commentersTotals(activity.total_comments, activity.unique_commenters)}</p>
              {activity.unique_commenters > 0 ? (
                <p className="mt-1 text-sm text-muted">
                  {s.returning(
                    activity.returning_commenters,
                    (activity.returning_commenters / activity.unique_commenters) * 100,
                  )}
                </p>
              ) : null}
              {activity.top_commenters.length > 0 ? (
                <>
                  <h4 className="mt-4 mb-2 text-xs font-semibold tracking-wide text-muted uppercase">
                    {s.topCommenters}
                  </h4>
                  <ol className="space-y-1.5 text-sm">
                    {activity.top_commenters.slice(0, 10).map((c) => (
                      <li key={c.username} className="flex justify-between gap-3">
                        <a
                          href={`https://www.instagram.com/${encodeURIComponent(c.username)}/`}
                          target="_blank"
                          rel="noreferrer"
                          className="truncate font-medium hover:underline"
                        >
                          @{c.username}
                        </a>
                        <span className="shrink-0 text-muted">{s.commenterLine(c.comments, c.posts)}</span>
                      </li>
                    ))}
                  </ol>
                </>
              ) : null}
              <p className="mt-4 text-xs text-muted">{s.commentersPrivacy}</p>
            </>
          ) : (
            <p className="text-sm text-muted">{messages.common.loading}</p>
          )}
        </Card>

        <Card className="p-5">
          <CardTitle>{s.terms}</CardTitle>
          {terms.length > 0 ? (
            <ul className="flex flex-wrap gap-2">
              {terms.map(({ term, count }) => (
                <li key={term} className="rounded-full bg-surface-2 px-3 py-1 text-sm">
                  {term} <span className="text-muted tabular-nums">{count}</span>
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-sm text-muted">{s.termsEmpty}</p>
          )}
          <h4 className="mt-5 mb-2 text-sm font-semibold">{s.keywords}</h4>
          {activity && activity.keywords.length > 0 ? (
            <BarList
              items={activity.keywords.map((k) => ({ key: k.keyword, label: k.keyword, value: k.count }))}
              format={compact}
            />
          ) : (
            <p className="text-sm text-muted">{s.keywordsEmpty}</p>
          )}
        </Card>
      </div>
    </Section>
  );
}

// ---- content -----------------------------------------------------------------------------

export function ContentSection({
  posts,
  followers,
}: {
  posts: readonly InsightPost[];
  followers: number | null;
}) {
  if (posts.length === 0) return null;
  const toItems = (groups: ReturnType<typeof bucketStats>) =>
    groups
      .filter((g) => g.posts > 0)
      .map((g) => ({ key: g.key, label: g.label, value: g.engagement ?? 0, sub: s.groupPosts(g.posts) }));
  const tags = hashtagStats(posts, followers).slice(0, 12);
  return (
    <Section title={s.content}>
      <div className="grid gap-4 lg:grid-cols-3">
        <Card className="p-5">
          <CardTitle>{s.captionLength}</CardTitle>
          <BarList items={toItems(bucketStats(posts, CAPTION_BUCKETS, followers))} format={(v) => percent(v)} />
        </Card>
        <Card className="p-5">
          <CardTitle>{s.hashtagCount}</CardTitle>
          <BarList items={toItems(bucketStats(posts, HASHTAG_BUCKETS, followers))} format={(v) => percent(v)} />
        </Card>
        <Card className="p-5">
          <CardTitle>{s.topHashtags}</CardTitle>
          {tags.length > 0 ? (
            <BarList
              items={tags.map((tag) => ({
                key: tag.tag,
                label: tag.tag,
                value: tag.engagement ?? 0,
                sub: `${s.groupPosts(tag.posts)} · ${t.metrics.reach}: ${compact(tag.reach)}`,
              }))}
              format={(v) => percent(v)}
            />
          ) : (
            <p className="text-sm text-muted">{s.hashtagsEmpty}</p>
          )}
        </Card>
      </div>
    </Section>
  );
}
