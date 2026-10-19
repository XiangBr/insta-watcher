import { requireSession } from "@/lib/dal";
import { CompetitorsView } from "./competitors-view";

export default async function CompetitorsPage() {
  await requireSession();
  return <CompetitorsView />;
}
