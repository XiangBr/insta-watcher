import { notFound } from "next/navigation";
import { serverApi } from "@/lib/api.server";
import { fromCampaign } from "@/lib/campaign-form";
import { requireSession } from "@/lib/dal";
import { CampaignForm } from "../../campaign-form";

export default async function EditCampaignPage({ params }: { params: Promise<{ id: string }> }) {
  await requireSession();
  const { id } = await params;
  if (!/^\d{1,9}$/.test(id)) notFound();

  const { data, response } = await serverApi().GET("/api/v1/campaigns/{campaign_id}", {
    params: { path: { campaign_id: Number(id) } },
  });
  if (response.status === 404) notFound();
  if (!data) throw new Error(`campaign ${id}: API answered ${response.status}`);

  return <CampaignForm mode="edit" campaignId={data.id} initial={fromCampaign(data)} />;
}
