/**
 * The Usage page's "why" views: what made each prompt cost what it did, what it
 * was asked, what it did, and which session it was done for.
 *
 * Every figure here is a FACT the gateway observed about a turn (tool calls,
 * how full the context was, a mid-turn compaction, how long it ran, whether it
 * finished). None is a split of the turn's credits: the backend bills one
 * opaque figure per turn, so the page shows what went with an expensive turn,
 * never how much of the bill each part caused.
 */
import { useState } from 'react'
import type { UsageDriverBand, UsageReason, UsageRecurringRow, UsageTurnRow } from '../../api/client'
import Clickable from '../../components/Clickable'
import { Badge } from '../../components/ui'
import { fmtCompact, fmtDateFields, fmtNumber, fmtPercent } from '../../i18n/format'
import { i18nT } from '../../i18n/t'
import { RankedBars } from './UsageCharts'
import {
  CATEGORY_COLOR,
  categoryLabel,
  contextBandLabel,
  credits,
  duration,
  modelLabel,
  outcomeLabel,
  outcomeVariant,
  reasonHint,
  reasonLabel,
  serviceLabel,
  toolBandLabel,
  toolKindLabel,
} from './usageModel'

const TH = 'text-left text-muted text-[12px] uppercase tracking-[.04em] px-2.5 py-2 border-b border-border font-medium whitespace-nowrap'
const TD_NUM = 'px-2.5 py-2 border-b border-border text-[13px] text-right font-mono tabular-nums whitespace-nowrap'

