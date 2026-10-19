"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { messages } from "@/lib/messages";

const t = messages.chat;
const tabs = [
  { href: "/chat", label: t.tabs.inbox },
  { href: "/chat/flows", label: t.tabs.flows },
  { href: "/chat/ai", label: t.tabs.ai },
] as const;

export function ChatTabs() {
  const pathname = usePathname();
  return (
    <div className="mt-4 flex gap-1 border-b border-line">
      {tabs.map(({ href, label }) => {
        const current = href === "/chat" ? pathname === "/chat" : pathname.startsWith(href);
        return (
          <Link
            key={href}
            href={href}
            aria-current={current ? "page" : undefined}
            className={`-mb-px border-b-2 px-4 py-2.5 text-sm font-semibold transition-colors ${
              current ? "border-accent text-accent" : "border-transparent text-muted hover:text-fg"
            }`}
          >
            {label}
          </Link>
        );
      })}
    </div>
  );
}
