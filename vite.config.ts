import { defineConfig, type Plugin } from "vite";
import react from "@vitejs/plugin-react";
import { readdirSync } from "fs";
import { resolve } from "path";
import { buildGuide } from "./frontend_src/guide/build.ts";

const GUIDE_TOPICS = resolve(__dirname, "frontend_src/guide/topics");
/** Every language with Guide topics, so adding a language's folder is all it takes to ship it. */
const GUIDE_LANGUAGE_FOLDERS = readdirSync(GUIDE_TOPICS, { withFileTypes: true }).filter((d) => d.isDirectory()).map((d) => d.name).sort();

/**
 * Compile each language's Guide topics into guide/<language>.json beside the panel bundle, with the
 * tool reference generated beside them and each tool's details in guide/tools.json (guide/build.ts).
 * A broken topic, or a translation whose topics and sections differ from English, fails the build.
 */
function guideTopics(): Plugin {
  return {
    name: "phoenix-guide-topics",
    generateBundle() {
      const guide = buildGuide(__dirname);
      for (const path of guide.read) this.addWatchFile(path);
      if (guide.problems.length) this.error(guide.problems.join("\n"));
      for (const { fileName, source } of guide.files) this.emitFile({ type: "asset", fileName, source });
    },
  };
}

export default defineConfig({
  plugins: [react(), guideTopics()],
  define: { __GUIDE_LANGUAGES__: JSON.stringify(GUIDE_LANGUAGE_FOLDERS) },
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
