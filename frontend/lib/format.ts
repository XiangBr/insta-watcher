const MINUTE = 60;
const HOUR = 60 * MINUTE;
const DAY = 24 * HOUR;

/** "agora", "há 5 min", "há 3 h", "há 2 dias". Future times read as "agora". */
export function relativeTime(iso: string | null | undefined, now: Date = new Date()): string {
  if (!iso) return "nunca";
  const seconds = Math.floor((now.getTime() - new Date(iso).getTime()) / 1000);
  if (Number.isNaN(seconds)) return "nunca";
  if (seconds < 45) return "agora";
  if (seconds < HOUR) return `há ${Math.max(1, Math.round(seconds / MINUTE))} min`;
  if (seconds < DAY) return `há ${Math.round(seconds / HOUR)} h`;
  const days = Math.round(seconds / DAY);
  return days === 1 ? "há 1 dia" : `há ${days} dias`;
}

export function dateTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleString("pt-BR", { dateStyle: "short", timeStyle: "short" });
}

export function dayLabel(isoDay: string): string {
  const [year, month, day] = isoDay.split("-");
  return `${day}/${month}/${year}`;
}

/** Seconds until a point in time, for the "retry in" hints. Never negative. */
export function secondsUntil(iso: string, now: Date = new Date()): number {
  return Math.max(0, Math.ceil((new Date(iso).getTime() - now.getTime()) / 1000));
}

/** "em 5 min", "em 2 h", "em 1 dia" for something scheduled. Past or now reads as "agora". */
export function timeUntil(iso: string, now: Date = new Date()): string {
  const seconds = Math.floor((new Date(iso).getTime() - now.getTime()) / 1000);
  if (Number.isNaN(seconds) || seconds < 45) return "agora";
  if (seconds < HOUR) return `em ${Math.max(1, Math.round(seconds / MINUTE))} min`;
  if (seconds < DAY) return `em ${Math.round(seconds / HOUR)} h`;
  const days = Math.round(seconds / DAY);
  return days === 1 ? "em 1 dia" : `em ${days} dias`;
}
