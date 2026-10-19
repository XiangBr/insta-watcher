import { describe, expect, it } from "vitest";
import {
  campaignHref,
  collectedCount,
  isConfident,
  labelText,
  mainOption,
  mixOf,
  multiplier,
  topPosts,
  type Competitor,
  type CompetitorPost,
  type TaxonomyQuestion,
} from "@/lib/competitors";

const format: TaxonomyQuestion = {
  key: "format",
  type: "choice",
  label: "Formato",
  options: [
    { key: "tutorial", label: "Tutorial" },
    { key: "humor", label: "Humor" },
  ],
};
const level: TaxonomyQuestion = {
  key: "level",
  type: "score",
  label: "Nível",
  options: [
    { key: "0", label: "Iniciante" },
    { key: "1", label: "Intermediário" },
    { key: "2", label: "Avançado" },
  ],
};
const dm: TaxonomyQuestion = {
  key: "comment_to_dm",
  type: "noul",
  label: "Usa comentário para DM",
  options: [
    { key: "true", label: "Sim" },
    { key: "false", label: "Não" },
  ],
};

function post(id: string, performance: number | null, labels = {}, pinned = false): CompetitorPost {
  return {
    id,
    url: null,
    media_type: "Image",
    caption: null,
    posted_at: "2026-10-01T00:00:00Z",
    likes: 10,
    comments: 1,
    views: null,
    display_url: null,
    is_pinned: pinned,
    engagement_rate: 0.01,
    performance,
    labels,
  };
}

function competitor(username: string, posts: CompetitorPost[], extra: Partial<Competitor> = {}): Competitor {
  return {
    id: username.length,
    username,
    is_self: false,
    full_name: null,
    biography: null,
    category: null,
    external_url: null,
    profile_pic_url: null,
    followers: 1000,
    follows: null,
    posts_count: null,
    verified: null,
    private: null,
    scraped_at: "2026-10-01T00:00:00Z",
    scrape_error: null,
    classify_error: null,
    labels: {},
    stats: {
      posts: posts.length,
      median_engagement_rate: 0.01,
      avg_likes: null,
      avg_comments: null,
      posts_per_week: null,
      video_share: null,
    },
    posts,
    ...extra,
  };
}

describe("labelText", () => {
  it("names choices, levels and yes/no", () => {
    expect(labelText(format, { value: "humor", confidence: 0.9 })).toBe("Humor");
    expect(labelText(format, { value: "invented" })).toBeNull();
    expect(labelText(level, { value: 1.6 })).toBe("Avançado");
    expect(labelText(level, { value: 9 })).toBe("Avançado");
    expect(labelText(dm, { value: 0.8 })).toBe("Sim");
    expect(labelText(dm, { value: 0.2 })).toBe("Não");
    expect(labelText(undefined, { value: "x" })).toBeNull();
  });

  it("treats a missing confidence as sure", () => {
    expect(isConfident({ value: 0.9 })).toBe(true);
    expect(isConfident({ value: "x", confidence: 0.3 })).toBe(false);
  });
});

describe("posts", () => {
  it("ranks other profiles' posts by performance, never yours or pinned", () => {
    const me = competitor("eu", [post("m1", 9)], { is_self: true });
    const rival = competitor("rival", [post("r1", 1.2), post("r2", 3), post("r3", 50, {}, true)]);
    const other = competitor("outro", [post("o1", 2), post("o2", null)]);
    expect(topPosts([me, rival, other], 2).map((r) => r.post.id)).toEqual(["r2", "o1"]);
  });

  it("finds the format mix and the main format", () => {
    const posts = [
      post("1", 1, { format: { value: "tutorial" } }),
      post("2", 1, { format: { value: "tutorial" } }),
      post("3", 1, { format: { value: "humor" } }),
      post("4", 1),
    ];
    expect(mixOf(posts, "format")).toEqual([
      { option: "tutorial", share: 2 / 3 },
      { option: "humor", share: 1 / 3 },
    ]);
    expect(mainOption(posts, "format")).toBe("tutorial");
    expect(mainOption([post("5", 1)], "format")).toBeNull();
  });

  it("counts only competitors collected without an error", () => {
    expect(
      collectedCount([
        competitor("a", []),
        competitor("b", [], { scraped_at: null }),
        competitor("c", [], { scrape_error: "perfil privado" }),
        competitor("eu", [], { is_self: true }),
      ]),
    ).toBe(1);
  });
});

describe("formats and links", () => {
  it("formats the multiplier in pt-BR", () => {
    expect(multiplier(1.84)).toBe("1,8×");
    expect(multiplier(2)).toBe("2,0×");
    expect(multiplier(null)).toBe("—");
  });

  it("builds the campaign link with the keyword encoded", () => {
    expect(campaignHref("setup")).toBe("/campaigns/new?keyword=setup");
    expect(campaignHref("a&b")).toBe("/campaigns/new?keyword=a%26b");
    expect(campaignHref("  ")).toBe("/campaigns/new");
  });
});
