import { describe, expect, it } from "vitest";
import { parseApiError } from "@/lib/api-error";
import {
  addKeywords,
  emptyForm,
  fromCampaign,
  toPayload,
  validateForm,
  type Campaign,
  type CampaignForm,
  type Limits,
} from "@/lib/campaign-form";
import { previewDm, renderDm } from "@/lib/dm-preview";
import { relativeTime, secondsUntil } from "@/lib/format";

const LIMITS: Limits = {
  name_max: 80,
  keywords_max: 20,
  keyword_max: 100,
  message_max: 1000,
  button_title_max: 20,
  link_max: 2048,
  public_replies_max: 10,
  public_reply_max: 300,
  gate_message_max: 640,
};

const valid: CampaignForm = {
  ...emptyForm,
  name: "ebook",
  postId: "18446074984116754",
  keywords: ["ebook"],
  message: "Oi {username}!",
};

describe("parseApiError", () => {
  it("maps validation details to fields and drops pydantic's prefix", () => {
    const parsed = parseApiError({
      error: {
        code: "validation_failed",
        message: "invalid input",
        details: [
          { loc: ["body", "link_url"], msg: "Value error, link must start with https://", type: "x" },
          { loc: ["body", "link_url"], msg: "second message for the same field", type: "x" },
          { loc: ["body", "public_reply_messages", "0"], msg: "too long", type: "x" },
        ],
      },
    });
    expect(parsed.code).toBe("validation_failed");
    expect(parsed.fields.link_url).toBe("link must start with https://");
    expect(parsed.fields.public_reply_messages).toBe("too long");
  });

  it("puts a rule across fields under _form and shows it as the message", () => {
    const parsed = parseApiError({
      error: {
        code: "validation_failed",
        message: "invalid input",
        details: [{ loc: ["body"], msg: "Value error, choose exactly one", type: "x" }],
      },
    });
    expect(parsed.fields._form).toBe("choose exactly one");
    expect(parsed.message).toBe("choose exactly one");
  });

  it.each([null, undefined, "boom", {}, { error: "text" }, { error: { details: "nope" } }])(
    "falls back for %j",
    (input) => {
      const parsed = parseApiError(input, "Falhou.");
      expect(parsed.message).toBe("Falhou.");
      expect(parsed.fields).toEqual({});
    },
  );
});

describe("dm preview", () => {
  it("fills {username} everywhere", () => {
    expect(renderDm("Oi {username}! Tchau {username}", "ana")).toBe("Oi ana! Tchau ana");
  });

  it("puts the link in a button when there is a label", () => {
    const preview = previewDm({
      message: "Oi {username}",
      linkUrl: "https://a.test/x",
      buttonLabel: "Abrir",
    });
    expect(preview).toEqual({
      text: "Oi maria",
      button: { title: "Abrir", url: "https://a.test/x" },
    });
  });

  it("appends the link to the text without a label, unless it is already there", () => {
    expect(previewDm({ message: "Oi", linkUrl: "https://a.test", buttonLabel: "" })).toEqual({
      text: "Oi\nhttps://a.test",
      button: null,
    });
    expect(
      previewDm({ message: "Veja https://a.test", linkUrl: "https://a.test", buttonLabel: "" }).text,
    ).toBe("Veja https://a.test");
  });

  it("shows plain text when there is no link", () => {
    expect(previewDm({ message: "Oi", linkUrl: "", buttonLabel: "Abrir" })).toEqual({
      text: "Oi",
      button: null,
    });
  });
});

describe("campaign form", () => {
  it("builds the API body for a specific post", () => {
    expect(toPayload({ ...valid, linkUrl: " https://a.test ", buttonLabel: " Abrir " })).toEqual({
      name: "ebook",
      keywords: ["ebook"],
      match_mode: "WHOLE_WORD",
      post_id: "18446074984116754",
      match_any_post: false,
      trigger_comments: true,
      trigger_story_replies: false,
      trigger_messages: false,
      dm_message: "Oi {username}!",
      link_url: "https://a.test",
      link_button_label: "Abrir",
      follow_gate_enabled: false,
      follow_gate_message: null,
      follow_gate_button_label: null,
      public_reply_messages: [],
    });
  });

  it("sends exactly one target", () => {
    const any = toPayload({ ...valid, target: "any", postId: "123456" });
    expect(any.post_id).toBeNull();
    expect(any.match_any_post).toBe(true);
  });

  it("drops a button label that has no link, and blank replies", () => {
    const payload = toPayload({ ...valid, buttonLabel: "Abrir", publicReplies: ["  ", "Olha o direct"] });
    expect(payload.link_button_label).toBeNull();
    expect(payload.public_reply_messages).toEqual(["Olha o direct"]);
  });

  it("round-trips a stored campaign", () => {
    const stored = {
      id: 1,
      name: "x",
      is_active: true,
      post_id: null,
      match_any_post: true,
      trigger_comments: true,
      trigger_story_replies: true,
      trigger_messages: false,
      keywords: ["a", "b"],
      match_mode: "CONTAINS",
      dm_message: "m",
      link_url: "https://a.test",
      link_button_label: "Abrir",
      follow_gate_enabled: false,
      follow_gate_message: null,
      follow_gate_button_label: null,
      public_reply_messages: ["r"],
      created_at: "2026-10-02T00:00:00Z",
      updated_at: "2026-10-02T00:00:00Z",
      deliveries: { pending: 0, sent: 0, failed: 0, unconfirmed: 0 },
    } satisfies Campaign;
    const form = fromCampaign(stored);
    expect(form.target).toBe("any");
    expect(form.triggerStoryReplies).toBe(true);
    const payload = toPayload(form);
    expect(payload.match_any_post).toBe(true);
    expect(payload.trigger_story_replies).toBe(true);
    expect(payload.keywords).toEqual(["a", "b"]);
    expect(payload.link_button_label).toBe("Abrir");
  });

  it("adds keywords split on commas and lines, without duplicates or empties", () => {
    expect(addKeywords(["Preço"], "preço, quero\n\n  link ,Link", 20)).toEqual([
      "Preço",
      "quero",
      "link",
    ]);
  });

  it("stops adding keywords at the limit", () => {
    expect(addKeywords(["a", "b"], "c, d, e", 3)).toEqual(["a", "b", "c"]);
  });
});

