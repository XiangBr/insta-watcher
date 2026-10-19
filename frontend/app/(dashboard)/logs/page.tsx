import { requireSession } from "@/lib/dal";
import { LogsView } from "./logs-view";

const STATUSES = new Set(["PENDING", "SENT", "FAILED", "UNCONFIRMED"]);

export default async function LogsPage({
  searchParams,
}: {
  searchParams: Promise<{ status?: string; campaign?: string }>;
}) {
  await requireSession();
  const { status, campaign } = await searchParams;
  return (
    <LogsView
      initialStatus={status && STATUSES.has(status) ? status : ""}
      initialCampaign={campaign && /^\d{1,9}$/.test(campaign) ? campaign : ""}
    />
  );
}
