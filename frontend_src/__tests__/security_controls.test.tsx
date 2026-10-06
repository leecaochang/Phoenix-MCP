import { it, expect, vi } from "vitest";
import { fireEvent, render, screen, within, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { PermissionSelector } from "../components/PermissionSelector";
import { DashboardPreview } from "../components/DashboardPreview";
import { Modal } from "../components/Modal";
import type { AccessKeyRecord } from "../types";

vi.mock("../api", () => ({ api: { patchAccessKey: vi.fn() } }));
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

it("changes a permission only on a primary press of the primary contact", () => {
  const onChange = vi.fn();
  render(<PermissionSelector value="GREY" onChange={onChange} />);
  const write = screen.getByRole("button", { name: "W: Read and write" });
  write.releasePointerCapture ??= () => {};
  fireEvent.pointerDown(write, { button: 2, pointerId: 1, isPrimary: true });
  fireEvent.pointerDown(write, { button: 1, pointerId: 1, isPrimary: true });
  fireEvent.pointerDown(write, { button: 0, pointerId: 2, isPrimary: false });
  expect(onChange).not.toHaveBeenCalled();
  fireEvent.pointerDown(write, { button: 0, pointerId: 1, isPrimary: true });
  expect(onChange.mock.calls).toEqual([["GREEN"]]);
  fireEvent.pointerUp(document, { pointerId: 1 });
});

it("lets only the pointer that started a paint gesture continue or end it", () => {
  const first = vi.fn(), second = vi.fn();
  render(<><PermissionSelector value="GREY" label="first" onChange={first} />
    <PermissionSelector value="GREY" label="second" onChange={second} /></>);
  const write = screen.getAllByRole("button", { name: "W: Read and write" })[0];
  write.releasePointerCapture ??= () => {};
  const row = screen.getByRole("group", { name: "second" });
  fireEvent.pointerDown(write, { button: 0, pointerId: 1, isPrimary: true });
  fireEvent.pointerEnter(row, { pointerId: 2, isPrimary: false, buttons: 1 });
  fireEvent.pointerUp(document, { pointerId: 2 });
  expect(second).not.toHaveBeenCalled();
  fireEvent.pointerEnter(row, { pointerId: 1, isPrimary: true, buttons: 1 });
  expect(second.mock.calls).toEqual([["GREEN"]]);
  fireEvent.pointerUp(document, { pointerId: 1 });
  fireEvent.pointerEnter(row, { pointerId: 1, isPrimary: true, buttons: 1 });
  expect(second).toHaveBeenCalledTimes(1);
});

it.each([
  ["a hover with no button held", () => {}, 0],
  ["focus lost before the release", () => fireEvent.blur(window), 1],
  ["the page hidden before the release", () => fireEvent(window, new Event("pagehide")), 1],
])("does not paint after %s", (_label, interrupt, buttons) => {
  const first = vi.fn(), second = vi.fn();
  render(<><PermissionSelector value="GREY" label="first" onChange={first} />
    <PermissionSelector value="GREY" label="second" onChange={second} /></>);
  const write = screen.getAllByRole("button", { name: "W: Read and write" })[0];
  write.releasePointerCapture ??= () => {};
  const row = screen.getByRole("group", { name: "second" });
  fireEvent.pointerDown(write, { button: 0, pointerId: 1, isPrimary: true });
  interrupt();
  fireEvent.pointerEnter(row, { pointerId: 1, isPrimary: true, buttons });
  fireEvent.pointerEnter(row, { pointerId: 1, isPrimary: true, buttons: 1 });
  expect(second).not.toHaveBeenCalled();
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

it("keeps Tab inside a dialog whose last reachable control is a closed details summary", async () => {
  // The rotated-key dialog's shape: its Close button is disabled for a few
  // seconds, and the connection help is a collapsed <details> with buttons inside.
  render(<><button>Background</button><Modal titleId="rotated"><h2 id="rotated">Rotated key</h2>
    <button>Copy key</button>
    <details><summary>Connection help</summary><button>Copy command</button></details>
    <button disabled>Close</button>
  </Modal></>);
  const user = userEvent.setup();
  const summary = screen.getByText("Connection help");
  summary.focus();
  await user.tab();
  expect(screen.getByRole("button", { name: "Copy key" })).toHaveFocus();
  await user.tab({ shift: true });
  expect(summary).toHaveFocus();
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
  let finish!: (accessKey: AccessKeyRecord) => void;
  vi.mocked(api.patchAccessKey).mockReturnValue(new Promise((resolve) => { finish = resolve; }));
  const accessKey = { id: "test", cap_automation_write: "deny" } as AccessKeyRecord;
  const onUpdate = vi.fn();
  render(<CapabilityMatrix accessKey={accessKey} onUpdate={onUpdate} />);
  const group = screen.getByRole("group", { name: "Manage automations" });
  const deny = within(group).getByRole("button", { name: "Deny" });
  const confirm = within(group).getByRole("button", { name: "Confirm" });
  deny.focus();
  const user = userEvent.setup();
  await user.keyboard("{ArrowRight}{ArrowRight}");
  expect(api.patchAccessKey).not.toHaveBeenCalled();
  confirm.focus();
  await user.keyboard("{Enter}");
  expect(api.patchAccessKey).toHaveBeenCalledExactlyOnceWith("test", { cap_automation_write: "confirm" });
  expect(confirm).toHaveFocus();
  expect(confirm).not.toBeDisabled();
  finish({ ...accessKey, cap_automation_write: "confirm" });
  await waitFor(() => expect(onUpdate).toHaveBeenCalled());
});