function when(ts: number): string {
  return fmtDateFields(ts * 1000, { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' })
}

/** A turn's reasons as chips; with `onPick` each chip filters the prompt list to that reason. */
export function ReasonChips({ reasons, onPick, active }: { reasons: UsageReason[]; onPick?: (code: string) => void; active?: string }) {
  if (reasons.length === 0) return null
  return (
    <span className="flex flex-wrap gap-1">
      {reasons.map(r =>
        onPick ? (
          <Clickable
            key={r.code}
            onClick={() => onPick(r.code)}
            aria-pressed={active === r.code}
            aria-label={i18nT('pages.usagePage.filter_to', { name: reasonLabel(r) })}
            title={reasonHint(r.code)}
            className="rounded-full outline-none focus-visible:ring-2 focus-visible:ring-accent"
          >
            <Badge variant={active === r.code ? 'aim' : 'warn'}>{reasonLabel(r)}</Badge>
          </Clickable>
        ) : (
          <span key={r.code} title={reasonHint(r.code)}>
            <Badge variant="warn">{reasonLabel(r)}</Badge>
          </span>
        ),
      )}
    </span>
  )
}

/** One labelled fact in the detail grid; absent values read as a dash. */
function Fact({ label, value }: { label: string; value: string }) {
  return (
    <div className="min-w-0">
      <div className="text-[11px] uppercase tracking-[.04em] text-muted">{label}</div>
      <div className="truncate text-[13px] text-text" title={value}>{value}</div>
    </div>
  )
}

/**
 * Everything recorded about one prompt: what was asked, why it cost what it did,
 * what it did, and on whose behalf it ran.
 */
export function PromptDetail({ turn, onFilter }: { turn: UsageTurnRow; onFilter: (key: 'session' | 'reason', value: string) => void }) {
  const kinds = Object.entries(turn.tool_kinds).sort((a, b) => b[1] - a[1])
  const cause =
    turn.share === 'subagents'
      ? i18nT('pages.usagePage.cause_subagent', { session: turn.title ?? turn.session })
      : turn.share === 'background'
        ? i18nT('pages.usagePage.cause_background', { session: turn.title ?? turn.session })
        : i18nT('pages.usagePage.cause_own', { kind: categoryLabel(turn.category) })
  return (
    <div className="flex flex-col gap-3 rounded-md border border-border bg-[var(--bg)] p-3">
      <div>
        <div className="text-[11px] uppercase tracking-[.04em] text-muted">{i18nT('pages.usagePage.detail_request')}</div>
        <p className="mt-0.5 text-[13px] text-text break-words">{turn.request ?? i18nT('pages.usagePage.detail_request_none')}</p>
      </div>
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-[12px] text-muted">
        <span className="flex items-center gap-1.5">
          <span aria-hidden="true" className="inline-block h-2 w-2 rounded-sm" style={{ background: CATEGORY_COLOR[turn.category] }} />
          {cause}
        </span>
        <Clickable
          onClick={() => onFilter('session', turn.session)}
          className="rounded text-accent outline-none focus-visible:ring-2 focus-visible:ring-accent"
        >
          {i18nT('pages.usagePage.detail_show_session')}
        </Clickable>
      </div>
      {turn.reasons.length > 0 ? (
        <div>
          <div className="text-[11px] uppercase tracking-[.04em] text-muted mb-1">{i18nT('pages.usagePage.detail_why')}</div>
          <ReasonChips reasons={turn.reasons} onPick={code => onFilter('reason', code)} />
          <ul className="mt-1.5 list-disc pl-4 text-[12px] text-muted">
            {turn.reasons.map(r => (
              <li key={r.code}>{reasonHint(r.code)}</li>
            ))}
          </ul>
        </div>
      ) : (
        <p className="text-[12px] text-muted">{i18nT('pages.usagePage.detail_why_none')}</p>
      )}
      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <Fact label={i18nT('pages.usagePage.col_credits')} value={credits(turn.credits)} />
        <Fact
          label={i18nT('pages.usagePage.col_model')}
          value={
            turn.rate_multiplier
              ? i18nT('pages.usagePage.model_with_rate', { model: modelLabel(turn.model), rate: fmtNumber(turn.rate_multiplier, { maximumFractionDigits: 2 }) })
              : modelLabel(turn.model)
          }
        />
        <Fact label={i18nT('pages.usagePage.col_response')} value={duration(turn.duration_ms)} />
        <Fact label={i18nT('pages.usagePage.col_first_token')} value={duration(turn.ttft_ms)} />
        <Fact
          label={i18nT('pages.usagePage.detail_context')}
          value={
            turn.context_pct === null
              ? '—'
              : turn.context_tokens
                ? i18nT('pages.usagePage.detail_context_value', { pct: fmtPercent(turn.context_pct / 100), tokens: fmtCompact(turn.context_tokens) })
                : fmtPercent(turn.context_pct / 100)
          }
        />
        <Fact label={i18nT('pages.usagePage.detail_tool_calls')} value={turn.tool_calls === null ? '—' : fmtNumber(turn.tool_calls)} />
        <Fact label={i18nT('pages.usagePage.detail_sent')} value={turn.prompt_chars === null ? '—' : i18nT('pages.usagePage.chars', { size: fmtCompact(turn.prompt_chars) })} />
        <Fact label={i18nT('pages.usagePage.detail_received')} value={turn.output_chars === null ? '—' : i18nT('pages.usagePage.chars', { size: fmtCompact(turn.output_chars) })} />
        <Fact label={i18nT('pages.usagePage.detail_compactions')} value={turn.compactions === null ? '—' : fmtNumber(turn.compactions)} />
        <Fact label={i18nT('pages.usagePage.col_outcome')} value={outcomeLabel(turn.outcome)} />
        <Fact label={i18nT('pages.usagePage.detail_agent')} value={turn.agent || '—'} />
        <Fact label={i18nT('pages.usagePage.detail_session_key')} value={turn.slot} />
      </div>
      {kinds.length > 0 && (
        <div>
          <div className="text-[11px] uppercase tracking-[.04em] text-muted mb-1">{i18nT('pages.usagePage.detail_tools')}</div>
          <div className="flex flex-wrap gap-1.5">
            {kinds.map(([kind, n]) => (
              <Badge key={kind} variant="muted">
                {i18nT('pages.usagePage.tool_count', { tool: toolKindLabel(kind), count_text: fmtNumber(n) })}
              </Badge>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}

/** Average credits per prompt by tool-call count and by context fill, from the user's own rows. */
export function DriverBars({ drivers }: { drivers: { tool_calls: UsageDriverBand[]; context_fill: UsageDriverBand[]; measured_turns: number } }) {
  const rows = (bands: UsageDriverBand[], label: (band: string) => string) =>
    bands
      .filter(b => b.turns > 0)
      .map(b => ({
        key: b.band,
        label: label(b.band),
        value: b.avg_credits,
        valueText: i18nT('pages.usagePage.avg_per_prompt', { value: credits(b.avg_credits) }),
        sub: i18nT('pages.usagePage.readout_prompts', { value: fmtNumber(b.turns) }),
      }))
  const tools = rows(drivers.tool_calls, toolBandLabel)
  const context = rows(drivers.context_fill, contextBandLabel)
  return (
    <div className="flex flex-col gap-4">
      <div>
        <div className="mb-2 text-[12px] font-medium text-text">{i18nT('pages.usagePage.drivers_by_tools')}</div>
        {tools.length > 0 ? (
          <RankedBars rows={tools} />
        ) : (
          <p className="text-[12px] text-muted">{i18nT('pages.usagePage.drivers_no_data')}</p>
        )}
      </div>
      <div>
        <div className="mb-2 text-[12px] font-medium text-text">{i18nT('pages.usagePage.drivers_by_context')}</div>
        {context.length > 0 ? (
          <RankedBars rows={context} />
        ) : (
          <p className="text-[12px] text-muted">{i18nT('pages.usagePage.drivers_no_data')}</p>
        )}
      </div>
    </div>
  )
}

/** The range's most expensive prompts, each expandable to its full detail. */
export function TopPrompts({ turns, onFilter }: { turns: UsageTurnRow[]; onFilter: (key: 'session' | 'reason', value: string) => void }) {
  const [open, setOpen] = useState<number | null>(null)
  if (turns.length === 0) return <p className="text-[13px] text-muted">{i18nT('pages.usagePage.no_prompts')}</p>
  return (
    <ul className="flex flex-col divide-y divide-border">
      {turns.map((t, i) => (
        <li key={`${t.ts}-${t.slot}`} className="py-2">
          <Clickable
            onClick={() => setOpen(open === i ? null : i)}
            aria-expanded={open === i}
            className="flex flex-col gap-1 rounded-md px-1.5 py-1 -mx-1.5 outline-none hover:bg-bg-hover focus-visible:ring-2 focus-visible:ring-accent"
          >
            <div className="flex items-baseline justify-between gap-3">
              <span className="min-w-0 truncate text-[13px] text-text" title={serviceLabel(t.service)}>{serviceLabel(t.service)}</span>
              <span className="shrink-0 font-mono tabular-nums text-[13px] text-text">{credits(t.credits)}</span>
            </div>
            {t.request && <span className="truncate text-[12px] text-muted" title={t.request}>{t.request}</span>}
            <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-[11px] text-muted">
              <span>{when(t.ts)}</span>
              <ReasonChips reasons={t.reasons} />
            </div>
          </Clickable>
          {open === i && (
            <div className="mt-2">
              <PromptDetail turn={t} onFilter={onFilter} />
            </div>
          )}
        </li>
      ))}
    </ul>
  )
}

/** Background services' rhythm and what they cost at their current pace. */
export function RecurringTable({ rows }: { rows: UsageRecurringRow[] }) {
  if (rows.length === 0) return <p className="text-[13px] text-muted">{i18nT('pages.usagePage.recurring_empty')}</p>
  return (
    <div className="overflow-x-auto">
      <table className="w-full border-collapse table-striped">
        <thead>
          <tr>
            <th className={TH}>{i18nT('pages.usagePage.col_what')}</th>
            <th className={`${TH} text-right`}>{i18nT('pages.usagePage.col_runs_per_day')}</th>
            <th className={`${TH} text-right`}>{i18nT('pages.usagePage.col_per_run')}</th>
            <th className={`${TH} text-right hidden md:table-cell`}>{i18nT('pages.usagePage.col_per_day')}</th>
            <th className={`${TH} text-right`}>{i18nT('pages.usagePage.col_next_30_days')}</th>
          </tr>
        </thead>
        <tbody>
          {rows.map(r => (
            <tr key={r.service}>
              <td className="px-2.5 py-2 border-b border-border text-[13px] max-w-[260px]">
                <div className="truncate text-text" title={serviceLabel(r.service)}>{serviceLabel(r.service)}</div>
                <div className="flex items-center gap-1.5 text-[11px] text-muted">
                  {i18nT('pages.usagePage.recurring_runs', { runs: fmtNumber(r.runs) })}
                  {r.faults > 0 && <Badge variant={outcomeVariant('error')}>{i18nT('pages.usagePage.recurring_failed', { value: fmtNumber(r.faults) })}</Badge>}
                </div>
              </td>
              <td className={TD_NUM}>{fmtNumber(r.runs_per_day, { maximumFractionDigits: 1 })}</td>
              <td className={TD_NUM}>{credits(r.credits_per_run)}</td>
              <td className={`${TD_NUM} hidden md:table-cell`}>{credits(r.credits_per_day)}</td>
              <td className={TD_NUM}>{credits(r.projected_30d)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

/** Tool kinds by calls; the sub-line totals the prompts that used each kind. */
export function ToolsList({ tools }: { tools: { kind: string; calls: number; turns: number; credits: number }[] }) {
  if (tools.length === 0) return <p className="text-[13px] text-muted">{i18nT('pages.usagePage.tools_empty')}</p>
  return (
    <RankedBars
      rows={tools.map(t => ({
        key: t.kind,
        label: toolKindLabel(t.kind),
        value: t.calls,
        valueText: i18nT('pages.usagePage.tool_calls_value', { value: fmtNumber(t.calls) }),
        sub: i18nT('pages.usagePage.tool_turns_credits', { prompts: fmtNumber(t.turns), credits: credits(t.credits) }),
        color: 'var(--usage-cat-background)',
      }))}
    />
  )
}
