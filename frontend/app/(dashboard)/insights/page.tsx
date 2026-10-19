import { requireSession } from "@/lib/dal";
import { InsightsView } from "./insights-view";

export default async function InsightsPage() {
  await requireSession();
  return <InsightsView />;
}
