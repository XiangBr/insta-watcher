import { describe, expect, it } from "vitest";
import {
  NO_FILTERS,
  filterCampaigns,
  isFiltered,
  sortByRecent,
  triggerLines,
  type Campaign,
} from "@/lib/campaign-list";
import { normalizeForSearch } from "@/lib/search";
import {
  captionLine,
  indexPosts,
  isVideo,
  postLabel,
  thumbnailOf,
  type Post,
} from "@/lib/posts";
import { THEME_COOKIE, parseTheme, themeAttribute, themeCookie } from "@/lib/theme";

describe("theme", () => {
  it.each(["light", "dark", "system"])("accepts %s", (value) => {
    expect(parseTheme(value)).toBe(value);
  });

  it.each([undefined, null, "", "blue", "DARK", "light; path=/"])(
    "treats %j as the system theme, so a bad cookie never breaks the page",
    (value) => {
      expect(parseTheme(value)).toBe("system");
    },
  );

  it("leaves the attribute off for the system theme, so the media query decides", () => {
    expect(themeAttribute("system")).toBeUndefined();
    expect(themeAttribute("light")).toBe("light");
    expect(themeAttribute("dark")).toBe("dark");
  });

  it("writes a year-long cookie the server can read, secure only over https", () => {
    const cookie = themeCookie("dark", false);
    expect(cookie.startsWith(`${THEME_COOKIE}=dark;`)).toBe(true);
    expect(cookie).toContain("max-age=31536000");
    expect(cookie).toContain("samesite=lax");
    expect(cookie).not.toContain("secure");
    expect(themeCookie("dark", true)).toContain("; secure");
  });
});

describe("normalizeForSearch", () => {
  it("ignores case and accents", () => {
    expect(normalizeForSearch("  PREÇO  ")).toBe("preco");
    expect(normalizeForSearch("Ação")).toBe("acao");
  });
});

function post(overrides: Partial<Post> = {}): Post {
  return { id: "1", ...overrides };
}

describe("posts", () => {
  it("uses only an https thumbnail, falling back to the media url", () => {
    expect(thumbnailOf(post({ thumbnail_url: "https://cdn.test/t.jpg" }))).toBe(
      "https://cdn.test/t.jpg",
    );
    expect(thumbnailOf(post({ media_url: "https://cdn.test/m.jpg" }))).toBe("https://cdn.test/m.jpg");
    expect(thumbnailOf(post({ thumbnail_url: "http://cdn.test/t.jpg" }))).toBeNull();
    expect(thumbnailOf(post({ thumbnail_url: "javascript:alert(1)" }))).toBeNull();
    expect(thumbnailOf(post())).toBeNull();
  });

  it("knows videos and reels", () => {
    expect(isVideo(post({ media_type: "VIDEO" }))).toBe(true);
    expect(isVideo(post({ media_type: "REELS" }))).toBe(true);
    expect(isVideo(post({ media_type: "IMAGE" }))).toBe(false);
  });

  it("recognises a post by the first line of its caption", () => {
    expect(captionLine(post({ caption: "\n\n  Primeira   linha  \nsegunda" }))).toBe("Primeira linha");
    expect(captionLine(post({ caption: "x".repeat(200) }), 20)).toHaveLength(20);
    expect(captionLine(post({ caption: "x".repeat(200) }), 20).endsWith("…")).toBe(true);
    expect(captionLine(post())).toBe("");
  });

  it("falls back to the date so a card is never blank", () => {
    expect(postLabel(post({ caption: "Olá" }))).toBe("Olá");
    expect(postLabel(post({ timestamp: "2026-10-02T12:00:00Z" }))).toMatch(/2026|26/);
    expect(postLabel(post())).toBe("—");
  });

  it("indexes posts by id", () => {
    const map = indexPosts([post({ id: "a" }), post({ id: "b" })]);
    expect([...map.keys()]).toEqual(["a", "b"]);
    expect(indexPosts(undefined).size).toBe(0);
  });
});

