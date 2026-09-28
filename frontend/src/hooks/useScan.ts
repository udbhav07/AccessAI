// All scan state: the current result, what is in progress, and the error to show.
// Starts, verifies, downloads and restores scans, keeping the scan id in the address bar.

import { useCallback, useEffect, useRef, useState } from 'react'

import { ApiError, api } from '../api/client'
import type { Scan } from '../api/types'
import { saveFile } from '../utils/saveFile'

export type Phase = 'idle' | 'restoring' | 'scanning' | 'verifying'

export interface ScanState {
  scan: Scan | null
  phase: Phase
  error: string | null
}

// Scan id is kept in the URL so a reload or shared link restores the result.
const SCAN_PARAM = 'scan'

function readScanId(): string | null {
  return new URLSearchParams(window.location.search).get(SCAN_PARAM)
}

function writeScanId(id: string | null) {
  const url = new URL(window.location.href)
  if (id) url.searchParams.set(SCAN_PARAM, id)
  else url.searchParams.delete(SCAN_PARAM)
  window.history.replaceState(null, '', url)
}

function isAbort(err: unknown) {
  return err instanceof DOMException && err.name === 'AbortError'
}

function isExpired(err: unknown) {
  return err instanceof ApiError && err.code === 'scan_expired'
}

/** Starting a new request aborts the one in flight. */
export function useScan() {
  const [state, setState] = useState<ScanState>(() => ({
    scan: null,
    phase: readScanId() ? 'restoring' : 'idle',
    error: null,
  }))
  const inflight = useRef<AbortController | null>(null)

  const begin = useCallback(() => {
    inflight.current?.abort()
    const controller = new AbortController()
    inflight.current = controller
    return controller
  }, [])

  const finish = useCallback((controller: AbortController) => {
    if (inflight.current === controller) inflight.current = null
  }, [])

  // The server may drop a result early (e.g. after a restart); show it as expired.
  const fail = useCallback((err: unknown) => {
    const now = Date.now() / 1000
    setState((s) => ({
      scan: isExpired(err) && s.scan ? { ...s.scan, expires_at: now } : s.scan,
      phase: 'idle',
      error: (err as Error).message,
    }))
  }, [])

  useEffect(() => {
    const id = readScanId()
    if (!id) return
    const controller = begin()
    api
      .getScan(id, controller.signal)
      .then((scan) => setState({ scan, phase: 'idle', error: null }))
      .catch((err: Error) => {
        if (isAbort(err)) return
        writeScanId(null)
        setState({ scan: null, phase: 'idle', error: err.message })
      })
      .finally(() => finish(controller))
    return () => controller.abort()
  }, [begin, finish])

  const startScan = useCallback(
    async (url: string) => {
      const controller = begin()
      setState((s) => ({ ...s, phase: 'scanning', error: null }))
      try {
        const scan = await api.createScan(url, controller.signal)
        writeScanId(scan.id)
        setState({ scan, phase: 'idle', error: null })
      } catch (err) {
        if (isAbort(err)) return
        // keep the previous result on screen
        setState((s) => ({ ...s, phase: 'idle', error: (err as Error).message }))
      } finally {
        finish(controller)
      }
    },
    [begin, finish],
  )

  const verify = useCallback(async () => {
    const scan = state.scan
    if (!scan) return
    const controller = begin()
    setState((s) => ({ ...s, phase: 'verifying', error: null }))
    try {
      const { report } = await api.verifyScan(scan.id, controller.signal)
      setState((s) =>
        s.scan?.id === scan.id
          ? { scan: { ...s.scan, report }, phase: 'idle', error: null }
          : s,
      )
    } catch (err) {
      if (isAbort(err)) return
      fail(err)
    } finally {
      finish(controller)
    }
  }, [state.scan, begin, finish, fail])

  const download = useCallback(async () => {
    const scan = state.scan
    if (!scan) return
    try {
      const { blob, filename } = await api.downloadScan(scan.id)
      saveFile(blob, filename)
    } catch (err) {
      fail(err)
    }
  }, [state.scan, fail])

  const dismissError = useCallback(() => setState((s) => ({ ...s, error: null })), [])

  useEffect(() => () => inflight.current?.abort(), [])

  return { ...state, startScan, verify, download, dismissError }
}
