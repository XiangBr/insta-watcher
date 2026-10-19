import { requireSession } from "@/lib/dal";
import { SettingsView } from "./settings-view";

export default async function SettingsPage() {
  await requireSession();
  return <SettingsView />;
}
