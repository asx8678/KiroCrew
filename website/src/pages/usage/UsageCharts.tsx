/**
 * The Usage page's charts. Hand-built from divs and CSS (`clip-path` traces, see
 * `components/charts/clipPath.ts`): the review gates refuse inline SVG and the
 * stack takes no chart library.
 *
 * Every chart keeps ONE selected mark, set by pointer hover, by tap and by
 * keyboard alike, and prints that mark's figures in a live readout beneath it.
 * A readout rather than a floating tooltip: it works the same on a phone, where
 * there is no hover, and it never covers the marks a reader is comparing. Every
 * value a mark encodes is also in its accessible label, and each chart's page
 * section offers the numbers as a table, since three light-mode category hues sit
 * under 3:1 on white.
 */
import { useState, type ComponentProps, type ReactNode } from 'react'
import type { UsageBucket, UsageCategory } from '../../api/client'
import Clickable from '../../components/Clickable'
import { areaStyle, bandStyle, pointsAt, strokeStyle, vertices } from '../../components/charts/clipPath'
import { fmtNumber, fmtWeekday } from '../../i18n/format'
import { i18nT } from '../../i18n/t'
import { CATEGORIES, CATEGORY_COLOR, categoryLabel, credits, parseDayKey } from './usageModel'

// ── shared pieces ────────────────────────────────────────────────────────────

/** A swatch + name (+ value) key, so identity is never carried by colour alone. */
export function Legend({ items }: { items: { key: string; label: string; color: string; value?: string }[] }) {
  return (
    <ul className="flex flex-wrap gap-x-4 gap-y-1.5 text-[12px] text-muted">
      {items.map(item => (
        <li key={item.key} className="flex items-center gap-1.5 min-w-0">
          <span aria-hidden="true" className="inline-block h-2.5 w-2.5 shrink-0 rounded-sm" style={{ background: item.color }} />
          <span className="truncate">{item.label}</span>
          {item.value && <span className="font-mono tabular-nums text-text">{item.value}</span>}
        </li>
      ))}
    </ul>
  )
}

/** The figures of the selected mark, announced as they change. */
function Readout({ children }: { children: ReactNode }) {
  return (
    <div aria-live="polite" className="mt-2 min-h-[20px] text-[12px] text-muted flex flex-wrap items-center gap-x-3 gap-y-1">
      {children}
    </div>
  )
}

/**
 * One cell of a heat scale from "nothing" to the peak: the chat hue mixed into the
 * card by the cell's share of the peak, so it follows the theme.
 */
function HeatCell({ value, peak, ...rest }: { value: number; peak: number } & ComponentProps<typeof Clickable>) {
  const pct = value > 0 && peak > 0 ? Math.round(18 + (value / peak) * 82) : 0
  return (
    <Clickable
      {...rest}
      style={pct > 0 ? { background: `color-mix(in srgb, var(--usage-cat-chat) ${pct}%, var(--card))` } : { background: 'var(--bg)' }}
    />
  )
}

// ── credits over time ────────────────────────────────────────────────────────

export type StackPoint = {
  key: string
  label: string
  tick: string
  total: number
  turns: number
  parts: Partial<Record<UsageCategory, number>>
}

/** How often to print an x tick so at most ~8 labels share the axis. */
function tickStride(n: number): number {
  return Math.max(1, Math.ceil(n / 8))
}

/**
 * Credits per hour/day/month as stacked columns, one segment per category in
 * the fixed category order (so a colour always sits beside the same neighbours,
 * the order its colour-blind check was run on). `singleColor` draws each column
 * as one segment instead, for a series with no category split (the months).
 */
