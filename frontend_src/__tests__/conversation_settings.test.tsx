/**
 * Conversation Log settings: each retention limit saves itself shortly after it
 * changes, a value out of range is never saved and goes back when the field is
 * left, the storage used shows under the limits with its name in bold, and with
 * all logging off recording and its limits are greyed, saying why in Help.
 */
import { it, expect, vi, afterEach } from "vitest";
import { act, render, fireEvent, waitFor } from "@testing-library/react";

vi.mock("../api", () => ({ api: { getConversations: vi.fn().mockResolvedValue({ bytes: 280000, max_bytes: 25000000 }) } }));

import { ConversationSettings } from "../components/ConversationSettings";
import type { GlobalSettings } from "../types";

afterEach(() => { vi.useRealTimers(); });

const SETTINGS = { disable_all_logging: false, log_entity_names: false, conversation_log_enabled: true,
  conversation_log_max_entries: 100, conversation_log_max_days: 7, conversation_log_max_mb: 25 } as GlobalSettings;

it("greys recording and every limit while all logging is off, with the reason in Help and no notice (user, 2026-10-31)", () => {
  const view = render(<ConversationSettings settings={{ ...SETTINGS, disable_all_logging: true }} saving={false} onChange={vi.fn()} />);
  for (const name of ["Record conversations", "Maximum entries", "Maximum age (days)", "Maximum storage (MB)"]) expect(view.getByLabelText(name)).toBeDisabled();
  // Read out with the switch for screen readers, but not shown.
  expect(view.queryAllByText(/Disable all logging is on/).filter((el) => !el.closest(".sr-only"))).toEqual([]);
  expect(view.getByLabelText("Record conversations").closest(".toggle-row")).toHaveClass("toggle-row-greyed");
  expect(view.getByLabelText("Maximum entries").closest("fieldset")).toHaveAttribute("data-help", "conversations.loggingDisabled");
});

it("shows no notice when entity-name logging is off (user, 2026-10-31)", () => {
  const view = render(<ConversationSettings settings={SETTINGS} saving={false} onChange={vi.fn()} />);
  expect(view.queryByText(/entity-name logging is disabled/)).toBeNull();
});

it("saves a limit by itself after a pause, and offers no Save", () => {
  vi.useFakeTimers();
  const onChange = vi.fn();
  const view = render(<ConversationSettings settings={SETTINGS} saving={false} onChange={onChange} />);
  expect(view.queryByRole("button", { name: "Save" })).toBeNull();
  expect(view.getByLabelText("Record conversations")).toBeEnabled();
  fireEvent.change(view.getByLabelText("Maximum entries"), { target: { value: "200" } });
  expect(onChange).not.toHaveBeenCalled();
  act(() => { vi.advanceTimersByTime(600); });
  expect(onChange).toHaveBeenCalledExactlyOnceWith("conversation_log_max_entries", 200);
});

it("never saves a value out of range, and puts the saved one back when the field is left", () => {
  vi.useFakeTimers();
  const onChange = vi.fn();
  const view = render(<ConversationSettings settings={SETTINGS} saving={false} onChange={onChange} />);
  const mb = view.getByLabelText("Maximum storage (MB)");
  fireEvent.change(mb, { target: { value: "0" } });
  act(() => { vi.advanceTimersByTime(1000); });
  fireEvent.blur(mb);
  expect(onChange).not.toHaveBeenCalled();
  expect(mb).toHaveValue(25);
});

it("saves at once on Enter", () => {
  const onChange = vi.fn();
  const view = render(<ConversationSettings settings={SETTINGS} saving={false} onChange={onChange} />);
  const days = view.getByLabelText("Maximum age (days)");
  fireEvent.change(days, { target: { value: "14" } });
  fireEvent.keyDown(days, { key: "Enter" });
  expect(onChange).toHaveBeenCalledExactlyOnceWith("conversation_log_max_days", 14);
});

it("shows the storage used last, with its name in bold", async () => {
  const view = render(<ConversationSettings settings={SETTINGS} saving={false} onChange={vi.fn()} />);
  const storage = await waitFor(() => {
    const el = view.container.querySelector(".conversation-storage");
    expect(el).not.toBeNull();
    return el!;
  });
  expect(storage.querySelector("strong")?.textContent).toBe("Storage:");
  expect(storage.textContent).toBe("Storage: 0.28 of 25 MB");
  expect(storage.parentElement!.lastElementChild).toBe(storage);
});
