import { useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import type { ChatMessage } from '../types'
import { extractChatLinks, type ExtractedLink } from '../utils/extractChatLinks'
import { api } from '../api/client'
import { isSystemNoticeKind } from '../lib/systemNotice'
import { TURN_OPENER_ROLES } from '../pages/chat/groupDisplayItems'

export interface ChatSection {
  /** Stable identity for the turn: the message's `meta.clientTs` / `meta.mid` / `ts`, falling back to its index. */
  id: string
  /** Short label for list UIs: the compact prompt cut to 60 chars with an ellipsis. */
  label: string
  /** The full prompt with whitespace collapsed. */
  prompt: string
  /** The turn's final non-empty assistant reply as written, or '' when none has landed. Consumers compact it at render time. */
  response: string
  /** ISO timestamp of the user message, for the preview card's relative time. */
  ts?: string
  msgIdx: number
  displayIdx: number
}

export interface ChatNavigationData {
  links: ExtractedLink[]
  sections: ChatSection[]
  resolving: boolean
}

/** Rows that begin a new transcript turn: the display layer's opener roles
 *  (user, nudge, subagent) plus injected prompts (cron, note, synthesis). A
 *  `recovery` inject resumes the SAME turn after a tool stall, so it is not an
 *  opener and the resumed reply still belongs to the turn it continues. */
function opensTurn(m: ChatMessage): boolean {
  if (m.role === 'inject') return (m.meta as Record<string, unknown> | undefined)?.injectKind !== 'recovery'
  return TURN_OPENER_ROLES.has(m.role)
}

/** Collapse runs of whitespace (including newlines) to one space. */
function compactText(text: string): string {
  return text.replace(/\s+/g, ' ').trim()
}

/** Extract ~3 lines of context around a URL from the message text */
function extractContext(text: string, url: string): string {
  const idx = text.indexOf(url)
  if (idx < 0) return ''
  const start = text.lastIndexOf('\n', Math.max(0, idx - 150))
  const end = text.indexOf('\n', idx + url.length + 150)
  return text.slice(start === -1 ? 0 : start, end === -1 ? undefined : end).trim().slice(0, 400)
}

const labelCache = new Map<string, string>()

export function useChatNavigation(
  messages: ChatMessage[],
  messageToDisplayIdx: Map<number, number>,
  options?: { resolve?: boolean },
): ChatNavigationData {
  const resolve = options?.resolve !== false
  const [labels, setLabels] = useState(() => new Map(labelCache))
  const links = useMemo(() => extractChatLinks(messages), [messages])

  // Compute which links need LLM resolution (bare URLs, not from markdown)
  const linksToResolve = useMemo(() => {
    return links
      .map((link, i) => ({ url: link.url, context: extractContext(messages[link.msgIdx]?.content || '', link.url), idx: i }))
      .filter((_, i) => !links[i].fromMarkdown)
  }, [links, messages])

  // Stable batch key for the entire set of links to resolve
  const pending = useMemo(
    () => linksToResolve.filter(link => !labels.has(link.url)).slice(0, 20),
    [linksToResolve, labels],
  )
  const batchKey = useMemo(() => pending.map(l => l.url).join('|'), [pending])

  const { isLoading: resolving } = useQuery({
    queryKey: ['nav-link-summaries', batchKey],
    queryFn: () =>
      api.resolveNavLinks(pending.map(({ url, context }) => ({ url, context }))).then(r => {
        pending.forEach((link, i) => {
          const summary = r.summaries[i]
          if (summary && summary.length >= 3) labelCache.set(link.url, summary)
        })
        setLabels(new Map(labelCache))
        return r.summaries
      }),
    staleTime: Infinity,
    gcTime: Infinity,
    enabled: resolve && pending.length > 0,
  })

  const resolvedLinks = useMemo(() => {
    const result = [...links]
    for (const link of linksToResolve) {
      const summary = labels.get(link.url)
      if (summary) result[link.idx] = { ...result[link.idx], label: summary }
    }
    return result
  }, [links, linksToResolve, labels])

  const sections = useMemo(() => {
    const result: ChatSection[] = []
    // Legacy rows may share a `ts`; suffix repeats so ids stay unique (they are
    // React keys and the minimap's selection identity).
    const seen = new Map<string, number>()
    for (let i = 0; i < messages.length; i++) {
      if (messages[i].role !== 'user') continue
      const content = messages[i].content || ''
      if (!content.trim()) continue
      const displayIdx = messageToDisplayIdx.get(i) ?? -1
      if (displayIdx < 0) continue
      const prompt = compactText(content)
      const label = prompt.slice(0, 60) + (prompt.length > 60 ? '…' : '')
      // The turn's reply is the last non-empty assistant message before the
      // next turn opener (user / nudge / subagent, or an injected prompt),
      // skipping compaction / reload notices that
      // share the assistant role but are not replies.
      let response = ''
      for (let next = i + 1; next < messages.length && !opensTurn(messages[next]); next++) {
        const m = messages[next]
        if (m.role !== 'assistant') continue
        if (isSystemNoticeKind(m.kind ?? (m.meta?.kind as string | undefined))) continue
        const text = m.content || ''
        if (text.trim()) response = text
      }
      const meta = messages[i].meta as Record<string, unknown> | undefined
      // `clientTs` is set at send time and never changes; `mid` arrives once the
      // send is accepted, so preferring it would re-identify the turn mid-flight.
      const explicitId = meta?.clientTs ?? meta?.mid ?? messages[i].ts
      const baseId = typeof explicitId === 'string' && explicitId ? explicitId : `turn-${i}`
      const dup = seen.get(baseId) ?? 0
      seen.set(baseId, dup + 1)
      result.push({
        id: dup === 0 ? baseId : `${baseId}#${dup}`,
        label,
        prompt,
        response,
        ts: messages[i].ts || undefined,
        msgIdx: i,
        displayIdx,
      })
    }
    return result
  }, [messages, messageToDisplayIdx])

  return { links: resolvedLinks, sections, resolving }
}
