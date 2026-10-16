import { describe, it, expect, vi, afterEach } from "vitest";
import {
  ATTACHMENT_EDGE_PX,
  ATTACHMENT_MAX_BYTES,
  AttachmentError,
  SENT_IMAGE_PLACEHOLDER,
  imageFiles,
  prepareAttachment,
  withoutSentImages,
} from "../utils/chat_attachments";

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("withoutSentImages", () => {
  it("replaces the sent images in both provider shapes and keeps the text", () => {
    const claude = [
      { role: "user", content: [
        { type: "image", source: { type: "base64", media_type: "image/jpeg", data: "QUJD" } },
        { type: "text", text: "what is wrong?" },
      ] },
      { role: "assistant", content: "the card overflows" },
    ];
    const openai = [{ role: "user", content: [
      { type: "image_url", image_url: { url: "data:image/jpeg;base64,QUJD" } },
      { type: "text", text: "what is wrong?" },
    ] }];

    for (const messages of [claude, openai]) {
      const kept = withoutSentImages(messages);
      expect(JSON.stringify(kept)).not.toContain("QUJD");
      expect((kept[0] as { content: unknown[] }).content).toEqual([
        { type: "text", text: SENT_IMAGE_PLACEHOLDER },
        { type: "text", text: "what is wrong?" },
      ]);
      expect(kept.slice(1)).toEqual(messages.slice(1));
    }
  });

  it("leaves text-only and empty histories alone", () => {
    const text = [{ role: "user", content: "hello" }];
    expect(withoutSentImages(text)).toBe(text);
    expect(withoutSentImages([])).toEqual([]);
  });
});

describe("imageFiles", () => {
  const png = new File(["x"], "shot.png", { type: "image/png" });
  const pdf = new File(["x"], "doc.pdf", { type: "application/pdf" });

  it("takes only the images from a file paste or drop", () => {
    expect(imageFiles({ types: ["Files"], files: [png, pdf] } as unknown as DataTransfer)).toEqual([png]);
  });

  const withText = (text: string) => ({
    types: ["text/plain", "Files"], files: [png], getData: (type: string) => (type === "text/plain" ? text : ""),
  } as unknown as DataTransfer);

  it("treats a clipboard that also carries real text as a text paste", () => {
    expect(imageFiles(withText("Kitchen\t21.5\nHall\t19.0"))).toEqual([]);
    expect(imageFiles(withText("Turn the porch light on at sunset"))).toEqual([]);
    expect(imageFiles(null)).toEqual([]);
  });

  it("still attaches when the text only names the copied image", () => {
    expect(imageFiles(withText("Screenshot 2026-10-16 at 12.00.00.png"))).toEqual([png]);
    expect(imageFiles(withText("https://example.com/card.jpg?w=400"))).toEqual([png]);
    expect(imageFiles(withText("  "))).toEqual([png]);
  });
});

describe("prepareAttachment", () => {
  function stubCanvas(blobSizes: number[]) {
    const drawn: number[][] = [];
    const sizes = [...blobSizes];
    vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockImplementation(function (this: HTMLCanvasElement) {
      const canvas = this;
      return {
        fillStyle: "",
        fillRect: () => {},
        drawImage: (_source: unknown, x: number, y: number, w: number, h: number) => {
          drawn.push([x, y, w, h, canvas.width, canvas.height]);
        },
      } as unknown as CanvasRenderingContext2D;
    });
    vi.spyOn(HTMLCanvasElement.prototype, "toBlob").mockImplementation((callback: BlobCallback) => {
      const size = sizes.shift() ?? 10;
      callback(new Blob([new Uint8Array(size)], { type: "image/jpeg" }));
    });
    return drawn;
  }

  it("shrinks the long edge and re-encodes as a JPEG", async () => {
    const close = vi.fn();
    vi.stubGlobal("createImageBitmap", vi.fn().mockResolvedValue({ width: ATTACHMENT_EDGE_PX * 2, height: 1000, close }));
    const drawn = stubCanvas([10]);

    const attachment = await prepareAttachment(new File(["png"], "shot.png", { type: "image/png" }));

    expect(drawn).toEqual([[0, 0, ATTACHMENT_EDGE_PX, 500, ATTACHMENT_EDGE_PX, 500]]);
    expect(attachment).toMatchObject({ name: "shot.png", mimeType: "image/jpeg" });
    expect(attachment.dataUrl).toBe(`data:image/jpeg;base64,${attachment.data}`);
    expect(atob(attachment.data)).toHaveLength(10);
    expect(close).toHaveBeenCalledOnce();
  });

  it("lowers the quality once, then refuses an image that is still too large", async () => {
    vi.stubGlobal("createImageBitmap", vi.fn().mockResolvedValue({ width: 100, height: 100, close: () => {} }));
    stubCanvas([ATTACHMENT_MAX_BYTES + 1, 20]);
    expect((await prepareAttachment(new File(["a"], "a.png", { type: "image/png" }))).data).toBeTruthy();

    stubCanvas([ATTACHMENT_MAX_BYTES + 1, ATTACHMENT_MAX_BYTES + 1]);
    await expect(prepareAttachment(new File(["b"], "b.png", { type: "image/png" })))
      .rejects.toMatchObject({ problem: "size", fileName: "b.png" });
  });

  it("refuses non-images, oversized sources and undecodable files", async () => {
    await expect(prepareAttachment(new File(["%PDF"], "doc.pdf", { type: "application/pdf" })))
      .rejects.toBeInstanceOf(AttachmentError);
    const huge = new File(["x"], "huge.png", { type: "image/png" });
    Object.defineProperty(huge, "size", { value: 50 * 1024 * 1024 });
    await expect(prepareAttachment(huge)).rejects.toMatchObject({ problem: "size" });

    vi.stubGlobal("createImageBitmap", vi.fn().mockRejectedValue(new Error("bad")));
    await expect(prepareAttachment(new File(["junk"], "junk.png", { type: "image/png" })))
      .rejects.toMatchObject({ problem: "unreadable", fileName: "junk.png" });
  });
});
