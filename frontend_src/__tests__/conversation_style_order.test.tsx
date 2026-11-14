/** The Conversation style list reads alphabetically, with the English-only characters grouped apart. */
import { expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { ConversationBehaviorControls } from "../components/ConversationBehaviorControls";

it("lists each group of styles in alphabetical order of its labels", () => {
  render(<ConversationBehaviorControls surface="Agent Chat" style="direct" detail="concise" saving={false}
    onStyleChange={() => {}} onDetailChange={() => {}} />);
  const select = screen.getAllByRole("combobox")[0] as HTMLSelectElement;
  const group = select.querySelector("optgroup")!;
  const plain = [...select.children].filter((el) => el.tagName === "OPTION").map((o) => o.textContent);
  const characters = [...group.querySelectorAll("option")].map((o) => o.textContent);
  expect(plain).toEqual(["Calm Guide", "Direct", "Explanatory", "Lively", "Technical", "Warm"]);
  expect(characters).toEqual([
    "Butler", "Caveman", "Drama Queen", "Grumpy Teenager", "Nature Documentary",
    "Noir Detective", "Pirate", "Sarcastic", "Shakespearean", "Starship Computer",
  ]);
});
