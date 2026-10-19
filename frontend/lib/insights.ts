import type { components } from "@/lib/api-types";
import { normalizeForSearch } from "@/lib/search";

// Pure rules behind the insights page. Every number the page shows comes from here, so the
// page stays a layout and these stay testable.

export type InsightPost = components["schemas"]["InsightPost"];
export type InsightDay = components["schemas"]["InsightDayOut"];
export type ActivityHour = components["schemas"]["ActivityHour"];

export type Format = "REELS" | "CAROUSEL" | "PHOTO";
export const FORMATS: readonly Format[] = ["REELS", "CAROUSEL", "PHOTO"];

export type SortMetric =
  | "engagement"
  | "reach"
  | "views"
  | "interactions"
  | "likes"
  | "comments"
  | "saved"
  | "shares"
  | "follows"
  | "profile_visits";

export const SORT_METRICS: readonly SortMetric[] = [
  "engagement",
  "reach",
  "views",
  "interactions",
  "likes",
  "comments",
  "saved",
  "shares",
  "follows",
  "profile_visits",
];

const DAY_MS = 86_400_000;
const HOUR_MS = 3_600_000;
// Fewer posts than this in a group and a comparison is noise, so no advice comes from it.
export const MIN_GROUP = 3;

export function formatOf(post: InsightPost): Format {
  if (post.kind === "REELS") return "REELS";
  return post.media_type === "CAROUSEL_ALBUM" ? "CAROUSEL" : "PHOTO";
}

/** Likes, comments, saves and shares. Meta's total when it has one, else our sum. */
export function interactions(post: InsightPost): number {
  const m = post.metrics;
  if (m.total_interactions != null) return m.total_interactions;
  const likes = m.likes ?? post.like_count ?? 0;
  const comments = m.comments ?? post.comments_count ?? 0;
  return likes + comments + (m.saved ?? 0) + (m.shares ?? 0);
}

/**
 * Interactions per account reached: the fairest way to compare posts of different reach.
 * Without insights (no permission yet) it falls back to interactions per follower.
 */
export function engagementRate(post: InsightPost, followers?: number | null): number | null {
  const reach = post.metrics.reach;
  if (reach && reach > 0) return interactions(post) / reach;
  if (followers && followers > 0) return interactions(post) / followers;
  return null;
}

export function metricValue(
  post: InsightPost,
  metric: SortMetric,
  followers?: number | null,
): number | null {
  const m = post.metrics;
  switch (metric) {
    case "engagement":
      return engagementRate(post, followers);
    case "interactions":
      return interactions(post);
    case "likes":
      return m.likes ?? post.like_count ?? null;
    case "comments":
      return m.comments ?? post.comments_count ?? null;
    default:
      return m[metric] ?? null;
  }
}

// ---- filters -----------------------------------------------------------------------------

export type Range = { from: Date; to: Date };

export type PostFilters = {
  range: Range | null;
  formats: readonly Format[];
  search: string;
};

export function filterPosts(posts: readonly InsightPost[], f: PostFilters): InsightPost[] {
  const needle = normalizeForSearch(f.search.trim());
  return posts.filter((post) => {
    const at = new Date(post.posted_at).getTime();
    if (f.range && (at < f.range.from.getTime() || at >= f.range.to.getTime())) return false;
    if (f.formats.length > 0 && !f.formats.includes(formatOf(post))) return false;
    if (needle && !normalizeForSearch(post.caption ?? "").includes(needle)) return false;
    return true;
  });
}

/** The last `days` days up to now. null means everything. */
export function presetRange(days: number | null, now: Date = new Date()): Range | null {
  if (days == null) return null;
  return { from: new Date(now.getTime() - days * DAY_MS), to: now };
}

/** The range of the same length right before this one, to compare against. */
export function previousRange(range: Range): Range {
  const span = range.to.getTime() - range.from.getTime();
  return { from: new Date(range.from.getTime() - span), to: range.from };
}

// ---- statistics --------------------------------------------------------------------------

