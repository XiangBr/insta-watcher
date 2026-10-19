import { PageHeader } from "@/components/ui";
import { requireSession } from "@/lib/dal";
import { messages } from "@/lib/messages";
import { ChatTabs } from "../chat-tabs";
import { FlowsView } from "./flows-view";

export default async function FlowsPage() {
  await requireSession();
  return (
    <div>
      <PageHeader title={messages.chat.title} />
      <ChatTabs />
      <FlowsView />
    </div>
  );
}
