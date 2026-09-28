// Tests for the API client's requests and how it turns failures into readable errors.

import { describe, expect, it, vi } from 'vitest'

import { apiError, config, json, scan } from '../test/fixtures'
import { ApiError, api } from './client'

describe('api client', () => {
  it('returns the parsed body on success', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(json(config))
    await expect(api.getConfig()).resolves.toEqual(config)
  })

  it('posts the URL as JSON', async () => {
    const fetch = vi.spyOn(globalThis, 'fetch').mockResolvedValue(json(scan, 201))
    await api.createScan('example.com')

    const [path, init] = fetch.mock.calls[0]
    expect(path).toBe('/api/scans')
    expect(init?.method).toBe('POST')
    expect(init?.body).toBe(JSON.stringify({ url: 'example.com' }))
    expect(new Headers(init?.headers).get('Content-Type')).toBe('application/json')
  })

  it("surfaces the server's error code and message", async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      apiError(429, 'rate_limited', 'That is more than 20 scans in an hour.'),
    )
    const err = await api.createScan('x').catch((e: unknown) => e)
    expect(err).toBeInstanceOf(ApiError)
    expect(err).toMatchObject({ status: 429, code: 'rate_limited', message: 'That is more than 20 scans in an hour.' })
  })

  it('explains a non-JSON failure', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response('<html>Server error</html>', { status: 500 }))
    await expect(api.getConfig()).rejects.toMatchObject({ code: 'http_error', message: 'The server answered with HTTP 500.' })
  })

  it('refuses a 2xx that is not JSON, rather than returning null', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response('<!doctype html><div id="root"></div>', { status: 200 }),
    )
    await expect(api.getConfig()).rejects.toMatchObject({ code: 'bad_response' })
  })

  it('turns a network failure into a readable error', async () => {
    vi.spyOn(globalThis, 'fetch').mockRejectedValue(new TypeError('Failed to fetch'))
    await expect(api.getConfig()).rejects.toMatchObject({ code: 'network_error', status: 0 })
  })

  it('lets an abort through untouched, so callers can ignore it', async () => {
    vi.spyOn(globalThis, 'fetch').mockRejectedValue(new DOMException('aborted', 'AbortError'))
    await expect(api.getConfig()).rejects.toMatchObject({ name: 'AbortError' })
  })

  it('escapes ids in paths', async () => {
    const fetch = vi.spyOn(globalThis, 'fetch').mockResolvedValue(json(scan))
    await api.getScan('a/b')
    expect(fetch.mock.calls[0][0]).toBe('/api/scans/a%2Fb')
  })

  it('explains a proxy timeout', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response('Gateway Timeout', { status: 504 }))
    await expect(api.createScan('x')).rejects.toMatchObject({ code: 'timeout' })
  })

  it('says the server is down when a quick request gets a bare gateway error', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response('', { status: 502 }))
    await expect(api.getConfig()).rejects.toMatchObject({
      code: 'unavailable',
      message: expect.stringContaining('The AccessAI server is down'),
    })
  })

  it('downloads a file with the name the server gives it', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response('<html></html>', {
        headers: { 'Content-Disposition': 'attachment; filename="a.com-accessible.html"' },
      }),
    )
    const { filename, blob } = await api.downloadScan('abc')
    expect(filename).toBe('a.com-accessible.html')
    expect(await blob.text()).toBe('<html></html>')
  })

  it('reports an expired download as an API error', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(apiError(404, 'scan_expired', 'Gone.'))
    await expect(api.downloadScan('abc')).rejects.toMatchObject({ code: 'scan_expired' })
  })
})
