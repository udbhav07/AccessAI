// The verification verdict, and each check with its tier, summary and details.

import type { Report, Verdict } from '../api/types'

const VERDICTS: Record<Verdict, { badge: string; meaning: string }> = {
  PASS: { badge: 'bg-success', meaning: 'Everything held.' },
  REVIEW: { badge: 'bg-warning text-dark', meaning: 'Only advisory checks flagged something — worth a look.' },
  INCOMPLETE: { badge: 'bg-warning text-dark', meaning: 'A fix did not land, or contrast regressed. Safe, but unfinished.' },
  BROKEN: { badge: 'bg-danger', meaning: 'The page itself changed. Do not ship this remediation.' },
  ERROR: { badge: 'bg-danger', meaning: 'The page could not be rendered for comparison.' },
}

export function VerificationReport({ report }: { report: Report }) {
  const verdict = VERDICTS[report.verdict] ?? VERDICTS.ERROR

  return (
    <section className="report mt-4" aria-labelledby="report-heading">
      <h2 id="report-heading" className="h3 pb-1">
        Verification: <span className={`badge ${verdict.badge}`}>{report.verdict}</span>
      </h2>
      <p className="text-body-secondary">{verdict.meaning}</p>
      {report.error && <p className="text-danger">{report.error}</p>}

      <ul className="list-unstyled font-monospace checks">
        {report.checks.map((check) => (
          <li key={check.name} className="mb-1">
            <span className={check.passed ? 'text-success' : 'text-danger'}>
              <span aria-hidden="true">{check.passed ? '✓' : '✗'}</span>
              <span className="visually-hidden">{check.passed ? 'Passed' : 'Failed'}</span>
            </span>{' '}
            <strong className="check-name">{check.name}</strong>{' '}
            <span className="badge text-bg-light border fw-normal me-1">{check.tier}</span>
            {check.summary}
            {check.details.length > 0 && (
              <details className="ms-4">
                <summary>{check.details.length} element(s)</summary>
                <ul>
                  {check.details.map((detail, i) => (
                    <li key={i}>{detail}</li>
                  ))}
                </ul>
              </details>
            )}
          </li>
        ))}
      </ul>
    </section>
  )
}
