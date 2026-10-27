/**
 * Behaviour: a dashboard preview builds its cards one per task, not all in
 * the render that mounts them, so a large dashboard never holds the page.
 * Pins that no card is built synchronously, that every card is eventually
 * built in layout order, that a card is attached only once it has a config
 * (HA's hui-card throws on a first update without one), and that a preview
 * closed half way builds no more.
 */
import { it, expect, vi } from "vitest";
import { render, waitFor } from "@testing-library/react";
import { DashboardPreview } from "../components/DashboardPreview";

vi.mock("../api", () => ({ api: {}, currentHass: () => ({ user: { is_admin: true } }) }));

const built: string[] = [];
const attachedWithoutConfig: boolean[] = [];
customElements.define("hui-card", class extends HTMLElement {
  private hasConfig = false;
  connectedCallback() { attachedWithoutConfig.push(!this.hasConfig); }
  set config(value: { name: string }) { this.hasConfig = true; built.push(value.name); }
  set hass(_value: unknown) { /* not needed here */ }
  set preview(_value: unknown) { /* not needed here */ }
});

const cards = (count: number) => Array.from({ length: count }, (_, i) => ({ type: "button", name: `card ${i}` }));

it("builds the cards after the render, one task at a time, in layout order", async () => {
  built.length = 0;
  const { container } = render(<DashboardPreview config={{ views: [{ cards: cards(12) }] }} />);
  expect(container.querySelectorAll(".dash-preview-card-slot")).toHaveLength(12);
  expect(container.querySelectorAll("hui-card")).toHaveLength(0);
  expect(built).toEqual([]);
  await waitFor(() => expect(built).toHaveLength(12));
  expect(built).toEqual(cards(12).map((c) => c.name));
  expect(container.querySelectorAll(".dash-preview-card-slot > hui-card")).toHaveLength(12);
  expect(attachedWithoutConfig).toEqual(Array(12).fill(false));
});

it("drops the waiting builds of a preview that is closed", async () => {
  built.length = 0;
  const first = render(<DashboardPreview config={{ views: [{ cards: cards(40) }] }} />);
  first.unmount();
  const second = render(<DashboardPreview config={{ views: [{ cards: [{ type: "button", name: "kept" }] }] }} />);
  await waitFor(() => expect(built).toContain("kept"));
  expect(built.filter((name) => name.startsWith("card "))).toEqual([]);
  second.unmount();
});
