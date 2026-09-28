// The only place that calls the backend. Every failure (network, proxy, API error,
// non-JSON reply) becomes an ApiError with a message ready to show the user.

import type { ApiErrorBody, AppConfig, Report, Scan } from './types'

// Empty in dev (Vite proxies /api); the backend's URL in production.
const BASE_URL = (import.meta.env.VITE_API_BASE_URL ?? '').replace(/\/+$/, '')

export class ApiError extends Error {
  readonly status: number
  readonly code: string

  constructor(status: number, code: string, message: string) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.code = code
  }
}

function isErrorBody(body: unknown): body is ApiErrorBody {
  return (
    typeof body === 'object' &&
    body !== null &&
    'error' in body &&
    typeof (body as ApiErrorBody).error?.message === 'string'
  )
}

function isAbort(err: unknown) {
  return err instanceof DOMException && err.name === 'AbortError'
}

const SERVER_DOWN = 'The AccessAI server is down. Check that the backend is running, then try again.'

async function send(path: string, init: RequestInit = {}): Promise<Response> {
  try {
    return await fetch(`${BASE_URL}${path}`, init)
  } catch (err) {
    if (isAbort(err)) throw err
    throw new ApiError(0, 'network_error', SERVER_DOWN)
  }
}

async function readBody(response: Response): Promise<unknown> {
  try {
    return await response.json()
  } catch (err) {
    if (isAbort(err)) throw err
    return null
  }
}

// slow: the request does real work (a scan or verification), so a gateway error most
// likely means the proxy gave up waiting. Otherwise the backend is down or unreachable
// (the Vite dev proxy also answers with a bare 5xx when nothing is listening).
async function errorFrom(response: Response, slow = false): Promise<ApiError> {
  const body = await readBody(response)
  if (isErrorBody(body)) return new ApiError(response.status, body.error.code, body.error.message)
  if ([502, 503, 504].includes(response.status)) {
    return slow
      ? new ApiError(
          response.status,
          'timeout',
          'The server took too long to respond. Please try again, or try a smaller page.',
        )
      : new ApiError(response.status, 'unavailable', SERVER_DOWN)
  }
  return new ApiError(response.status, 'http_error', `The server answered with HTTP ${response.status}.`)
}

async function request<T>(path: string, init: RequestInit = {}, slow = false): Promise<T> {
  const response = await send(path, {
    ...init,
    headers: { Accept: 'application/json', ...init.headers },
  })
  if (!response.ok) throw await errorFrom(response, slow)

  const body = await readBody(response)
  if (body === null || typeof body !== 'object') {
    // Usually means VITE_API_BASE_URL is unset and /api returned index.html.
    throw new ApiError(
      response.status,
      'bad_response',
      'The AccessAI server sent an unexpected response. Check that the frontend points at the API.',
    )
  }
  return body as T
}

async function download(path: string): Promise<{ blob: Blob; filename: string }> {
  const response = await send(path)
  if (!response.ok) throw await errorFrom(response)
  const disposition = response.headers.get('Content-Disposition') ?? ''
  const filename = /filename="([^"]+)"/.exec(disposition)?.[1] ?? 'accessible.html'
  return { blob: await response.blob(), filename }
}

export const api = {
  getConfig: () => request<AppConfig>('/api/config'),

  createScan: (url: string, signal?: AbortSignal) =>
    request<Scan>('/api/scans', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ url }),
      signal,
    }, true),

  getScan: (id: string, signal?: AbortSignal) =>
    request<Scan>(`/api/scans/${encodeURIComponent(id)}`, { signal }),

  verifyScan: (id: string, signal?: AbortSignal) =>
    request<{ report: Report }>(`/api/scans/${encodeURIComponent(id)}/verification`, {
      method: 'POST',
      signal,
    }, true),

  downloadScan: (id: string) => download(`/api/scans/${encodeURIComponent(id)}/download`),
}
