import type { components } from "@/lib/api-types";
import { messages } from "@/lib/messages";

export type Campaign = components["schemas"]["CampaignOut"];
export type Limits = components["schemas"]["LimitsOut"];
export type CampaignPayload = components["schemas"]["CampaignInput"];

export type CampaignForm = {
  name: string;
  triggerComments: boolean;
  triggerStoryReplies: boolean;
  triggerMessages: boolean;
  target: "post" | "any";
  postId: string;
  keywords: string[];
  matchMode: "WHOLE_WORD" | "CONTAINS";
  message: string;
  linkUrl: string;
  buttonLabel: string;
  followGateEnabled: boolean;
  followGateMessage: string;
  followGateButtonLabel: string;
  publicReplies: string[];
};

export const emptyForm: CampaignForm = {
  name: "",
  triggerComments: true,
  triggerStoryReplies: false,
  triggerMessages: false,
  target: "post",
  postId: "",
  keywords: [],
  matchMode: "WHOLE_WORD",
  message: "",
  linkUrl: "",
  buttonLabel: "",
  followGateEnabled: false,
  followGateMessage: messages.builder.gateDefaultMessage,
  followGateButtonLabel: messages.builder.gateDefaultButton,
  publicReplies: [],
};

export function fromCampaign(campaign: Campaign): CampaignForm {
  return {
    name: campaign.name,
    triggerComments: campaign.trigger_comments,
    triggerStoryReplies: campaign.trigger_story_replies,
    triggerMessages: campaign.trigger_messages ?? false,
    target: campaign.match_any_post ? "any" : "post",
    postId: campaign.post_id ?? "",
    keywords: [...campaign.keywords],
    matchMode: campaign.match_mode,
    message: campaign.dm_message,
    linkUrl: campaign.link_url ?? "",
    buttonLabel: campaign.link_button_label ?? "",
    followGateEnabled: campaign.follow_gate_enabled,
    followGateMessage: campaign.follow_gate_message ?? messages.builder.gateDefaultMessage,
    followGateButtonLabel:
      campaign.follow_gate_button_label ?? messages.builder.gateDefaultButton,
    publicReplies: [...campaign.public_reply_messages],
  };
}

/** The body the API validates. The active state is not part of an edit. */
export function toPayload(form: CampaignForm): Omit<CampaignPayload, "is_active"> {
  const link = form.linkUrl.trim();
  const label = form.buttonLabel.trim();
  return {
    name: form.name.trim(),
    keywords: form.keywords.map((k) => k.trim()).filter(Boolean),
    match_mode: form.matchMode,
    // A post target only means something when the campaign answers comments.
    post_id: form.triggerComments && form.target === "post" ? form.postId.trim() : null,
    match_any_post: form.triggerComments && form.target === "any",
    trigger_comments: form.triggerComments,
    trigger_story_replies: form.triggerStoryReplies,
    trigger_messages: form.triggerMessages,
    dm_message: form.message,
    link_url: link || null,
    // The API refuses a button without a link; with no link there is no button to name.
    link_button_label: link ? label || null : null,
    // The invite only exists while the gate is on. The API clears it otherwise, and so do we.
    follow_gate_enabled: form.followGateEnabled,
    follow_gate_message: form.followGateEnabled ? form.followGateMessage.trim() : null,
    follow_gate_button_label: form.followGateEnabled
      ? form.followGateButtonLabel.trim() || null
      : null,
    // There is no comment to answer in public when the campaign only answers stories.
    public_reply_messages: form.triggerComments
      ? form.publicReplies.map((m) => m.trim()).filter(Boolean)
      : [],
  };
}

/** Splits pasted or typed input on commas and line breaks and keeps new, non-empty terms. */
export function addKeywords(current: string[], raw: string, max: number): string[] {
  const next = [...current];
  const seen = new Set(current.map((k) => k.toLocaleLowerCase("pt-BR")));
  for (const piece of raw.split(/[,\n]/)) {
    const term = piece.trim();
    const key = term.toLocaleLowerCase("pt-BR");
    if (!term || seen.has(key) || next.length >= max) continue;
    seen.add(key);
    next.push(term);
  }
  return next;
}

export type FormErrors = Partial<Record<string, string>>;

/** Quick checks so obvious mistakes show before a round trip. The API still decides. */
export function validateForm(form: CampaignForm, limits: Limits): FormErrors {
  const errors: FormErrors = {};
  const payload = toPayload(form);
  if (!payload.name) errors.name = "Dê um nome à campanha.";
  if (!form.triggerComments && !form.triggerStoryReplies && !form.triggerMessages) {
    errors.trigger = "Escolha o que ativa a campanha: comentários, respostas a stories ou mensagens no direct.";
  }
  if (form.triggerComments && form.target === "post" && !form.postId.trim()) {
    errors.post_id = "Escolha um post ou use qualquer post.";
  }
  if (form.followGateEnabled) {
    if (!payload.link_url) errors.link_url = messages.builder.gateNeedsLink;
    if (!form.followGateMessage.trim()) {
      errors.follow_gate_message = "Escreva a mensagem de convite.";
    } else if (form.followGateMessage.trim().length > limits.gate_message_max) {
      errors.follow_gate_message = `O convite aceita até ${limits.gate_message_max} caracteres.`;
    }
    if (form.followGateButtonLabel.trim().length > limits.button_title_max) {
      errors.follow_gate_button_label = `O botão aceita até ${limits.button_title_max} caracteres.`;
    }
  }
  if (payload.keywords.length === 0) errors.keywords = "Adicione ao menos uma palavra-chave.";
  if (!form.message.trim()) errors.dm_message = "Escreva a mensagem da DM.";
  const link = payload.link_url ?? "";
  if (link && !/^https:\/\//i.test(link)) errors.link_url = "O link precisa começar com https://";
  if (form.message.length + (link ? link.length + 1 : 0) > limits.message_max) {
    errors.dm_message = `A mensagem com o link passa de ${limits.message_max} caracteres.`;
  }
  if (form.buttonLabel.trim().length > limits.button_title_max) {
    errors.link_button_label = `O botão aceita até ${limits.button_title_max} caracteres.`;
  }
  if (form.buttonLabel.trim() && !link) {
    errors.link_url = "Um botão precisa de um link.";
  }
  if ((payload.public_reply_messages ?? []).some((m) => m.length > limits.public_reply_max)) {
    errors.public_reply_messages = `Cada resposta aceita até ${limits.public_reply_max} caracteres.`;
  }
  return errors;
}
