/**
 * Shared vocabulary of the Usage page: the date ranges it offers, the spend
 * categories and their colours, and the human names of the services a usage row
 * can be filed under. Label maps hold catalog KEYS, never strings, so nothing is
 * translated at import time.
 */
import type { UsageCategory, UsageReason } from '../../api/client'
import { fmtCompact, fmtCredits, fmtDateFields, fmtElapsed, fmtNumber, fmtPercent } from '../../i18n/format'
import { i18nT } from '../../i18n/t'

export type UsageRange = 'today' | '7d' | '30d' | '90d' | '12m'

export const RANGES: UsageRange[] = ['today', '7d', '30d', '90d', '12m']

export const RANGE_LABEL_KEY: Record<UsageRange, string> = {
  today: 'pages.usagePage.range_today',
  '7d': 'pages.usagePage.range_7d',
  '30d': 'pages.usagePage.range_30d',
  '90d': 'pages.usagePage.range_90d',
  '12m': 'pages.usagePage.range_12m',
}

/** A local calendar date as the API's `YYYY-MM-DD` (a wire value, not display text). */
export function isoDay(d: Date): string {
  const mm = String(d.getMonth() + 1).padStart(2, '0')
  const dd = String(d.getDate()).padStart(2, '0')
  return [d.getFullYear(), mm, dd].join('-')
}

/** The `[from, to]` local days a range covers, ending today. */
export function rangeDays(range: UsageRange, now: Date = new Date()): { from: string; to: string } {
  const to = isoDay(now)
  const start = new Date(now.getFullYear(), now.getMonth(), now.getDate())
  if (range === '12m') return { from: isoDay(new Date(now.getFullYear(), now.getMonth() - 11, 1)), to }
  const back = { today: 0, '7d': 6, '30d': 29, '90d': 89 }[range]
  start.setDate(start.getDate() - back)
  return { from: isoDay(start), to }
}

/** Parse a `YYYY-MM-DD` / `YYYY-MM` key as a LOCAL date (never UTC midnight). */
export function parseDayKey(key: string): Date {
  const [y, m, d] = key.split('-').map(Number)
  return new Date(y, (m || 1) - 1, d || 1)
}

export const CATEGORIES: UsageCategory[] = ['chat', 'channels', 'background', 'subagents', 'workflows', 'apps']

export const CATEGORY_LABEL_KEY: Record<UsageCategory, string> = {
  chat: 'pages.usagePage.cat_chat',
  channels: 'pages.usagePage.cat_channels',
  background: 'pages.usagePage.cat_background',
  subagents: 'pages.usagePage.cat_subagents',
  workflows: 'pages.usagePage.cat_workflows',
  apps: 'pages.usagePage.cat_apps',
}

/** Polarity-fixed category hues (`--usage-cat-*` in index.css). */
export const CATEGORY_COLOR: Record<UsageCategory, string> = {
  chat: 'var(--usage-cat-chat)',
  channels: 'var(--usage-cat-channels)',
  background: 'var(--usage-cat-background)',
  subagents: 'var(--usage-cat-subagents)',
  workflows: 'var(--usage-cat-workflows)',
  apps: 'var(--usage-cat-apps)',
}

export function categoryLabel(category: string): string {
  const key = CATEGORY_LABEL_KEY[category as UsageCategory]
  return key ? i18nT(key) : category
}

/**
 * Human names for the kind part of a row's service (`cron` of `cron:Daily digest`)
 * or, for a row with no service, its surface. An unknown kind shows as written:
 * a new writer is visible rather than folded into a guess.
 */