describe("campaign triggers", () => {
  const storyOnly: CampaignForm = {
    ...valid,
    triggerComments: false,
    triggerStoryReplies: true,
    postId: "",
    publicReplies: ["Te mandei no direct!"],
  };

  it("sends no post target and no public replies for a story-only campaign", () => {
    const payload = toPayload(storyOnly);
    expect(payload.trigger_comments).toBe(false);
    expect(payload.trigger_story_replies).toBe(true);
    expect(payload.post_id).toBeNull();
    expect(payload.match_any_post).toBe(false);
    expect(payload.public_reply_messages).toEqual([]);
  });

  it("drops a leftover post target when comments are switched off", () => {
    const payload = toPayload({ ...storyOnly, target: "any", postId: "18446074984116754" });
    expect(payload.post_id).toBeNull();
    expect(payload.match_any_post).toBe(false);
  });

  it("keeps the post target when the campaign answers both", () => {
    const payload = toPayload({ ...valid, triggerStoryReplies: true });
    expect(payload.post_id).toBe("18446074984116754");
    expect(payload.trigger_comments).toBe(true);
    expect(payload.trigger_story_replies).toBe(true);
  });

  it("accepts a story-only campaign without a post", () => {
    expect(validateForm(storyOnly, LIMITS)).toEqual({});
  });

  it("refuses a campaign with no trigger", () => {
    const errors = validateForm({ ...valid, triggerComments: false, triggerStoryReplies: false }, LIMITS);
    expect(errors.trigger).toMatch(/comentários/);
  });

  it("still asks for a post when comments are on", () => {
    expect(validateForm({ ...valid, postId: "", triggerStoryReplies: true }, LIMITS).post_id).toBeTruthy();
  });
});

describe("validateForm", () => {
  it("accepts a complete form", () => {
    expect(validateForm(valid, LIMITS)).toEqual({});
  });

  it("flags what is missing", () => {
    const errors = validateForm(emptyForm, LIMITS);
    expect(Object.keys(errors).sort()).toEqual(["dm_message", "keywords", "name", "post_id"]);
  });

  it("does not ask for a post when the target is any post", () => {
    expect(validateForm({ ...valid, target: "any", postId: "" }, LIMITS)).toEqual({});
  });

  it("flags an insecure link, an oversized button and a button without a link", () => {
    expect(validateForm({ ...valid, linkUrl: "http://a.test" }, LIMITS).link_url).toMatch(/https/);
    expect(
      validateForm({ ...valid, linkUrl: "https://a.test", buttonLabel: "x".repeat(21) }, LIMITS)
        .link_button_label,
    ).toMatch(/20/);
    expect(validateForm({ ...valid, buttonLabel: "Abrir" }, LIMITS).link_url).toMatch(/link/);
  });

  it("counts the link against the message size, like the API does", () => {
    const form = { ...valid, message: "x".repeat(995), linkUrl: "https://a.test" };
    expect(validateForm(form, LIMITS).dm_message).toMatch(/1000/);
  });
});

describe("relativeTime", () => {
  const now = new Date("2026-10-02T12:00:00Z");
  it.each([
    ["2026-10-02T11:59:50Z", "agora"],
    ["2026-10-02T11:55:00Z", "há 5 min"],
    ["2026-10-02T09:00:00Z", "há 3 h"],
    ["2026-09-30T12:00:00Z", "há 2 dias"],
    ["2026-10-01T10:00:00Z", "há 1 dia"],
    ["2026-10-02T12:30:00Z", "agora"],
  ])("%s -> %s", (iso, expected) => {
    expect(relativeTime(iso, now)).toBe(expected);
  });

  it("handles missing and invalid values", () => {
    expect(relativeTime(null, now)).toBe("nunca");
    expect(relativeTime("not a date", now)).toBe("nunca");
  });

  it("never returns a negative wait", () => {
    expect(secondsUntil("2026-10-02T11:00:00Z", now)).toBe(0);
    expect(secondsUntil("2026-10-02T12:00:30Z", now)).toBe(30);
  });
});
