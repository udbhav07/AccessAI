// Runs before the tests: adds jest-dom matchers, and resets the DOM and address bar after each test.

import '@testing-library/jest-dom/vitest'

import { cleanup } from '@testing-library/react'
import { afterEach } from 'vitest'

afterEach(() => {
  cleanup()
  window.history.replaceState(null, '', '/')
})
