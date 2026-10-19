import { describe, expect, it } from "vitest";
import {
  emptyFlow,
  emptyOption,
  emptyStep,
  fromFlow,
  inHumanMode,
  nextStepId,
  removeStep,
  toFlowInput,
  validateDraft,
  windowHoursLeft,
  type Contact,
  type Flow,
  type FlowDraft,
} from "@/lib/chat";

const NOW = new Date("2026-10-19T12:00:00Z");

function contact(extra: Partial<Contact> = {}): Contact {
  return {
    id: 1,
    username: "fan",
    tags: [],
    first_seen_at: "2026-10-19T00:00:00Z",
    last_inbound_at: "2026-10-19T10:00:00Z",
    last_outbound_at: null,
    window_closes_at: "2026-10-20T10:00:00Z",
    human_until: null,
    human_reason: null,
    messages_in: 1,
    messages_out: 0,
    last_text: "oi",
    last_source: "USER",
    ...extra,
  };
}

function draft(): FlowDraft {
  const d = emptyFlow();
  d.name = "Menu";
  d.keywords = ["menu"];
  d.steps[0] = {
    ...emptyStep("inicio"),
    message: "Oi!",
    tags: "Lead, lead, vip",
    options: [
      { ...emptyOption(), label: "Curso", action: "step", step: "curso" },
      { ...emptyOption(), label: "PDF", action: "link", url: " https://a.test/pdf " },
      { ...emptyOption(), label: "Humano", action: "handoff", step: "ignored" },
    ],
  };
  d.steps.push({
    ...emptyStep("curso"),
    message: "Abre segunda.",
    linkUrl: "https://a.test",
    followupOn: true,
    followupMessage: "Dúvidas?",
    followupMinutes: 30,
  });
  return d;
}

describe("window and human mode", () => {
  it("counts whole hours left and closes at zero", () => {
    expect(windowHoursLeft(contact(), NOW)).toBe(22);
    expect(windowHoursLeft(contact({ window_closes_at: "2026-10-19T11:59:00Z" }), NOW)).toBeNull();
    expect(windowHoursLeft(contact({ window_closes_at: null }), NOW)).toBeNull();
  });

  it("knows when a person is with a human", () => {
    expect(inHumanMode(contact({ human_until: "2026-10-19T13:00:00Z" }), NOW)).toBe(true);
    expect(inHumanMode(contact({ human_until: "2026-10-19T11:00:00Z" }), NOW)).toBe(false);
    expect(inHumanMode(contact(), NOW)).toBe(false);
  });
});

describe("flow draft", () => {
  it("builds the API body: only the fields each option uses, clean tags", () => {
    const body = toFlowInput(draft());
    const [start, curso] = body.steps;
    expect(start?.tags).toEqual(["lead", "vip"]);
    expect(start?.link_url).toBeNull();
    expect(start?.options?.map((o) => [o.action, o.step, o.url])).toEqual([
      ["step", "curso", null],
      ["link", null, "https://a.test/pdf"],
      ["handoff", null, null],
    ]);
    expect(curso?.link_url).toBe("https://a.test");
    expect(curso?.followup).toEqual({ delay_minutes: 30, message: "Dúvidas?", only_if_no_reply: true });
  });

  it("round-trips through the API shape", () => {
    const body = toFlowInput(draft());
    const flow = {
      ...body,
      id: 3,
      created_at: "",
      updated_at: "",
      sent: 0,
      people: 0,
      keywords: body.keywords ?? [],
      is_active: true,
      match_mode: "WHOLE_WORD",
    } as Flow;
    expect(toFlowInput(fromFlow(flow))).toEqual(body);
  });

  it("flags the obvious mistakes", () => {
    const d = draft();
    d.name = " ";
    d.steps[0]!.options[0]!.step = "nope";
    d.steps[0]!.options[1]!.url = "http://a.test";
    d.steps[1]!.message = "";
    d.steps[1]!.followupMessage = "";
    expect(Object.keys(validateDraft(d)).sort()).toEqual([
      "name",
      "steps.0.options.0.step",
      "steps.0.options.1.url",
      "steps.1.followup",
      "steps.1.message",
    ]);
    expect(validateDraft(draft())).toEqual({});
  });

  it("removing a step unlinks the options that pointed to it", () => {
    const after = removeStep(draft(), 1);
    expect(after.steps.map((s) => s.id)).toEqual(["inicio"]);
    expect(after.steps[0]?.options[0]?.step).toBe("");
  });

  it("picks a free step id", () => {
    expect(nextStepId([emptyStep("inicio")])).toBe("passo-2");
    expect(nextStepId([emptyStep("inicio"), emptyStep("passo-3"), emptyStep("passo-x")])).toBe("passo-4");
  });
});

describe("timeUntil", () => {
  it("reads scheduled times", async () => {
    const { timeUntil } = await import("@/lib/format");
    expect(timeUntil("2026-10-19T13:35:00Z", NOW)).toBe("em 2 h");
    expect(timeUntil("2026-10-19T12:10:00Z", NOW)).toBe("em 10 min");
    expect(timeUntil("2026-10-20T12:00:00Z", NOW)).toBe("em 1 dia");
    expect(timeUntil("2026-10-19T11:00:00Z", NOW)).toBe("agora");
  });
});
