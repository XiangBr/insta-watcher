import { PageHeader } from "@/components/ui";
import { requireSession } from "@/lib/dal";
import { messages } from "@/lib/messages";
import { ChatTabs } from "./chat-tabs";
import { InboxView } from "./inbox-view";

export default async function ChatPage() {
  await requireSession();
  return (
    <div>
      <PageHeader title={messages.chat.title} />
      <ChatTabs />
      <InboxView />
    </div>
  );
}