const SERVICE_KIND_KEY: Record<string, string> = {
  dashboard: 'pages.usagePage.svc_dashboard',
  cli: 'pages.usagePage.svc_cli',
  slack: 'pages.usagePage.svc_slack',
  telegram: 'pages.usagePage.svc_telegram',
  discord: 'pages.usagePage.svc_discord',
  channel: 'pages.usagePage.svc_channel',
  cron: 'pages.usagePage.svc_cron',
  cron_result: 'pages.usagePage.svc_cron_result',
  heartbeat: 'pages.usagePage.svc_heartbeat',
  autonudge: 'pages.usagePage.svc_autonudge',
  monitor: 'pages.usagePage.svc_monitor',
  webhook: 'pages.usagePage.svc_webhook',
  taskrunner: 'pages.usagePage.svc_taskrunner',
  taskrunner_summary: 'pages.usagePage.svc_taskrunner_summary',
  taskrunner_decompose: 'pages.usagePage.svc_taskrunner_decompose',
  taskrunner_refine: 'pages.usagePage.svc_taskrunner_refine',
  taskrunner_lesson: 'pages.usagePage.svc_taskrunner_lesson',
  subagent: 'pages.usagePage.svc_subagent',
  subagent_completion: 'pages.usagePage.svc_subagent_completion',
  subagent_recovery: 'pages.usagePage.svc_subagent_recovery',
  subagent_synthesis: 'pages.usagePage.svc_subagent_synthesis',
  workflow: 'pages.usagePage.svc_workflow',
  workflow_author: 'pages.usagePage.svc_workflow_author',
  workflow_result: 'pages.usagePage.svc_workflow_result',
  judge: 'pages.usagePage.svc_judge',
  knowledge: 'pages.usagePage.svc_knowledge',
  auto_research: 'pages.usagePage.svc_auto_research',
  chat_title: 'pages.usagePage.svc_chat_title',
  session_summary: 'pages.usagePage.svc_session_summary',
  memory_consolidation: 'pages.usagePage.svc_memory_consolidation',
  skill_extraction: 'pages.usagePage.svc_skill_extraction',
  compaction: 'pages.usagePage.svc_compaction',
  issue_radar: 'pages.usagePage.svc_issue_radar',
  app: 'pages.usagePage.svc_app',
  mcp_app: 'pages.usagePage.svc_mcp_app',
  spec_builder: 'pages.usagePage.svc_spec_builder',
  auto_improvement: 'pages.usagePage.svc_auto_improvement',
  code_review_sage: 'pages.usagePage.svc_code_review_sage',
  meetings: 'pages.usagePage.svc_meetings',
  meetings_translate: 'pages.usagePage.svc_meetings_translate',
  eval: 'pages.usagePage.svc_eval',
  eval_judge: 'pages.usagePage.svc_eval_judge',
  optimizer: 'pages.usagePage.svc_optimizer',
  side: 'pages.usagePage.svc_side',
  thread: 'pages.usagePage.svc_thread',
  bg: 'pages.usagePage.svc_bg',
}

/** `cron:Daily digest` → "Scheduled job · Daily digest"; `chat_title` → "Chat titles". */
export function serviceLabel(service: string): string {
  const cut = service.indexOf(':')
  const kind = cut < 0 ? service : service.slice(0, cut)
  const detail = cut < 0 ? '' : service.slice(cut + 1)
  const key = SERVICE_KIND_KEY[kind]
  const kindLabel = key ? i18nT(key) : kind || i18nT('pages.usagePage.svc_unknown')
  return detail ? i18nT('pages.usagePage.service_with_detail', { kind: kindLabel, detail }) : kindLabel
}

export function modelLabel(model: string): string {
  if (!model || model === 'unknown') return i18nT('pages.usagePage.model_unknown')
  if (model === 'auto') return i18nT('pages.usagePage.model_auto')
  return model
}

const OUTCOME_LABEL_KEY: Record<string, string> = {
  ok: 'pages.usagePage.outcome_ok',
  error: 'pages.usagePage.outcome_error',
  cancelled: 'pages.usagePage.outcome_cancelled',
  timeout: 'pages.usagePage.outcome_timeout',
  tool_stall: 'pages.usagePage.outcome_tool_stall',
  stale_recover: 'pages.usagePage.outcome_stale_recover',
  stall_exhausted: 'pages.usagePage.outcome_stall_exhausted',
}

export function outcomeLabel(outcome: string | null): string {
  const key = outcome ? OUTCOME_LABEL_KEY[outcome] : undefined
  return key ? i18nT(key) : i18nT('pages.usagePage.outcome_unknown')
}

export function outcomeVariant(outcome: string | null): 'ok' | 'err' | 'warn' | 'muted' {
  if (outcome === 'ok') return 'ok'
  if (outcome === 'error' || outcome === 'timeout' || outcome === 'stall_exhausted') return 'err'
  if (outcome === 'tool_stall' || outcome === 'stale_recover') return 'warn'
  return 'muted'
}

/** Credits for display, `—` for nothing. */
export function credits(value: number | null | undefined): string {
  return value === null || value === undefined ? '—' : fmtCredits(value)
}

/** A measured duration for display, `—` when the surface did not measure one. */
export function duration(ms: number | null | undefined): string {
  return ms === null || ms === undefined || ms <= 0 ? '—' : fmtElapsed(ms)
}

/** The label of one series point (an hour, a day or a month). */
export function seriesPointLabel(key: string, granularity: 'hour' | 'day' | 'month', day?: string): string {
  if (granularity === 'hour') {
    const base = day ? parseDayKey(day) : new Date()
    base.setHours(Number(key), 0, 0, 0)
    return fmtDateFields(base, { hour: 'numeric' })
  }
  if (granularity === 'month') return fmtDateFields(parseDayKey(key), { month: 'short', year: 'numeric' })
  return fmtDateFields(parseDayKey(key), { month: 'short', day: 'numeric' })
}

/** The short axis tick for one series point. */
export function seriesTickLabel(key: string, granularity: 'hour' | 'day' | 'month'): string {
  if (granularity === 'hour') return key
  if (granularity === 'month') return fmtDateFields(parseDayKey(key), { month: 'short' })
  return fmtDateFields(parseDayKey(key), { day: 'numeric' })
}

