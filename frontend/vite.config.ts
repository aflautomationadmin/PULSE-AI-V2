import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    port: 5173,
    proxy: {
      '/chat': 'http://localhost:8000',
      '/threads': 'http://localhost:8000',
      '/schema': 'http://localhost:8000',
      '/memory': 'http://localhost:8000',
      '/cache': 'http://localhost:8000',
      '/context': 'http://localhost:8000',
      '/charts': 'http://localhost:8000',
      '/health': 'http://localhost:8000',
    },
  },
})
