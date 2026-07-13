import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

// The frontend calls the backend at a relative `/ask`, so the same build works
// whether Vite's dev server serves it or FastAPI serves the static bundle in
// prod. In dev we proxy the API paths to the uvicorn container on :8000.
const BACKEND = process.env.VITE_BACKEND_URL ?? 'http://localhost:8000'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    proxy: {
      '/ask': { target: BACKEND, changeOrigin: true },
      '/healthz': { target: BACKEND, changeOrigin: true },
    },
  },
})
