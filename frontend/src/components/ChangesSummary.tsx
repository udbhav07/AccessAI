// What the scan did: a table of changes, and a collapsible list of items left alone with the reason.

import type { Issue } from '../api/types'

const TYPE_LABELS: Record<string, string> = {
  alt: 'Alt text',
  label: 'Label',
  contrast: 'Contrast',
  'contrast-skipped': 'Contrast',
}

function typeLabel(type: string) {
  return TYPE_LABELS[type] ?? type
}

export function ChangesSummary({ issues }: { issues: Issue[] }) {
  // no `new` value = the fixer skipped it
  const changes = issues.filter((issue) => issue.new)
  const skipped = issues.filter((issue) => !issue.new)

  return (
    <section className="changes mt-4" aria-labelledby="changes-heading">
      <h2 id="changes-heading" className="h3 pb-2">
        {changes.length} change{changes.length === 1 ? '' : 's'} applied
      </h2>

      {changes.length > 0 && (
        <div className="table-responsive">
          <table className="table table-sm align-middle">
            <thead>
              <tr>
                <th scope="col">Type</th>
                <th scope="col">Where</th>
                <th scope="col">Before</th>
                <th scope="col">After</th>
                <th scope="col">Contrast</th>
              </tr>
            </thead>
            <tbody>
              {changes.map((issue, i) => (
                <tr key={i}>
                  <td>
                    <span className="badge bg-secondary">{typeLabel(issue.type)}</span>
                  </td>
                  <td className="font-monospace small text-break">{issue.target || '—'}</td>
                  <td className="font-monospace small">{issue.old || '(none)'}</td>
                  <td className="font-monospace small">{issue.new}</td>
                  <td className="small text-nowrap">
                    {issue.ratio_before ? (
                      <>
                        {issue.ratio_before}:1 → <strong>{issue.ratio_after}:1</strong>
                      </>
                    ) : (
                      '—'
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {skipped.length > 0 && (
        <details className="mb-3">
          <summary>{skipped.length} left alone</summary>
          <ul className="small">
            {skipped.map((issue, i) => (
              <li key={i}>
                <span className="font-monospace">{issue.target || issue.type}</span> —{' '}
                {issue.reason || issue.note || 'not changed'}
              </li>
            ))}
          </ul>
        </details>
      )}
    </section>
  )
}
