// Tests the verdict badge and the pass/fail mark on each check.

import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { report } from '../test/fixtures'
import { VerificationReport } from './VerificationReport'

describe('VerificationReport', () => {
  it('shows the verdict and every check', () => {
    render(<VerificationReport report={report} />)
    expect(screen.getByRole('heading', { name: /Verification: PASS/ })).toBeInTheDocument()
    expect(screen.getByText('7/7 elements unchanged in size')).toBeInTheDocument()
    expect(screen.getByText('alt 0/2 -> 1/2')).toBeInTheDocument()
  })

  it('announces pass and fail in words, not just symbols', () => {
    render(<VerificationReport report={report} />)
    expect(screen.getByText('Passed')).toBeInTheDocument()
    expect(screen.getByText('Failed')).toBeInTheDocument()
  })

  it('marks a check that passed but left work unfinished', () => {
    const coverage = { name: 'Coverage', passed: true, partial: true, summary: 'labels 2/4 -> 2/4', details: [], tier: 'objective' as const }
    render(<VerificationReport report={{ ...report, checks: [coverage] }} />)
    expect(screen.getByText('Partly done')).toBeInTheDocument()
    expect(screen.queryByText('Passed')).not.toBeInTheDocument()
  })

  it('lists the elements a check flagged', () => {
    render(<VerificationReport report={report} />)
    expect(screen.getByText('1 element(s)')).toBeInTheDocument()
    expect(screen.getByText('img#cat')).toBeInTheDocument()
  })

  it('explains an ERROR verdict', () => {
    render(<VerificationReport report={{ verdict: 'ERROR', error: 'Could not render the page', checks: [] }} />)
    expect(screen.getByText('Could not render the page')).toBeInTheDocument()
    expect(screen.getByText(/could not be rendered/)).toBeInTheDocument()
  })
})
