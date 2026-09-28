import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    headers: {
      // Cornerstone3D requires SharedArrayBuffer for volume rendering.
      // require-corp + proxy ensures same-origin NIfTI loading (no opaque responses).
      "Cross-Origin-Opener-Policy": "same-origin",
      "Cross-Origin-Embedder-Policy": "require-corp",
    },
    proxy: {
      // Proxy /api → backend so NIfTI volumes load from same origin.
      // Cornerstone nifti loader uses XHR which would get opaque responses
      // under credentialless COEP.
      "/api": {
        target: "http://localhost:8000",
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, ""),
      },
    },
  },
});
