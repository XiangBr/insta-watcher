import { describe, expect, it } from "vitest";
import {
  availableTabs,
  dmThread,
  pickTab,
  sampleComment,
  type DmThreadInput,
} from "@/lib/phone-preview";
import { messages } from "@/lib/messages";

describe("availableTabs", () => {
  it("shows the post and its comments for a comment campaign", () => {
    expect(availableTabs({ triggerComments: true, triggerStoryReplies: false })).toEqual([
      "post",
      "comments",
      "dm",
    ]);
  });

  it("shows only the story for a story campaign", () => {
    expect(availableTabs({ triggerComments: false, triggerStoryReplies: true })).toEqual([
      "story",
      "dm",
    ]);
  });

  it("shows everything when the campaign answers both", () => {
    expect(availableTabs({ triggerComments: true, triggerStoryReplies: true })).toEqual([
      "post",
      "comments",
      "story",
      "dm",
    ]);
  });

  it("always has the DM", () => {
    expect(availableTabs({ triggerComments: false, triggerStoryReplies: false })).toEqual(["dm"]);
  });
});

describe("pickTab", () => {
  it("keeps the wanted tab while it exists", () => {
    expect(pickTab("dm", ["post", "dm"])).toBe("dm");
  });

  it("falls back to the first tab when the wanted one went away", () => {
    expect(pickTab("comments", ["story", "dm"])).toBe("story");
  });

  it("never returns nothing", () => {
    expect(pickTab("post", [])).toBe("dm");
  });
});

describe("sampleComment", () => {
  it("is the first keyword, trimmed", () => {
    expect(sampleComment(["  preço ", "link"])).toBe("preço");
  });

  it("has a default when there are no keywords yet", () => {
    expect(sampleComment([])).toBe(messages.builder.phone.sampleComment);
    expect(sampleComment(["  "])).toBe(messages.builder.phone.sampleComment);
  });
});

describe("dmThread", () => {
  const base: DmThreadInput = {
    message: "Oi {username}! Aqui está",
    linkUrl: "https://a.test/x",
    buttonLabel: "Abrir",
    gate: { enabled: false, message: "Segue e toca!", buttonLabel: "Já segui" },
  };

  it("is the one message, with the link in its button, when there is no gate", () => {
    expect(dmThread(base)).toEqual([{ from: "account", text: "Oi maria! Aqui está", button: "Abrir" }]);
  });

  it("puts the link in the text when there is no button label", () => {
    const thread = dmThread({ ...base, buttonLabel: "" });
    expect(thread).toEqual([
      { from: "account", text: "Oi maria! Aqui está\nhttps://a.test/x", button: undefined },
    ]);
  });

  it("invites first, shows the tap, and only then the link", () => {
    const thread = dmThread({ ...base, gate: { ...base.gate, enabled: true } });
    expect(thread.map((m) => [m.from, m.button ?? null])).toEqual([
      ["account", "Já segui"],
      ["person", null],
      ["account", "Abrir"],
    ]);
    expect(thread[1]?.text).toBe("Já segui");
    // The link never shows before the person has tapped.
    expect(thread[0]?.text).not.toContain("a.test");
  });

  it("uses the default button label when the field is empty", () => {
    const gate = { enabled: true, message: "Segue!", buttonLabel: "  " };
    expect(dmThread({ ...base, gate })[0]?.button).toBe(messages.builder.gateDefaultButton);
  });

  it("shows only the invite while the message is still empty", () => {
    const thread = dmThread({
      ...base,
      message: "",
      linkUrl: "",
      buttonLabel: "",
      gate: { ...base.gate, enabled: true },
    });
    expect(thread.map((m) => m.from)).toEqual(["account", "person"]);
  });

  it("is empty with no gate and nothing written", () => {
    expect(dmThread({ ...base, message: "", linkUrl: "", buttonLabel: "" })).toEqual([]);
  });
});
