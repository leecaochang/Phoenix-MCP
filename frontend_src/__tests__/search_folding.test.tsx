/** Typed searches fold case and accents the way list searches do, so "kuche" finds "Küche". */
import { afterAll, describe, expect, it } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import en from "../../custom_components/phoenix_mcp/catalogs/en.json";
import fr from "../../custom_components/phoenix_mcp/catalogs/fr.json";
import { TagInput } from "../components/TagInput";
import { primeTranslations } from "../i18n";
import { auditSourceFilterValue } from "../utils/audit_source";

afterAll(() => primeTranslations(en.panel));

describe("TagInput", () => {
  function typeTag(query: string) {
    render(<TagInput value={[]} onChange={() => {}} canonicalTags={["room.küche", "room.salón", "lighting.task"]} />);
    const field = screen.getByRole("combobox");
    fireEvent.focus(field);
    fireEvent.change(field, { target: { value: query } });
  }

  it("finds a tag with accents from a query typed without them", () => {
    typeTag("kuche");
    expect(screen.getAllByRole("option").map((option) => option.textContent)).toEqual([expect.stringContaining("küche")]);
  });

  it("folds case too", () => {
    typeTag("SALON");
    expect(screen.getAllByRole("option").map((option) => option.textContent)).toEqual([expect.stringContaining("salón")]);
  });
});

describe("audit source search", () => {
  it("maps a friendly name typed without accents to its stored marker", () => {
    primeTranslations(fr.panel, "fr");
    expect(auditSourceFilterValue("tache d'ia")).toBe("ai_task");
    expect(auditSourceFilterValue("TÂCHE")).toBe("ai_task");
    // An address or ambiguous text passes through unchanged.
    expect(auditSourceFilterValue("192.0.2.1")).toBe("192.0.2.1");
  });
});
