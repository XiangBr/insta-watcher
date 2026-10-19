"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  ActivityIcon,
  BoltIcon,
  ChartIcon,
  ChatIcon,
  HomeIcon,
  SendIcon,
  SlidersIcon,
  TargetIcon,
} from "@/components/icons";
import { messages } from "@/lib/messages";

const items = [
  { href: "/", label: messages.nav.overview, icon: HomeIcon },
  { href: "/insights", label: messages.nav.insights, icon: ChartIcon },
  { href: "/competitors", label: messages.nav.competitors, icon: TargetIcon },
  { href: "/chat", label: messages.nav.chat, icon: ChatIcon },
  { href: "/campaigns", label: messages.nav.campaigns, icon: BoltIcon },
  { href: "/logs", label: messages.nav.deliveries, icon: SendIcon },
  { href: "/settings", label: messages.nav.settings, icon: SlidersIcon },
  { href: "/diagnostics", label: messages.nav.diagnostics, icon: ActivityIcon },
] as const;

export function Nav() {
  const pathname = usePathname();
  return (
    <nav aria-label={messages.nav.label} className="flex gap-1 overflow-x-auto md:flex-col">
      {items.map(({ href, label, icon: Icon }) => {
        const current = href === "/" ? pathname === "/" : pathname.startsWith(href);
        return (
          <Link
            key={href}
            href={href}
            aria-current={current ? "page" : undefined}
            className={`flex items-center gap-3 whitespace-nowrap rounded-lg px-3 py-2.5 text-sm transition-colors ${
              current
                ? "bg-accent-soft font-semibold text-accent"
                : "text-muted hover:bg-surface-2 hover:text-fg"
            }`}
          >
            <Icon size={19} />
            <span>{label}</span>
          </Link>
        );
      })}
    </nav>
  );
}
