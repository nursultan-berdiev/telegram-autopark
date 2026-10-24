import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// В дев-режиме (npm run dev) проксируем /api и /admin на core-api, чтобы кука
// веб-сессии была one-origin. В проде статику и прокси раздаёт Caddy (Phase 5).
const CORE = process.env.CORE_API_ORIGIN ?? "http://127.0.0.1:8099";

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/api": { target: CORE, changeOrigin: true, rewrite: (p) => p.replace(/^\/api/, "") },
      "/admin": { target: CORE, changeOrigin: true },
    },
  },
  build: { outDir: "dist" },
});
