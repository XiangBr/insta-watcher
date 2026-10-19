import { describe, expect, it } from "vitest";
import {
  emptyForm,
  fromCampaign,
  toPayload,
  validateForm,
  type Campaign,
  type CampaignForm,
  type Limits,
} from "@/lib/campaign-form";
import { dmSummary, gateTone, type Delivery } from "@/lib/delivery-view";
import { messages } from "@/lib/messages";

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

const base: CampaignForm = {
  ...emptyForm,
  name: "ebook",
  postId: "18446074984116754",
  keywords: ["ebook"],
  message: "Aqui está",
  linkUrl: "https://exemplo.com/ebook",
  buttonLabel: "Baixar",
};

describe("follow gate in the campaign form", () => {
  it("starts switched off, with a friendly invite ready to use", () => {
    expect(emptyForm.followGateEnabled).toBe(false);
    expect(emptyForm.followGateMessage).toBe(messages.builder.gateDefaultMessage);
    expect(emptyForm.followGateButtonLabel).toBe("Já segui");
  });

  it("sends no invite while the gate is off, whatever the form still holds", () => {
    const payload = toPayload({ ...base, followGateMessage: "texto antigo" });
    expect(payload.follow_gate_enabled).toBe(false);
    expect(payload.follow_gate_message).toBeNull();
    expect(payload.follow_gate_button_label).toBeNull();
  });

  it("sends the trimmed invite when the gate is on", () => {
    const payload = toPayload({
      ...base,
      followGateEnabled: true,
      followGateMessage: "  Segue e toca!  ",
      followGateButtonLabel: " Já segui ",
    });
    expect(payload).toMatchObject({
      follow_gate_enabled: true,
      follow_gate_message: "Segue e toca!",
      follow_gate_button_label: "Já segui",
    });
  });

  it("lets the API pick the default label when the field is empty", () => {
    const payload = toPayload({ ...base, followGateEnabled: true, followGateButtonLabel: "  " });
    expect(payload.follow_gate_button_label).toBeNull();
  });

  it("accepts a complete gate", () => {
    expect(validateForm({ ...base, followGateEnabled: true }, LIMITS)).toEqual({});
  });

  it("needs a link to hold back", () => {
    const form = { ...base, followGateEnabled: true, linkUrl: "", buttonLabel: "" };
    expect(validateForm(form, LIMITS).link_url).toBe(messages.builder.gateNeedsLink);
  });

  it("needs an invite, and keeps it within the size Meta allows for a button message", () => {
    const empty = validateForm({ ...base, followGateEnabled: true, followGateMessage: " " }, LIMITS);
    expect(empty.follow_gate_message).toMatch(/convite/);
    const long = validateForm(
      { ...base, followGateEnabled: true, followGateMessage: "x".repeat(641) },
      LIMITS,
    );
    expect(long.follow_gate_message).toMatch(/640/);
  });

  it("limits the button label", () => {
    const errors = validateForm(
      { ...base, followGateEnabled: true, followGateButtonLabel: "x".repeat(21) },
      LIMITS,
    );
    expect(errors.follow_gate_button_label).toMatch(/20/);
  });

  it("does not check the invite while the gate is off", () => {
    expect(validateForm({ ...base, followGateMessage: "" }, LIMITS)).toEqual({});
  });

  it("round-trips a stored campaign, and falls back to the defaults when it has none", () => {
    const stored = {
      id: 1,
      name: "x",
      is_active: true,
      post_id: "18446074984116754",
      match_any_post: false,
      trigger_comments: true,
      trigger_story_replies: false,
      trigger_messages: false,
      keywords: ["a"],
      match_mode: "WHOLE_WORD",
      dm_message: "m",
      link_url: "https://a.test",
      link_button_label: "Abrir",
      follow_gate_enabled: true,
      follow_gate_message: "Segue!",
      follow_gate_button_label: "Pronto",
      public_reply_messages: [],
      created_at: "2026-10-02T00:00:00Z",
      updated_at: "2026-10-02T00:00:00Z",
      deliveries: { pending: 0, sent: 0, failed: 0, unconfirmed: 0 },
    } satisfies Campaign;
    const form = fromCampaign(stored);
    expect(form.followGateEnabled).toBe(true);
    expect(form.followGateMessage).toBe("Segue!");
    expect(form.followGateButtonLabel).toBe("Pronto");

    const off = fromCampaign({
      ...stored,
      follow_gate_enabled: false,
      follow_gate_message: null,
      follow_gate_button_label: null,
    });
    expect(off.followGateEnabled).toBe(false);
    expect(off.followGateMessage).toBe(messages.builder.gateDefaultMessage);
  });
});

function delivery(overrides: Partial<Delivery>): Delivery {
  return {
    id: 1,
    created_at: "2026-10-02T12:00:00Z",
    campaign_id: 1,
    campaign_name: "c",
    trigger: "STORY_REPLY",
    origin: "WEBHOOK",
    media_id: null,
    actor_username: "ana",
    text: "link",
    matched_keyword: "link",
    status: "SENT",
    attempts: 1,
    dm_sent_at: "2026-10-02T12:00:03Z",
    error_message: null,
    public_reply_status: null,
    public_reply_sent_at: null,
    public_reply_error: null,
    gate_state: null,
    gate_taps: 0,
    followup_status: null,
    followup_sent_at: null,
    followup_error: null,
    anonymized: false,
    ...overrides,
  };
}

describe("dmSummary", () => {
  const t = messages.deliveries;

  it("is the plain status for a delivery without a gate", () => {
    expect(dmSummary(delivery({}))).toEqual({ status: "SENT", label: t.status.SENT });
    expect(dmSummary(delivery({ status: "FAILED" }))).toEqual({
      status: "FAILED",
      label: t.status.FAILED,
    });
  });

  it("does not call an invite 'sent': the person has no link yet", () => {
    expect(dmSummary(delivery({ gate_state: "WAITING" }))).toEqual({
      status: "PENDING",
      label: t.gate.waiting,
    });
  });

  it.each([
    ["CHECKING", "PENDING", t.gate.checking],
    ["REMINDED", "PENDING", t.gate.reminded],
    ["STOPPED", "FAILED", t.gate.stopped],
  ])("says where a %s delivery is", (state, status, label) => {
    expect(dmSummary(delivery({ gate_state: state }))).toEqual({ status, label });
  });

  it.each([
    ["SENT", "SENT", t.gate.linkSent],
    ["FAILED", "FAILED", t.gate.linkFailed],
    ["UNCONFIRMED", "UNCONFIRMED", t.gate.linkUnconfirmed],
    ["PENDING", "PENDING", t.gate.linkSending],
  ])("follows the link message to the end (%s)", (followup, status, label) => {
    const d = delivery({ gate_state: "PASSED", followup_status: followup });
    expect(dmSummary(d)).toEqual({ status, label });
  });

  it("shows a problem with the invite itself as it is", () => {
    for (const status of ["PENDING", "FAILED", "UNCONFIRMED"]) {
      const d = delivery({ gate_state: "WAITING", status });
      expect(dmSummary(d)).toEqual({ status, label: t.status[status] });
    }
  });

  it("has a text and a colour for every gate state", () => {
    for (const state of ["WAITING", "CHECKING", "PASSED", "RELEASED", "REMINDED", "STOPPED"]) {
      expect(t.gate.state[state]).toBeTruthy();
      expect(gateTone(state)).toMatch(/^bg-/);
    }
    expect(gateTone("PASSED")).toBe("bg-ok");
    expect(gateTone("STOPPED")).toBe("bg-fail");
  });
});
