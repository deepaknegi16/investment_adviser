import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    // Bind to all interfaces so the dashboard is reachable from a phone on the
    // same Wi-Fi (http://<mac-lan-ip>:5173). The backend stays on 127.0.0.1 —
    // the proxy below runs inside this dev server, so the API is never exposed
    // to the network directly.
    host: true,
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8000",
        changeOrigin: true,
        // AI analysis can take minutes — don't let the proxy cut it off.
        timeout: 600000,
        proxyTimeout: 600000,
      },
    },
  },
});
