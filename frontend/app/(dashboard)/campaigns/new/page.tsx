import { requireSession } from "@/lib/dal";
import { addKeywords, emptyForm } from "@/lib/campaign-form";
import { CampaignForm } from "../campaign-form";

export default async function NewCampaignPage({
  searchParams,
}: {
  searchParams: Promise<{ [key: string]: string | string[] | undefined }>;
}) {
  await requireSession();
  // An idea from the competitors report arrives with its keyword: ?keyword=setup
  const keyword = (await searchParams).keyword;
  const initial =
    typeof keyword === "string" && keyword.trim()
      ? { ...emptyForm, keywords: addKeywords([], keyword.slice(0, 100), 1) }
      : emptyForm;
  return <CampaignForm mode="create" initial={initial} />;
}
