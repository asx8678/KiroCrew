import { describe, it, expect, vi, afterEach } from 'vitest'
import { renderHook, act } from '@testing-library/react'

import { useFileWatch } from './useFileWatch'

/*
 * The watch must distinguish two onerror shapes (REL-7). A missing path or
 * directory is TERMINAL: the backend answers 404, so reconnecting would spin
 * forever. A transient drop — gateway restart, laptop sleep, network blip —
 * must re-subscribe on its own with bounded backoff, so a visible file tab
 * keeps updating without the user switching tabs or reopening the file.
 * The classifier is one probe of the file-read endpoint: 404 settles into
 * 'error' with no retry; anything else schedules a capped-backoff reopen.
 */

// Minimal controllable EventSource mock: capture instances and let tests fire
// lifecycle events synchronously.
class MockEventSource {
  static instances: MockEventSource[] = []
  url: string
  readyState = 0
  closed = false
  onopen: (() => void) | null = null
  onmessage: ((ev: { data: string }) => void) | null = null
  onerror: (() => void) | null = null
  constructor(url: string) {
    this.url = url
    MockEventSource.instances.push(this)
  }
  close() {
    this.closed = true
    this.readyState = 2
  }
}

const probe = (status: number) =>
  vi.fn().mockResolvedValue({ status } as Response)

afterEach(() => {
  MockEventSource.instances = []
  vi.unstubAllGlobals()
  vi.useRealTimers()
})

describe('useFileWatch reconnect guard', () => {
  it('settles into terminal error (no reconnect) when the probe answers 404', async () => {
    vi.stubGlobal('EventSource', MockEventSource as unknown as typeof EventSource)
    vi.stubGlobal('fetch', probe(404))
    const { result } = renderHook(() => useFileWatch('/some/dir', () => {}))

    // One stream opened, status connecting.
    expect(MockEventSource.instances).toHaveLength(1)
    expect(result.current.status).toBe('connecting')

    // Backend 404 (directory) → onerror fires.
    await act(async () => { MockEventSource.instances[0].onerror?.() })

    // The stream is closed (so the browser cannot auto-reconnect) and the hook
    // settles into a stable 'error' state — it does NOT open a second stream.
    expect(MockEventSource.instances[0].closed).toBe(true)
    expect(result.current.status).toBe('error')
    expect(MockEventSource.instances).toHaveLength(1)
  })

  it('re-subscribes with backoff when the probe says the file still exists (REL-7)', async () => {
    vi.useFakeTimers()
    vi.stubGlobal('EventSource', MockEventSource as unknown as typeof EventSource)
    vi.stubGlobal('fetch', probe(200))
    const { result } = renderHook(() => useFileWatch('/a/live/file.md', () => {}))

    expect(MockEventSource.instances).toHaveLength(1)
    act(() => { MockEventSource.instances[0].onerror?.() })
    expect(MockEventSource.instances[0].closed).toBe(true)

    // Not yet: the first backoff step has not elapsed.
    expect(MockEventSource.instances).toHaveLength(1)

    await act(async () => { await vi.advanceTimersByTimeAsync(1_000) })
    expect(MockEventSource.instances).toHaveLength(2)
    expect(result.current.status).toBe('connecting')

    // A successful open resets the attempt counter, so the next drop starts
    // at the 1 s step again rather than doubling forever.
    act(() => { MockEventSource.instances[1].onopen?.() })
    expect(result.current.status).toBe('open')
    act(() => { MockEventSource.instances[1].onerror?.() })
    await act(async () => { await vi.advanceTimersByTimeAsync(1_000) })
    expect(MockEventSource.instances).toHaveLength(3)
  })

  it('opens nothing after unmount during the backoff window', async () => {
    vi.useFakeTimers()
    vi.stubGlobal('EventSource', MockEventSource as unknown as typeof EventSource)
    vi.stubGlobal('fetch', probe(200))
    const { result, unmount } = renderHook(() => useFileWatch('/a/live/file.md', () => {}))

    await act(async () => { MockEventSource.instances[0].onerror?.() })
    unmount()

    await act(async () => { await vi.advanceTimersByTimeAsync(30_000) })
    expect(MockEventSource.instances).toHaveLength(1)
    // The cancelled retry timer opened nothing, and the dropped stream stays closed.
    expect(MockEventSource.instances[0].closed).toBe(true)
  })

  it('delivers content on message and is idle with no path', () => {
    vi.stubGlobal('EventSource', MockEventSource as unknown as typeof EventSource)
    const onContent = vi.fn()
    const { result, rerender } = renderHook(
      ({ p }: { p: string | null }) => useFileWatch(p, onContent),
      { initialProps: { p: '/a/file.md' as string | null } },
    )
    act(() => {
      MockEventSource.instances[0].onopen?.()
      MockEventSource.instances[0].onmessage?.({ data: JSON.stringify({ content: 'hi' }) })
    })
    expect(result.current.status).toBe('open')
    expect(onContent).toHaveBeenCalledWith('hi')

    // Clearing the path tears down and returns to idle.
    rerender({ p: null })
    expect(result.current.status).toBe('idle')
    expect(MockEventSource.instances[0].closed).toBe(true)
  })
})
