// Vite setup: dev and preview servers that proxy /api to the backend, and Vitest settings.
/// <reference types="vitest/config" />
import react from '@vitejs/plugin-react'
import { defineConfig, loadEnv } from 'vite'

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '')
  // Proxy /api to the backend locally so no CORS setup is needed.
  // Production builds call VITE_API_BASE_URL instead.
  const backend = env.BACKEND_URL || 'http://localhost:8000'

  return {
    plugins: [react()],
    server: {
      port: 5173,
      strictPort: true,
      proxy: { '/api': { target: backend, changeOrigin: true } },
    },
    preview: {
      port: 4173,
      proxy: { '/api': { target: backend, changeOrigin: true } },
    },
    test: {
      environment: 'jsdom',
      setupFiles: ['./src/test/setup.ts'],
      css: false,
      restoreMocks: true,
    },
  }
})
