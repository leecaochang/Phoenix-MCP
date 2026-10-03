import { it, expect, vi } from "vitest";
import { render, fireEvent } from "@testing-library/react";
import { ConversationSettings } from "../components/ConversationSettings";
import type { GlobalSettings } from "../types";

it("saves all retention limits together and explains disabled recording", () => {
  const settings = { disable_all_logging: true, log_entity_names: false, conversation_log_enabled: true } as GlobalSettings;
  const onSave = vi.fn().mockResolvedValue(undefined);
  const view = render(<ConversationSettings settings={settings} saving={false} onChange={vi.fn()} onSave={onSave} />);
  expect(view.getAllByRole("button", { name: "Save" })).toHaveLength(1);
  expect(view.getByLabelText("Record conversations")).toBeDisabled();
  expect(view.getByText(/Disable all logging is on/)).toBeTruthy();
  expect(view.getByText(/entity-name logging is disabled/)).toBeTruthy();
  fireEvent.change(view.getByLabelText("Maximum entries"), { target: { value: "200" } });
  fireEvent.change(view.getByLabelText("Maximum age (days)"), { target: { value: "14" } });
  fireEvent.click(view.getByRole("button", { name: "Save" }));
  expect(onSave).toHaveBeenCalledWith({ conversation_log_max_entries: 200, conversation_log_max_days: 14, conversation_log_max_mb: 25 });
  fireEvent.change(view.getByLabelText("Maximum storage (MB)"), { target: { value: "0" } });
  expect(view.getByRole("button", { name: "Save" })).toBeDisabled();
});