export function StackedColumns({ points, height = 180, singleColor }: { points: StackPoint[]; height?: number; singleColor?: string }) {
  const lastWithSpend = points.reduce((acc, p, i) => (p.total > 0 ? i : acc), points.length - 1)
  const [selected, setSelected] = useState<number | null>(null)
  const active = points[selected ?? lastWithSpend]
  const peak = Math.max(...points.map(p => p.total), 0)
  const stride = tickStride(points.length)
  return (
    <div>
      <div className="flex items-baseline justify-between text-[11px] text-muted mb-1">
        <span>{i18nT('pages.usagePage.axis_peak', { value: credits(peak) })}</span>
      </div>
      <div className="relative border-b border-border" style={{ height }}>
        <div aria-hidden="true" className="absolute inset-x-0 top-0 border-t border-dashed border-border" />
        <div className="absolute inset-0 flex items-end gap-px md:gap-[3px]" onPointerLeave={() => setSelected(null)}>
          {points.map((p, i) => (
            <Clickable
              key={p.key}
              onClick={() => setSelected(i)}
              onPointerEnter={() => setSelected(i)}
              onFocus={() => setSelected(i)}
              aria-pressed={i === (selected ?? lastWithSpend)}
              aria-label={i18nT('pages.usagePage.column_label', {
                when: p.label,
                credits: credits(p.total),
                prompts: fmtNumber(p.turns),
              })}
              className={`flex h-full min-w-0 flex-1 flex-col justify-end rounded-t-[3px] outline-none focus-visible:ring-2 focus-visible:ring-accent ${
                i === (selected ?? lastWithSpend) ? 'bg-bg-hover' : ''
              }`}
            >
              <span className="flex flex-col-reverse gap-[2px] overflow-hidden rounded-t-[3px]" style={{ height: `${peak > 0 ? (p.total / peak) * 100 : 0}%` }}>
                {singleColor
                  ? p.total > 0 && <span className="block h-full w-full" style={{ background: singleColor }} />
                  : CATEGORIES.map(c => {
                      const v = p.parts[c] ?? 0
                      if (v <= 0 || p.total <= 0) return null
                      return <span key={c} className="block w-full shrink-0" style={{ height: `${(v / p.total) * 100}%`, minHeight: 2, background: CATEGORY_COLOR[c] }} />
                    })}
              </span>
            </Clickable>
          ))}
        </div>
      </div>
      <div aria-hidden="true" className="flex gap-px md:gap-[3px] mt-1 text-[10px] text-muted">
        {points.map((p, i) => (
          <span key={p.key} className="min-w-0 flex-1 overflow-visible whitespace-nowrap text-center">
            {i % stride === 0 ? p.tick : ''}
          </span>
        ))}
      </div>
      {active && (
        <Readout>
          <span className="text-text font-medium">{active.label}</span>
          <span>{i18nT('pages.usagePage.readout_credits', { value: credits(active.total) })}</span>
          <span>{i18nT('pages.usagePage.readout_prompts', { value: fmtNumber(active.turns) })}</span>
          {!singleColor && CATEGORIES.filter(c => (active.parts[c] ?? 0) > 0).map(c => (
            <span key={c} className="flex items-center gap-1">
              <span aria-hidden="true" className="inline-block h-2 w-2 rounded-sm" style={{ background: CATEGORY_COLOR[c] }} />
              {categoryLabel(c)} {credits(active.parts[c])}
            </span>
          ))}
        </Readout>
      )}
    </div>
  )
}

// ── ranked lists ─────────────────────────────────────────────────────────────

export type RankedRow = {
  key: string
  label: string
  value: number
  valueText: string
  sub?: string
  color?: string
}

/**
 * Horizontal bars on one shared scale, largest first. With `onPick` each row is a
 * button that filters the page to that key; `activeKey` marks the current filter.
 */
export function RankedBars({
  rows,
  onPick,
  activeKey,
  pickLabel,
}: {
  rows: RankedRow[]
  onPick?: (key: string) => void
  activeKey?: string
  pickLabel?: (row: RankedRow) => string
}) {
  const peak = Math.max(...rows.map(r => r.value), 0)
  return (
    <ul className="flex flex-col gap-1.5">
      {rows.map(r => {
        const body = (
          <>
            <div className="flex items-baseline justify-between gap-3 text-[12px]">
              <span className="min-w-0 truncate text-text" title={r.label}>{r.label}</span>
              <span className="shrink-0 font-mono tabular-nums text-text">{r.valueText}</span>
            </div>
            <div className="mt-1 flex items-center gap-2">
              <div aria-hidden="true" className="h-1.5 min-w-0 flex-1 overflow-hidden rounded-full bg-[var(--bg)]">
                <span className="block h-full rounded-full" style={{ width: `${peak > 0 ? (r.value / peak) * 100 : 0}%`, background: r.color ?? 'var(--accent)' }} />
              </div>
              {r.sub && <span className="shrink-0 text-[11px] text-muted tabular-nums">{r.sub}</span>}
            </div>
          </>
        )
        return (
          <li key={r.key}>
            {onPick ? (
              <Clickable
                onClick={() => onPick(r.key)}
                aria-pressed={activeKey === r.key}
                aria-label={pickLabel ? pickLabel(r) : r.label}
                className={`rounded-md px-1.5 py-1 -mx-1.5 outline-none focus-visible:ring-2 focus-visible:ring-accent hover:bg-bg-hover ${
                  activeKey === r.key ? 'bg-bg-hover ring-1 ring-border' : ''
                }`}
              >
                {body}
              </Clickable>
            ) : (
              <div className="py-1">{body}</div>
            )}
          </li>
        )
      })}
    </ul>
  )
}

