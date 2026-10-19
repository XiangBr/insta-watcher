import { serverApi } from "@/lib/api.server";
import { requireSession } from "@/lib/dal";
import { ConfigError } from "@/lib/env";
import type { Status } from "@/lib/attention";
import { Overview } from "./overview";

async function loadStatus(): Promise<Status | null> {
  try {
    const { data } = await serverApi().GET("/api/v1/status");
    return data ?? null;
  } catch (error) {
    console.error(
      "overview: status failed:",
      error instanceof ConfigError ? error.message : "request failed",
    );
    return null;
  }
}

export default async function OverviewPage() {
  await requireSession();
  // Server-rendered first paint; the client component keeps it fresh every 10 s.
  return <Overview initial={await loadStatus()} />;
}
