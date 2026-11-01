/**
 * F6 and Shift+F6 move focus between the panel's areas (user, 2026-11-01):
 * the toolbar, each window in reading order, the Guide; folded windows by their
 * menu button; and back to where focus last was in an area.
 */
import { describe, it, expect, afterEach } from "vitest";
import { render, fireEvent } from "@testing-library/react";
import { useRef } from "react";
import { focusAreas, nextArea, useAreaKeys } from "../shell/areaFocus";

/** Give an element a box, since jsdom lays nothing out. */
function place(el: Element, top: number, left: number) {
  (el as HTMLElement).getBoundingClientRect = () => ({ top, left, width: 100, height: 50, right: left + 100, bottom: top + 50, x: left, y: top, toJSON: () => ({}) });
}

function Panel({ foldSecond = false }: { foldSecond?: boolean }) {
  const root = useRef<HTMLDivElement | null>(null);
  useAreaKeys(root);
  return (
    <div ref={root}>
      <div className="phx-toolbar"><button>Access Keys</button><button>Logs</button></div>
      <div className="phx-workspace-frame">
        {/* Declared right-then-left to prove the order comes from the screen, not the markup. */}
        <div className="dv-groupview"><button data-phx-window-menu="right">menu</button></div>
        <div className="dv-groupview"><button data-phx-window-menu="left">menu</button></div>
        <div className="phx-window-body" data-phx-window="right" role="region" aria-label="Right"><input aria-label="field" /></div>
        <div className="phx-window-body" data-phx-window="left" role="region" aria-label="Left" hidden={foldSecond} />
      </div>
      <section className="phx-guide" aria-label="Guide"><a href="#x">link</a></section>
    </div>
  );
}

function layOut(container: HTMLElement) {
  place(container.querySelector(".phx-toolbar")!, 0, 0);
  for (const b of container.querySelectorAll(".phx-toolbar button")) place(b, 0, 0);
  const menus = container.querySelectorAll("[data-phx-window-menu]");
  place(menus[0], 60, 600); // right
  place(menus[1], 60, 0); // left
  place(container.querySelector('[data-phx-window="right"]')!, 100, 600);
  place(container.querySelector('[data-phx-window="left"]')!, 100, 0);
  place(container.querySelector('[aria-label="field"]')!, 120, 610);
  place(container.querySelector(".phx-guide")!, 200, 900);
  place(container.querySelector(".phx-guide a")!, 210, 910);
}

const f6 = (shift = false) => fireEvent.keyDown(document.activeElement ?? document.body, { key: "F6", shiftKey: shift, bubbles: true });

afterEach(() => { (document.activeElement as HTMLElement | null)?.blur?.(); });

describe("area focus", () => {
  it("lists the toolbar, the windows as the screen shows them, then the Guide", () => {
    const { container } = render(<Panel />);
    layOut(container);
    expect(focusAreas(container).map((a) => a.key)).toEqual(["toolbar", "window:left", "window:right", "guide"]);
  });

  it("wraps around, goes back with Shift, and starts at the first area from outside", () => {
    const { container } = render(<Panel />);
    layOut(container);
    const areas = focusAreas(container);
    expect(nextArea(areas, null, false)?.key).toBe("toolbar");
    expect(nextArea(areas, null, true)?.key).toBe("guide");
    expect(nextArea(areas, container.querySelector(".phx-guide a"), false)?.key).toBe("toolbar");
    expect(nextArea(areas, container.querySelector('[aria-label="field"]'), true)?.key).toBe("window:left");
  });

  it("moves with F6 and Shift+F6, landing on a window itself the first time", () => {
    const { container } = render(<Panel />);
    layOut(container);
    (container.querySelector(".phx-toolbar button") as HTMLElement).focus();
    f6();
    expect(document.activeElement).toBe(container.querySelector('[data-phx-window="left"]'));
    f6();
    expect(document.activeElement).toBe(container.querySelector('[data-phx-window="right"]'));
    f6(true);
    expect(document.activeElement).toBe(container.querySelector('[data-phx-window="left"]'));
  });

  it("returns to where focus last was in an area", () => {
    const { container } = render(<Panel />);
    layOut(container);
    const field = container.querySelector('[aria-label="field"]') as HTMLElement;
    field.focus();
    fireEvent.focusIn(field);
    f6(); // to the Guide
    f6(true); // back to the right window
    expect(document.activeElement).toBe(field);
  });

  it("reaches a folded window by its menu button", () => {
    const { container } = render(<Panel foldSecond />);
    layOut(container);
    const left = focusAreas(container).find((a) => a.key === "window:left")!;
    expect(left.target).toBe(container.querySelectorAll("[data-phx-window-menu]")[1]);
  });
});
