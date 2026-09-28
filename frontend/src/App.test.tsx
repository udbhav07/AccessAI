// UI tests with a faked fetch: scanning, verifying, restoring from the address bar,
// expiry, downloading, and every notice.

import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import App from './App'
import { apiError, config, json, report, scan } from './test/fixtures'

type Handler = (init?: RequestInit) => Response | Promise<Response>

function mockApi(routes: Record<string, Handler>) {
  return vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    const key = `${init?.method ?? 'GET'} ${String(input)}`
    const handler = routes[key]
    if (!handler) throw new Error(`unexpected request: ${key}`)
    return handler(init)
  })
}

async function submitUrl(url: string) {
  const user = userEvent.setup()
  await user.type(screen.getByLabelText(/Enter the link/), url)
  await user.click(screen.getByRole('button', { name: 'Scan' }))
  return user
}

describe('App', () => {
  beforeEach(() => {
    window.history.replaceState(null, '', '/')
  })

  it('scans a URL and shows the result', async () => {
    const fetch = mockApi({
      'GET /api/config': () => json(config),
      'POST /api/scans': () => json(scan, 201),
    })
    render(<App />)
    await submitUrl('example.com')

    expect(await screen.findByRole('heading', { name: '2 changes applied' })).toBeInTheDocument()
    expect(screen.getByTitle('Remediated page preview')).toHaveAttribute('srcdoc', scan.html)
    expect(screen.getByRole('button', { name: 'Download HTML' })).toBeEnabled()
    expect(window.location.search).toBe('?scan=abc123')
    expect(fetch.mock.calls.find(([, i]) => i?.method === 'POST')?.[1]?.body).toBe(
      JSON.stringify({ url: 'example.com' }),
    )
  })

  it('keeps the preview sandboxed with no permissions', async () => {
    mockApi({
      'GET /api/config': () => json(config),
      'POST /api/scans': () => json(scan, 201),
    })
    render(<App />)
    await submitUrl('example.com')

    const frame = await screen.findByTitle('Remediated page preview')
    expect(frame).toHaveAttribute('sandbox', '')
  })

  it('shows the busy state while scanning and blocks a second submit', async () => {
    let finish!: (r: Response) => void
    mockApi({
      'GET /api/config': () => json(config),
      'POST /api/scans': () => new Promise<Response>((resolve) => (finish = resolve)),
    })
    render(<App />)
    await submitUrl('example.com')

    const button = screen.getByRole('button', { name: /Scanning/ })
    expect(button).toBeDisabled()
    expect(screen.getByText(/a minute or two/)).toBeInTheDocument()

    finish(json(scan, 201))
    expect(await screen.findByRole('button', { name: 'Scan' })).toBeEnabled()
  })

  it("shows the server's explanation when a scan fails", async () => {
    mockApi({
      'GET /api/config': () => json(config),
      'POST /api/scans': () =>
        apiError(422, 'blocked_url', "That URL can't be scanned: localhost resolves to a non-public address"),
    })
    render(<App />)
    await submitUrl('localhost')

    expect(await screen.findByRole('alert')).toHaveTextContent("That URL can't be scanned")
    expect(screen.queryByTitle('Remediated page preview')).not.toBeInTheDocument()
  })

  it('lets the user dismiss an error', async () => {
    mockApi({
      'GET /api/config': () => json(config),
      'POST /api/scans': () => apiError(502, 'unreachable', 'Could not reach that site.'),
    })
    render(<App />)
    const user = await submitUrl('example.com')

    await screen.findByRole('alert')
    await user.click(screen.getByRole('button', { name: 'Dismiss' }))
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })

  it('verifies the result and shows the report', async () => {
    mockApi({
      'GET /api/config': () => json(config),
      'POST /api/scans': () => json(scan, 201),
      'POST /api/scans/abc123/verification': () => json({ report }),
    })
    render(<App />)
    const user = await submitUrl('example.com')

    await user.click(await screen.findByRole('button', { name: 'Verify fixes' }))
    expect(await screen.findByRole('heading', { name: /Verification: PASS/ })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Verify again' })).toBeEnabled()
  })

  it('keeps the result on screen when verification fails', async () => {
    mockApi({
      'GET /api/config': () => json(config),
      'POST /api/scans': () => json(scan, 201),
      'POST /api/scans/abc123/verification': () =>
        apiError(500, 'verification_failed', 'Verification could not be completed.'),
    })
    render(<App />)
    const user = await submitUrl('example.com')

    await user.click(await screen.findByRole('button', { name: 'Verify fixes' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Verification could not be completed.')
    expect(screen.getByTitle('Remediated page preview')).toBeInTheDocument()
  })

  it('restores the result named in the address bar', async () => {
    window.history.replaceState(null, '', '/?scan=abc123')
    mockApi({
      'GET /api/config': () => json(config),
      'GET /api/scans/abc123': () => json({ ...scan, report }),
    })
    render(<App />)

    expect(screen.getByText(/Loading the previous result/)).toBeInTheDocument()
    expect(await screen.findByRole('heading', { name: /Verification: PASS/ })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: '2 changes applied' })).toBeInTheDocument()
  })

  it('explains an expired result and clears it from the address bar', async () => {
    window.history.replaceState(null, '', '/?scan=gone')
    mockApi({
      'GET /api/config': () => json(config),
      'GET /api/scans/gone': () =>
        apiError(404, 'scan_expired', 'That result is no longer available -- results are kept for 60 minutes.'),
    })
    render(<App />)

    expect(await screen.findByRole('alert')).toHaveTextContent('no longer available')
    expect(window.location.search).toBe('')
  })

  it('warns when no API key is configured', async () => {
    mockApi({ 'GET /api/config': () => json({ ...config, ai_enabled: false }) })
    render(<App />)
    expect(await screen.findByText(/AI Service being used is down/)).toBeInTheDocument()
    expect(screen.getByText('Giving you the response without AI')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'me@udbhavsai.com' })).toHaveAttribute(
      'href',
      'mailto:me@udbhavsai.com',
    )
  })

  it('does not warn when the model is available', async () => {
    const fetch = mockApi({ 'GET /api/config': () => json(config) })
    render(<App />)
    await waitFor(() => expect(fetch).toHaveBeenCalled())
    expect(screen.queryByText(/AI Service being used is down/)).not.toBeInTheDocument()
  })

  it('says so when the backend is down', async () => {
    vi.spyOn(globalThis, 'fetch').mockRejectedValue(new TypeError('Failed to fetch'))
    render(<App />)
    expect(await screen.findByText(/The AccessAI server is down/)).toBeInTheDocument()
  })

  it('downloads the fixed page as a file', async () => {
    mockApi({
      'GET /api/config': () => json(config),
      'POST /api/scans': () => json(scan, 201),
      'GET /api/scans/abc123/download': () =>
        new Response('<html></html>', {
          headers: { 'Content-Disposition': 'attachment; filename="example.com-accessible.html"' },
        }),
    })
    const createUrl = vi.fn(() => 'blob:x')
    Object.assign(URL, { createObjectURL: createUrl, revokeObjectURL: vi.fn() })
    const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})

    render(<App />)
    const user = await submitUrl('example.com')
    await user.click(await screen.findByRole('button', { name: 'Download HTML' }))

    await waitFor(() => expect(click).toHaveBeenCalled())
    expect(createUrl).toHaveBeenCalled()
    expect((click.mock.contexts[0] as HTMLAnchorElement).download).toBe('example.com-accessible.html')
  })

  it('disables verify and download once a result has expired', async () => {
    window.history.replaceState(null, '', '/?scan=abc123')
    mockApi({
      'GET /api/config': () => json(config),
      'GET /api/scans/abc123': () => json({ ...scan, expires_at: Date.now() / 1000 - 1 }),
    })
    render(<App />)

    expect(await screen.findByText(/This result has expired/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Verify fixes' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Download HTML' })).toBeDisabled()
  })

  it('marks the result expired when the server has already dropped it', async () => {
    mockApi({
      'GET /api/config': () => json(config),
      'POST /api/scans': () => json(scan, 201),
      'POST /api/scans/abc123/verification': () =>
        apiError(404, 'scan_expired', 'That result is no longer available.'),
    })
    render(<App />)
    const user = await submitUrl('example.com')

    await user.click(await screen.findByRole('button', { name: 'Verify fixes' }))
    expect(await screen.findByText(/This result has expired/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Download HTML' })).toBeDisabled()
  })

  it('shows the same AI notice when a scan fell back', async () => {
    mockApi({
      'GET /api/config': () => json(config),
      'POST /api/scans': () => json({ ...scan, ai_fallback: true }, 201),
    })
    render(<App />)
    await submitUrl('example.com')
    expect(await screen.findByText(/AI Service being used is down/)).toBeInTheDocument()
    expect(screen.getByText('Giving you the response without AI')).toBeInTheDocument()
  })
})
