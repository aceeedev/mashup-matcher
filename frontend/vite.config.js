import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    // 0.0.0.0 so this is reachable from outside the container when run via Docker
    host: true,
    port: 5173,
  },
})