function campaign(overrides: Partial<Campaign> = {}): Campaign {
  return {
    id: 1,
    name: "ebook",
    is_active: true,
    post_id: "18446074984116754",
    match_any_post: false,
    trigger_comments: true,
    trigger_story_replies: false,
    trigger_messages: false,
    keywords: ["ebook"],
    match_mode: "WHOLE_WORD",
    dm_message: "Oi",
    link_url: null,
    link_button_label: null,
    follow_gate_enabled: false,
    follow_gate_message: null,
    follow_gate_button_label: null,
    public_reply_messages: [],
    created_at: "2026-10-01T00:00:00Z",
    updated_at: "2026-10-01T00:00:00Z",
    deliveries: { pending: 0, sent: 0, failed: 0, unconfirmed: 0 },
    ...overrides,
  };
}

describe("campaign list", () => {
  const all = [
    campaign({ id: 1, name: "Receitas", keywords: ["preço", "link"] }),
    campaign({ id: 2, name: "Promo", trigger_comments: false, trigger_story_replies: true, post_id: null }),
    campaign({ id: 3, name: "VIP", is_active: false, follow_gate_enabled: true }),
  ];

  it("shows everything with no filters", () => {
    expect(filterCampaigns(all, NO_FILTERS)).toHaveLength(3);
    expect(isFiltered(NO_FILTERS)).toBe(false);
  });

  it("searches the name and the keywords, ignoring case and accents", () => {
    expect(filterCampaigns(all, { ...NO_FILTERS, query: "RECEITAS" }).map((c) => c.id)).toEqual([1]);
    expect(filterCampaigns(all, { ...NO_FILTERS, query: "preco" }).map((c) => c.id)).toEqual([1]);
    expect(filterCampaigns(all, { ...NO_FILTERS, query: "  " })).toHaveLength(3);
    expect(filterCampaigns(all, { ...NO_FILTERS, query: "nada" })).toEqual([]);
  });

  it("filters by what starts the campaign", () => {
    const ids = (trigger: "comments" | "stories" | "gate") =>
      filterCampaigns(all, { ...NO_FILTERS, trigger }).map((c) => c.id);
    expect(ids("comments")).toEqual([1, 3]);
    expect(ids("stories")).toEqual([2]);
    expect(ids("gate")).toEqual([3]);
  });

  it("filters by live or paused", () => {
    expect(filterCampaigns(all, { ...NO_FILTERS, state: "active" }).map((c) => c.id)).toEqual([1, 2]);
    expect(filterCampaigns(all, { ...NO_FILTERS, state: "paused" }).map((c) => c.id)).toEqual([3]);
  });

  it("combines the filters", () => {
    const filters = { query: "vip", trigger: "gate" as const, state: "paused" as const };
    expect(filterCampaigns(all, filters).map((c) => c.id)).toEqual([3]);
    expect(isFiltered(filters)).toBe(true);
  });

  it("puts the most recently changed first, newest id breaking a tie", () => {
    const list = [
      campaign({ id: 1, updated_at: "2026-10-01T00:00:00Z" }),
      campaign({ id: 2, updated_at: "2026-10-03T00:00:00Z" }),
      campaign({ id: 3, updated_at: "2026-10-03T00:00:00Z" }),
    ];
    expect(sortByRecent(list).map((c) => c.id)).toEqual([3, 2, 1]);
    expect(list.map((c) => c.id)).toEqual([1, 2, 3]); // the input is left alone
  });

  it("describes what starts each campaign", () => {
    expect(triggerLines(campaign())).toEqual([{ kind: "comments", postId: "18446074984116754" }]);
    expect(triggerLines(campaign({ match_any_post: true, post_id: null }))).toEqual([
      { kind: "comments", postId: null },
    ]);
    expect(
      triggerLines(
        campaign({ trigger_story_replies: true, follow_gate_enabled: true }),
      ).map((line) => line.kind),
    ).toEqual(["comments", "stories", "gate"]);
    expect(
      triggerLines(campaign({ trigger_comments: false, trigger_story_replies: true })).map(
        (line) => line.kind,
      ),
    ).toEqual(["stories"]);
  });
});
