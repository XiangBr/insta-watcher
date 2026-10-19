// Light, dark, or follow the system. The choice lives in a cookie, so the server renders the
// right theme on the first paint and no inline script is needed (the CSP forbids one anyway).

export type Theme = "light" | "dark" | "system";

export const THEME_COOKIE = "iw_theme";
export const THEME_MAX_AGE_SECONDS = 365 * 24 * 60 * 60;
const THEMES: readonly Theme[] = ["light", "dark", "system"];

/** Anything that is not a known theme means "system", so a bad cookie never breaks the page. */
export function parseTheme(value: string | undefined | null): Theme {
  return THEMES.find((theme) => theme === value) ?? "system";
}

/** The data-theme attribute. Absent for "system", so the media query decides. */
export function themeAttribute(theme: Theme): "light" | "dark" | undefined {
  return theme === "system" ? undefined : theme;
}

/** Browser only: paints the theme now and remembers it for the server's next render. */
export function applyTheme(theme: Theme): void {
  const root = document.documentElement;
  const attribute = themeAttribute(theme);
  if (attribute) root.dataset.theme = attribute;
  else delete root.dataset.theme;
  document.cookie = themeCookie(theme, window.location.protocol === "https:");
}

export function themeCookie(theme: Theme, secure: boolean): string {
  return `${THEME_COOKIE}=${theme}; path=/; max-age=${THEME_MAX_AGE_SECONDS}; samesite=lax${
    secure ? "; secure" : ""
  }`;
}
