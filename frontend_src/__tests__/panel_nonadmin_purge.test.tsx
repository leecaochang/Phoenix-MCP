/** The panel bundle purges an administrator's saved Agent Chat when a non-admin's session reaches it. */
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { purgeIfNotAdmin } from "../utils/agentcli_state";

vi.mock("../phoenix-mcp-panel.css?inline", () => ({ default: "" }));
vi.mock("../shell/dock/dock.css?inline", () => ({ default: "" }));
vi.mock("dockview-react/dist/styles/dockview.css?inline", () => ({ default: "" }));
vi.mock("../api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api")>();
  return { ...actual, api: new Proxy({}, { get: () => vi.fn(() => new Promise(() => {})) }) };
});

await import("../index");

const SAVED = "phx-agentcli-conversation:admin-1";

beforeEach(() => {
  localStorage.clear();
  localStorage.setItem(SAVED, JSON.stringify({ entries: [{ kind: "assistant", text: "the alarm code is" }] }));
});

afterEach(() => {
  document.body.innerHTML = "";
});

function mountWith(user: Record<string, unknown>): void {
  const el = document.createElement("phoenix-mcp-panel") as HTMLElement & { hass: unknown };
  document.body.appendChild(el);
  el.hass = { user, language: "en", locale: { language: "en" } };
}

it("removes the saved conversation when a non-admin's hass reaches the panel", () => {
  mountWith({ id: "guest", is_admin: false });
  expect(localStorage.getItem(SAVED)).toBeNull();
});

it("leaves it for an administrator", () => {
  mountWith({ id: "admin-2", is_admin: true });
  expect(localStorage.getItem(SAVED)).not.toBeNull();
});

it("waits for Home Assistant to name the user before deciding", () => {
  expect(purgeIfNotAdmin(null)).toBe(false);
  expect(purgeIfNotAdmin({ user: { id: "x" } })).toBe(false);
  expect(localStorage.getItem(SAVED)).not.toBeNull();
  expect(purgeIfNotAdmin({ user: { id: "x", is_admin: false } })).toBe(true);
  expect(localStorage.getItem(SAVED)).toBeNull();
});
