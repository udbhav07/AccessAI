// Sample config, scan and report for the tests, and helpers that build fake fetch responses.

import type { AppConfig, Report, Scan } from '../api/types'

export const config: AppConfig = { ai_enabled: true, scans_per_hour: 20, result_ttl_minutes: 60 }

export const report: Report = {
  verdict: 'PASS',
  error: null,
  checks: [
    { name: 'Layout', passed: true, summary: '7/7 elements unchanged in size', details: [], tier: 'blocking' },
    { name: 'Coverage', passed: false, summary: 'alt 0/2 -> 1/2', details: ['img#cat'], tier: 'objective' },
  ],
}

export const scan: Scan = {
  id: 'abc123',
  url: 'https://example.com/',
  html: '<html><body><p>fixed page</p></body></html>',
  issues: [
    { type: 'alt', target: 'cat.png', old: '', new: 'a sleeping cat', ids: ['1'] },
    { type: 'contrast', target: 'h2', old: '#00f', new: '#8888ff', ratio_before: 2.1, ratio_after: 4.9, ids: ['2'] },
    { type: 'contrast-skipped', target: 'p', old: null, new: null, reason: 'text sits on a non-uniform background', ids: [] },
  ],
  warnings: [],
  report: null,
  created_at: Date.now() / 1000,
  expires_at: Date.now() / 1000 + 3600,
}

export function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

export function apiError(status: number, code: string, message: string) {
  return json({ error: { code, message } }, status)
}
