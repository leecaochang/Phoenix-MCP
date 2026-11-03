import { defineConfig, type Plugin } from "vite";
import react from "@vitejs/plugin-react";
import { existsSync, readdirSync, readFileSync } from "fs";
import { resolve } from "path";
import { compileGuide, translationProblems, type GuideBundle } from "./frontend_src/guide/compile.ts";

const GUIDE_TOPICS = resolve(__dirname, "frontend_src/guide/topics");
const CATALOG = resolve(__dirname, "custom_components/phoenix_mcp/catalogs/en.json");
/** Every language with Guide topics, so adding a language's folder is all it takes to ship it. */
const GUIDE_LANGUAGE_FOLDERS = readdirSync(GUIDE_TOPICS, { withFileTypes: true }).filter((d) => d.isDirectory()).map((d) => d.name).sort();

/**
 * Compile each language's Guide topics into guide/<language>.json beside the panel bundle; a broken topic,
 * or a translation whose topics and sections differ from English, fails the build.
 */
function guideTopics(): Plugin {
  return {
    name: "phoenix-guide-topics",
    generateBundle() {
      const panel = (JSON.parse(readFileSync(CATALOG, "utf8")) as { panel: Record<string, unknown> }).panel;
      const labelOf = (key: string): string | undefined => {
        let node: unknown = panel;
        for (const part of key.split(".")) node = node && typeof node === "object" ? (node as Record<string, unknown>)[part] : undefined;
        return typeof node === "string" ? node : undefined;
      };
      const labelExists = (key: string) => labelOf(key) !== undefined;
      const fileExists = (path: string) => !path.startsWith("/") && !path.includes("..") && existsSync(resolve(__dirname, path));
      const bundles: GuideBundle[] = [];
      for (const language of readdirSync(GUIDE_TOPICS)) {
        const dir = resolve(GUIDE_TOPICS, language);
        const files = readdirSync(dir).filter((f) => f.endsWith(".md")).sort().map((f) => {
          const path = resolve(dir, f);
          this.addWatchFile(path);
          return { file: `${language}/${f}`, source: readFileSync(path, "utf8") };
        });
        const { bundle, problems } = compileGuide(language, files, labelExists, fileExists, language === "en" ? (key) => labelOf(key)! : undefined);
        if (problems.length) this.error(`Guide topics:\n${problems.join("\n")}`);
        bundles.push(bundle);
        this.emitFile({ type: "asset", fileName: `guide/${language}.json`, source: JSON.stringify(bundle) });
      }
      const english = bundles.find((b) => b.language === "en");
      const mismatched = english ? bundles.filter((b) => b !== english).flatMap((b) => translationProblems(english, b)) : ["Guide topics: there are no English topics"];
      if (mismatched.length) this.error(`Guide translations:\n${mismatched.join("\n")}`);
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
