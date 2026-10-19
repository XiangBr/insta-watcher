import { describe, expect, it } from "vitest";
import {
  activityGrid,
  bestGroup,
  byFormat,
  engagementRate,
  filterPosts,
  followerSeries,
  formatOf,
  groupStats,
  hashtagStats,
  hashtagsOf,
  interactions,
  lift,
  localDay,
  localParts,
  median,
  peakHours,
  postsByWeekday,
  postsPerWeek,
  presetRange,
  previousRange,
  rankPosts,
  shareOf,
  topTerms,
  type InsightDay,
  type InsightPost,
} from "@/lib/insights";
import { buildAdvice } from "@/lib/insights-advice";
import { changePct, compact, formatMetric, percent, seconds } from "@/lib/insights-format";

let seq = 0;
function post(overrides: Partial<InsightPost> = {}): InsightPost {
  seq += 1;
  return {
    id: `p${seq}`,
    posted_at: "2026-10-01T15:00:00Z",
    media_type: "IMAGE",
    kind: "FEED",
    caption: null,
    permalink: null,
    thumbnail_url: null,
    media_url: null,
    like_count: 10,
    comments_count: 2,
    metrics: {},
    terms: {},
    insights_error: null,
    ...overrides,
  };
}

function day(d: string, metrics: Record<string, number>, followers: number | null = null): InsightDay {
  return { day: d, metrics, followers_count: followers, complete: true };
}

const BRT = -180;

describe("engagement", () => {
  it("uses Meta's total and the reach when there are insights", () => {
    const p = post({ metrics: { total_interactions: 50, reach: 1000 } });
    expect(interactions(p)).toBe(50);
    expect(engagementRate(p)).toBe(0.05);
  });

  it("adds likes, comments, saves and shares without a total", () => {
    const p = post({ metrics: { likes: 10, comments: 2, saved: 3, shares: 1, reach: 160 } });
    expect(interactions(p)).toBe(16);
    expect(engagementRate(p)).toBe(0.1);
  });

  it("falls back to followers without insights, and to nothing without both", () => {
    const p = post({ like_count: 18, comments_count: 2 });
    expect(engagementRate(p, 400)).toBe(0.05);
    expect(engagementRate(p)).toBeNull();
  });
});

describe("formats and filters", () => {
  it("names the format", () => {
    expect(formatOf(post({ kind: "REELS", media_type: "VIDEO" }))).toBe("REELS");
    expect(formatOf(post({ media_type: "CAROUSEL_ALBUM" }))).toBe("CAROUSEL");
    expect(formatOf(post())).toBe("PHOTO");
  });

  it("filters by range, format and caption, ignoring accents and case", () => {
    const posts = [
      post({ posted_at: "2026-09-01T00:00:00Z", caption: "Receita de PÃO" }),
      post({ posted_at: "2026-10-05T00:00:00Z", caption: "pão caseiro", kind: "REELS", media_type: "VIDEO" }),
      post({ posted_at: "2026-10-06T00:00:00Z", caption: "bolo" }),
    ];
    const range = { from: new Date("2026-10-01T00:00:00Z"), to: new Date("2026-10-10T00:00:00Z") };
    expect(filterPosts(posts, { range, formats: [], search: "" })).toHaveLength(2);
    expect(filterPosts(posts, { range: null, formats: [], search: "pao" })).toHaveLength(2);
    expect(filterPosts(posts, { range, formats: ["REELS"], search: "" })).toHaveLength(1);
  });

  it("builds the range before, of the same length", () => {
    const range = presetRange(7, new Date("2026-10-08T00:00:00Z"));
    expect(range).not.toBeNull();
    const before = previousRange(range!);
    expect(before.to).toEqual(range!.from);
    expect(before.from.toISOString()).toBe("2026-09-24T00:00:00.000Z");
    expect(presetRange(null)).toBeNull();
  });
});

describe("statistics", () => {
  it("medians", () => {
    expect(median([])).toBeNull();
    expect(median([3, 1, 2])).toBe(2);
    expect(median([4, 1, 2, 3])).toBe(2.5);
  });

  it("ranks best first and leaves out posts without the metric", () => {
    const a = post({ metrics: { reach: 100 } });
    const b = post({ metrics: { reach: 300 } });
    const c = post();
    expect(rankPosts([a, b, c], "reach").map((p) => p.id)).toEqual([b.id, a.id]);
  });

  it("groups by format with medians, so one viral post does not dominate", () => {
    const reels = [10, 12, 500].map((r) =>
      post({ kind: "REELS", media_type: "VIDEO", metrics: { reach: r * 10, total_interactions: r } }),
    );
    const stats = byFormat(reels);
    expect(stats).toHaveLength(1);
    expect(stats[0]?.reach).toBe(120);
  });

  it("needs enough posts in two groups to pick a best one", () => {
    const g = (key: string, posts: number, engagement: number) => ({
      ...groupStats(key, []),
      posts,
      engagement,
    });
    expect(bestGroup([g("a", 5, 0.1), g("b", 2, 0.9)])).toBeNull();
    expect(bestGroup([g("a", 5, 0.1), g("b", 3, 0.2)])?.key).toBe("b");
  });

  it("only counts a lift of 15% or more", () => {
    expect(lift(1.1, 1)).toBeNull();
    expect(lift(1.5, 1)).toBe(1.5);
    expect(lift(1, 0)).toBeNull();
  });

  it("shares of a breakdown", () => {
    expect(shareOf({ FOLLOWER: 30, NON_FOLLOWER: 70 }, "NON_FOLLOWER")).toBe(0.7);
    expect(shareOf({}, "x")).toBeNull();
  });
});

