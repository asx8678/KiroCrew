/**
 * The Usage page's tables: sessions ranked by credits, the per-prompt rows, and
 * the chart data as a table. Wide tables scroll sideways inside their card on a
 * phone, and lower-priority columns only appear from `md` up.
 */
import { keepPreviousData, useInfiniteQuery } from '@tanstack/react-query'
import { ChevronDown, ChevronRight, Inbox } from 'lucide-react'
import { useState, type ReactNode } from 'react'
import { api, type UsageSessionRow, type UsageTurnsQuery } from '../../api/client'
import Clickable from '../../components/Clickable'
import ErrorNotice from '../../components/ErrorNotice'
import { Badge, Btn, EmptyState } from '../../components/ui'
import { fmtDateFields, fmtNumber, fmtPercent } from '../../i18n/format'
import { i18nT } from '../../i18n/t'
import { SplitBar } from './UsageCharts'
import { PromptDetail, ReasonChips } from './UsageInsights'
import { categoryLabel, credits, duration, modelLabel, outcomeLabel, outcomeVariant, serviceLabel } from './usageModel'

const TH = 'text-left text-muted text-[12px] uppercase tracking-[.04em] px-2.5 py-2 border-b border-border font-medium whitespace-nowrap'
const TH_NUM = `${TH} text-right`
const TD = 'px-2.5 py-2 border-b border-border text-[13px] align-top'
const TD_NUM = `${TD} text-right font-mono tabular-nums whitespace-nowrap`
/** Columns a phone can do without. */
const WIDE = 'hidden md:table-cell'

function Table({ head, children }: { head: ReactNode; children: ReactNode }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full border-collapse table-striped">
        <thead>
          <tr>{head}</tr>
        </thead>
        <tbody>{children}</tbody>
      </table>
    </div>
  )
}