/** One session's own spend, its subagents' share and the background work done for it. */
export function SplitBar({ own, sub, bg = 0 }: { own: number; sub: number; bg?: number }) {
  const total = own + sub + bg
  if (total <= 0) return null
  const parts: [number, string][] = [
    [own, 'var(--usage-cat-chat)'],
    [sub, 'var(--usage-cat-subagents)'],
    [bg, 'var(--usage-cat-background)'],
  ]
  return (
    <div aria-hidden="true" className="flex h-1.5 w-full gap-[2px] overflow-hidden rounded-full bg-[var(--bg)]">
      {parts.map(([value, color]) =>
        value > 0 ? <span key={color} className="block h-full" style={{ width: `${(value / total) * 100}%`, background: color }} /> : null,
      )}
    </div>
  )
}

// ── distributions ────────────────────────────────────────────────────────────

/** A bucket's label: "< 1s", "1s–2s", "5min+". */
function bucketLabel(b: UsageBucket, fmt: (v: number) => string): string {
  if (b.lo <= 0 && b.hi !== null) return i18nT('pages.usagePage.bucket_under', { hi: fmt(b.hi) })
  if (b.hi === null) return i18nT('pages.usagePage.bucket_over', { lo: fmt(b.lo) })
  return i18nT('pages.usagePage.bucket_range', { lo: fmt(b.lo), hi: fmt(b.hi) })
}

/** Counts per bucket as columns, bucket order kept (sorting would destroy the shape). */
export function Distribution({ buckets, fmt, color = 'var(--accent)' }: { buckets: UsageBucket[]; fmt: (v: number) => string; color?: string }) {
  const [selected, setSelected] = useState<number | null>(null)
  const peak = Math.max(...buckets.map(b => b.count), 0)
  const total = buckets.reduce((a, b) => a + b.count, 0)
  const modal = buckets.reduce((best, b, i) => (b.count > buckets[best].count ? i : best), 0)
  const active = buckets[selected ?? modal]
  return (
    <div>
      <div className="flex h-[96px] items-end gap-[3px] border-b border-border" onPointerLeave={() => setSelected(null)}>
        {buckets.map((b, i) => (
          <Clickable
            key={b.lo}
            onClick={() => setSelected(i)}
            onPointerEnter={() => setSelected(i)}
            onFocus={() => setSelected(i)}
            aria-pressed={i === (selected ?? modal)}
            aria-label={i18nT('pages.usagePage.bucket_aria', { bucket: bucketLabel(b, fmt), prompts: fmtNumber(b.count) })}
            className={`flex h-full min-w-0 flex-1 flex-col justify-end rounded-t-[3px] outline-none focus-visible:ring-2 focus-visible:ring-accent ${
              i === (selected ?? modal) ? 'bg-bg-hover' : ''
            }`}
          >
            <span className="block w-full rounded-t-[3px]" style={{ height: `${peak > 0 ? (b.count / peak) * 100 : 0}%`, minHeight: b.count > 0 ? 2 : 0, background: color }} />
          </Clickable>
        ))}
      </div>
      {active && total > 0 && (
        <Readout>
          <span className="text-text font-medium">{bucketLabel(active, fmt)}</span>
          <span>{i18nT('pages.usagePage.readout_prompts', { value: fmtNumber(active.count) })}</span>
        </Readout>
      )}
    </div>
  )
}

// ── when spend happens ───────────────────────────────────────────────────────

/** Credits by hour of day, darker = more. */
export function HourStrip({ values }: { values: number[] }) {
  const [selected, setSelected] = useState<number | null>(null)
  const peak = Math.max(...values, 0)
  const busiest = values.reduce((best, v, i) => (v > values[best] ? i : best), 0)
  const active = selected ?? busiest
  const hourText = (h: number) => i18nT('pages.usagePage.hour_label', { hour: String(h).padStart(2, '0') })
  return (
    <div>
      <div className="grid grid-cols-12 gap-[3px] md:grid-cols-24" onPointerLeave={() => setSelected(null)}>
        {values.map((v, h) => (
          <HeatCell
            key={h}
            value={v}
            peak={peak}
            onClick={() => setSelected(h)}
            onPointerEnter={() => setSelected(h)}
            onFocus={() => setSelected(h)}
            aria-pressed={h === active}
            aria-label={i18nT('pages.usagePage.hour_aria', { hour: hourText(h), credits: credits(v) })}
            className={`h-7 rounded-[3px] outline-none focus-visible:ring-2 focus-visible:ring-accent ${h === active ? 'ring-1 ring-text' : ''}`}
          />
        ))}
      </div>
      <div aria-hidden="true" className="mt-1 hidden md:grid md:grid-cols-4 text-[10px] text-muted">
        {[0, 6, 12, 18].map(h => <span key={h}>{hourText(h)}</span>)}
      </div>
      <Readout>
        <span className="text-text font-medium">{hourText(active)}</span>
        <span>{i18nT('pages.usagePage.readout_credits', { value: credits(values[active] ?? 0) })}</span>
      </Readout>
    </div>
  )
}

