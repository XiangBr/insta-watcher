import { cookies } from "next/headers";
import { LogoutIcon } from "@/components/icons";
import { Brand } from "@/components/brand";
import { Nav } from "@/components/nav";
import { ThemeToggle } from "@/components/theme-toggle";
import { requireSession } from "@/lib/dal";
import { messages } from "@/lib/messages";
import { THEME_COOKIE, parseTheme } from "@/lib/theme";

export default async function DashboardLayout({ children }: { children: React.ReactNode }) {
  await requireSession();
  const theme = parseTheme((await cookies()).get(THEME_COOKIE)?.value);
  return (
    <div className="min-h-screen md:flex">
      <aside className="border-b border-line bg-surface md:sticky md:top-0 md:h-screen md:w-60 md:shrink-0 md:border-r md:border-b-0">
        <div className="flex flex-wrap items-center justify-between gap-x-3 gap-y-2 px-3 py-3 md:h-full md:flex-col md:flex-nowrap md:items-stretch md:justify-start md:gap-6 md:px-4 md:py-5">
          <Brand />
          <div className="order-last w-full min-w-0 md:order-none md:w-auto md:flex-none">
            <Nav />
          </div>
          <div className="flex items-center gap-2 md:mt-auto md:flex-col md:items-stretch md:gap-3">
            <ThemeToggle initial={theme} />
            <form action="/api/logout" method="post">
              <button
                type="submit"
                className="flex w-full items-center gap-3 rounded-lg px-3 py-2 text-sm text-muted transition-colors hover:bg-surface-2 hover:text-fg"
              >
                <LogoutIcon size={18} />
                <span className="max-md:sr-only">{messages.nav.logout}</span>
              </button>
            </form>
          </div>
        </div>
      </aside>
      <main className="min-w-0 flex-1 px-4 py-8 md:px-10 md:py-10">
        <div className="mx-auto max-w-5xl">{children}</div>
      </main>
    </div>
  );
}
