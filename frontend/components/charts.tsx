"use client";

import { useId, useState, useSyncExternalStore, type ReactNode } from "react";
import { messages } from "@/lib/messages";

// Small single-series charts drawn in SVG and CSS, so the dashboard needs no chart package.
// One hue (the accent) everywhere: these charts show magnitude, never identity. Each one has
// a readout line that names the hovered (or last) value, so nothing depends on color alone.

// The CSP has no 'unsafe-inline' for styles, so a style attribute in server-rendered HTML is
// dropped by the browser, and hydration does not put it back. Bar sizes are set as inline
// styles, so the charts render only in the browser, where React sets them through the CSSOM.
const noop = () => () => {};
function useInBrowser(): boolean {
  return useSyncExternalStore(
    noop,
    () => true,
    () => false,
  );
}

/** Holds the space until the browser draws the chart. Classes only: see useInBrowser. */
function Placeholder() {
  return <div aria-hidden className="h-32 animate-pulse rounded-lg bg-surface-2" />;
}

export type Point = { label: string; value: number };
type Format = (value: number) => string;

function Readout({ children }: { children: ReactNode }) {
  return (
    <p aria-live="polite" className="min-h-5 text-xs text-muted tabular-nums">
      {children}
    </p>
  );
}

function Empty() {
  return <p className="py-8 text-center text-sm text-muted">{messages.insights.chart.noData}</p>;
}

