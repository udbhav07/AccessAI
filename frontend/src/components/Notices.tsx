// Alert boxes: a dismissible error, AI unavailable, scan warnings, and server down.

interface ErrorNoticeProps {
  message: string
  onDismiss: () => void
}

export function ErrorNotice({ message, onDismiss }: ErrorNoticeProps) {
  return (
    <div className="alert alert-warning alert-dismissible mt-3" role="alert">
      {message}
      <button type="button" className="btn-close" aria-label="Dismiss" onClick={onDismiss} />
    </div>
  )
}

const SUPPORT_EMAIL = 'me@udbhavsai.com'

// Same text whatever the cause (no key, quota, timeout, ...); the server log has the reason.
export function AiDownNotice() {
  return (
    <div className="alert alert-secondary mt-3" role="status">
      <p className="mb-1">
        <strong>
          AI Service being used is down, please Contact administrator{' '}
          <a href={`mailto:${SUPPORT_EMAIL}`}>{SUPPORT_EMAIL}</a> for further support
        </strong>
      </p>
      <p className="mb-0">Giving you the response without AI</p>
    </div>
  )
}

export function ScanWarnings({ warnings }: { warnings: string[] }) {
  if (warnings.length === 0) return null
  return (
    <div className="alert alert-warning mt-4" role="status">
      {warnings.map((warning) => (
        <p key={warning} className="mb-0">
          {warning}
        </p>
      ))}
    </div>
  )
}

export function ServerDownNotice({ message }: { message: string }) {
  return (
    <div className="alert alert-danger mt-3" role="alert">
      {message}
    </div>
  )
}
