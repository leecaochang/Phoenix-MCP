/** The simulator reports the server's verdict for an entity, not one rebuilt from the path.
 *
 * The resolution path lists only the tree's rules. The server's verdict also
 * applies what the path cannot show: a missing entity, a blocked domain, a
 * persona or household limit. Recomputing from the path would show WRITE for an
 * entity every tool call refuses.
 */
import { beforeEach, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen } from "@testing-library/react";

vi.mock("../api", () => ({ api: { resolve: vi.fn() } }));
import { api } from "../api";
import { PermissionSimulator } from "../components/PermissionSimulator";
import type { ResolveResult } from "../types";

const GREEN_DOMAIN = [
  { level: "global", state: "GREY" },
  { level: "domain:light", state: "GREEN" },
  { level: "entity:light.x", state: "GREY" },
] as ResolveResult["resolution_path"];

beforeEach(() => vi.resetAllMocks());

async function verdict(effective: ResolveResult["effective"], depth: "entity" | "device" | "domain" = "entity") {
  vi.mocked(api.resolve).mockResolvedValue({
    entity_id: "light.x", resolution_path: GREEN_DOMAIN, effective, effective_hint: null,
  });
  render(<PermissionSimulator accessKeyId="k" externalEntityId="light.x" resolveDepth={depth} />);
  const result = await screen.findByText(/Result:/);
  return result.querySelector("span")!.textContent;
}

it("shows NOT FOUND for a missing entity under a writable domain", async () => {
  expect(await verdict("NOT_FOUND")).toBe("NOT FOUND");
});

it("shows a limit the path cannot express", async () => {
  expect(await verdict("READ")).toBe("READ");
});

it("previews a domain row from its own rules", async () => {
  expect(await verdict("READ", "domain")).toBe("WRITE");
});

it("never previews a domain rule for an entity that does not exist", async () => {
  expect(await verdict("NOT_FOUND", "domain")).toBe("NOT FOUND");
});

it("drops a pending verdict when the entity box is cleared", async () => {
  let reply!: (r: ResolveResult) => void;
  vi.mocked(api.resolve).mockReturnValue(new Promise((r) => { reply = r; }));
  render(<PermissionSimulator accessKeyId="k" externalEntityId="light.x" resolveDepth="entity" />);
  fireEvent.change(screen.getByRole("textbox"), { target: { value: "" } });
  await act(async () => reply({ entity_id: "light.x", resolution_path: GREEN_DOMAIN, effective: "WRITE", effective_hint: null }));
  expect(screen.queryByText(/Result:/)).toBeNull();
});

it("refreshes what the box shows, not the earlier selection", async () => {
  vi.mocked(api.resolve).mockResolvedValue({
    entity_id: "light.x", resolution_path: GREEN_DOMAIN, effective: "WRITE", effective_hint: null,
  });
  const view = render(<PermissionSimulator accessKeyId="k" externalEntityId="light.x" resolveDepth="entity" triggerVersion={1} />);
  await screen.findByText(/Result:/);
  fireEvent.change(screen.getByRole("textbox"), { target: { value: "" } });
  expect(screen.queryByText(/Result:/)).toBeNull();
  const calls = vi.mocked(api.resolve).mock.calls.length;
  view.rerender(<PermissionSimulator accessKeyId="k" externalEntityId="light.x" resolveDepth="entity" triggerVersion={2} />);
  await act(async () => {});
  expect(vi.mocked(api.resolve).mock.calls.length).toBe(calls);
  expect(screen.queryByText(/Result:/)).toBeNull();
});
