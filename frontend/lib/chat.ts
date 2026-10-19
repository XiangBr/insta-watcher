// Pure rules of the chat pages: the 24 h window, who wrote each message, and the flow editor's
// draft (what the form holds) and its conversion to the API body.
import type { components } from "@/lib/api-types";

export type Contact = components["schemas"]["ContactOut"];
export type ChatMessage = components["schemas"]["ChatMessageOut"];
export type Flow = components["schemas"]["FlowOut"];
export type FlowStep = components["schemas"]["FlowStep"];
export type FlowOption = components["schemas"]["FlowOption"];
export type FlowInput = components["schemas"]["FlowInput"];
export type ChatConfig = components["schemas"]["ChatConfig"];

export const MAX_OPTIONS = 10;
export const OPTION_LABEL_MAX = 20;
export const MESSAGE_MAX = 1000;
export const FOLLOWUP_MAX_MINUTES = 23 * 60;

const HOUR_MS = 3_600_000;

/** Hours left in the 24 h window, rounded down. null when it is closed. */
export function windowHoursLeft(contact: Contact, now: Date = new Date()): number | null {
  if (!contact.window_closes_at) return null;
  const left = Date.parse(contact.window_closes_at) - now.getTime();
  return left > 0 ? Math.floor(left / HOUR_MS) : null;
}

export function inHumanMode(contact: Contact, now: Date = new Date()): boolean {
  return contact.human_until != null && Date.parse(contact.human_until) > now.getTime();
}

/** Messages of the account's side are on the right, like in the Instagram inbox. */
export function isOurs(message: ChatMessage): boolean {
  return message.direction === "OUT";
}

// ---- flow editor -------------------------------------------------------------------------

export type OptionDraft = {
  label: string;
  action: "step" | "link" | "handoff";
  step: string;
  url: string;
  linkText: string;
  linkLabel: string;
  tags: string;
};

export type StepDraft = {
  id: string;
  message: string;
  linkUrl: string;
  linkLabel: string;
  options: OptionDraft[];
  tags: string;
  followupOn: boolean;
  followupMinutes: number;
  followupMessage: string;
  followupOnlyIfQuiet: boolean;
};

export type FlowDraft = {
  name: string;
  isActive: boolean;
  keywords: string[];
  matchMode: "WHOLE_WORD" | "CONTAINS";
  steps: StepDraft[];
};

export function emptyOption(): OptionDraft {
  return { label: "", action: "step", step: "", url: "", linkText: "", linkLabel: "", tags: "" };
}

export function emptyStep(id: string): StepDraft {
  return {
    id,
    message: "",
    linkUrl: "",
    linkLabel: "",
    options: [],
    tags: "",
    followupOn: false,
    followupMinutes: 60,
    followupMessage: "",
    followupOnlyIfQuiet: true,
  };
}

export function emptyFlow(): FlowDraft {
  return { name: "", isActive: true, keywords: [], matchMode: "WHOLE_WORD", steps: [emptyStep("inicio")] };
}

/** A step id that is not taken yet: passo-2, passo-3... */
export function nextStepId(steps: readonly StepDraft[]): string {
  const taken = new Set(steps.map((s) => s.id));
  let n = steps.length + 1;
  while (taken.has(`passo-${n}`)) n += 1;
  return `passo-${n}`;
}

function splitTags(raw: string): string[] {
  return [...new Set(raw.split(",").map((t) => t.trim().toLowerCase()).filter(Boolean))];
}

export function fromFlow(flow: Flow): FlowDraft {
  return {
    name: flow.name,
    isActive: flow.is_active,
    keywords: [...flow.keywords],
    matchMode: flow.match_mode,
    steps: flow.steps.map((s) => ({
      id: s.id,
      message: s.message,
      linkUrl: s.link_url ?? "",
      linkLabel: s.link_label ?? "",
      options: (s.options ?? []).map((o) => ({
        label: o.label,
        action: o.action,
        step: o.step ?? "",
        url: o.url ?? "",
        linkText: o.link_text ?? "",
        linkLabel: o.link_label ?? "",
        tags: (o.tags ?? []).join(", "),
      })),
      tags: (s.tags ?? []).join(", "),
      followupOn: Boolean(s.followup),
      followupMinutes: s.followup?.delay_minutes ?? 60,
      followupMessage: s.followup?.message ?? "",
      followupOnlyIfQuiet: s.followup?.only_if_no_reply ?? true,
    })),
  };
}

export function toFlowInput(draft: FlowDraft): FlowInput {
  return {
    name: draft.name.trim(),
    is_active: draft.isActive,
    keywords: draft.keywords.map((k) => k.trim()).filter(Boolean),
    match_mode: draft.matchMode,
    steps: draft.steps.map((s) => ({
      id: s.id,
      message: s.message.trim(),
      link_url: s.options.length === 0 && s.linkUrl.trim() ? s.linkUrl.trim() : null,
      link_label: s.options.length === 0 && s.linkUrl.trim() && s.linkLabel.trim() ? s.linkLabel.trim() : null,
      options: s.options.map((o) => ({
        label: o.label.trim(),
        action: o.action,
        step: o.action === "step" ? o.step : null,
        url: o.action === "link" ? o.url.trim() : null,
        link_text: o.action === "link" && o.linkText.trim() ? o.linkText.trim() : null,
        link_label: o.action === "link" && o.linkLabel.trim() ? o.linkLabel.trim() : null,
        tags: splitTags(o.tags),
      })),
      tags: splitTags(s.tags),
      followup: s.followupOn
        ? {
            delay_minutes: s.followupMinutes,
            message: s.followupMessage.trim(),
            only_if_no_reply: s.followupOnlyIfQuiet,
          }
        : null,
    })),
  };
}

export type DraftErrors = Record<string, string>;

/** Obvious mistakes, keyed by "name", "steps.0.message", "steps.0.options.1.url"... */
export function validateDraft(draft: FlowDraft): DraftErrors {
  const errors: DraftErrors = {};
  if (!draft.name.trim()) errors.name = "Dê um nome ao fluxo.";
  const ids = draft.steps.map((s) => s.id);
  draft.steps.forEach((s, i) => {
    const at = `steps.${i}`;
    if (!s.message.trim()) errors[`${at}.message`] = "Escreva a mensagem deste passo.";
    if (s.message.length > MESSAGE_MAX) errors[`${at}.message`] = `No máximo ${MESSAGE_MAX} caracteres.`;
    if (s.linkUrl.trim() && !s.linkUrl.trim().startsWith("https://"))
      errors[`${at}.linkUrl`] = "O link precisa começar com https://";
    if (s.followupOn && !s.followupMessage.trim())
      errors[`${at}.followup`] = "Escreva a mensagem de acompanhamento.";
    s.options.forEach((o, j) => {
      const oat = `${at}.options.${j}`;
      if (!o.label.trim()) errors[`${oat}.label`] = "Dê um texto ao botão.";
      if (o.action === "step" && !ids.includes(o.step)) errors[`${oat}.step`] = "Escolha o passo.";
      if (o.action === "link" && !o.url.trim().startsWith("https://"))
        errors[`${oat}.url`] = "O link precisa começar com https://";
    });
  });
  return errors;
}

/** Remove a step and every option that pointed to it (they would point nowhere). */
export function removeStep(draft: FlowDraft, index: number): FlowDraft {
  const gone = draft.steps[index]?.id;
  return {
    ...draft,
    steps: draft.steps
      .filter((_, i) => i !== index)
      .map((s) => ({
        ...s,
        options: s.options.map((o) => (o.action === "step" && o.step === gone ? { ...o, step: "" } : o)),
      })),
  };
}
