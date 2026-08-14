import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The console is served from the same origin as the API in every environment that
// matters, so there is no CORS story and no API base URL to configure — a relative
// `/api/v1/...` is correct in dev (via the proxy below), in preview, and in Lambda.
//
// `base` is `/console/` because FastAPI mounts the built assets there. Vite needs to
// know at build time so the asset URLs it writes into index.html are right; getting
// this wrong produces a white page with 404s for every chunk, which looks like a
// build failure and is not one.
export default defineConfig({
  plugins: [react()],
  base: "/console/",
  build: {
    outDir: "dist",
    sourcemap: true,
  },
  server: {
    port: 5180,
    proxy: {
      // Dev only. `dev.sh` runs the Python app on 8099 against the real cluster.
      "/api": {
        target: "http://127.0.0.1:8099",
        changeOrigin: false,
      },
    },
  },
});
