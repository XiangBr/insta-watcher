import { previewDm, renderDm } from "@/lib/dm-preview";
import { messages } from "@/lib/messages";

export type PreviewTab = "post" | "comments" | "story" | "dm";

/** Only the screens that exist for this campaign: no comments tab for a story-only one. */
export function availableTabs(triggers: {
  triggerComments: boolean;
  triggerStoryReplies: boolean;
}): PreviewTab[] {
  const tabs: PreviewTab[] = [];
  if (triggers.triggerComments) tabs.push("post", "comments");
  if (triggers.triggerStoryReplies) tabs.push("story");
  tabs.push("dm");
  return tabs;
}

/** The tab to show: the wanted one if it still exists, else the first that does. */
export function pickTab(wanted: PreviewTab, tabs: readonly PreviewTab[]): PreviewTab {
  return tabs.includes(wanted) ? wanted : (tabs[0] ?? "dm");
}

/** What the sample person writes: the first keyword, so the preview matches the campaign. */
export function sampleComment(keywords: readonly string[]): string {
  return keywords.find((k) => k.trim() !== "")?.trim() ?? messages.builder.phone.sampleComment;
}

export type ThreadMessage = {
  from: "account" | "person";
  text: string;
  /** The label of the button under an account message. */
  button?: string;
};

export type DmThreadInput = {
  message: string;
  linkUrl: string;
  buttonLabel: string;
  gate: { enabled: boolean; message: string; buttonLabel: string };
};

/**
 * The conversation as the person sees it. Without the gate it is the one message. With the
 * gate the invite comes first, the person taps the button, and only then does the link
 * message arrive. Same rules the worker follows when it sends.
 */
export function dmThread(input: DmThreadInput): ThreadMessage[] {
  const dm = previewDm({
    message: input.message,
    linkUrl: input.linkUrl,
    buttonLabel: input.buttonLabel,
  });
  const empty = dm.text.trim() === "" && !dm.button;
  const link: ThreadMessage[] = empty
    ? []
    : [{ from: "account", text: dm.text, button: dm.button?.title }];
  if (!input.gate.enabled) return link;
  const label = input.gate.buttonLabel.trim() || messages.builder.gateDefaultButton;
  return [
    { from: "account", text: renderDm(input.gate.message), button: label },
    { from: "person", text: label },
    ...link,
  ];
}
