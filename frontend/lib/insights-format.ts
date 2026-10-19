// Number formats of the insights page, in pt-BR.

const compactFormat = new Intl.NumberFormat("pt-BR", {
  notation: "compact",
  maximumFractionDigits: 1,
});
const wholeFormat = new Intl.NumberFormat("pt-BR", { maximumFractionDigits: 0 });

/** 1234 -> "1,2 mil". Small numbers stay whole. */
export function compact(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return "—";
  return Math.abs(value) < 10_000 ? wholeFormat.format(value) : compactFormat.format(value);
}

/** 0.0534 -> "5,3%". */
export function percent(value: number | null | undefined, digits = 1): string {
  if (value == null || !Number.isFinite(value)) return "—";
  return `${(value * 100).toLocaleString("pt-BR", { maximumFractionDigits: digits })}%`;
}

/** Milliseconds as "12,4 s". */
export function seconds(ms: number | null | undefined): string {
  if (ms == null || !Number.isFinite(ms)) return "—";
  return `${(ms / 1000).toLocaleString("pt-BR", { maximumFractionDigits: 1 })} s`;
}

/** How a post metric reads: rates as percent, watch time in seconds, the rest compact. */
export function formatMetric(metric: string, value: number | null | undefined): string {
  if (metric === "engagement") return percent(value);
  if (metric === "ig_reels_avg_watch_time") return seconds(value);
  return compact(value);
}

/** Change from previous to current, as a percent. null when there is nothing to compare. */
export function changePct(current: number | null, previous: number | null): number | null {
  if (current == null || previous == null || previous <= 0) return null;
  return ((current - previous) / previous) * 100;
}

/** "02/10" from "2026-10-02". */
export function shortDay(isoDay: string): string {
  const [, month, day] = isoDay.split("-");
  return `${day}/${month}`;
}

/** "BR" -> "Brasil". Unknown codes stay as they are. */
export function countryName(code: string): string {
  try {
    return new Intl.DisplayNames(["pt-BR"], { type: "region" }).of(code) ?? code;
  } catch {
    return code;
  }
}