/** A line over time. Hover (or touch) moves a crosshair and the readout follows it. */
export function LineChart({
  points,
  format,
  label,
  height = 160,
}: {
  points: readonly Point[];
  format: Format;
  label: string;
  height?: number;
}) {
  const [active, setActive] = useState<number | null>(null);
  const gradient = useId();
  const inBrowser = useInBrowser();
  if (points.length === 0) return <Empty />;
  if (!inBrowser) return <Placeholder />;
  const width = 600;
  const pad = { top: 8, bottom: 4 };
  const values = points.map((p) => p.value);
  const min = Math.min(...values);
  const max = Math.max(...values);
  const span = max - min || 1;
  const x = (i: number) => (points.length === 1 ? width / 2 : (i / (points.length - 1)) * width);
  const y = (v: number) => pad.top + (1 - (v - min) / span) * (height - pad.top - pad.bottom);
  const path = points.map((p, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(p.value).toFixed(1)}`).join("");
  const area = `${path}L${x(points.length - 1)},${height}L${x(0)},${height}Z`;
  const shown = points[active ?? points.length - 1];

  function onMove(event: React.PointerEvent<SVGSVGElement>) {
    const rect = event.currentTarget.getBoundingClientRect();
    const ratio = (event.clientX - rect.left) / rect.width;
    setActive(Math.max(0, Math.min(points.length - 1, Math.round(ratio * (points.length - 1)))));
  }

  return (
    <figure>
      <Readout>{shown ? `${shown.label}: ${format(shown.value)}` : null}</Readout>
      <div className="relative mt-1">
        <svg
          role="img"
          aria-label={`${label}: de ${format(points[0]?.value ?? 0)} a ${format(points[points.length - 1]?.value ?? 0)}`}
          viewBox={`0 0 ${width} ${height}`}
          preserveAspectRatio="none"
          className="block w-full touch-none"
          style={{ height }}
          onPointerMove={onMove}
          onPointerDown={onMove}
          onPointerLeave={() => setActive(null)}
        >
          <defs>
            <linearGradient id={gradient} x1="0" x2="0" y1="0" y2="1">
              <stop offset="0%" stopColor="var(--accent)" stopOpacity="0.18" />
              <stop offset="100%" stopColor="var(--accent)" stopOpacity="0" />
            </linearGradient>
          </defs>
          <line x1="0" x2={width} y1={height - 0.5} y2={height - 0.5} stroke="var(--line)" vectorEffect="non-scaling-stroke" />
          <path d={area} fill={`url(#${gradient})`} />
          <path d={path} fill="none" stroke="var(--accent)" strokeWidth="2" vectorEffect="non-scaling-stroke" strokeLinejoin="round" />
          {active != null ? (
            <line
              x1={x(active)}
              x2={x(active)}
              y1="0"
              y2={height}
              stroke="var(--muted)"
              strokeDasharray="3 3"
              vectorEffect="non-scaling-stroke"
            />
          ) : null}
        </svg>
        {active != null && points[active] ? (
          <span
            aria-hidden
            className="pointer-events-none absolute size-2.5 -translate-x-1/2 -translate-y-1/2 rounded-full border-2 border-surface bg-accent"
            style={{ left: `${(x(active) / width) * 100}%`, top: y(points[active].value) }}
          />
        ) : null}
      </div>
      <div className="mt-1 flex justify-between text-xs text-muted">
        <span>{points[0]?.label}</span>
        <span>
          {format(min)} – {format(max)}
        </span>
        <span>{points[points.length - 1]?.label}</span>
      </div>
    </figure>
  );
}

/** Vertical bars, e.g. per hour or per day. `strong` marks the bars worth pointing at. */
export function Columns({
  items,
  format,
  label,
  height = 120,
  strong,
  faint,
  tick = 1,
}: {
  items: readonly (Point & { hint?: string })[];
  format: Format;
  label: string;
  height?: number;
  strong?: (index: number) => boolean;
  /** Bars drawn lighter, e.g. groups too small to trust. */
  faint?: (index: number) => boolean;
  /** Show every n-th label under the bars. */
  tick?: number;
}) {
  const [active, setActive] = useState<number | null>(null);
  const inBrowser = useInBrowser();
  if (items.length === 0 || items.every((i) => i.value === 0)) return <Empty />;
  if (!inBrowser) return <Placeholder />;
  const max = Math.max(...items.map((i) => i.value)) || 1;
  // At rest the readout names the highlighted bar, or else the biggest one.
  const resting = items.findIndex((_, i) => strong?.(i));
  const peak = items.reduce((best, item, i) => (item.value > (items[best]?.value ?? 0) ? i : best), 0);
  const shown = items[active ?? (resting >= 0 ? resting : peak)];
  return (
    <figure>
      <Readout>
        {shown ? `${shown.label}: ${format(shown.value)}${shown.hint ? ` · ${shown.hint}` : ""}` : null}
      </Readout>
      <div
        role="img"
        aria-label={label}
        className="mt-2 flex items-end gap-[2px]"
        style={{ height }}
        onPointerLeave={() => setActive(null)}
      >
        {items.map((item, i) => (
          <div
            key={item.label}
            className="flex h-full min-w-0 flex-1 items-end"
            onPointerEnter={() => setActive(i)}
            onPointerDown={() => setActive(i)}
          >
            <div
              className={`w-full rounded-t-[4px] transition-opacity ${
                strong?.(i) ? "bg-accent" : faint?.(i) ? "bg-accent/20" : "bg-accent/50"
              } ${active != null && active !== i ? "opacity-60" : ""}`}
              style={{ height: `${Math.max(item.value > 0 ? 3 : 0, (item.value / max) * 100)}%` }}
            />
          </div>
        ))}
      </div>
      <div className="mt-1 flex gap-[2px] text-[10px] text-muted">
        {items.map((item, i) => (
          <span key={item.label} className="min-w-0 flex-1 overflow-hidden text-center whitespace-nowrap">
            {i % tick === 0 ? item.label : ""}
          </span>
        ))}
      </div>
    </figure>
  );
}

/** Horizontal bars with the label and value as text: for rankings and breakdowns. */
export function BarList({
  items,
  format,
  max,
}: {
  items: readonly { key: string; label: ReactNode; value: number; sub?: ReactNode }[];
  format: Format;
  max?: number;
}) {
  const inBrowser = useInBrowser();
  if (items.length === 0) return <Empty />;
  if (!inBrowser) return <Placeholder />;
  const top = max ?? Math.max(...items.map((i) => i.value), 0);
  return (
    <ul className="space-y-3">
      {items.map((item) => (
        <li key={item.key}>
          <div className="flex items-baseline justify-between gap-3 text-sm">
            <span className="min-w-0 truncate">{item.label}</span>
            <span className="shrink-0 font-semibold tabular-nums">{format(item.value)}</span>
          </div>
          <div className="mt-1.5 h-2 overflow-hidden rounded-full bg-surface-2">
            <div
              className="h-full rounded-full bg-accent"
              style={{ width: `${top > 0 ? Math.max(1.5, (item.value / top) * 100) : 0}%` }}
            />
          </div>
          {item.sub ? <p className="mt-1 text-xs text-muted">{item.sub}</p> : null}
        </li>
      ))}
    </ul>
  );
}

/** Weekday (rows) by hour (columns). Darker is more. */
export function Heatmap({
  grid,
  rows,
  format,
  label,
}: {
  grid: readonly (readonly number[])[];
  rows: readonly string[];
  format: Format;
  label: string;
}) {
  const [active, setActive] = useState<{ r: number; c: number } | null>(null);
  const inBrowser = useInBrowser();
  const max = Math.max(0, ...grid.flat());
  if (max === 0) return <Empty />;
  if (!inBrowser) return <Placeholder />;
  let busiest = { r: 0, c: 0 };
  grid.forEach((row, r) =>
    row.forEach((count, c) => {
      if (count > (grid[busiest.r]?.[busiest.c] ?? 0)) busiest = { r, c };
    }),
  );
  // At rest the readout names the busiest cell.
  const cell = active ?? busiest;
  const value = grid[cell.r]?.[cell.c] ?? 0;
  return (
    <figure>
      <Readout>{`${rows[cell.r]}, ${cell.c}h: ${format(value)}`}</Readout>
      <div
        role="img"
        aria-label={label}
        className="mt-2 grid gap-[2px]"
        style={{ gridTemplateColumns: "2.25rem repeat(24, minmax(0, 1fr))" }}
        onPointerLeave={() => setActive(null)}
      >
        {grid.map((row, r) => (
          <div key={rows[r]} className="contents">
            <span className="pr-1 text-[11px] leading-none text-muted self-center">{rows[r]}</span>
            {row.map((count, c) => (
              <span
                key={c}
                onPointerEnter={() => setActive({ r, c })}
                onPointerDown={() => setActive({ r, c })}
                className={`aspect-square rounded-[3px] ${
                  active && active.r === r && active.c === c ? "ring-2 ring-fg" : ""
                }`}
                style={{
                  background:
                    count === 0
                      ? "var(--surface-2)"
                      : `color-mix(in oklab, var(--accent) ${Math.round(18 + (count / max) * 82)}%, var(--surface-2))`,
                }}
              />
            ))}
          </div>
        ))}
        <span />
        {Array.from({ length: 24 }, (_, h) => (
          <span key={h} className="text-center text-[10px] text-muted">
            {h % 3 === 0 ? h : ""}
          </span>
        ))}
      </div>
    </figure>
  );
}

/** Two shares of one whole as a single split bar, labeled on both sides. */
export function SplitBar({
  left,
  right,
}: {
  left: { label: string; value: number };
  right: { label: string; value: number };
}) {
  const inBrowser = useInBrowser();
  const total = left.value + right.value;
  if (total <= 0) return <Empty />;
  if (!inBrowser) return <Placeholder />;
  const pct = (left.value / total) * 100;
  return (
    <div>
      <div className="flex h-3 gap-[2px] overflow-hidden rounded-full">
        <div className="h-full bg-accent" style={{ width: `${pct}%` }} />
        <div className="h-full flex-1 bg-accent/35" />
      </div>
      <div className="mt-2 flex justify-between gap-4 text-sm">
        <span>
          <span className="font-semibold tabular-nums">{Math.round(pct)}%</span>{" "}
          <span className="text-muted">{left.label}</span>
        </span>
        <span className="text-right">
          <span className="font-semibold tabular-nums">{Math.round(100 - pct)}%</span>{" "}
          <span className="text-muted">{right.label}</span>
        </span>
      </div>
    </div>
  );
}
