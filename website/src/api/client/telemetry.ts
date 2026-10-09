/**
 * Local telemetry and usage readouts: crew-log projections, startup
 * telemetry, the per-session context trace and per-turn usage, WakaTime stats
 * and export, the beacon and collection privacy posture, and Kiro credit and
 * provider usage.
 *
 * The Kiro usage payload types stay defined in `api/client.ts`, next to the
 * view model normalized from them, so they are imported from there as types.
 *
 * `wakatimeExportDownload` is defined in `api/client.ts`: it reads
 * `api.wakatimeExportUrl` at call time, as it always has.
 */

import type { KiroUsagePayload, KiroUsageRefreshResponse } from '../client'
import type { ClientTransport } from './transport'

/** WakaTime coding-stats payload (GET /api/wakatime/stats). When the
 *  integration is off the endpoint returns { configured: false } instead. */
export type WakaTimeStatsEntry = { name: string; total_seconds: number }

export type WakaTimeStats = {
  configured: boolean
  range?: string
  stats?: {
    total_seconds?: number
    daily_average?: number
    languages?: WakaTimeStatsEntry[]
    projects?: WakaTimeStatsEntry[]
  }
}

/** The Usage page's spend categories, in display order (`credit_report.CATEGORIES`). */
export type UsageCategory = 'chat' | 'channels' | 'background' | 'subagents' | 'workflows' | 'apps'

/** Running totals for one breakdown key of the credit summary. */
export type UsageFigures = {
  credits: number
  turns: number
  avg_credits: number
  faults: number
  p50_duration_ms: number | null
  last_ts: number | null
}

/** One bucket of a distribution: `[lo, hi)`; `hi` is null for the open top bucket. */
export type UsageBucket = { lo: number; hi: number | null; count: number }

export type UsageSessionRow = {
  slot: string
  title: string | null
  category: UsageCategory
  channel: string
  credits: number
  own_credits: number
  subagent_credits: number
  background_credits: number
  turns: number
  subagent_turns: number
  background_turns: number
  models: string[]
  first_ts: number
  last_ts: number
  peak_context_pct: number | null
}

/** The account's own consumption against what the row store recorded. */
export type UsageReconciliation = {
  from_ts: number
  to_ts: number
  readings: number
  account_credits: number
  recorded_credits: number
  unattributed_credits: number
  series: { ts: number; account: number; recorded: number }[]
}

/** GET /api/usage/credits/summary — every figure counts every row, subagents included. */
export type UsageCreditSummary = {
  range: { from: string; to: string; days: number; granularity: 'hour' | 'day' | 'month' }
  totals: {
    credits: number
    cost_usd: number
    turns: number
    sessions: number
    models: number
    avg_credits: number
    p50_duration_ms: number | null
    p90_duration_ms: number | null
    p50_ttft_ms: number | null
    p90_ttft_ms: number | null
    faults: number
    outcomes: Record<string, number>
  }
  prior: { from: string; to: string; credits: number; turns: number; delta_pct: number | null }
  series: {
    key: string
    credits: number
    turns: number
    by_category: Partial<Record<UsageCategory, number>>
    p50_duration_ms: number | null
    p90_duration_ms: number | null
  }[]
  by_category: (UsageFigures & { category: UsageCategory })[]
  by_service: (UsageFigures & { service: string; surface: string; category: UsageCategory })[]
  by_model: (UsageFigures & { model: string })[]
  by_hour: number[]
  distributions: { duration_ms: UsageBucket[]; ttft_ms: UsageBucket[]; credits: UsageBucket[] }
  sessions: UsageSessionRow[]
  sessions_total: number
  months: { month: string; credits: number; turns: number }[]
  reconciliation: UsageReconciliation | null
  /** Average credits per prompt by tool-call count and by context fill. */
  drivers: {
    tool_calls: UsageDriverBand[]
    context_fill: UsageDriverBand[]
    measured_turns: number
  }
  /** The range's most expensive prompts, each with its reasons. */
  top_turns: UsageTurnRow[]
  /** Background services' rhythm: runs, cost per run and per day, a 30-day pace. */
  recurring: UsageRecurringRow[]
  /** Tool kinds by calls; `credits` totals the prompts that used the kind. */
  tools: { kind: string; calls: number; turns: number; credits: number }[]
  /** The account's own credits per day across its recent readings. */
  account_rate: { per_day: number; days: number; readings: number } | null
}

export type UsageDriverBand = { band: string; turns: number; credits: number; avg_credits: number }

export type UsageRecurringRow = {
  service: string
  runs: number
  active_days: number
  runs_per_day: number
  credits: number
  credits_per_run: number
  credits_per_day: number
  projected_30d: number
  faults: number
  last_ts: number
}

