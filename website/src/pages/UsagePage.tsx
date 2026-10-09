/**
 * Usage: what Kiro credits were spent on — every prompt, session, background
 * service, model, day and month — and how long the answers took. Reads the
 * per-turn usage row store through GET /api/usage/credits/summary and /turns
 * (`dashboard/handlers/credit_report.py`) and the account balance through the
 * credit pill's own `['kiro-usage']` query.
 */
import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { useMemo, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { api, type UsageCategory, type UsageCreditSummary } from '../api/client'
import { isApiKeyUsage, parseKiroUsagePayload, type KiroUsageState } from '../api/kiroUsage'
import ErrorNotice from '../components/ErrorNotice'
import InfoTip from '../components/InfoTip'
import SegmentedControl from '../components/SegmentedControl'
import { Btn, Card, CardTitle, ContentSkeleton, PageHeader, StatCard } from '../components/ui'
import { Progress } from '../components/ui/progress'
import { fmtDate, fmtNumber, fmtPercent } from '../i18n/format'
import { i18nT } from '../i18n/t'
import {
  CalendarHeatmap,
  Distribution,
  HourStrip,
  Legend,
  RankedBars,
  ReconcileChart,
  StackedColumns,
  TrendLine,
  type RankedRow,
  type StackPoint,
} from './usage/UsageCharts'
import { DriverBars, RecurringTable, ToolsList, TopPrompts } from './usage/UsageInsights'
import { PromptsTable, SeriesTable, SessionsTable } from './usage/UsageTables'
import {
  CATEGORIES,
  CATEGORY_COLOR,
  RANGE_LABEL_KEY,
  RANGES,
  categoryLabel,
  credits,
  duration,
  modelLabel,
  parseDayKey,
  rangeDays,
  reasonName,
  seriesPointLabel,
  seriesTickLabel,
  serviceLabel,
  type UsageRange,
} from './usage/usageModel'

/** The services list shows this many before folding the rest into "Other". */
const TOP_SERVICES = 12

type Filters = { session?: string; service?: string; category?: UsageCategory; model?: string; reason?: string }

export default function UsagePage() {
  const [params, setParams] = useSearchParams()
  const range: UsageRange = RANGES.includes(params.get('range') as UsageRange) ? (params.get('range') as UsageRange) : '30d'
  const { from, to } = useMemo(() => rangeDays(range), [range])
  const [filters, setFilters] = useState<Filters>({})

  const summaryQ = useQuery<UsageCreditSummary>({
    queryKey: ['usage-credits-summary', from, to],
    queryFn: () => api.usageCreditsSummary(from, to),
    refetchInterval: 60_000,
    placeholderData: keepPreviousData,
  })
  // The same query the top-bar credit pill reads, so the two never disagree.
  const usageQ = useQuery<KiroUsageState>({
    queryKey: ['kiro-usage'],
    queryFn: () => api.sessionsUsage().then(parseKiroUsagePayload),
    refetchInterval: 30_000,
  })

  const setRange = (next: UsageRange) => {
    const nextParams = new URLSearchParams(params)
    nextParams.set('range', next)
    setParams(nextParams, { replace: true })
  }
  const toggle = <K extends keyof Filters>(key: K, value: Filters[K]) =>
    setFilters(f => ({ ...f, [key]: f[key] === value ? undefined : value }))

  const summary = summaryQ.data
  return (
    <>
      <PageHeader
        title={i18nT('pages.usagePage.title')}
        subtitle={i18nT('pages.usagePage.subtitle')}
        actions={
          <SegmentedControl
            segments={RANGES.map(r => ({ key: r, label: i18nT(RANGE_LABEL_KEY[r]) }))}
            value={range}
            onChange={setRange}
            ariaLabel={i18nT('pages.usagePage.range_aria')}
            layoutId="usage-range"
          />
        }
      />
      <div className="px-4 md:px-6 pb-8 overflow-y-auto flex-1 min-h-0">
        <AccountCard usage={usageQ.data} summary={summary} />
        {summaryQ.isError && !summary && (
          <ErrorNotice message={summaryQ.error instanceof Error ? summaryQ.error.message : String(summaryQ.error)} askAgent />
        )}
        {!summary && !summaryQ.isError && <ContentSkeleton rows={6} />}
        {summary && (
          <UsageBody
            summary={summary}
            filters={filters}
            toggle={toggle}
            setFilter={(key, value) => setFilters(f => ({ ...f, [key]: value }))}
            clear={() => setFilters({})}
          />
        )}
      </div>
    </>
  )
}

/** The plan balance, for an account kiro-cli could read one for. */
function AccountCard({ usage, summary }: { usage: KiroUsageState | undefined; summary: UsageCreditSummary | undefined }) {
  if (isApiKeyUsage(usage)) {
    return (
      <Card>
        <CardTitle>{i18nT('pages.usagePage.account_title')}</CardTitle>
        <p className="text-[13px] text-muted">{i18nT('pages.usagePage.account_api_key')}</p>
      </Card>
    )
  }
  if (!usage || typeof usage !== 'object' || !('limit' in usage)) return null
  const pct = usage.limit > 0 ? usage.used / usage.limit : 0
  // The account's own rate counts every credit it was charged; the recorded rate
  // only what Kiro Crew saw, so it is the fallback until two readings exist.
  const days = summary?.range.days ?? 0
  const accountRate = summary?.account_rate?.per_day
  const perDay = accountRate ?? (days > 0 && summary ? summary.totals.credits / days : 0)
  const resetsAt = usage.resets ? parseDayKey(usage.resets) : null
  const daysLeft = resetsAt ? Math.max(0, Math.ceil((resetsAt.getTime() - Date.now()) / 86_400_000)) : null
  const projected = daysLeft !== null && perDay > 0 ? usage.used + perDay * daysLeft : null
  return (
    <Card>
      <CardTitle>
        {i18nT('pages.usagePage.account_title')}
        {usage.plan && <span className="font-normal text-muted">· {usage.plan}</span>}
        <InfoTip text={i18nT('pages.usagePage.account_tip')} />
      </CardTitle>
      <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
        <span className="text-[22px] font-bold tabular-nums text-text-strong">{credits(usage.used)}</span>
        <span className="text-[13px] text-muted">{i18nT('pages.usagePage.account_of', { limit: credits(usage.limit) })}</span>
        <span className="text-[13px] text-muted">({fmtPercent(pct)})</span>
      </div>
      <Progress className="mt-2 h-1.5" value={Math.min(100, pct * 100)} aria-label={i18nT('pages.usagePage.account_progress')} />
      <div className="mt-3 flex flex-wrap gap-x-6 gap-y-1 text-[12px] text-muted">
        {resetsAt && <span>{i18nT('pages.usagePage.account_resets', { date: fmtDate(resetsAt) })}</span>}
        {usage.overage > 0 && <span className="text-warn">{i18nT('pages.usagePage.account_overage', { value: credits(usage.overage) })}</span>}
        {usage.bonusCredits.map(b => (
          <span key={b.name}>{i18nT('pages.usagePage.account_bonus', { name: b.name, used: credits(b.used), total: credits(b.total) })}</span>
        ))}
        {projected !== null && (
          <span className={projected > usage.limit ? 'text-warn' : undefined}>
            {i18nT(accountRate !== undefined ? 'pages.usagePage.account_projected' : 'pages.usagePage.account_projected_local', {
              value: credits(projected),
              per_day: credits(perDay),
            })}
          </span>
        )}
        {usage.stale && <span>{i18nT('pages.usagePage.account_stale')}</span>}
      </div>
    </Card>
  )
}

function UsageBody({
  summary,
  filters,
  toggle,
  setFilter,
  clear,
}: {
  summary: UsageCreditSummary
  filters: Filters
  toggle: <K extends keyof Filters>(key: K, value: Filters[K]) => void
  setFilter: (key: 'session' | 'reason', value: string) => void
  clear: () => void
}) {
  const [timeView, setTimeView] = useState<'chart' | 'table'>('chart')
  const [allServices, setAllServices] = useState(false)
  const { totals, prior, range } = summary
  const g = range.granularity

  const points: StackPoint[] = summary.series.map(p => ({
    key: p.key,
    label: seriesPointLabel(p.key, g, range.from),
    tick: seriesTickLabel(p.key, g),
    total: p.credits,
    turns: p.turns,
    parts: p.by_category,
  }))
  const categoryTotals = new Map(summary.by_category.map(c => [c.category, c]))

  const serviceRows: RankedRow[] = summary.by_service.map(s => ({
    key: s.service,
    label: serviceLabel(s.service),
    value: s.credits,
    valueText: credits(s.credits),
    sub: i18nT('pages.usagePage.sub_prompts_avg', { prompts: fmtNumber(s.turns), avg: credits(s.avg_credits) }),
    color: CATEGORY_COLOR[s.category],
  }))
  const shownServices = allServices ? serviceRows : serviceRows.slice(0, TOP_SERVICES)
  const restServices = serviceRows.slice(TOP_SERVICES)
  if (!allServices && restServices.length > 0) {
    const rest = restServices.reduce((a, r) => a + r.value, 0)
    shownServices.push({
      key: '__other__',
      label: i18nT('pages.usagePage.other_services', { number: fmtNumber(restServices.length) }),
      value: rest,
      valueText: credits(rest),
      color: 'var(--muted-strong)',
    })
  }

  const filterParts = [
    filters.category && categoryLabel(filters.category),
    filters.service && serviceLabel(filters.service),
    filters.model && modelLabel(filters.model),
    filters.session && (summary.sessions.find(s => s.slot === filters.session)?.title ?? filters.session),
    filters.reason && reasonName(filters.reason),
  ].filter(Boolean) as string[]

  const dayRows = g === 'day' ? summary.series.map(p => ({ key: p.key, credits: p.credits, turns: p.turns })) : []
  const rec = summary.reconciliation

  return (
    <>
      <div className="grid gap-3.5 grid-cols-[repeat(auto-fit,minmax(150px,1fr))] mb-6">
        <StatCard
          label={i18nT('pages.usagePage.kpi_credits')}
          value={credits(totals.credits)}
          title={
            prior.delta_pct === null
              ? undefined
              : i18nT('pages.usagePage.kpi_credits_delta', { delta: fmtPercent(prior.delta_pct / 100, { signDisplay: 'always' }), prior: credits(prior.credits) })
          }
          accent
          delay={0}
        />
        <StatCard label={i18nT('pages.usagePage.kpi_prompts')} value={fmtNumber(totals.turns)} delay={1} />
        <StatCard label={i18nT('pages.usagePage.kpi_avg')} value={credits(totals.avg_credits)} delay={2} />
        <StatCard label={i18nT('pages.usagePage.kpi_response')} value={duration(totals.p50_duration_ms)} title={i18nT('pages.usagePage.kpi_response_tip', { p90: duration(totals.p90_duration_ms) })} delay={3} />
        <StatCard label={i18nT('pages.usagePage.kpi_first_token')} value={duration(totals.p50_ttft_ms)} title={i18nT('pages.usagePage.kpi_first_token_tip')} delay={4} />
        <StatCard
          label={i18nT('pages.usagePage.kpi_failed')}
          value={fmtNumber(totals.faults)}
          colorClass={totals.faults > 0 ? 'text-danger' : undefined}
          delay={5}
        />
        {rec && (
          <StatCard
            label={i18nT('pages.usagePage.kpi_unattributed')}
            value={credits(rec.unattributed_credits)}
            title={i18nT('pages.usagePage.kpi_unattributed_tip')}
            colorClass={rec.unattributed_credits > 0.5 ? 'text-warn' : undefined}
            delay={6}
          />
        )}
      </div>

      <Card>
        <div className="flex flex-wrap items-start justify-between gap-3 mb-3">
          <CardTitle className="mb-0">
            {i18nT('pages.usagePage.over_time_title')}
            <InfoTip text={i18nT('pages.usagePage.over_time_tip')} />
          </CardTitle>
          <SegmentedControl
            segments={[
              { key: 'chart', label: i18nT('pages.usagePage.view_chart') },
              { key: 'table', label: i18nT('pages.usagePage.view_table') },
            ]}
            value={timeView}
            onChange={v => setTimeView(v)}
            ariaLabel={i18nT('pages.usagePage.view_aria')}
            layoutId="usage-time-view"
            collapse={false}
          />
        </div>
        <div className="mb-3">
          <Legend
            items={CATEGORIES.filter(c => (categoryTotals.get(c)?.credits ?? 0) > 0).map(c => ({
              key: c,
              label: categoryLabel(c),
              color: CATEGORY_COLOR[c],
              value: credits(categoryTotals.get(c)?.credits ?? 0),
            }))}
          />
        </div>
        {timeView === 'chart' ? (
          <StackedColumns points={points} />
        ) : (
          <SeriesTable
            rows={points.map(p => ({
              key: p.key,
              label: p.label,
              credits: p.total,
              turns: p.turns,
              extra: CATEGORIES.filter(c => (p.parts[c] ?? 0) > 0)
                .map(c => i18nT('pages.usagePage.breakdown_part', { name: categoryLabel(c), value: credits(p.parts[c]) }))
                .join(', '),
            }))}
          />
        )}
      </Card>

      <div className="grid items-start gap-x-4 md:grid-cols-2">
        <Card>
          <CardTitle>
            {i18nT('pages.usagePage.categories_title')}
            <InfoTip text={i18nT('pages.usagePage.categories_tip')} />
          </CardTitle>
          <RankedBars
            rows={summary.by_category
              .filter(c => c.turns > 0)
              .map(c => ({
                key: c.category,
                label: categoryLabel(c.category),
                value: c.credits,
                valueText: credits(c.credits),
                sub: totals.credits > 0 ? fmtPercent(c.credits / totals.credits) : undefined,
                color: CATEGORY_COLOR[c.category],
              }))}
            onPick={key => toggle('category', key as UsageCategory)}
            activeKey={filters.category}
            pickLabel={r => i18nT('pages.usagePage.filter_to', { name: r.label })}
          />
        </Card>
        <Card>
          <CardTitle>
            {i18nT('pages.usagePage.services_title')}
            <InfoTip text={i18nT('pages.usagePage.services_tip')} />
          </CardTitle>
          <RankedBars
            rows={shownServices}
            onPick={key => (key === '__other__' ? setAllServices(true) : toggle('service', key))}
            activeKey={filters.service}
            pickLabel={r => (r.key === '__other__' ? i18nT('pages.usagePage.show_all_services') : i18nT('pages.usagePage.filter_to', { name: r.label }))}
          />
        </Card>
      </div>

      <div className="grid items-start gap-x-4 md:grid-cols-2">
        <Card>
          <CardTitle>
            {i18nT('pages.usagePage.models_title')}
            <InfoTip text={i18nT('pages.usagePage.models_tip')} />
          </CardTitle>
          <RankedBars
            rows={summary.by_model.map(m => ({
              key: m.model,
              label: modelLabel(m.model),
              value: m.credits,
              valueText: credits(m.credits),
              sub: i18nT('pages.usagePage.sub_prompts_avg', { prompts: fmtNumber(m.turns), avg: credits(m.avg_credits) }),
            }))}
            onPick={key => toggle('model', key)}
            activeKey={filters.model}
            pickLabel={r => i18nT('pages.usagePage.filter_to', { name: r.label })}
          />
        </Card>
        <Card>
          <CardTitle>
            {i18nT('pages.usagePage.speed_title')}
            <InfoTip text={i18nT('pages.usagePage.speed_tip')} />
          </CardTitle>
          <div className="text-[12px] text-muted mb-1">
            {i18nT('pages.usagePage.speed_trend', { p50: duration(totals.p50_duration_ms), p90: duration(totals.p90_duration_ms) })}
          </div>
          <TrendLine values={summary.series.map(p => p.p50_duration_ms)} />
          <div className="mt-4 text-[12px] font-medium text-text">{i18nT('pages.usagePage.response_distribution')}</div>
          <Distribution buckets={summary.distributions.duration_ms} fmt={duration} />
          {summary.distributions.ttft_ms.some(b => b.count > 0) && (
            <>
              <div className="mt-4 text-[12px] font-medium text-text">{i18nT('pages.usagePage.first_token_distribution')}</div>
              <Distribution buckets={summary.distributions.ttft_ms} fmt={duration} color="var(--usage-cat-background)" />
            </>
          )}
        </Card>
      </div>

      <div className="grid items-start gap-x-4 md:grid-cols-2">
        <Card>
          <CardTitle>
            {i18nT('pages.usagePage.when_title')}
            <InfoTip text={i18nT('pages.usagePage.when_tip')} />
          </CardTitle>
          <div className="text-[12px] font-medium text-text mb-2">{i18nT('pages.usagePage.by_hour')}</div>
          <HourStrip values={summary.by_hour} />
          {dayRows.length >= 14 && (
            <>
              <div className="mt-4 text-[12px] font-medium text-text mb-2">{i18nT('pages.usagePage.by_day')}</div>
              <CalendarHeatmap days={dayRows} dayLabel={key => seriesPointLabel(key, 'day')} />
            </>
          )}
        </Card>
        <Card>
          <CardTitle>
            {i18nT('pages.usagePage.reconcile_title')}
            <InfoTip text={i18nT('pages.usagePage.reconcile_tip')} />
          </CardTitle>
          {rec ? (
            <>
              <div className="mb-3 flex flex-wrap gap-x-6 gap-y-1 text-[13px]">
                <span>{i18nT('pages.usagePage.reconcile_account', { value: credits(rec.account_credits) })}</span>
                <span>{i18nT('pages.usagePage.reconcile_recorded', { value: credits(rec.recorded_credits) })}</span>
                <span className={rec.unattributed_credits > 0.5 ? 'text-warn' : 'text-muted'}>
                  {i18nT('pages.usagePage.reconcile_gap', { value: credits(rec.unattributed_credits) })}
                </span>
              </div>
              <ReconcileChart series={rec.series} />
              <div className="mt-2">
                <Legend
                  items={[
                    { key: 'account', label: i18nT('pages.usagePage.legend_account'), color: 'var(--usage-cat-channels)' },
                    { key: 'recorded', label: i18nT('pages.usagePage.legend_recorded'), color: 'var(--usage-cat-chat)' },
                    { key: 'gap', label: i18nT('pages.usagePage.legend_gap'), color: 'var(--muted-strong)' },
                  ]}
                />
              </div>
            </>
          ) : (
            <p className="text-[13px] text-muted">{i18nT('pages.usagePage.reconcile_empty')}</p>
          )}
        </Card>
      </div>

      <div className="grid items-start gap-x-4 md:grid-cols-2">
        <Card>
          <CardTitle>
            {i18nT('pages.usagePage.drivers_title')}
            <InfoTip text={i18nT('pages.usagePage.drivers_tip')} />
          </CardTitle>
          <DriverBars drivers={summary.drivers} />
        </Card>
        <Card>
          <CardTitle>
            {i18nT('pages.usagePage.top_title')}
            <InfoTip text={i18nT('pages.usagePage.top_tip')} />
          </CardTitle>
          <TopPrompts turns={summary.top_turns} onFilter={setFilter} />
        </Card>
      </div>

      <Card>
        <CardTitle>
          {i18nT('pages.usagePage.recurring_title')}
          <InfoTip text={i18nT('pages.usagePage.recurring_tip')} />
        </CardTitle>
        <RecurringTable rows={summary.recurring} />
      </Card>

      <div className="grid items-start gap-x-4 md:grid-cols-2">
        <Card>
          <CardTitle>
            {i18nT('pages.usagePage.tools_title')}
            <InfoTip text={i18nT('pages.usagePage.tools_tip')} />
          </CardTitle>
          <ToolsList tools={summary.tools} />
        </Card>
        <Card>
          <CardTitle>
            {i18nT('pages.usagePage.months_title')}
            <InfoTip text={i18nT('pages.usagePage.months_tip')} />
          </CardTitle>
          <StackedColumns
            height={120}
            singleColor="var(--accent)"
            points={summary.months.map(m => ({
              key: m.month,
              label: seriesPointLabel(m.month, 'month'),
              tick: seriesTickLabel(m.month, 'month'),
              total: m.credits,
              turns: m.turns,
              parts: {},
            }))}
          />
        </Card>
      </div>

      <Card>
        <CardTitle>
          {i18nT('pages.usagePage.sessions_title')}
          <InfoTip text={i18nT('pages.usagePage.sessions_tip')} />
        </CardTitle>
        <SessionsTable rows={summary.sessions} total={summary.sessions_total} active={filters.session ?? ''} onPick={slot => toggle('session', slot)} />
      </Card>

      <Card>
        <div className="flex flex-wrap items-start justify-between gap-3 mb-3">
          <CardTitle className="mb-0">
            {i18nT('pages.usagePage.prompts_title')}
            <InfoTip text={i18nT('pages.usagePage.prompts_tip')} />
          </CardTitle>
          {filterParts.length > 0 && <Btn onClick={clear}>{i18nT('pages.usagePage.clear_filters')}</Btn>}
        </div>
        {filterParts.length > 0 && (
          <p className="mb-2 text-[12px] text-muted">{i18nT('pages.usagePage.filtered_to', { filters: filterParts.join(' · ') })}</p>
        )}
        <PromptsTable
          query={{
            from: range.from,
            to: range.to,
            session: filters.session,
            service: filters.service,
            category: filters.category,
            model: filters.model,
            reason: filters.reason,
          }}
          onFilter={setFilter}
        />
      </Card>
    </>
  )
}
