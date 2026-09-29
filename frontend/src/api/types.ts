// Shapes of the backend's JSON responses. Keep in sync with backend/accessai/api/routes.py.

export interface AppConfig {
  ai_enabled: boolean
  scans_per_hour: number
  result_ttl_minutes: number
}

/** `new` is null when the fixer left the element unchanged. */
export interface Issue {
  type: string // "alt" | "label" | "contrast" | "contrast-skipped" | ...
  source?: string
  target?: string | null
  old?: string | null
  new?: string | null
  ratio_before?: number | null
  ratio_after?: number | null
  reason?: string | null
  note?: string | null
  ids?: string[]
}

export type CheckTier = 'blocking' | 'objective' | 'advisory'

export interface Check {
  name: string
  passed: boolean
  /** Passed, but not everything is fixed yet. Missing in reports saved before it existed. */
  partial?: boolean
  summary: string
  details: string[]
  tier: CheckTier
}

export type Verdict = 'PASS' | 'REVIEW' | 'INCOMPLETE' | 'BROKEN' | 'ERROR'

export interface Report {
  verdict: Verdict
  error: string | null
  checks: Check[]
}

export interface Scan {
  id: string
  url: string
  /** Still has the data-aai-* stamps. */
  html: string
  issues: Issue[]
  /** Non-AI problems, e.g. contrast could not be checked. */
  warnings: string[]
  /** The model was not used for part of the page; the server log says why. Missing on older scans. */
  ai_fallback?: boolean
  report: Report | null
  created_at: number
  expires_at: number
}

export interface ApiErrorBody {
  error: { code: string; message: string }
}
