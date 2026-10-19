// Pure rules of the competitors page. The numbers come computed from the API; this only
// picks, sorts and names them.
import type { components } from "@/lib/api-types";

export type CompetitorsData = components["schemas"]["CompetitorsOut"];
export type Competitor = components["schemas"]["CompetitorOut"];
export type CompetitorPost = components["schemas"]["CompetitorPostOut"];
export type Taxonomy = components["schemas"]["Taxonomy"];
export type TaxonomyQuestion = components["schemas"]["TaxonomyQuestion"];
export type Label = components["schemas"]["Label"];

/** Below this confidence a label is shown faded: the model was split between options. */
export const LOW_CONFIDENCE = 0.5;

export function question(
  taxonomy: Taxonomy,
  group: "post" | "profile",
  key: string,
): TaxonomyQuestion | undefined {
  return taxonomy[group].find((q) => q.key === key);
}

/** How a label reads: the option name, the level name, or yes/no. null if unknown. */
export function labelText(q: TaxonomyQuestion | undefined, label: Label | undefined): string | null {
  if (!q || !label) return null;
  const { value } = label;
  if (q.type === "choice") {
    return q.options.find((o) => o.key === value)?.label ?? null;
  }
  if (typeof value !== "number") return null;
  if (q.type === "score") {
    const index = Math.min(Math.max(Math.round(value), 0), q.options.length - 1);
    return q.options[index]?.label ?? null;
  }
  return value >= 0.5 ? (q.options[0]?.label ?? null) : (q.options[1]?.label ?? null);
}

export function isConfident(label: Label | undefined): boolean {
  return label?.confidence == null || label.confidence >= LOW_CONFIDENCE;
}

/** 1.84 -> "1,8×". */
export function multiplier(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return "—";
  return `${value.toLocaleString("pt-BR", { maximumFractionDigits: 1, minimumFractionDigits: 1 })}×`;
}

export type RankedPost = { post: CompetitorPost; competitor: Competitor };

/** The posts that did best against their own profile, across competitors (never you). */
export function topPosts(competitors: readonly Competitor[], limit: number): RankedPost[] {
  return competitors
    .filter((c) => !c.is_self)
    .flatMap((competitor) =>
      competitor.posts
        .filter((p) => !p.is_pinned && p.performance != null)
        .map((post) => ({ post, competitor })),
    )
    .sort((a, b) => (b.post.performance ?? 0) - (a.post.performance ?? 0))
    .slice(0, limit);
}

/** Share of a profile's posts in each option of a post question, largest first. */
export function mixOf(
  posts: readonly CompetitorPost[],
  key: string,
): { option: string; share: number }[] {
  const counts = new Map<string, number>();
  let total = 0;
  for (const post of posts) {
    const value = post.labels[key]?.value;
    if (typeof value !== "string") continue;
    counts.set(value, (counts.get(value) ?? 0) + 1);
    total += 1;
  }
  return [...counts.entries()]
    .map(([option, n]) => ({ option, share: n / total }))
    .sort((a, b) => b.share - a.share);
}

/** Main option of a post question for one profile, if any post was classified. */
export function mainOption(posts: readonly CompetitorPost[], key: string): string | null {
  return mixOf(posts, key)[0]?.option ?? null;
}

/** Where a campaign for this keyword starts: the campaign form, already filled. */
export function campaignHref(keyword: string): string {
  const clean = keyword.trim().slice(0, 100);
  return clean ? `/campaigns/new?keyword=${encodeURIComponent(clean)}` : "/campaigns/new";
}

/** Competitors collected without an error. */
export function collectedCount(competitors: readonly Competitor[]): number {
  return competitors.filter((c) => !c.is_self && c.scraped_at && !c.scrape_error).length;
}
