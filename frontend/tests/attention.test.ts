import { describe, expect, it } from "vitest";
import { attentionItems, type Status } from "@/lib/attention";

const NOW = new Date("2026-10-02T12:00:00Z");

const healthy: Status = {
  account: {
    username: "dono",
    ig_user_id: "1",
    status: "ACTIVE",
    sending_paused: false,
    token_days_left: 40,
    token_expiry_confirmed: true,
  },
  active_campaigns: 2,
  last_webhook_at: "2026-10-02T11:00:00Z",
  pending_webhook_events: 0,
  jobs: { pending: 0, running: 0, unconfirmed: 0, failed: 0 },
  rate_limit: { used: 3, limit: 750 },
  unresolved_errors: 0,
};

const ids = (status: Status) => attentionItems(status, NOW).map((i) => i.id);

describe("attentionItems", () => {
  it("is empty when everything is fine", () => {
    expect(ids(healthy)).toEqual([]);
  });

  it("asks for a token when there is no account, and nothing else about the account", () => {
    expect(ids({ ...healthy, account: null, active_campaigns: 0 })).toEqual(["no-account"]);
  });

  it("flags a rejected token and a pause", () => {
    const status = {
      ...healthy,
      account: { ...healthy.account!, status: "TOKEN_INVALID", sending_paused: true },
    };
    expect(ids(status)).toEqual(["invalid", "paused"]);
  });

  it("warns about the token 7 days out and escalates under 3", () => {
    const at = (days: number) =>
      attentionItems({ ...healthy, account: { ...healthy.account!, token_days_left: days } }, NOW);
    expect(at(7)).toEqual([]);
    expect(at(6)[0]).toMatchObject({ id: "token", tone: "warn" });
    expect(at(2)[0]).toMatchObject({ id: "token", tone: "danger" });
    expect(at(0)[0]?.text).toMatch(/hoje/);
  });

  it("sends unconfirmed and failed deliveries to the filtered list", () => {
    const items = attentionItems({ ...healthy, jobs: { ...healthy.jobs, unconfirmed: 2, failed: 1 } }, NOW);
    expect(items.map((i) => [i.id, i.href])).toEqual([
      ["unconfirmed", "/logs?status=UNCONFIRMED"],
      ["failed", "/logs?status=FAILED"],
    ]);
    expect(items[0]?.text).toMatch(/caixa de entrada/);
  });

  it("puts urgent items first", () => {
    const status: Status = {
      ...healthy,
      unresolved_errors: 1,
      jobs: { ...healthy.jobs, unconfirmed: 1 },
    };
    expect(ids(status)).toEqual(["unconfirmed", "errors"]);
  });

  it("flags a silent webhook only while a campaign is active", () => {
    const silent = { ...healthy, last_webhook_at: "2026-09-28T12:00:00Z" };
    expect(ids(silent)).toEqual(["webhook"]);
    expect(ids({ ...silent, active_campaigns: 0 })).toEqual([]);
    expect(ids({ ...healthy, last_webhook_at: null })).toEqual(["webhook"]);
    expect(attentionItems({ ...healthy, last_webhook_at: null }, NOW)[0]?.text).toMatch(/ainda/);
  });

  it("warns near the hourly limit", () => {
    expect(ids({ ...healthy, rate_limit: { used: 675, limit: 750 } })).toEqual(["limit"]);
    expect(ids({ ...healthy, rate_limit: { used: 674, limit: 750 } })).toEqual([]);
  });
});
