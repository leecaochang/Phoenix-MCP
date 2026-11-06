/**
 * What the catalog checks cannot see, comparing whole values only: one register
 * in German, one term for "provider" in Traditional Chinese, and count-neutral
 * forms where the server sends a count without choosing a plural.
 */
import { describe, expect, it } from "vitest";
import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";
import de from "../../custom_components/phoenix_mcp/catalogs/de.json";
import pl from "../../custom_components/phoenix_mcp/catalogs/pl.json";
import ru from "../../custom_components/phoenix_mcp/catalogs/ru.json";
import zhHant from "../../custom_components/phoenix_mcp/catalogs/zh-Hant.json";

function flat(node: unknown, path = ""): [string, string][] {
  if (typeof node === "string") return [[path, node]];
  if (!node || typeof node !== "object") return [];
  return Object.entries(node).flatMap(([key, value]) => flat(value, path ? `${path}.${key}` : key));
}

const DU_FORMS = /\b(du|dein\w*|dich|dir)\b/i;
const DU_IMPERATIVES = /(?:^|[.!?:;]\s+)(Prüfe|Lade|Versuche|Wähle|Öffne|Gib|Starte|Füge|Stelle|Lege|Entferne|Kürze|Ändere|Klicke|Sieh|Nutze|Verwende|Aktiviere|Deaktiviere|Aktualisiere|Warte|Melde|Antworte|Sage)\b/;

describe("German", () => {
  it("addresses the reader in one register", () => {
    // This one is the operator's own message to the agent, not text addressed to the operator.
    const addressed = flat(de).filter(([key]) => key !== "panel.wizard.promptChat");
    expect(addressed.filter(([, value]) => DU_FORMS.test(value) || DU_IMPERATIVES.test(value))).toEqual([]);
  });
});

describe("Traditional Chinese", () => {
  it("has one term for a provider in the catalog", () => {
    const values = flat(zhHant);
    expect(values.filter(([, value]) => value.includes("提供方"))).toEqual([]);
    expect(values.filter(([, value]) => value.includes("供應商")).map(([key]) => key)).toEqual(["panel.mesa.vendorBadge"]);
  });

  it("has one term for a provider in the Guide", () => {
    const folder = "frontend_src/guide/topics/zh-Hant";
    const pages = readdirSync(folder).filter((name) => readFileSync(join(folder, name), "utf8").includes("提供方"));
    expect(pages).toEqual([]);
  });
});

describe("a count with no plural chosen", () => {
  it.each([["Polish", pl], ["Russian", ru]])("%s puts no counted word after it", (_name, catalog) => {
    expect((catalog as typeof pl).panel.agentchat.progress.waitingApprovals).not.toMatch(/\{count\}\s*\p{L}/u);
  });
});
