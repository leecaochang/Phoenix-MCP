import type { Plugin } from "vite";
import { createHash } from "node:crypto";

/**
 * Give the complete emitted module graph one content-addressed `?v=` revision.
 *
 * Chunk filenames stay stable, so the deployed frontend/ directory holds the
 * same file set every build; the query param gives every changed rebuild a URL
 * the browser cannot have cached. Needed because the static route is registered
 * with cache_headers=False, which only omits Cache-Control (browsers then cache
 * the bare chunk URL heuristically), and panel.py's `?v=` on the entry URL never
 * covers the chunks an entry imports. Static parent imports share the revision
 * panel.py reads from the entry's .version asset, so a change to any dependency
 * invalidates every graph URL, including reciprocal entry imports.
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
          // All three quote styles: the emitted form depends on the bundler
          // version (double-quoted string or template literal). Missing one
          // silently drops the cache-bust rather than failing the build, so
          // handle the full set; a test asserts on the emitted output.
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
