// The URL box and Scan button.

import { useState, type FormEvent } from 'react'

import { Spinner } from './Spinner'

interface ScanFormProps {
  busy: boolean
  scanning: boolean
  onScan: (url: string) => void
}

export function ScanForm({ busy, scanning, onScan }: ScanFormProps) {
  const [url, setUrl] = useState('')

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const trimmed = url.trim()
    if (trimmed) onScan(trimmed)
  }

  return (
    <form className="scan-form" onSubmit={handleSubmit} aria-busy={scanning}>
      <label htmlFor="website-url" className="h4 d-block text-center mb-3">
        Enter the link of the website below to get started.
      </label>
      <div className="input-group">
        <span className="input-group-text" aria-hidden="true">
          <LinkIcon />
        </span>
        {/* not type="url": the backend accepts a bare example.com */}
        <input
          id="website-url"
          className="form-control"
          type="text"
          inputMode="url"
          autoComplete="url"
          spellCheck={false}
          placeholder="https://example.com"
          value={url}
          onChange={(e) => setUrl(e.target.value)}
          required
        />
        <button className="btn btn-success" type="submit" disabled={busy}>
          {scanning ? (
            <>
              <Spinner />
              Scanning…
            </>
          ) : (
            'Scan'
          )}
        </button>
      </div>
      {scanning && (
        <p className="form-text text-center mt-2 mb-0">
          This can take a minute or two — images, labels and colours are checked together.
        </p>
      )}
    </form>
  )
}

function LinkIcon() {
  return (
    <svg width="16" height="16" fill="currentColor" viewBox="0 0 16 16" focusable="false">
      <path d="M4.715 6.542 3.343 7.914a3 3 0 1 0 4.243 4.243l1.828-1.829A3 3 0 0 0 8.586 5.5L8 6.086a1 1 0 0 0-.154.199 2 2 0 0 1 .861 3.337L6.88 11.45a2 2 0 1 1-2.83-2.83l.793-.792a4 4 0 0 1-.128-1.287z" />
      <path d="M6.586 4.672A3 3 0 0 0 7.414 9.5l.775-.776a2 2 0 0 1-.896-3.346L9.12 3.55a2 2 0 1 1 2.83 2.83l-.793.792c.112.42.155.855.128 1.287l1.372-1.372a3 3 0 1 0-4.243-4.243z" />
    </svg>
  )
}
