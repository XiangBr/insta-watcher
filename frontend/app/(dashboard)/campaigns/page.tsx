import { requireSession } from "@/lib/dal";
import { messages } from "@/lib/messages";
import { CampaignsList } from "./campaigns-list";

const DONE_TEXT: Record<string, string> = {
  created: messages.builder.created,
  updated: messages.builder.updated,
};

export default async function CampaignsPage({
  searchParams,
}: {
  searchParams: Promise<{ done?: string }>;
}) {
  await requireSession();
  const { done } = await searchParams;
  return <CampaignsList initialNotice={done ? (DONE_TEXT[done] ?? null) : null} />;
}
