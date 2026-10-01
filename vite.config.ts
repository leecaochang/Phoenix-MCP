import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { resolve } from "path";

export default defineConfig({
  plugins: [react()],
  publicDir: "frontend_src/public",
  base: "/local/phoenix-mcp/",
  build: {
    outDir: "custom_components/phoenix_mcp/frontend",
    emptyOutDir: true,
    rollupOptions: {
      input: resolve(__dirname, "frontend_src/index.tsx"),
      output: {
        format: "iife",
        entryFileNames: "phoenix-mcp-panel.js",
        assetFileNames: "[name][extname]",
      },
    },
    target: "es2020",
    minify: true,
  },
});
