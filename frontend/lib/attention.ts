import type { components } from "@/lib/api-types";
import { relativeTime } from "@/lib/format";
import { messages } from "@/lib/messages";

export type Status = components["schemas"]["StatusOut"];
export type AttentionItem = { id: string; tone: "danger" | "warn"; text: string; href: string };

const t = messages.overview.attention;
const TOKEN_WARNING_DAYS = 7;
const NEAR_LIMIT = 0.9;
const WEBHOOK_SILENCE_MS = 3 * 24 * 60 * 60 * 1000;

/**
 * What the owner should look at, most urgent first. A silent webhook only counts while a
 * campaign is active: with nothing to answer, silence is normal.
 */
export function attentionItems(status: Status, now: Date = new Date()): AttentionItem[] {
  const items: AttentionItem[] = [];
  const { account, jobs } = status;

  if (account === null || account === undefined) {
    items.push({ id: "no-account", tone: "warn", text: t.noAccount, href: "/settings" });
  } else {
    if (account.status === "TOKEN_INVALID") {
      items.push({ id: "invalid", tone: "danger", text: t.invalid, href: "/settings" });
    }
    if (account.sending_paused) {
      items.push({ id: "paused", tone: "warn", text: t.paused, href: "/settings" });
    }
    const days = account.token_days_left;
    if (days !== null && days !== undefined && days < TOKEN_WARNING_DAYS) {
      items.push({
        id: "token",
        tone: days < 3 ? "danger" : "warn",
        text: t.tokenExpiring(days),
        href: "/settings",
      });
    }
  }
  if (jobs.unconfirmed > 0) {
    items.push({
      id: "unconfirmed",
      tone: "danger",
      text: t.unconfirmed(jobs.unconfirmed),
      href: "/logs?status=UNCONFIRMED",
    });
  }
  if (jobs.failed > 0) {
    items.push({ id: "failed", tone: "warn", text: t.failed(jobs.failed), href: "/logs?status=FAILED" });
  }
  if (status.unresolved_errors > 0) {
    items.push({
      id: "errors",
      tone: "warn",
      text: t.errors(status.unresolved_errors),
      href: "/diagnostics",
    });
  }
  if (status.active_campaigns > 0 && account) {
    const last = status.last_webhook_at ? new Date(status.last_webhook_at).getTime() : null;
    if (last === null || now.getTime() - last > WEBHOOK_SILENCE_MS) {
      const since = last === null ? "ainda" : relativeTime(status.last_webhook_at, now);
      items.push({ id: "webhook", tone: "warn", text: t.noWebhook(since), href: "/settings" });
    }
  }
  const { used, limit } = status.rate_limit;
  if (used >= limit * NEAR_LIMIT) {
    items.push({ id: "limit", tone: "warn", text: t.nearLimit(used, limit), href: "/diagnostics" });
  }
  return items.sort((a, b) => Number(b.tone === "danger") - Number(a.tone === "danger"));
}
