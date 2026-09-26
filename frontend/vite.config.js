import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    // 0.0.0.0 so this is reachable from outside the container when run via Docker
    host: true,
    port: 5173,
    watch: {
      // File-change events don't cross Docker Desktop's Windows -> Linux bind mount,
      // so inside the container Vite never sees edits and keeps serving stale modules.
      // Polling fixes that; it's only switched on by compose.yml, since native watching
      // works fine when running Vite directly on the host.
      usePolling: process.env.VITE_USE_POLLING === 'true',
      interval: 300,
    },
  },
})
