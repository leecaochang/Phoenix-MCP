import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
import { readdirSync } from "fs";

/** As in vite.config.ts: every folder of Guide topics is a language the Guide ships. */
const GUIDE_LANGUAGE_FOLDERS = readdirSync("frontend_src/guide/topics", { withFileTypes: true })
  .filter((d) => d.isDirectory()).map((d) => d.name).sort();

export default defineConfig({
  plugins: [react()],
  define: { __GUIDE_LANGUAGES__: JSON.stringify(GUIDE_LANGUAGE_FOLDERS) },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./frontend_src/__tests__/setup.ts"],
    include: ["frontend_src/__tests__/**/*.test.{ts,tsx}"],
  },
});
