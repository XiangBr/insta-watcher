import { requireSession } from "@/lib/dal";
import { DiagnosticsView } from "./diagnostics-view";

export default async function DiagnosticsPage() {
  await requireSession();
  return <DiagnosticsView />;
}
