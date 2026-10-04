import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// У dev-режимі API проксіюється на бекенд (https://localhost через Traefik)
export default defineConfig({
  plugins: [react()],
  build: {
    sourcemap: false,
    // жодних inline-скриптів: сумісно з CSP script-src 'self'
    assetsInlineLimit: 0,
    modulePreload: { polyfill: false },
  },
  server: {
    proxy: {
      "/api": { target: "https://localhost", changeOrigin: false, secure: false },
      "/ws": { target: "wss://localhost", ws: true, secure: false },
    },
  },
});
