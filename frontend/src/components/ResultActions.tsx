// Verify and Download buttons for a scan, with a hint saying when the result expires.

import type { Scan } from '../api/types'
import { useExpired } from '../hooks/useExpired'
import { Spinner } from './Spinner'

interface ResultActionsProps {
  scan: Scan
  busy: boolean
  verifying: boolean
  onVerify: () => void
  onDownload: () => void
}

export function ResultActions({ scan, busy, verifying, onVerify, onDownload }: ResultActionsProps) {
  const expired = useExpired(scan.expires_at)
  const expires = new Date(scan.expires_at * 1000).toLocaleTimeString([], {
    hour: '2-digit',
    minute: '2-digit',
  })

  let hint = `This result is kept until ${expires}.`
  if (verifying) hint = 'Rendering both versions in a headless browser — this takes a moment.'
  if (expired) hint = 'This result has expired. Scan the page again to verify or download it.'

  return (
    <section className="result-actions mt-4 text-center" aria-label="Result actions">
      <p className="mb-2 text-break">
        Results for{' '}
        <a href={scan.url} target="_blank" rel="noopener noreferrer">
          {scan.url}
        </a>
      </p>
      <div className="d-flex flex-wrap justify-content-center gap-2">
        <button
          type="button"
          className="btn btn-success"
          onClick={onVerify}
          disabled={busy || expired}
          aria-busy={verifying}
        >
          {verifying ? (
            <>
              <Spinner />
              Verifying…
            </>
          ) : scan.report ? (
            'Verify again'
          ) : (
            'Verify fixes'
          )}
        </button>
        <button
          type="button"
          className="btn btn-outline-success"
          onClick={onDownload}
          disabled={expired}
        >
          Download HTML
        </button>
      </div>
      <p className="form-text mt-2 mb-0">{hint}</p>
    </section>
  )
}