/** A fact that made a turn heavier than a plain answer (never a share of its bill). */
export type UsageReason = { code: string; value: number | string }

/** One per-prompt row of GET /api/usage/credits/turns. */
export type UsageTurnRow = {
  ts: number
  session: string
  slot: string
  parent: string | null
  for_slot: string | null
  share: 'own' | 'subagents' | 'background'
  title?: string | null
  category: UsageCategory
  surface: string
  service: string
  request: string | null
  model: string
  rate_multiplier: number | null
  agent: string
  app: string
  credits: number
  cost_usd: number
  duration_ms: number | null
  ttft_ms: number | null
  outcome: string | null
  stop_reason: string | null
  context_pct: number | null
  context_tokens: number | null
  tool_calls: number | null
  tool_kinds: Record<string, number>
  prompt_chars: number | null
  output_chars: number | null
  compactions: number | null
  reasons: UsageReason[]
}

/** Exact-match filters for the per-prompt page; empty values are omitted. */
export type UsageTurnsQuery = {
  from: string
  to: string
  session?: string
  service?: string
  category?: string
  model?: string
  outcome?: string
  reason?: string
  before?: number
  limit?: number
}

export type UsageTurnsPage = {
  turns: UsageTurnRow[]
  next_before: number | null
  range: { from: string; to: string }
}

/**
 * Fill the fields a gateway older than the page may not send (the cost-driver
 * blocks, the per-turn activity and reasons), so a dashboard served by a gateway
 * that has not restarted onto the new backend renders the older data instead of
 * failing on an absent array.
 */
function normalizeTurnRow(row: Partial<UsageTurnRow>): UsageTurnRow {
  return {
    parent: null,
    for_slot: null,
    share: row.parent ? 'subagents' : 'own',
    request: null,
    rate_multiplier: null,
    stop_reason: null,
    context_tokens: null,
    tool_calls: null,
    prompt_chars: null,
    output_chars: null,
    compactions: null,
    ...row,
    tool_kinds: row.tool_kinds ?? {},
    reasons: row.reasons ?? [],
  } as UsageTurnRow
}

function normalizeSummary(summary: Partial<UsageCreditSummary>): UsageCreditSummary {
  return {
    ...summary,
    sessions: (summary.sessions ?? []).map(s => ({
      ...s,
      background_credits: s.background_credits ?? 0,
      background_turns: s.background_turns ?? 0,
    })),
    drivers: summary.drivers ?? { tool_calls: [], context_fill: [], measured_turns: 0 },
    top_turns: (summary.top_turns ?? []).map(normalizeTurnRow),
    recurring: summary.recurring ?? [],
    tools: summary.tools ?? [],
    account_rate: summary.account_rate ?? null,
  } as UsageCreditSummary
}

