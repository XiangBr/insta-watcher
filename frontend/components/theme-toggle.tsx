"use client";

import { useState } from "react";
import { messages } from "@/lib/messages";
import { applyTheme, type Theme } from "@/lib/theme";
import { MonitorIcon, MoonIcon, SunIcon } from "@/components/icons";

const OPTIONS: { value: Theme; label: string; icon: React.ReactNode }[] = [
  { value: "light", label: messages.theme.light, icon: <SunIcon size={16} /> },
  { value: "dark", label: messages.theme.dark, icon: <MoonIcon size={16} /> },
  { value: "system", label: messages.theme.system, icon: <MonitorIcon size={16} /> },
];

/** Switches the theme at once (the attribute) and remembers it (the cookie the server reads). */
export function ThemeToggle({ initial }: { initial: Theme }) {
  const [theme, setTheme] = useState<Theme>(initial);

  function choose(next: Theme) {
    setTheme(next);
    applyTheme(next);
  }

  return (
    <div
      role="radiogroup"
      aria-label={messages.theme.label}
      className="flex rounded-lg bg-surface-2 p-0.5"
    >
      {OPTIONS.map((option) => (
        <button
          key={option.value}
          type="button"
          role="radio"
          aria-checked={theme === option.value}
          aria-label={option.label}
          title={option.label}
          onClick={() => choose(option.value)}
          className={`grid h-8 flex-1 place-items-center rounded-md transition-colors ${
            theme === option.value
              ? "bg-surface text-fg shadow-card"
              : "text-muted hover:text-fg"
          }`}
        >
          {option.icon}
        </button>
      ))}
    </div>
  );
}
