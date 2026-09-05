import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    // Im Entwicklungsbetrieb laeuft das Backend getrennt. Der Proxy sorgt
    // dafuer, dass der Player dieselben relativen URLs benutzt wie spaeter im
    // Container - sonst gaebe es beim Streamen CORS-Aerger mit Range-Headern.
    proxy: {
      "/api": { target: "http://127.0.0.1:8000", changeOrigin: false },
    },
  },
  build: { outDir: "dist", sourcemap: false },
});
