/** A render failure above the windows leaves a recoverable panel, not a blank page. */
import { afterEach, expect, it, vi } from "vitest";
import { act, waitFor } from "@testing-library/react";

vi.mock("../phoenix-mcp-panel.css?inline", () => ({ default: "" }));
vi.mock("../shell/dock/dock.css?inline", () => ({ default: "" }));
vi.mock("../themes.css?inline", () => ({ default: "" }));
vi.mock("dockview-react/dist/styles/dockview.css?inline", () => ({ default: "" }));

const state = vi.hoisted(() => ({ broken: true }));
vi.mock("../api", () => ({ api: {}, setHass: vi.fn(), ApiError: class extends Error {} }));
vi.mock("../shell/PanelData", () => ({ PanelDataProvider: ({ children }: { children: unknown }) => {
  if (state.broken) throw new Error("fixture render failure");
  return children;
} }));
vi.mock("../shell/Shell", () => ({ Shell: () => <p>Recovered panel</p> }));

await import("../index");

afterEach(() => {
  document.body.replaceChildren();
  vi.restoreAllMocks();
});

it("shows the failure with a working retry when a provider throws", async () => {
  vi.spyOn(console, "error").mockImplementation(() => {});
  state.broken = true;
  const panel = document.createElement("phoenix-mcp-panel");
  await act(async () => {
    document.body.append(panel);
    (panel as HTMLElement & { hass: unknown }).hass = { user: { id: "admin" } };
  });
  const root = panel.shadowRoot!;
  await waitFor(() => expect(root.querySelector('[role="alert"]')).not.toBeNull());

  state.broken = false;
  await act(async () => { root.querySelector<HTMLButtonElement>("button")!.click(); });
  await waitFor(() => expect(root.textContent).toContain("Recovered panel"));
  expect(root.querySelector('[role="alert"]')).toBeNull();
});
