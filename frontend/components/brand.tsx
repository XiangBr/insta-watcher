import { EyeIcon } from "@/components/icons";
import { messages } from "@/lib/messages";

export function Brand() {
  return (
    <div className="flex items-center gap-2.5 px-1">
      <span className="grid size-8 place-items-center rounded-lg bg-accent text-accent-fg">
        <EyeIcon size={18} />
      </span>
      <span className="text-base font-bold tracking-tight">{messages.appName}</span>
    </div>
  );
}
