import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { act, render, fireEvent, screen, waitFor } from "@testing-library/react";

const agentCliChat = vi.fn();
const getAgentCliModels = vi.fn();
const prepareAttachment = vi.fn();

vi.mock("../api", () => ({
  agentCliChat: (...args: unknown[]) => agentCliChat(...args),
  api: {
    getAgentCliModels: (...args: unknown[]) => getAgentCliModels(...args),
    getApproval: vi.fn(),
  },
}));

// The canvas work is covered in chat_attachments.test.ts; jsdom cannot decode images.
vi.mock("../utils/chat_attachments", async (importActual) => ({
  ...(await importActual<typeof import("../utils/chat_attachments")>()),
  prepareAttachment: (...args: unknown[]) => prepareAttachment(...args),
}));

import { AgentCliWindow } from "../components/AgentCliWindow";
import { getSessionTurns, __resetAgentCliState, __reloadFromStorage } from "../utils/agentcli_state";
import { SENT_IMAGE_PLACEHOLDER } from "../utils/chat_attachments";
import type { AgentCliInstance, AccessKeyRecord } from "../types";

const KEY = { id: "t1", name: "alpha", persona: "custom" } as AccessKeyRecord;
const UNKNOWN: AgentCliInstance = { id: "i-gpt", kind: "chatgpt", name: "GPT", model: "gpt-5" };
const VISION: AgentCliInstance = { ...UNKNOWN, capabilities: { "gpt-5": { vision: true } } };
const NO_VISION: AgentCliInstance = { ...UNKNOWN, capabilities: { "gpt-5": { vision: false } } };

function file(name: string): File {
  return new File(["png"], name, { type: "image/png" });
}

function prepared(name: string, data = "QUJD") {
  return { id: `id-${name}`, name, mimeType: "image/jpeg", data, dataUrl: `data:image/jpeg;base64,${data}` };
}

async function openWindow(instance: AgentCliInstance = UNKNOWN, accessKey: AccessKeyRecord = KEY) {
  const view = render(<AgentCliWindow accessKeys={[accessKey]} instances={[instance]} scrollbackLines={500}
                                      initialAccessKeyId="t1" onClose={() => {}} />);
  await waitFor(() => expect(getAgentCliModels).toHaveBeenCalled());
  return { view, textarea: screen.getByLabelText("Message Agent Chat") as HTMLTextAreaElement };
}

function paste(target: Element, files: File[], types = ["Files"], text = "") {
  fireEvent.paste(target, { clipboardData: { types, files, getData: () => text } });
}

