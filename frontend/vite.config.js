import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
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
