import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// This file runs in Node, but the project has no @types/node and does not need one for
// a single env var. Declaring the shape used here keeps `npm run typecheck` strict.
declare const process: { env: Record<string, string | undefined> };

// `dev.ps1 -Port …` sets this so both halves agree on where the API is.
const apiPort = process.env.ECT_API_PORT ?? "8000";

export default defineConfig({
  plugins: [react()],
  server: {
    // Explicit IPv4: Node resolves "localhost" to ::1 on Windows, and the netsh
    // portproxy behind http://ect (port 80 -> 5173) forwards to 127.0.0.1.
    host: "127.0.0.1",
    port: 5173,
    // "ect" isn't a *.localhost name, so Vite's Host-header check (an anti-DNS-rebind
    // guard) rejects it unless explicitly allowed here.
    allowedHosts: ["ect"],
    // Everything under /api goes to the FastAPI backend, so the app is same-origin in
    // dev and getUserMedia keeps its secure context on localhost.
    proxy: {
      "/api": {
        target: `http://127.0.0.1:${apiPort}`,
        changeOrigin: true,
      },
    },
  },
});
