import { useEffect, useRef, useCallback, useState } from 'react'

export type WatchStatus = 'idle' | 'connecting' | 'open' | 'error'

/** REL-7: reconnect backoff — 1 s doubling, capped at 30 s. */
const RETRY_BASE_MS = 1_000
const RETRY_MAX_MS = 30_000

/** Subscribe to SSE file-change events at GET /api/file-watch?path=... */
export function useFileWatch(
  filePath: string | null,
  onContent: (content: string) => void,
) {
  const cbRef = useRef(onContent)
  cbRef.current = onContent
  const esRef = useRef<EventSource | null>(null)
  const timerRef = useRef<number | null>(null)
  const attemptRef = useRef(0)
  const [status, setStatus] = useState<WatchStatus>('idle')

  const clearRetry = useCallback(() => {
    if (timerRef.current != null) {
      clearTimeout(timerRef.current)
      timerRef.current = null
    }
  }, [])

  const stop = useCallback(() => {
    clearRetry()
    esRef.current?.close()
    esRef.current = null
    setStatus('idle')
  }, [clearRetry])

  useEffect(() => {
    if (!filePath) { setStatus('idle'); return }

    let cancelled = false

    const scheduleRetry = () => {
      // REL-7: a transient drop re-subscribes with capped exponential
      // backoff; the attempt counter resets on a successful open.
      const delay = Math.min(RETRY_BASE_MS * 2 ** attemptRef.current, RETRY_MAX_MS)
      attemptRef.current += 1
      timerRef.current = window.setTimeout(() => {
        if (!cancelled) open()
      }, delay)
    }

    const open = () => {
      setStatus('connecting')
      const es = new EventSource('/api/file-watch?path=' + encodeURIComponent(filePath))
      esRef.current = es

      es.onopen = () => {
        attemptRef.current = 0
        setStatus('open')
      }
      es.onmessage = (ev) => {
        try {
          const data = JSON.parse(ev.data)
          if (data.content != null) cbRef.current(data.content)
        } catch { /* ignore parse errors */ }
      }
      es.onerror = () => {
        // A directory or missing path makes the backend return 404 (see
        // api_file_watch's os.path.isfile guard); a refused/closed stream
        // also fires onerror. EventSource AUTO-RECONNECTS on error, so a
        // permanently bad path would turn into an endless reconnect loop
        // hammering the endpoint — the stream is always closed explicitly.
        // REL-7: then CLASSIFY before settling. Probe the file-read endpoint
        // once: a 404 is terminal (status 'error', no retry loop); anything
        // else — a gateway restart, laptop sleep, network drop, or a probe
        // that cannot even connect — is transient and re-subscribes with
        // bounded backoff, so a live file tab survives the drop instead of
        // going quiet until the user switches tabs.
        es.close()
        esRef.current = null
        fetch('/api/file-read?path=' + encodeURIComponent(filePath))
          .then(probe => {
            if (cancelled) return
            if (probe.status === 404) { setStatus('error'); return }
            scheduleRetry()
          })
          .catch(() => {
            if (!cancelled) scheduleRetry()
          })
      }
    }

    open()

    return () => {
      cancelled = true
      clearRetry()
      esRef.current?.close()
      esRef.current = null
      setStatus('idle')
    }
  }, [filePath, clearRetry])

  return { stop, status }
}