export function median(values: readonly number[]): number | null {
  if (values.length === 0) return null;
  const sorted = [...values].sort((a, b) => a - b);
  const mid = Math.floor(sorted.length / 2);
  const upper = sorted[mid] ?? 0;
  return sorted.length % 2 ? upper : ((sorted[mid - 1] ?? upper) + upper) / 2;
}

export function mean(values: readonly number[]): number | null {
  return values.length ? values.reduce((a, b) => a + b, 0) / values.length : null;
}

function present(values: readonly (number | null)[]): number[] {
  return values.filter((v): v is number => v != null && Number.isFinite(v));
}

/** Posts ranked by a metric, best first. Posts without the metric are left out. */
export function rankPosts(
  posts: readonly InsightPost[],
  metric: SortMetric,
  followers?: number | null,
): InsightPost[] {
  return posts
    .map((post) => ({ post, value: metricValue(post, metric, followers) }))
    .filter((x): x is { post: InsightPost; value: number } => x.value != null)
    .sort((a, b) => b.value - a.value)
    .map((x) => x.post);
}

export type GroupStats = {
  key: string;
  posts: number;
  /** Median engagement rate. Medians, because one viral post would skew an average. */
  engagement: number | null;
  reach: number | null;
  saved: number | null;
  shares: number | null;
  comments: number | null;
};

export function groupStats(
  key: string,
  posts: readonly InsightPost[],
  followers?: number | null,
): GroupStats {
  return {
    key,
    posts: posts.length,
    engagement: median(present(posts.map((p) => engagementRate(p, followers)))),
    reach: median(present(posts.map((p) => p.metrics.reach ?? null))),
    saved: median(present(posts.map((p) => p.metrics.saved ?? null))),
    shares: median(present(posts.map((p) => p.metrics.shares ?? null))),
    comments: median(present(posts.map((p) => metricValue(p, "comments")))),
  };
}

export function byFormat(posts: readonly InsightPost[], followers?: number | null): GroupStats[] {
  return FORMATS.map((format) =>
    groupStats(
      format,
      posts.filter((p) => formatOf(p) === format),
      followers,
    ),
  ).filter((g) => g.posts > 0);
}

// ---- time --------------------------------------------------------------------------------

/** Day of week (0 = Sunday) and hour in the account's time zone (fixed UTC offset). */
export function localParts(iso: string, offsetMinutes: number): { weekday: number; hour: number } {
  const shifted = new Date(new Date(iso).getTime() + offsetMinutes * 60_000);
  return { weekday: shifted.getUTCDay(), hour: shifted.getUTCHours() };
}

/** "2026-10-02" in the account's time zone. */
export function localDay(iso: string | Date, offsetMinutes: number): string {
  const time = typeof iso === "string" ? new Date(iso).getTime() : iso.getTime();
  return new Date(time + offsetMinutes * 60_000).toISOString().slice(0, 10);
}

export const TIME_BLOCKS = 8; // 3-hour blocks: 0-3, 3-6, ..., 21-24

export function postsByWeekday(
  posts: readonly InsightPost[],
  offset: number,
  followers?: number | null,
): GroupStats[] {
  return Array.from({ length: 7 }, (_, day) =>
    groupStats(
      String(day),
      posts.filter((p) => localParts(p.posted_at, offset).weekday === day),
      followers,
    ),
  );
}

export function postsByTimeBlock(
  posts: readonly InsightPost[],
  offset: number,
  followers?: number | null,
): GroupStats[] {
  return Array.from({ length: TIME_BLOCKS }, (_, block) =>
    groupStats(
      String(block * 3),
      posts.filter((p) => Math.floor(localParts(p.posted_at, offset).hour / 3) === block),
      followers,
    ),
  );
}

/** The group with the best median engagement among those with enough posts. */
export function bestGroup(groups: readonly GroupStats[]): GroupStats | null {
  const eligible = groups.filter((g) => g.posts >= MIN_GROUP && g.engagement != null);
  if (eligible.length < 2) return null;
  return eligible.reduce((best, g) => ((g.engagement ?? 0) > (best.engagement ?? 0) ? g : best));
}

