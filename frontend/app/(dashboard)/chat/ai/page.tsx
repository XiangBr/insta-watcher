import { PageHeader } from "@/components/ui";
import { requireSession } from "@/lib/dal";
import { messages } from "@/lib/messages";
import { ChatTabs } from "../chat-tabs";
import { AiView } from "./ai-view";

export default async function ChatAiPage() {
  await requireSession();
  return (
    <div>
      <PageHeader title={messages.chat.title} />
      <ChatTabs />
      <AiView />
    </div>
  );
}