beforeEach(() => {
  vi.clearAllMocks();
  __resetAgentCliState();
  getAgentCliModels.mockResolvedValue({ models: ["gpt-5"] });
  prepareAttachment.mockImplementation(async (source: File) => prepared(source.name));
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("Agent Chat image attachments", () => {
  it("keeps the newest image for follow-ups, replaces it when a new one is attached, and stores only names", async () => {
    agentCliChat.mockImplementation(async (body, onEvent: (n: string, p: unknown) => void) => {
      const user = body.attachments
        ? { role: "user", content: [
          ...body.attachments.map((a: { data: string }) => ({ type: "image_url", image_url: { url: `data:image/jpeg;base64,${a.data}` } })),
          { type: "text", text: body.user }] }
        : { role: "user", content: body.user };
      onEvent("assistant_delta", { text: `Reply to ${body.user}` });
      onEvent("messages", { messages: [...body.messages, user, { role: "assistant", content: "reply" }] });
      onEvent("done", { stop_reason: "end_turn" });
    });
    const { view, textarea } = await openWindow();

    paste(textarea, [file("shot.png")]);
    await screen.findByAltText("Attached image shot.png");
    fireEvent.change(textarea, { target: { value: "what is wrong?" } });
    fireEvent.keyDown(textarea, { key: "Enter" });
    await screen.findByText("Reply to what is wrong?");

    expect(agentCliChat.mock.calls[0][0].attachments).toEqual([{ mime_type: "image/jpeg", data: "QUJD" }]);
    expect(document.querySelector(".agentcli-attachment-list")).toBeNull();
    expect(document.querySelector(".agentcli-msg-user img")).toHaveAttribute("src", "data:image/jpeg;base64,QUJD");

    // A follow-up question still sends the image so the model can look again.
    fireEvent.change(textarea, { target: { value: "and the right one?" } });
    fireEvent.keyDown(textarea, { key: "Enter" });
    await screen.findByText("Reply to and the right one?");
    const second = agentCliChat.mock.calls[1][0];
    expect(second.attachments).toBeUndefined();
    expect(JSON.stringify(second.messages)).toContain("QUJD");
    expect(JSON.stringify(second.messages)).not.toContain(SENT_IMAGE_PLACEHOLDER);

    // A new image replaces it: only the new one goes, the old becomes a note.
    prepareAttachment.mockImplementationOnce(async (source: File) => prepared(source.name, "REVG"));
    paste(textarea, [file("after.png")]);
    await screen.findByAltText("Attached image after.png");
    fireEvent.change(textarea, { target: { value: "fixed now?" } });
    fireEvent.keyDown(textarea, { key: "Enter" });
    await screen.findByText("Reply to fixed now?");
    const third = agentCliChat.mock.calls[2][0];
    expect(third.attachments).toEqual([{ mime_type: "image/jpeg", data: "REVG" }]);
    expect(JSON.stringify(third.messages)).not.toContain("QUJD");
    expect(JSON.stringify(third.messages)).toContain(SENT_IMAGE_PLACEHOLDER);

    // The live session may show the image; browser storage keeps only its name.
    expect(JSON.stringify(getSessionTurns())).toContain("shot.png");
    const stored = localStorage.getItem("phx-agentcli-conversation:test-admin") ?? "";
    expect(stored).toContain("shot.png");
    expect(stored).not.toContain("QUJD");
    view.unmount();
    __reloadFromStorage();
    await openWindow();
    expect(screen.getByText("shot.png was not kept after reload.")).toBeInTheDocument();
  });

  it("sends an image without text", async () => {
    agentCliChat.mockImplementation(async (_body, onEvent: (n: string, p: unknown) => void) => {
      onEvent("done", { stop_reason: "end_turn" });
    });
    const { textarea } = await openWindow(VISION);
    paste(textarea, [file("label.png")]);
    await screen.findByAltText("Attached image label.png");
    fireEvent.click(screen.getByRole("button", { name: "Send" }));
    await waitFor(() => expect(agentCliChat).toHaveBeenCalledOnce());
    expect(agentCliChat.mock.calls[0][0]).toMatchObject({ user: "", attachments: [{ data: "QUJD" }] });
  });

  it("attaches files dropped anywhere on the window and reports non-images", async () => {
    prepareAttachment.mockImplementation(async (source: File) => {
      if (!source.type.startsWith("image/")) {
        const { AttachmentError } = await import("../utils/chat_attachments");
        throw new AttachmentError("type", source.name);
      }
      return prepared(source.name);
    });
    await openWindow();
    const transcript = document.querySelector(".agentcli-body") ?? screen.getByRole("dialog", { name: "Agent Chat" });
    const pdf = new File(["%PDF"], "manual.pdf", { type: "application/pdf" });
    const over = fireEvent.dragOver(transcript, { dataTransfer: { types: ["Files"], files: [] } });
    expect(over).toBe(false);  // default prevented: the browser will not open the file
    fireEvent.drop(transcript, { dataTransfer: { types: ["Files"], files: [file("card.png"), pdf] } });
    await screen.findByAltText("Attached image card.png");
    expect(screen.getByRole("alert")).toHaveTextContent("manual.pdf is not an image.");
  });

  it("keeps a text paste that also carries a picture as text", async () => {
    const { textarea } = await openWindow();
    paste(textarea, [file("cells.png")], ["text/plain", "Files"], "Kitchen\t21.5\nHall\t19.0");
    expect(prepareAttachment).not.toHaveBeenCalled();
  });

  it("removes a pending image and caps the count", async () => {
    const { textarea } = await openWindow();
    paste(textarea, ["a", "b", "c", "d", "e"].map((name) => file(`${name}.png`)));
    await screen.findByAltText("Attached image d.png");
    expect(prepareAttachment).toHaveBeenCalledTimes(4);
    expect(screen.getByRole("alert")).toHaveTextContent("You can attach up to 4 images to one message.");

    fireEvent.click(screen.getByRole("button", { name: "Remove b.png" }));
    expect(screen.queryByAltText("Attached image b.png")).toBeNull();
    expect(document.querySelectorAll(".agentcli-attachment")).toHaveLength(3);
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("drops pending images with /clear", async () => {
    const { textarea } = await openWindow();
    paste(textarea, [file("a.png"), file("b.png")]);
    await screen.findByAltText("Attached image b.png");
    fireEvent.change(textarea, { target: { value: "/clear" } });
    fireEvent.keyDown(textarea, { key: "Enter" });
    expect(document.querySelector(".agentcli-attachment-list")).toBeNull();
    expect(agentCliChat).not.toHaveBeenCalled();
  });

  it("keeps every image of the newest message for follow-ups", async () => {
    agentCliChat.mockImplementation(async (body, onEvent: (n: string, p: unknown) => void) => {
      const user = body.attachments
        ? { role: "user", content: [
          ...body.attachments.map((a: { data: string }) => ({ type: "image", source: { type: "base64", media_type: "image/jpeg", data: a.data } })),
          { type: "text", text: body.user }] }
        : { role: "user", content: body.user };
      onEvent("messages", { messages: [...body.messages, user, { role: "assistant", content: "ok" }] });
      onEvent("done", { stop_reason: "end_turn" });
    });
    prepareAttachment.mockImplementation(async (source: File) => prepared(source.name, source.name === "a.png" ? "QUFB" : "QkJC"));
    const { textarea } = await openWindow(VISION);
    paste(textarea, [file("a.png"), file("b.png")]);
    await screen.findByAltText("Attached image b.png");
    fireEvent.change(textarea, { target: { value: "compare these" } });
    fireEvent.keyDown(textarea, { key: "Enter" });
    await waitFor(() => expect(agentCliChat).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(textarea).not.toBeDisabled());
    fireEvent.change(textarea, { target: { value: "which is newer?" } });
    fireEvent.keyDown(textarea, { key: "Enter" });
    await waitFor(() => expect(agentCliChat).toHaveBeenCalledTimes(2));
    const resent = JSON.stringify(agentCliChat.mock.calls[1][0].messages);
    expect(resent).toContain("QUFB");
    expect(resent).toContain("QkJC");
  });

  it("says when the model has not declared image support, and not when it has", async () => {
    const { textarea, view } = await openWindow(UNKNOWN);
    paste(textarea, [file("a.png")]);
    await screen.findByText(/This model has not said whether it accepts images/);
    view.unmount();

    const second = await openWindow(VISION);
    paste(second.textarea, [file("a.png")]);
    await screen.findByAltText("Attached image a.png");
    expect(screen.queryByText(/This model has not said/)).toBeNull();
  });

  it.each([
    ["home_companion", UNKNOWN, "Home Companion takes text commands only, so images cannot be attached."],
    ["custom", NO_VISION, "This model does not accept images."],
  ])("blocks images for a %s access key when the model or persona cannot take them", async (persona, instance, reason) => {
    const { textarea } = await openWindow(instance, { ...KEY, persona } as AccessKeyRecord);
    const attach = screen.getByRole("button", { name: "Attach image" });
    expect(attach).toHaveAttribute("aria-disabled", "true");
    expect(attach).toHaveAttribute("title", reason);

    fireEvent.click(attach);
    expect(screen.getByRole("alert")).toHaveTextContent(reason);
    paste(textarea, [file("a.png")]);
    expect(prepareAttachment).not.toHaveBeenCalled();
  });

  it("puts the images back in the composer when the turn is cancelled", async () => {
    agentCliChat.mockImplementation(() => new Promise(() => {}));
    const { textarea } = await openWindow();
    paste(textarea, [file("shot.png")]);
    await screen.findByAltText("Attached image shot.png");
    fireEvent.change(textarea, { target: { value: "look" } });
    fireEvent.keyDown(textarea, { key: "Enter" });
    await waitFor(() => expect(agentCliChat).toHaveBeenCalled());
    expect(document.querySelector(".agentcli-attachment-list")).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    await waitFor(() => expect(document.querySelector(".agentcli-attachment-list img"))
      .toHaveAttribute("alt", "Attached image shot.png"));
    expect(textarea.value).toBe("look");
  });

  it("refreshes the account after the provider refuses images", async () => {
    const refreshed = vi.fn();
    window.addEventListener("phx-agentcli-providers-changed", refreshed);
    agentCliChat.mockImplementation(async (_body, onEvent: (n: string, p: unknown) => void) => {
      onEvent("error", { code: "provider_rejected", message: "refused", refused: "images" });
      onEvent("done", { stop_reason: "error" });
    });
    try {
      const { textarea } = await openWindow();
      paste(textarea, [file("a.png")]);
      await screen.findByAltText("Attached image a.png");
      await act(async () => {
        fireEvent.click(screen.getByRole("button", { name: "Send" }));
      });
      await waitFor(() => expect(refreshed).toHaveBeenCalledOnce());
    } finally {
      window.removeEventListener("phx-agentcli-providers-changed", refreshed);
    }
  });
});
