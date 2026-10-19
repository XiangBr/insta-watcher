import {
  CAPTION_BUCKETS,
  HASHTAG_BUCKETS,
  MIN_GROUP,
  bestGroup,
  bucketStats,
  byFormat,
  engagementRate,
  groupStats,
  lift,
  peakHours,
  postsByTimeBlock,
  postsByWeekday,
  rankPosts,
  shareOf,
  type Advice,
  type InsightPost,
} from "@/lib/insights";
import { captionLine } from "@/lib/posts";
import { messages } from "@/lib/messages";

const t = messages.insights;

export type AdviceInput = {
  posts: readonly InsightPost[];
  followers: number | null;
  offset: number;
  postsPerWeek: number | null;
  activity: number[][] | null;
  online: Record<string, number> | null | undefined;
  reachByFollowType: Record<string, number> | null | undefined;
  keywords: readonly { keyword: string; count: number }[];
};

/**
 * Plain-language advice, each backed by a comparison in the data. A rule says nothing when
 * the groups it compares are too small (MIN_GROUP) or the difference is under 15 %.
 */
export function buildAdvice(input: AdviceInput): Advice[] {
  const { posts, followers, offset } = input;
  const out: Advice[] = [];
  const overall = groupStats("all", posts, followers).engagement;

  // Format: best against worst, both with enough posts.
  const formats = byFormat(posts, followers).filter(
    (g) => g.posts >= MIN_GROUP && g.engagement != null,
  );
  if (formats.length >= 2) {
    const sorted = [...formats].sort((a, b) => (b.engagement ?? 0) - (a.engagement ?? 0));
    const best = sorted[0];
    const worst = sorted[sorted.length - 1];
    const ratio = best && worst ? lift(best.engagement, worst.engagement) : null;
    if (best && worst && ratio) {
      const name = (key: string) => t.formats[key] ?? key;
      out.push({ id: "format", text: t.advice.format(name(best.key), name(worst.key), ratio) });
    }
  }

  const day = bestGroup(postsByWeekday(posts, offset, followers));
  const dayLift = day ? lift(day.engagement, overall) : null;
  if (day && dayLift) {
    const name = t.weekdaysLong[Number(day.key)] ?? day.key;
    out.push({ id: "weekday", text: t.advice.weekday(name, (dayLift - 1) * 100) });
  }

  const block = bestGroup(postsByTimeBlock(posts, offset, followers));
  const blockLift = block ? lift(block.engagement, overall) : null;
  if (block && blockLift) {
    out.push({ id: "block", text: t.advice.block(Number(block.key), (blockLift - 1) * 100) });
  }

  if (input.activity) {
    const peaks = peakHours(input.activity, 2);
    const total = input.activity.flat().reduce((a, b) => a + b, 0);
    if (peaks.length > 0 && total >= 20) {
      const hours = [...peaks].sort((a, b) => a - b).map((h) => `${h}h`);
      out.push({ id: "activity", text: t.advice.activity(hours.join(" e ")) });
    }
  }

  if (input.online) {
    const entries = Object.entries(input.online).filter(([, v]) => v > 0);
    if (entries.length > 0) {
      const [hour] = entries.reduce((best, e) => (e[1] > best[1] ? e : best));
      out.push({ id: "online", text: t.advice.online(Number(hour)) });
    }
  }

  const nonFollowers = shareOf(input.reachByFollowType, "NON_FOLLOWER");
  if (nonFollowers != null) {
    if (nonFollowers < 0.2) out.push({ id: "discovery", text: t.advice.discoveryLow(nonFollowers * 100) });
    else if (nonFollowers > 0.6) out.push({ id: "discovery", text: t.advice.discoveryHigh(nonFollowers * 100) });
  }

  for (const [id, buckets] of [
    ["caption", CAPTION_BUCKETS],
    ["hashtags", HASHTAG_BUCKETS],
  ] as const) {
    const stats = bucketStats(posts, buckets, followers);
    const best = bestGroup(stats);
    const ratio = best ? lift(best.engagement, overall) : null;
    const phrase = stats.find((s) => s.key === best?.key)?.phrase;
    if (ratio && phrase) out.push({ id, text: t.advice.bucket(phrase, (ratio - 1) * 100) });
  }

  const totals = posts.reduce(
    (acc, p) => {
      const m = p.metrics;
      if (m.total_interactions == null) return acc;
      acc.all += m.total_interactions;
      acc.useful += (m.saved ?? 0) + (m.shares ?? 0);
      return acc;
    },
    { all: 0, useful: 0 },
  );
  if (totals.all >= 50 && totals.useful / totals.all >= 0.25) {
    out.push({ id: "saves", text: t.advice.saves((totals.useful / totals.all) * 100) });
  }

  const questions = posts.reduce((sum, p) => sum + (p.metrics.comment_questions ?? 0), 0);
  if (questions >= 5) out.push({ id: "questions", text: t.advice.questions(questions) });

  const asked = input.keywords.slice(0, 3).map((k) => `“${k.keyword}”`);
  if (asked.length > 0) out.push({ id: "keywords", text: t.advice.keywords(asked.join(", ")) });

  const top = rankPosts(posts, "engagement", followers)[0];
  if (top && posts.length >= 5 && engagementRate(top, followers) != null) {
    // Hashtags are not what a person remembers a post by.
    const caption = (top.caption ?? "").replace(/#[\p{L}\p{N}_]+/gu, "").trim();
    const line = captionLine({ id: top.id, caption }, 60);
    if (line) out.push({ id: "repeat", text: t.advice.repeat(line) });
  }

  if (input.postsPerWeek != null && input.postsPerWeek < 2) {
    out.push({ id: "frequency", text: t.advice.frequency(input.postsPerWeek) });
  }
  return out;
}
