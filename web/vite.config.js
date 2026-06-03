import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Proxy /api/* to the FastAPI service so the dashboard talks to it in dev.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: process.env.REMIT_API || "http://localhost:8000",
        changeOrigin: true,
        rewrite: (p) => p.replace(/^\/api/, ""),
      },
    },
  },
});
