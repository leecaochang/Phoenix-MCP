/** The Notes card saves explicitly, counts like the server and keeps unsaved edits. */
import { useState } from "react";
import { beforeEach, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen } from "@testing-library/react";

vi.mock("../api", () => ({ api: { patchAccessKey: vi.fn() } }));
import { api } from "../api";
import { ACCESS_KEY_NOTES_MAX_LENGTH, AccessKeyNotes } from "../components/AccessKeyNotes";
import type { AccessKeyRecord } from "../types";

const KEY = { id: "k1", name: "alpha", notes: null } as AccessKeyRecord;

function Parent({ initial = KEY }: { initial?: AccessKeyRecord }) {
  const [accessKey, setAccessKey] = useState(initial);
  return (
    <>
      <AccessKeyNotes accessKey={accessKey} onUpdate={setAccessKey} />
      <button onClick={() => setAccessKey({ ...accessKey, notes: "Changed elsewhere." })}>external</button>
    </>
  );
}

beforeEach(() => vi.resetAllMocks());

const field = () => screen.getByLabelText("Notes for agents") as HTMLTextAreaElement;
const save = () => screen.getByRole("button", { name: "Save" });

it("shows no actions until the notes change, then saves the draft", async () => {
  vi.mocked(api.patchAccessKey).mockResolvedValueOnce({ ...KEY, notes: "The guest room is the den." });
  render(<Parent />);
  expect(screen.queryByRole("button", { name: "Save" })).toBeNull();

  fireEvent.change(field(), { target: { value: "The guest room is the den." } });
  await act(async () => fireEvent.click(save()));
  expect(api.patchAccessKey).toHaveBeenCalledWith("k1", { notes: "The guest room is the den." });
  expect(field().value).toBe("The guest room is the den.");
  expect(screen.queryByRole("button", { name: "Save" })).toBeNull();
});

it("clears with null when only whitespace is left", async () => {
  vi.mocked(api.patchAccessKey).mockResolvedValueOnce({ ...KEY, notes: null });
  render(<Parent initial={{ ...KEY, notes: "Old notes." }} />);
  fireEvent.change(field(), { target: { value: "   \n " } });
  await act(async () => fireEvent.click(save()));
  expect(api.patchAccessKey).toHaveBeenCalledWith("k1", { notes: null });
});

it("counts characters the way the server does and refuses an oversized draft", () => {
  render(<Parent />);
  fireEvent.change(field(), { target: { value: "\u{1F642}\u{1F642}" } });
  expect(screen.getByText(/^2 \/ 2,000 characters$/)).toBeInTheDocument();

  fireEvent.change(field(), { target: { value: "x".repeat(ACCESS_KEY_NOTES_MAX_LENGTH + 1) } });
  expect(screen.getByText("Notes must be 2,000 characters or fewer.")).toBeInTheDocument();
  expect(save()).toHaveAttribute("aria-disabled", "true");
  fireEvent.click(save());
  expect(api.patchAccessKey).not.toHaveBeenCalled();
});

it("follows a change made elsewhere but never overwrites an unsaved draft", () => {
  render(<Parent />);
  fireEvent.click(screen.getByRole("button", { name: "external" }));
  expect(field().value).toBe("Changed elsewhere.");

  fireEvent.change(field(), { target: { value: "My unsaved edit." } });
  fireEvent.click(screen.getByRole("button", { name: "external" }));
  expect(field().value).toBe("My unsaved edit.");
  fireEvent.click(screen.getByRole("button", { name: "Discard changes" }));
  expect(field().value).toBe("Changed elsewhere.");
});