export function createTelemetryEndpoints({ get, post, j }: ClientTransport) {
  const usageReadouts = {
    /** The five session folds of a crew log, keyed by name, in ONE request.
     *
     *  The batch route is what makes the answer coherent: it resolves the session
     *  once and folds once, so all five values come from the same file at the same
     *  moment. Five per-name requests could not promise that -- a session replaced
     *  while they were in flight would leave some describing the unit going away and
     *  some the one arriving, and the panel would show a mix it cannot detect.
     *
     *  Each fold still carries its OWN `seq`, because they really do differ: an entry
     *  advances the folds it belongs to and leaves the rest where they were. */
    sessionCrewLogProjections: async (slot: string) => {
      const body = await fetch(`/api/sessions/${encodeURIComponent(slot)}/crew-log/projections`).then(j)
      const read = body as {
        projections?: Record<string, unknown>
        unit?: unknown
        resolved?: unknown
        writes_drained?: unknown
        recording?: unknown
        flag_value?: unknown
        flag_recognised?: unknown
        env_file?: unknown
      }
      return {
        folds: read.projections ?? {},
        // The unit these folds came from. A pushed `session_projection` frame names
        // its unit and is applied only when it names this one. Empty from an older
        // gateway, which pushes no such frame.
        unit: typeof read.unit === 'string' ? read.unit : '',
        // Whether a unit was NAMED for the id sent. An empty fold cannot say why it
        // is empty, and the two reasons need different words on screen: a slot that
        // never recorded anything, versus one whose ACP session was torn down and
        // whose record is still on disk under the retired id.
        resolved: read.resolved !== false,
        // False when the writer still owed this process entries as the fold was
        // taken, so the value may be behind the record. Absent reads as drained: an
        // older gateway does not send the field and did not race either.
        writesDrained: read.writes_drained !== false,
        // False only when the gateway says recording is switched off. Absent reads as
        // on: an older gateway does not send the field.
        recording: read.recording !== false,
        // The KIROCREW_CREW_LOG value that switched it off, so the panel can quote it.
        // Empty when the gateway does not send one.
        flagValue: typeof read.flag_value === 'string' ? read.flag_value : '',
        // False when that value is not one of the switch-off spellings.
        flagRecognised: read.flag_recognised !== false,
        // The `.env` the gateway reads; the default home's when the gateway sends none.
        envFile: typeof read.env_file === 'string' && read.env_file ? read.env_file : '~/.kiro/crew/.env',
      }
    },
    /** The conductor's accepted work, not worker-reported completion. */
    sessionWorkProjection: (slot: string) =>
      get(`/api/sessions/${encodeURIComponent(slot)}/crew-log/projection/work`).then(j),
    telemetryStartup: () => fetch('/api/telemetry/startup').then(j),
    // Per-turn context injection breakdown for one session. Independent of the
    // telemetry main switch: the usage rows it reads are always written.
    telemetryContextTrace: (slot: string) =>
      fetch('/api/telemetry/context-trace?slot=' + encodeURIComponent(slot)).then(j),
    /** Per-turn usage rows for one session — the Spend table's drill-down.
     *  Same always-written row store as the context trace; the dashboard reads
     *  every row (the endpoint's app-ownership filter applies to app callers). */
    usageTurns: (slot: string) =>
      fetch('/api/usage/turns?slot=' + encodeURIComponent(slot)).then(j),
    /** WakaTime coding stats for a named range. Returns { configured: false }
     *  when the integration is off; a 502 body carries { code: 'upstream_unavailable' }. */
    wakatimeStats: (range: string) =>
      fetch('/api/wakatime/stats?range=' + encodeURIComponent(range)).then(j) as Promise<WakaTimeStats>,
    /** Download URL for the billable-hours export. The browser navigates to it so
     *  the CSV/JSON arrives via the endpoint's own Content-Disposition. */
    wakatimeExportUrl: (start: string, end: string, format: 'csv' | 'json') =>
      `/api/wakatime/export?start=${encodeURIComponent(start)}&end=${encodeURIComponent(end)}&format=${format}`,
  }

  const privacyPosture = {
    beaconStatus: () => fetch('/api/telemetry/beacon').then(j),
    /** Local metric-collection posture for the Privacy panel's recording switch.
     *  Separate from telemetryStartup(), which parses every shard in the window. */
    collectionStatus: () => fetch('/api/telemetry/collection').then(j),
  }

  const creditUsage = {
    sessionsUsage: () => fetch('/api/sessions/usage').then(j) as Promise<{ usage?: KiroUsagePayload }>,
    /**
     * Refresh the credit reading now (the account modal's Refresh button). Same
     * `{usage}` envelope as `sessionsUsage`, so `parseKiroUsagePayload` reads
     * both. `skipped: 'scrape_parked'` (with `retry_after` seconds) means the
     * free API returned no plan and the gateway has parked the `/usage` scrape
     * after repeated failures, so no new reading was fetched: `usage` is a
     * same-identity prior reading dimmed `stale`, or an unavailable marker. The
     * one refusal is 409 `refresh_in_flight` while a refresh is already running.
     */
    sessionsUsageRefresh: () => post('/api/sessions/usage/refresh').then(j) as Promise<KiroUsageRefreshResponse>,
    providerUsage: () => fetch('/api/usage').then(j),
    /** The Usage page's aggregate over the local days `[from, to]` (YYYY-MM-DD). */
    usageCreditsSummary: (from: string, to: string) =>
      fetch(`/api/usage/credits/summary?from=${encodeURIComponent(from)}&to=${encodeURIComponent(to)}`)
        .then(j)
        .then(body => normalizeSummary(body as Partial<UsageCreditSummary>)),
    /** One page of per-prompt rows, newest first; pass `next_before` back as `before`. */
    usageCreditsTurns: (query: UsageTurnsQuery) => {
      const params = new URLSearchParams()
      for (const [key, value] of Object.entries(query)) {
        if (value !== undefined && value !== '') params.set(key, String(value))
      }
      return fetch('/api/usage/credits/turns?' + params.toString())
        .then(j)
        .then(body => {
          const page = body as UsageTurnsPage
          return { ...page, turns: (page.turns ?? []).map(normalizeTurnRow) }
        })
    },
  }

  const kiroUsage = {
    // A graceful no-op on a public install, where Kiro usage is stubbed; the
    // panels render empty when the feature is absent.
    kiroUsage: () => fetch('/api/usage/kiro').then(j),
  }

  return { usageReadouts, privacyPosture, creditUsage, kiroUsage }
}
