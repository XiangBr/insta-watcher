import { normalizeForSearch } from "@/lib/search";
import type { components } from "@/lib/api-types";

export type Campaign = components["schemas"]["CampaignOut"];

export type TriggerFilter = "all" | "comments" | "stories" | "messages" | "gate";
export type StateFilter = "all" | "active" | "paused";
export type ListFilters = { query: string; trigger: TriggerFilter; state: StateFilter };

export const NO_FILTERS: ListFilters = { query: "", trigger: "all", state: "all" };

/** What starts a campaign, in the order the list shows it. */
export type TriggerLine =
  | { kind: "comments"; postId: string | null }
  | { kind: "stories" }
  | { kind: "messages" }
  | { kind: "gate" };

export function triggerLines(c: Campaign): TriggerLine[] {
  const lines: TriggerLine[] = [];
  if (c.trigger_comments) lines.push({ kind: "comments", postId: c.match_any_post ? null : c.post_id });
  if (c.trigger_story_replies) lines.push({ kind: "stories" });
  if (c.trigger_messages) lines.push({ kind: "messages" });
  if (c.follow_gate_enabled) lines.push({ kind: "gate" });
  return lines;
}

function matchesQuery(c: Campaign, query: string): boolean {
  const wanted = normalizeForSearch(query);
  if (!wanted) return true;
  return [c.name, ...c.keywords].some((text) => normalizeForSearch(text).includes(wanted));
}

function matchesTrigger(c: Campaign, trigger: TriggerFilter): boolean {
  switch (trigger) {
    case "comments":
      return c.trigger_comments;
    case "stories":
      return c.trigger_story_replies;
    case "messages":
      return c.trigger_messages;
    case "gate":
      return c.follow_gate_enabled;
    default:
      return true;
  }
}

function matchesState(c: Campaign, state: StateFilter): boolean {
  return state === "all" || (state === "active") === c.is_active;
}

export function filterCampaigns(campaigns: readonly Campaign[], filters: ListFilters): Campaign[] {
  return campaigns.filter(
    (c) =>
      matchesQuery(c, filters.query) &&
      matchesTrigger(c, filters.trigger) &&
      matchesState(c, filters.state),
  );
}

/** Most recently changed first, which is what a person comes back to. */
export function sortByRecent(campaigns: readonly Campaign[]): Campaign[] {
  return [...campaigns].sort(
    (a, b) => new Date(b.updated_at).getTime() - new Date(a.updated_at).getTime() || b.id - a.id,
  );
}

export function isFiltered(filters: ListFilters): boolean {
  return filters.query.trim() !== "" || filters.trigger !== "all" || filters.state !== "all";
}
