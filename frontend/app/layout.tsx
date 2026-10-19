import type { Metadata } from "next";
import { Plus_Jakarta_Sans } from "next/font/google";
import { cookies } from "next/headers";
import { messages } from "@/lib/messages";
import { THEME_COOKIE, parseTheme, themeAttribute } from "@/lib/theme";
import "./globals.css";

// Self-hosted by Next at build time, so the CSP keeps font-src 'self'.
const sans = Plus_Jakarta_Sans({
  subsets: ["latin", "latin-ext"],
  variable: "--font-jakarta",
  display: "swap",
});

export const metadata: Metadata = {
  title: messages.appName,
  robots: { index: false, follow: false },
};

// Every page carries a per-request CSP nonce (proxy.ts), so none can be prerendered.
export const dynamic = "force-dynamic";

export default async function RootLayout({ children }: { children: React.ReactNode }) {
  // The theme comes from a cookie, so the server paints the right one from the first byte.
  const theme = parseTheme((await cookies()).get(THEME_COOKIE)?.value);
  return (
    <html lang="pt-BR" className={sans.variable} data-theme={themeAttribute(theme)}>
      <body className="min-h-screen antialiased">{children}</body>
    </html>
  );
}