// ── why a prompt cost what it did ────────────────────────────────────────────

/** A turn's reason codes (`credit_report.row_reasons`), as plain-language facts. */
export function reasonLabel(reason: UsageReason): string {
  const n = typeof reason.value === 'number' ? reason.value : NaN
  switch (reason.code) {
    case 'tools':
      return i18nT('pages.usagePage.reason_tools', { value: fmtNumber(n) })
    case 'context':
      return i18nT('pages.usagePage.reason_context', { pct: fmtPercent(n / 100) })
    case 'compacted':
      return i18nT('pages.usagePage.reason_compacted')
    case 'big_request':
      return i18nT('pages.usagePage.reason_big_request', { size: fmtCompact(n) })
    case 'long_answer':
      return i18nT('pages.usagePage.reason_long_answer', { size: fmtCompact(n) })
    case 'long_run':
      return i18nT('pages.usagePage.reason_long_run', { duration: fmtElapsed(n) })
    case 'premium_model':
      return i18nT('pages.usagePage.reason_premium_model', { value: fmtNumber(n, { maximumFractionDigits: 2 }) })
    case 'unfinished':
      return i18nT('pages.usagePage.reason_unfinished', { outcome: outcomeLabel(String(reason.value)) })
    default:
      return reason.code
  }
}

/** One sentence per reason code: why that fact costs credits. */
const REASON_HINT_KEY: Record<string, string> = {
  tools: 'pages.usagePage.reason_hint_tools',
  context: 'pages.usagePage.reason_hint_context',
  compacted: 'pages.usagePage.reason_hint_compacted',
  big_request: 'pages.usagePage.reason_hint_big_request',
  long_answer: 'pages.usagePage.reason_hint_long_answer',
  long_run: 'pages.usagePage.reason_hint_long_run',
  premium_model: 'pages.usagePage.reason_hint_premium_model',
  unfinished: 'pages.usagePage.reason_hint_unfinished',
}

export function reasonHint(code: string): string {
  const key = REASON_HINT_KEY[code]
  return key ? i18nT(key) : ''
}

const TOOL_KIND_KEY: Record<string, string> = {
  read: 'pages.usagePage.tool_read',
  edit: 'pages.usagePage.tool_edit',
  delete: 'pages.usagePage.tool_delete',
  move: 'pages.usagePage.tool_move',
  search: 'pages.usagePage.tool_search',
  execute: 'pages.usagePage.tool_execute',
  think: 'pages.usagePage.tool_think',
  fetch: 'pages.usagePage.tool_fetch',
  switch_mode: 'pages.usagePage.tool_switch_mode',
  other: 'pages.usagePage.tool_other',
}

/** `read` → "Read files"; `mcp:github` → "MCP · github". */
export function toolKindLabel(kind: string): string {
  if (kind.startsWith('mcp:')) return i18nT('pages.usagePage.tool_mcp', { server: kind.slice(4) })
  const key = TOOL_KIND_KEY[kind]
  return key ? i18nT(key) : kind
}

const TOOL_BAND_KEY: Record<string, string> = {
  '0': 'pages.usagePage.band_tools_none',
  '1-3': 'pages.usagePage.band_tools_1_3',
  '4-10': 'pages.usagePage.band_tools_4_10',
  '11-25': 'pages.usagePage.band_tools_11_25',
  '26+': 'pages.usagePage.band_tools_26',
}

const CONTEXT_BAND_KEY: Record<string, string> = {
  '0-25': 'pages.usagePage.band_context_0_25',
  '25-50': 'pages.usagePage.band_context_25_50',
  '50-75': 'pages.usagePage.band_context_50_75',
  '75-100': 'pages.usagePage.band_context_75_100',
}

export function toolBandLabel(band: string): string {
  const key = TOOL_BAND_KEY[band]
  return key ? i18nT(key) : band
}

export function contextBandLabel(band: string): string {
  const key = CONTEXT_BAND_KEY[band]
  return key ? i18nT(key) : band
}

const REASON_NAME_KEY: Record<string, string> = {
  tools: 'pages.usagePage.reason_name_tools',
  context: 'pages.usagePage.reason_name_context',
  compacted: 'pages.usagePage.reason_name_compacted',
  big_request: 'pages.usagePage.reason_name_big_request',
  long_answer: 'pages.usagePage.reason_name_long_answer',
  long_run: 'pages.usagePage.reason_name_long_run',
  premium_model: 'pages.usagePage.reason_name_premium_model',
  unfinished: 'pages.usagePage.reason_name_unfinished',
}

/** A reason code's name without a value, for a filter: "Many tool calls". */
export function reasonName(code: string): string {
  const key = REASON_NAME_KEY[code]
  return key ? i18nT(key) : code
}