/** Comments per weekday (rows, 0 = Sunday) and local hour (columns). */
export function activityGrid(hours: readonly ActivityHour[], offset: number): number[][] {
  const grid = Array.from({ length: 7 }, () => new Array<number>(24).fill(0));
  for (const { hour, count } of hours) {
    const { weekday, hour: h } = localParts(hour, offset);
    const row = grid[weekday];
    if (row) row[h] = (row[h] ?? 0) + count;
  }
  return grid;
}

/** The busiest hours of the grid, summed over the week. */
export function peakHours(grid: readonly number[][], top = 3): number[] {
  const totals = Array.from({ length: 24 }, (_, h) =>
    grid.reduce((sum, row) => sum + (row[h] ?? 0), 0),
  );
  return totals
    .map((total, hour) => ({ total, hour }))
    .filter((x) => x.total > 0)
    .sort((a, b) => b.total - a.total)
    .slice(0, top)
    .map((x) => x.hour);
}

// ---- account days ------------------------------------------------------------------------

export type DayPoint = { day: string; value: number };

export function daysInRange(days: readonly InsightDay[], range: Range | null, offset: number) {
  if (!range) return [...days];
  const from = localDay(range.from, offset);
  const to = localDay(range.to, offset);
  return days.filter((d) => d.day >= from && d.day <= to);
}

export function daySeries(days: readonly InsightDay[], metric: string): DayPoint[] {
  const out: DayPoint[] = [];
  for (const d of days) {
    const value = d.metrics[metric];
    if (value != null) out.push({ day: d.day, value });
  }
  return out;
}

export function sumMetric(days: readonly InsightDay[], metric: string): number | null {
  const values = present(days.map((d) => d.metrics[metric] ?? null));
  return values.length ? values.reduce((a, b) => a + b, 0) : null;
}

/**
 * Followers per day. Meta only tells today's count, so earlier days are rebuilt backwards
 * from each day's follows and unfollows, anchored on every count we did record.
 */
export function followerSeries(days: readonly InsightDay[]): DayPoint[] {
  const sorted = [...days].sort((a, b) => a.day.localeCompare(b.day));
  const out: DayPoint[] = [];
  let next: number | null = null;
  for (let i = sorted.length - 1; i >= 0; i--) {
    const day = sorted[i];
    if (!day) break;
    let value: number | null = day.followers_count ?? null;
    const later = sorted[i + 1];
    if (value == null && next != null && later) {
      const net = (later.metrics.follows ?? 0) - (later.metrics.unfollows ?? 0);
      value = later.metrics.follows == null ? null : next - net;
    }
    if (value == null) break;
    out.push({ day: day.day, value });
    next = value;
  }
  return out.reverse();
}

// ---- captions and comments ---------------------------------------------------------------

const HASHTAG = /#[\p{L}\p{N}_]+/gu;

export function hashtagsOf(caption: string | null | undefined): string[] {
  return [...new Set((caption ?? "").match(HASHTAG)?.map((t) => t.toLowerCase()) ?? [])];
}

export type TagStats = { tag: string; posts: number; reach: number | null; engagement: number | null };

export function hashtagStats(
  posts: readonly InsightPost[],
  followers?: number | null,
  minPosts = 2,
): TagStats[] {
  const groups = new Map<string, InsightPost[]>();
  for (const post of posts) {
    for (const tag of hashtagsOf(post.caption)) groups.set(tag, [...(groups.get(tag) ?? []), post]);
  }
  return [...groups.entries()]
    .filter(([, list]) => list.length >= minPosts)
    .map(([tag, list]) => {
      const g = groupStats(tag, list, followers);
      return { tag, posts: list.length, reach: g.reach, engagement: g.engagement };
    })
    .sort((a, b) => (b.engagement ?? 0) - (a.engagement ?? 0));
}

/** `label` names the group in a chart; `phrase` finishes "Posts ..." in the advice. */
export type Bucket = {
  key: string;
  label: string;
  phrase: string;
  test: (post: InsightPost) => boolean;
};