/** Days of the range as a calendar: one column per week, Monday at the top. */
export function CalendarHeatmap({ days, dayLabel }: { days: { key: string; credits: number; turns: number }[]; dayLabel: (key: string) => string }) {
  const [selected, setSelected] = useState<string | null>(null)
  if (days.length === 0) return null
  const peak = Math.max(...days.map(d => d.credits), 0)
  const first = parseDayKey(days[0].key)
  const lead = (first.getDay() + 6) % 7 // Monday = 0
  const cells: ({ key: string; credits: number; turns: number } | null)[] = [...Array(lead).fill(null), ...days]
  const weeks: (typeof cells)[] = []
  for (let i = 0; i < cells.length; i += 7) weeks.push(cells.slice(i, i + 7))
  const busiest = days.reduce((best, d) => (d.credits > best.credits ? d : best), days[0])
  const active = days.find(d => d.key === selected) ?? busiest
  return (
    <div>
      <div className="flex gap-2">
        <div aria-hidden="true" className="grid grid-rows-7 gap-[3px] text-[10px] text-muted">
          {[1, 2, 3, 4, 5, 6, 7].map(i => (
            <span key={i} className="flex h-4 items-center">{i % 2 === 1 ? fmtWeekday(i, 'narrow') : ''}</span>
          ))}
        </div>
        <div className="flex min-w-0 flex-1 gap-[3px] overflow-x-auto" onPointerLeave={() => setSelected(null)}>
          {weeks.map((week, w) => (
            <div key={w} className="grid grid-rows-7 gap-[3px]">
              {week.map((d, i) =>
                d ? (
                  <HeatCell
                    key={d.key}
                    value={d.credits}
                    peak={peak}
                    onClick={() => setSelected(d.key)}
                    onPointerEnter={() => setSelected(d.key)}
                    onFocus={() => setSelected(d.key)}
                    aria-pressed={d.key === active.key}
                    aria-label={i18nT('pages.usagePage.day_aria', { day: dayLabel(d.key), credits: credits(d.credits), prompts: fmtNumber(d.turns) })}
                    className={`h-4 w-4 rounded-[3px] outline-none focus-visible:ring-2 focus-visible:ring-accent ${d.key === active.key ? 'ring-1 ring-text' : ''}`}
                  />
                ) : (
                  <span key={`pad-${i}`} aria-hidden="true" className="h-4 w-4" />
                ),
              )}
            </div>
          ))}
        </div>
      </div>
      <Readout>
        <span className="text-text font-medium">{dayLabel(active.key)}</span>
        <span>{i18nT('pages.usagePage.readout_credits', { value: credits(active.credits) })}</span>
        <span>{i18nT('pages.usagePage.readout_prompts', { value: fmtNumber(active.turns) })}</span>
      </Readout>
    </div>
  )
}

// ── traces ───────────────────────────────────────────────────────────────────

/** One measure over the series points as a line with a soft area, e.g. median response time. */
export function TrendLine({ values, color = 'var(--accent)', height = 72 }: { values: (number | null)[]; color?: string; height?: number }) {
  const filled = values.map(v => v ?? 0)
  if (filled.filter(v => v > 0).length < 2) return null
  const peak = Math.max(...filled)
  const pts = vertices(filled, peak)
  return (
    <div aria-hidden="true" className="relative w-full" style={{ height }}>
      <div className="absolute inset-0 opacity-20" style={{ ...areaStyle(pts), background: color }} />
      <div className="absolute inset-0" style={{ ...strokeStyle(pts, 2), background: color }} />
    </div>
  )
}

/**
 * The account's cumulative consumption against the recorded rows', on one
 * axis, with the gap between them shaded: the gap IS the unattributed spend.
 */
export function ReconcileChart({ series, height = 160 }: { series: { ts: number; account: number; recorded: number }[]; height?: number }) {
  if (series.length < 2) return null
  const t0 = series[0].ts
  const span = Math.max(series[series.length - 1].ts - t0, 1)
  const xs = series.map(p => ((p.ts - t0) / span) * 100)
  const peak = Math.max(...series.map(p => Math.max(p.account, p.recorded)), 0)
  const account = pointsAt(xs, series.map(p => p.account), peak)
  const recorded = pointsAt(xs, series.map(p => p.recorded), peak)
  return (
    <div aria-hidden="true" className="relative w-full border-b border-border" style={{ height }}>
      <div className="absolute inset-x-0 top-0 border-t border-dashed border-border" />
      <div className="absolute inset-0" style={{ ...bandStyle(account, recorded), backgroundImage: 'var(--ctx-hatch)' }} />
      <div className="absolute inset-0" style={{ ...strokeStyle(account, 2), background: 'var(--usage-cat-channels)' }} />
      <div className="absolute inset-0" style={{ ...strokeStyle(recorded, 2), background: 'var(--usage-cat-chat)' }} />
    </div>
  )
}
