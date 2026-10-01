import type { Plugin } from "vite";
import { createHash } from "node:crypto";

/**
 * Give the complete emitted module graph one content-addressed URL revision.
 *
 * Chunk filenames stay stable, so the deployed frontend/ directory holds the
 * same file set every build instead of accumulating hashed variants; the query
 * param still gives every changed rebuild a URL the browser cannot have cached.
 * This is needed because the Phoenix MCP static route is registered with
 * cache_headers=False, which only omits Cache-Control (browsers then fall back
 * to heuristic caching of the bare chunk URL), and panel.py's mtime ?v=
 * cache-bust covers the ENTRY files only, never the chunks they import.
 * Static parent imports use the same revision as the HA entry URL. A change to
 * any dependency invalidates all graph URLs, including reciprocal entry imports.
 */
export function chunkQueryCacheBust(): Plugin {
  return {
    name: "phx-chunk-query-cache-bust",
    generateBundle(_options, bundle) {
      const chunks = Object.entries(bundle).filter(([, item]) => item.type === "chunk");
      const digest = createHash("sha256");
      for (const [fileName, item] of chunks.sort(([a], [b]) => a < b ? -1 : a > b ? 1 : 0)) {
        if (item.type === "chunk") digest.update(fileName).update("\0").update(item.code).update("\0");
      }
      const revision = digest.digest("hex").slice(0, 10);
      for (const [name, item] of Object.entries(bundle)) {
        if (item.type !== "chunk") continue;
        for (const [fileName] of chunks) {
          // All three quote styles: the emitted form is bundler-dependent and
          // has changed across major versions (Vite 5 emitted a double-quoted
          // string, Vite 8 emits a template literal). Missing one silently
          // drops the cache-bust rather than failing the build, so handle the
          // full set and assert on the output in vite_cachebust.test.ts.
          for (const q of ['"', "'", "`"]) {
            item.code = item.code.replaceAll(
              `${q}./${fileName}${q}`,
              `${q}./${fileName}?v=${revision}${q}`,
            );
          }
        }
        if (item.isEntry) {
          this.emitFile({ type: "asset", fileName: `${name}.version`, source: revision });
        }
      }
    },
  };
}
