import { defineConfig, type Plugin } from "vite";
import react from "@vitejs/plugin-react";
import { readdirSync, readFileSync } from "fs";
import { resolve } from "path";
import { compileGuide } from "./frontend_src/guide/compile.ts";

const GUIDE_TOPICS = resolve(__dirname, "frontend_src/guide/topics");
const CATALOG = resolve(__dirname, "custom_components/phoenix_mcp/catalogs/en.json");

/** Compile each language's Guide topics into guide/<language>.json beside the panel bundle; a broken topic fails the build. */
function guideTopics(): Plugin {
  return {
    name: "phoenix-guide-topics",
    generateBundle() {
      const panel = (JSON.parse(readFileSync(CATALOG, "utf8")) as { panel: Record<string, unknown> }).panel;
      const labelExists = (key: string) => {
        let node: unknown = panel;
        for (const part of key.split(".")) node = node && typeof node === "object" ? (node as Record<string, unknown>)[part] : undefined;
        return typeof node === "string";
      };
      for (const language of readdirSync(GUIDE_TOPICS)) {
        const dir = resolve(GUIDE_TOPICS, language);
        const files = readdirSync(dir).filter((f) => f.endsWith(".md")).sort().map((f) => {
          const path = resolve(dir, f);
          this.addWatchFile(path);
          return { file: `${language}/${f}`, source: readFileSync(path, "utf8") };
        });
        const { bundle, problems } = compileGuide(language, files, labelExists);
        if (problems.length) this.error(`Guide topics:\n${problems.join("\n")}`);
        this.emitFile({ type: "asset", fileName: `guide/${language}.json`, source: JSON.stringify(bundle) });
      }
    },
  };
}

export default defineConfig({
  plugins: [react(), guideTopics()],
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