describe("time", () => {
  it("moves to the account's offset before taking the day and hour", () => {
    // 01:30 UTC on Friday the 2nd is 22:30 on Thursday the 1st in Brasília.
    expect(localParts("2026-10-02T01:30:00Z", BRT)).toEqual({ weekday: 4, hour: 22 });
    expect(localDay("2026-10-02T01:30:00Z", BRT)).toBe("2026-10-01");
  });

  it("groups posts by local weekday", () => {
    const thursdayNight = post({ posted_at: "2026-10-02T01:30:00Z" });
    const groups = postsByWeekday([thursdayNight], BRT);
    expect(groups[4]?.posts).toBe(1);
    expect(groups[5]?.posts).toBe(0);
  });

  it("builds the comment heatmap and finds the peaks", () => {
    const grid = activityGrid(
      [
        { hour: "2026-10-01T22:00:00Z", count: 5 }, // 19h Thursday
        { hour: "2026-10-02T22:00:00Z", count: 4 }, // 19h Friday
        { hour: "2026-10-01T12:00:00Z", count: 2 }, // 9h Thursday
      ],
      BRT,
    );
    expect(grid[4]?.[19]).toBe(5);
    expect(grid[5]?.[19]).toBe(4);
    expect(peakHours(grid, 2)).toEqual([19, 9]);
  });

  it("posts per week need at least a week of span", () => {
    const range = { from: new Date("2026-09-01T00:00:00Z"), to: new Date("2026-09-15T00:00:00Z") };
    expect(postsPerWeek(6, range, [])).toBe(3);
    expect(postsPerWeek(1, null, [post()])).toBeNull();
  });
});

describe("followers", () => {
  it("rebuilds earlier days from follows and unfollows", () => {
    const series = followerSeries([
      day("2026-10-01", { follows: 5, unfollows: 1 }),
      day("2026-10-02", { follows: 10, unfollows: 2 }),
      day("2026-10-03", { follows: 3, unfollows: 0 }, 1000),
    ]);
    expect(series).toEqual([
      { day: "2026-10-01", value: 989 },
      { day: "2026-10-02", value: 997 },
      { day: "2026-10-03", value: 1000 },
    ]);
  });

  it("stops where the follows are unknown", () => {
    const series = followerSeries([day("2026-10-01", {}), day("2026-10-02", {}, 500)]);
    expect(series).toEqual([{ day: "2026-10-02", value: 500 }]);
  });
});

describe("captions and comments", () => {
  it("finds hashtags once per post, in lower case", () => {
    expect(hashtagsOf("Bolo #Receita #receita #doce_caseiro")).toEqual(["#receita", "#doce_caseiro"]);
    expect(hashtagsOf(null)).toEqual([]);
  });

  it("keeps hashtags used in at least two posts", () => {
    const posts = [
      post({ caption: "#bolo", metrics: { reach: 100, total_interactions: 10 } }),
      post({ caption: "#bolo #pao", metrics: { reach: 100, total_interactions: 20 } }),
    ];
    const stats = hashtagStats(posts);
    expect(stats.map((s) => s.tag)).toEqual(["#bolo"]);
    expect(stats[0]?.engagement).toBeCloseTo(0.15);
  });

  it("adds up the comment words of all posts", () => {
    const terms = topTerms([post({ terms: { preco: 2, link: 1 } }), post({ terms: { preco: 3 } })]);
    expect(terms[0]).toEqual({ term: "preco", count: 5 });
  });
});

describe("advice", () => {
  const base = {
    followers: 1000,
    offset: BRT,
    postsPerWeek: 3,
    activity: null,
    online: null,
    reachByFollowType: null,
    keywords: [],
  };

  it("says which format works better when the groups are big enough", () => {
    const reels = Array.from({ length: 3 }, () =>
      post({ kind: "REELS", media_type: "VIDEO", metrics: { reach: 1000, total_interactions: 100 } }),
    );
    const photos = Array.from({ length: 3 }, () =>
      post({ metrics: { reach: 1000, total_interactions: 20 } }),
    );
    const advice = buildAdvice({ ...base, posts: [...reels, ...photos] });
    const format = advice.find((a) => a.id === "format");
    expect(format?.text).toContain("Reels engajam 5x mais que fotos");
  });

  it("stays quiet with too little data", () => {
    const advice = buildAdvice({ ...base, posts: [post(), post()] });
    expect(advice.find((a) => a.id === "format")).toBeUndefined();
    expect(advice.find((a) => a.id === "weekday")).toBeUndefined();
  });

  it("talks about discovery, frequency and what people ask for", () => {
    const advice = buildAdvice({
      ...base,
      posts: [],
      postsPerWeek: 0.5,
      reachByFollowType: { FOLLOWER: 90, NON_FOLLOWER: 10 },
      keywords: [{ keyword: "ebook", count: 12 }],
    });
    const ids = advice.map((a) => a.id);
    expect(ids).toEqual(expect.arrayContaining(["discovery", "frequency", "keywords"]));
  });
});

describe("formatting", () => {
  it("formats numbers in pt-BR", () => {
    expect(compact(1234)).toBe("1.234");
    expect(compact(null)).toBe("—");
    expect(percent(0.0534)).toBe("5,3%");
    expect(seconds(12_400)).toBe("12,4 s");
    expect(formatMetric("engagement", 0.1)).toBe("10%");
    expect(changePct(120, 100)).toBe(20);
    expect(changePct(5, 0)).toBeNull();
  });
});
