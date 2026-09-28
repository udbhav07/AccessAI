// Tests that changes and skipped items are listed separately.

import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { scan } from '../test/fixtures'
import { ChangesSummary } from './ChangesSummary'

describe('ChangesSummary', () => {
  it('counts only real changes, not abstentions', () => {
    render(<ChangesSummary issues={scan.issues} />)
    expect(screen.getByRole('heading', { name: '2 changes applied' })).toBeInTheDocument()
    expect(screen.getByText('1 left alone')).toBeInTheDocument()
    expect(screen.getByText(/non-uniform background/)).toBeInTheDocument()
  })

  it('shows before, after and the contrast ratios', () => {
    render(<ChangesSummary issues={scan.issues} />)
    expect(screen.getByText('a sleeping cat')).toBeInTheDocument()
    expect(screen.getByText('(none)')).toBeInTheDocument()
    expect(screen.getByText('#00f')).toBeInTheDocument()
    expect(screen.getByText('#8888ff')).toBeInTheDocument()
    expect(screen.getByText('4.9:1')).toBeInTheDocument()
  })

  it('uses the singular for one change', () => {
    render(<ChangesSummary issues={[scan.issues[0]]} />)
    expect(screen.getByRole('heading', { name: '1 change applied' })).toBeInTheDocument()
    expect(screen.queryByText(/left alone/)).not.toBeInTheDocument()
  })

  it('handles a page with nothing to fix', () => {
    render(<ChangesSummary issues={[]} />)
    expect(screen.getByRole('heading', { name: '0 changes applied' })).toBeInTheDocument()
    expect(screen.queryByRole('table')).not.toBeInTheDocument()
  })
})
