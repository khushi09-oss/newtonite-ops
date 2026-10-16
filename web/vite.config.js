import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The browser talks to /api on its own origin; Vite forwards that to the API container.
// Same origin means the session cookie just works, with no CORS configuration anywhere.
export default defineConfig({
  plugins: [react()],
  server: {
    host: true,
    port: 5173,
    // A Windows or macOS bind mount does not deliver file events into the container; poll instead.
    watch: { usePolling: true, interval: 300 },
    proxy: {
      "/api": {
        target: process.env.API_URL || "http://localhost:8000",
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, ""),
      },
    },
  },
});
