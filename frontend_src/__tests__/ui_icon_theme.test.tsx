/** Shared icon glyphs and the theme rule every Phoenix surface follows. */
import { afterEach, describe, expect, it } from "vitest";
import { render } from "@testing-library/react";
import { Icon } from "../ui/Icon";
import { applyThemeClass, readThemePreference, resolveThemeClass } from "../ui/theme";

describe("Icon", () => {
  it("draws a decorative outline that assistive technology skips", () => {
    const { container } = render(<Icon name="close" />);
    const svg = container.querySelector("svg")!;
    expect(svg).toHaveAttribute("aria-hidden", "true");
    expect(svg).toHaveAttribute("fill", "none");
    expect(svg).toHaveAttribute("stroke-width", "1.75");
    expect(svg).toHaveClass("lucide-x");
  });

  it("fills only when asked, for marks such as the Home Companion star", () => {
    const { container } = render(<Icon name="star" filled />);
    expect(container.querySelector("svg")).toHaveAttribute("fill", "currentColor");
  });
});

describe("theme", () => {
  afterEach(() => localStorage.removeItem("phx-theme"));

  it("lets an explicit Phoenix choice win over Home Assistant's mode", () => {
    expect(resolveThemeClass(true, "light")).toBe("phx-theme-light");
    expect(resolveThemeClass(false, "dark")).toBe("phx-theme-dark");
  });

  it("follows Home Assistant under auto, and leaves an unknown mode to the stylesheet", () => {
    expect(resolveThemeClass(true, "auto")).toBe("phx-theme-dark");
    expect(resolveThemeClass(false, "auto")).toBe("phx-theme-light");
    expect(resolveThemeClass(undefined, "auto")).toBeNull();
  });

  it("reads the stored choice and treats anything else as auto", () => {
    localStorage.setItem("phx-theme", "dark");
    expect(readThemePreference()).toBe("dark");
    localStorage.setItem("phx-theme", "sepia");
    expect(readThemePreference()).toBe("auto");
  });

  it("swaps the theme class rather than stacking both", () => {
    const el = document.createElement("div");
    applyThemeClass(el, "phx-theme-dark");
    applyThemeClass(el, "phx-theme-light");
    expect([...el.classList]).toEqual(["phx-theme-light"]);
    applyThemeClass(el, null);
    expect(el.classList.length).toBe(0);
  });
});