export const CAPTION_BUCKETS: readonly Bucket[] = [
  { key: "short", label: "Curta (até 80)", phrase: "com legenda curta", test: (p) => (p.caption ?? "").length <= 80 },
  {
    key: "medium",
    label: "Média (81 a 300)",
    phrase: "com legenda média",
    test: (p) => (p.caption ?? "").length > 80 && (p.caption ?? "").length <= 300,
  },
  { key: "long", label: "Longa (mais de 300)", phrase: "com legenda longa", test: (p) => (p.caption ?? "").length > 300 },
];

export const HASHTAG_BUCKETS: readonly Bucket[] = [
  { key: "0", label: "Nenhuma", phrase: "sem hashtags", test: (p) => hashtagsOf(p.caption).length === 0 },
  {
    key: "1-3",
    label: "1 a 3",
    phrase: "com 1 a 3 hashtags",
    test: (p) => hashtagsOf(p.caption).length >= 1 && hashtagsOf(p.caption).length <= 3,
  },
  {
    key: "4-10",
    label: "4 a 10",
    phrase: "com 4 a 10 hashtags",
    test: (p) => hashtagsOf(p.caption).length >= 4 && hashtagsOf(p.caption).length <= 10,
  },
  { key: "11+", label: "11 ou mais", phrase: "com 11 hashtags ou mais", test: (p) => hashtagsOf(p.caption).length > 10 },
];

export function bucketStats(
  posts: readonly InsightPost[],
  buckets: readonly Bucket[],
  followers?: number | null,
): (GroupStats & { label: string; phrase: string })[] {
  return buckets.map((b) => ({
    ...groupStats(b.key, posts.filter(b.test), followers),
    label: b.label,
    phrase: b.phrase,
  }));
}

/** Words people use most in the comments of these posts. */
export function topTerms(posts: readonly InsightPost[], limit = 20): { term: string; count: number }[] {
  const totals = new Map<string, number>();
  for (const post of posts) {
    for (const [term, count] of Object.entries(post.terms)) {
      totals.set(term, (totals.get(term) ?? 0) + count);
    }
  }
  return [...totals.entries()]
    .map(([term, count]) => ({ term, count }))
    .sort((a, b) => b.count - a.count || a.term.localeCompare(b.term))
    .slice(0, limit);
}

/** Posts per week in the range. */
export function postsPerWeek(count: number, range: Range | null, posts: readonly InsightPost[]): number | null {
  let span: number;
  if (range) span = range.to.getTime() - range.from.getTime();
  else if (posts.length >= 2) {
    const times = posts.map((p) => new Date(p.posted_at).getTime());
    span = Math.max(...times) - Math.min(...times);
  } else return null;
  const weeks = span / (7 * DAY_MS);
  return weeks >= 1 ? count / weeks : null;
}

/** Share of a breakdown (e.g. reach from non-followers), 0 to 1. */
export function shareOf(values: Record<string, number> | null | undefined, key: string): number | null {
  if (!values) return null;
  const total = Object.values(values).reduce((a, b) => a + b, 0);
  return total > 0 ? (values[key] ?? 0) / total : null;
}

/** Breakdown entries sorted by value, the biggest first. */
export function sortedEntries(values: Record<string, number> | null | undefined, limit?: number) {
  const entries = Object.entries(values ?? {})
    .map(([key, value]) => ({ key, value }))
    .sort((a, b) => b.value - a.value);
  return limit ? entries.slice(0, limit) : entries;
}

export function hoursSince(iso: string | null | undefined, now: Date = new Date()): number | null {
  if (!iso) return null;
  return (now.getTime() - new Date(iso).getTime()) / HOUR_MS;
}

// ---- advice ------------------------------------------------------------------------------

export type Advice = { id: string; text: string };

/** How much better a is than b, as a ratio. null when it is not a real difference. */
export function lift(a: number | null, b: number | null, threshold = 0.15): number | null {
  if (a == null || b == null || b <= 0) return null;
  const ratio = a / b;
  return ratio - 1 >= threshold ? ratio : null;
}
