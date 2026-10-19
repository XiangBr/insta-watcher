import { emptyFlow } from "@/lib/chat";
import { requireSession } from "@/lib/dal";
import { FlowEditor } from "../flow-editor";

export default async function NewFlowPage() {
  await requireSession();
  return <FlowEditor mode="create" initial={emptyFlow()} />;
}