function when(ts: number): string {
  return fmtDateFields(ts * 1000, { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' })
}

/** A session's name: its title, else what kind of session it is. */
function sessionName(title: string | null, slot: string, category: string): string {
  if (title) return title
  if (slot === '_bg') return i18nT('pages.usagePage.session_background')
  return i18nT('pages.usagePage.session_untitled', { kind: categoryLabel(category) })
}

export function SessionsTable({
  rows,
  total,
  active,
  onPick,
}: {
  rows: UsageSessionRow[]
  total: number
  active: string
  onPick: (slot: string) => void
}) {
  if (rows.length === 0) {
    return <EmptyState icon={<Inbox size={20} />} title={i18nT('pages.usagePage.no_sessions')} />
  }
  return (
    <>
      <Table
        head={
          <>
            <th className={TH}>{i18nT('pages.usagePage.col_session')}</th>
            <th className={TH_NUM}>{i18nT('pages.usagePage.col_credits')}</th>
            <th className={TH_NUM}>{i18nT('pages.usagePage.col_prompts')}</th>
            <th className={`${TH} ${WIDE}`}>{i18nT('pages.usagePage.col_models')}</th>
            <th className={`${TH_NUM} ${WIDE}`}>{i18nT('pages.usagePage.col_last_active')}</th>
          </>
        }
      >
        {rows.map(r => (
          <tr key={r.slot} className={active === r.slot ? 'bg-bg-hover' : undefined}>
            <td className={`${TD} max-w-[280px]`}>
              <Clickable
                onClick={() => onPick(r.slot)}
                aria-pressed={active === r.slot}
                aria-label={i18nT('pages.usagePage.filter_to_session', { name: sessionName(r.title, r.slot, r.category) })}
                className="rounded outline-none focus-visible:ring-2 focus-visible:ring-accent"
              >
                <div className="truncate text-text" title={sessionName(r.title, r.slot, r.category)}>
                  {sessionName(r.title, r.slot, r.category)}
                </div>
                <div className="truncate font-mono text-[11px] text-muted" title={r.slot}>{categoryLabel(r.category)} · {r.slot}</div>
              </Clickable>
            </td>
            <td className={TD_NUM}>
              <div>{credits(r.credits)}</div>
              {(r.subagent_credits > 0 || r.background_credits > 0) && (
                <div
                  className="mt-1 w-28 ml-auto"
                  title={i18nT('pages.usagePage.split_title', {
                    own: credits(r.own_credits),
                    sub: credits(r.subagent_credits),
                    bg: credits(r.background_credits),
                  })}
                >
                  <SplitBar own={r.own_credits} sub={r.subagent_credits} bg={r.background_credits} />
                  {r.subagent_credits > 0 && (
                    <div className="mt-0.5 text-[11px] text-muted">{i18nT('pages.usagePage.split_sub', { value: credits(r.subagent_credits) })}</div>
                  )}
                  {r.background_credits > 0 && (
                    <div className="text-[11px] text-muted">{i18nT('pages.usagePage.split_bg', { value: credits(r.background_credits) })}</div>
                  )}
                </div>
              )}
            </td>
            <td className={TD_NUM}>{fmtNumber(r.turns)}</td>
            <td className={`${TD} ${WIDE} max-w-[200px]`}>
              <div className="truncate text-muted" title={r.models.map(modelLabel).join(', ')}>
                {r.models.map(modelLabel).join(', ') || '—'}
              </div>
            </td>
            <td className={`${TD_NUM} ${WIDE} text-muted`}>{when(r.last_ts)}</td>
          </tr>
        ))}
      </Table>
      {total > rows.length && (
        <p className="mt-2 text-[12px] text-muted">{i18nT('pages.usagePage.sessions_more', { shown: fmtNumber(rows.length), total: fmtNumber(total) })}</p>
      )}
    </>
  )
}

/** The per-prompt rows matching the page's filters, newest first, paged; a row expands to its detail. */
export function PromptsTable({
  query,
  onFilter,
}: {
  query: Omit<UsageTurnsQuery, 'before' | 'limit'>
  onFilter: (key: 'session' | 'reason', value: string) => void
}) {
  const [open, setOpen] = useState<string | null>(null)
  const q = useInfiniteQuery({
    queryKey: ['usage-credits-turns', query],
    queryFn: ({ pageParam }) => api.usageCreditsTurns({ ...query, limit: 50, before: pageParam }),
    initialPageParam: undefined as number | undefined,
    getNextPageParam: last => last.next_before ?? undefined,
    placeholderData: keepPreviousData,
    refetchInterval: 60_000,
  })
  if (q.isError && !q.data) {
    return <ErrorNotice message={q.error instanceof Error ? q.error.message : String(q.error)} askAgent />
  }
  const rows = q.data?.pages.flatMap(p => p.turns) ?? []
  if (!q.isLoading && rows.length === 0) {
    return <EmptyState icon={<Inbox size={20} />} title={i18nT('pages.usagePage.no_prompts')} subtitle={i18nT('pages.usagePage.no_prompts_hint')} />
  }
  return (
    <>
      <Table
        head={
          <>
            <th className={`${TH} ${WIDE}`}>{i18nT('pages.usagePage.col_when')}</th>
            <th className={TH}>{i18nT('pages.usagePage.col_what')}</th>
            <th className={`${TH} ${WIDE}`}>{i18nT('pages.usagePage.col_model')}</th>
            <th className={TH_NUM}>{i18nT('pages.usagePage.col_credits')}</th>
            <th className={`${TH_NUM} ${WIDE}`}>{i18nT('pages.usagePage.col_response')}</th>
            <th className={`${TH_NUM} ${WIDE}`}>{i18nT('pages.usagePage.col_first_token')}</th>
            <th className={`${TH} ${WIDE}`}>{i18nT('pages.usagePage.col_outcome')}</th>
            <th className={`${TH_NUM} ${WIDE}`}>{i18nT('pages.usagePage.col_context')}</th>
          </>
        }
      >
        {rows.flatMap(r => {
          const rowKey = `${r.ts}-${r.slot}`
          const expanded = open === rowKey
          return [
          <tr key={rowKey} className={expanded ? 'bg-bg-hover' : undefined}>
            <td className={`${TD} ${WIDE} whitespace-nowrap text-muted`}>{when(r.ts)}</td>
            <td className={`${TD} max-w-[200px] md:max-w-[300px]`}>
              <Clickable
                onClick={() => setOpen(expanded ? null : rowKey)}
                aria-expanded={expanded}
                aria-label={i18nT('pages.usagePage.toggle_detail', { name: serviceLabel(r.service) })}
                className="flex items-start gap-1 rounded outline-none focus-visible:ring-2 focus-visible:ring-accent"
              >
                {expanded ? <ChevronDown size={14} className="mt-0.5 shrink-0 text-muted" /> : <ChevronRight size={14} className="mt-0.5 shrink-0 text-muted" />}
                <span className="min-w-0">
                  <span className="block truncate text-text" title={serviceLabel(r.service)}>{serviceLabel(r.service)}</span>
                  {/* The When column is a desktop column; on a phone the time rides here. */}
                  <span className="block text-[11px] text-muted md:hidden">{when(r.ts)}</span>
                  {r.request && <span className="block truncate text-[11px] text-muted" title={r.request}>{r.request}</span>}
                  <span className="block truncate text-[11px] text-muted" title={r.title ?? r.session}>
                    {r.title ?? r.session}
                    {r.share === 'subagents' ? ` · ${i18nT('pages.usagePage.via_subagent')}` : ''}
                    {r.share === 'background' ? ` · ${i18nT('pages.usagePage.via_background')}` : ''}
                  </span>
                </span>
              </Clickable>
              {r.reasons.length > 0 && (
                <div className="mt-1 pl-[18px]">
                  <ReasonChips reasons={r.reasons} />
                </div>
              )}
            </td>
            <td className={`${TD} ${WIDE} max-w-[160px] truncate text-muted`} title={modelLabel(r.model)}>{modelLabel(r.model)}</td>
            <td className={TD_NUM}>{credits(r.credits)}</td>
            <td className={`${TD_NUM} ${WIDE}`}>{duration(r.duration_ms)}</td>
            <td className={`${TD_NUM} ${WIDE}`}>{duration(r.ttft_ms)}</td>
            <td className={`${TD} ${WIDE}`}>
              <Badge variant={outcomeVariant(r.outcome)}>{outcomeLabel(r.outcome)}</Badge>
            </td>
            <td className={`${TD_NUM} ${WIDE} text-muted`}>{r.context_pct === null ? '—' : fmtPercent(r.context_pct / 100)}</td>
          </tr>,
          expanded ? (
            <tr key={`${rowKey}-detail`}>
              <td colSpan={8} className="px-2.5 pb-3 border-b border-border">
                {/* Zero width, full cell: the detail fills the row without widening
                    the table, so on a phone it stays inside the visible column
                    instead of stretching the sideways scroll. */}
                <div className="w-0 min-w-full">
                  <PromptDetail turn={r} onFilter={onFilter} />
                </div>
              </td>
            </tr>
          ) : null,
          ]
        })}
      </Table>
      {q.isError && <ErrorNotice message={q.error instanceof Error ? q.error.message : String(q.error)} askAgent className="mt-2" />}
      {q.hasNextPage && (
        <div className="mt-3 flex justify-center">
          <Btn onClick={() => void q.fetchNextPage()} disabled={q.isFetchingNextPage}>
            {q.isFetchingNextPage ? i18nT('pages.usagePage.loading') : i18nT('pages.usagePage.load_more')}
          </Btn>
        </div>
      )}
    </>
  )
}

/** Any chart's points as rows: the table view every chart offers. */
export function SeriesTable({ rows }: { rows: { key: string; label: string; credits: number; turns: number; extra?: string }[] }) {
  return (
    <Table
      head={
        <>
          <th className={TH}>{i18nT('pages.usagePage.col_period')}</th>
          <th className={TH_NUM}>{i18nT('pages.usagePage.col_credits')}</th>
          <th className={TH_NUM}>{i18nT('pages.usagePage.col_prompts')}</th>
          <th className={`${TH} ${WIDE}`}>{i18nT('pages.usagePage.col_breakdown')}</th>
        </>
      }
    >
      {rows.map(r => (
        <tr key={r.key}>
          <td className={`${TD} whitespace-nowrap`}>{r.label}</td>
          <td className={TD_NUM}>{credits(r.credits)}</td>
          <td className={TD_NUM}>{fmtNumber(r.turns)}</td>
          <td className={`${TD} ${WIDE} text-muted`}>{r.extra ?? ''}</td>
        </tr>
      ))}
    </Table>
  )
}
