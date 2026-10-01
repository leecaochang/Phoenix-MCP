/** Documentation drawer and heading anchors preserve keyboard ownership. */
import { it, expect, vi, afterEach } from "vitest";
import { readFileSync } from "node:fs";

const script = readFileSync("docs/assets/site.js", "utf8");
afterEach(() => { document.body.innerHTML = ""; document.body.removeAttribute("data-home"); vi.unstubAllGlobals(); });

it("keeps a closed home drawer inert and restores it when opened", () => {
  document.body.setAttribute("data-home", "");
  document.body.innerHTML = '<header id="site-header"></header><aside id="sidebar"></aside><main class="content"><a class="card" href="quickstart.html"><h3>Start here</h3></a><h2>Ordinary section</h2></main><footer id="site-footer"></footer>';
  vi.stubGlobal("IntersectionObserver", class { observe() {} disconnect() {} });
  new Function(script)();
  const sidebar = document.getElementById("sidebar")!;
  expect(sidebar).toHaveAttribute("aria-hidden", "true");
  expect((sidebar as HTMLElement & { inert: boolean }).inert).toBe(true);
  document.getElementById("menu-btn")!.click();
  expect(sidebar).toHaveAttribute("aria-hidden", "false");
  expect((sidebar as HTMLElement & { inert: boolean }).inert).toBe(false);
  expect(document.querySelector("a.card a")).toBeNull();
  expect(document.querySelector("h2 .anchor-link")).not.toBeNull();
});
