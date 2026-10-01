import { it, expect, vi } from "vitest";
import { fireEvent, render, screen, within, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { PermissionSelector } from "../components/PermissionSelector";
import { DashboardPreview } from "../components/DashboardPreview";
import { Modal } from "../components/Modal";
import type { TokenRecord } from "../types";

vi.mock("../api", () => ({ api: { patchToken: vi.fn() } }));
import { api } from "../api";
import { CapabilityMatrix } from "../components/CapabilityMatrix";

it("activates entity permissions through keyboard clicks and assistive clicks", async () => {
  const onChange = vi.fn();
  render(<PermissionSelector value="GREY" onChange={onChange} />);
  const write = screen.getByRole("button", { name: "W: Read and write" });
  write.focus();
  const user = userEvent.setup();
  await user.keyboard("{Enter}");
  await user.keyboard(" ");
  expect(onChange.mock.calls).toEqual([["GREEN"], ["GREEN"]]);
  expect(write).toHaveFocus();
});

it("does not execute proposed custom cards or embedded HTML", () => {
  const configSetter = vi.fn();
  if (!customElements.get("hui-card")) {
    customElements.define("hui-card", class extends HTMLElement { set config(value: unknown) { configSetter(value); } });
  }
  const { container } = render(<DashboardPreview config={{ views: [{ cards: [{
    type: "custom:button-card", name: "[[[ hass.callService('lock', 'unlock'); ]]]",
    content: "<img src=x onerror=alert(1)>",
  }] }] }} />);
  expect(container.querySelector("hui-card")).toBeNull();
  expect(container.querySelector("img")).toBeNull();
  expect(configSetter).not.toHaveBeenCalled();
  expect(container.textContent).toContain("hass.callService");
});

it("keeps Tab trapped and restores the exact shadow-root opener", () => {
  const host = document.createElement("div");
  document.body.append(host);
  const shadow = host.attachShadow({ mode: "open" });
  const opener = document.createElement("button");
  opener.textContent = "Open";
  shadow.append(opener);
  opener.focus();
  const container = document.createElement("div");
  shadow.append(container);
  const view = render(<Modal titleId="title"><h2 id="title">Review</h2><button>First</button><button>Last</button></Modal>, { container });
  const first = within(container).getByRole("button", { name: "First" });
  const last = within(container).getByRole("button", { name: "Last" });
  expect(shadow.activeElement).toBe(first);
  last.focus();
  fireEvent.keyDown(last, { key: "Tab" });
  expect(shadow.activeElement).toBe(first);
  fireEvent.keyDown(first, { key: "Tab", shiftKey: true });
  expect(shadow.activeElement).toBe(last);
  view.unmount();
  expect(shadow.activeElement).toBe(opener);
  host.remove();
});

it("saves a capability only on explicit activation and retains focus while saving", async () => {
  let finish!: (token: TokenRecord) => void;
  vi.mocked(api.patchToken).mockReturnValue(new Promise((resolve) => { finish = resolve; }));
  const token = { id: "test", cap_automation_write: "deny" } as TokenRecord;
  const onUpdate = vi.fn();
  render(<CapabilityMatrix token={token} onUpdate={onUpdate} />);
  const group = screen.getByRole("group", { name: "Manage automations" });
  const deny = within(group).getByRole("button", { name: "Deny" });
  const confirm = within(group).getByRole("button", { name: "Confirm" });
  deny.focus();
  const user = userEvent.setup();
  await user.keyboard("{ArrowRight}{ArrowRight}");
  expect(api.patchToken).not.toHaveBeenCalled();
  confirm.focus();
  await user.keyboard("{Enter}");
  expect(api.patchToken).toHaveBeenCalledExactlyOnceWith("test", { cap_automation_write: "confirm" });
  expect(confirm).toHaveFocus();
  expect(confirm).not.toBeDisabled();
  finish({ ...token, cap_automation_write: "confirm" });
  await waitFor(() => expect(onUpdate).toHaveBeenCalled());
});
