"use client"

import { useCallback, useEffect, useRef, useState } from "react"

/** Fetches immediately, then every `intervalMs`; keeps the last good value on transient errors. */
export function usePoll<T>(fetcher: () => Promise<T>, intervalMs = 2000, deps: unknown[] = []) {
  const [data, setData] = useState<T | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [updatedAt, setUpdatedAt] = useState<Date | null>(null)
  const fetchRef = useRef(fetcher)
  fetchRef.current = fetcher

  const load = useCallback(async () => {
    try {
      setData(await fetchRef.current())
      setError(null)
      setUpdatedAt(new Date())
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }, [])

  useEffect(() => {
    load()
    if (!intervalMs) return
    const id = setInterval(load, intervalMs)
    return () => clearInterval(id)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [load, intervalMs, ...deps])

  return { data, error, updatedAt, reload: load }
}
