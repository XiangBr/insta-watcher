import { notFound } from "next/navigation";
import { serverApi } from "@/lib/api.server";
import { fromFlow } from "@/lib/chat";
import { requireSession } from "@/lib/dal";
import { FlowEditor } from "../flow-editor";

export default async function EditFlowPage({ params }: { params: Promise<{ id: string }> }) {
  await requireSession();
  const { id } = await params;
  if (!/^\d{1,9}$/.test(id)) notFound();

  const { data, response } = await serverApi().GET("/api/v1/chat/flows/{flow_id}", {
    params: { path: { flow_id: Number(id) } },
  });
  if (response.status === 404) notFound();
  if (!data) throw new Error(`flow ${id}: API answered ${response.status}`);

  return <FlowEditor mode="edit" flowId={data.id} initial={fromFlow(data)} />;
}
