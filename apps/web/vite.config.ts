import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    // Proxy so the browser talks to one origin. Avoids CORS entirely in dev and
    // means the UI needs no knowledge of where the API lives.
    proxy: {
      "/api": { target: "http://localhost:8000", changeOrigin: true },
      "/ws": { target: "ws://localhost:8000", ws: true },
      "/health": { target: "http://localhost:8000", changeOrigin: true },
    },
  },
});
