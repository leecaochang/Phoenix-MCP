// Images an administrator attaches to an Agent Chat message.
//
// Every image is decoded and redrawn onto a canvas before it leaves the browser:
// the long edge shrinks to the size providers analyse anyway, the result is a
// JPEG on a white background, and redrawing drops EXIF data such as a phone
// photo's location. The server checks its own bounds independently
// (`_chat_attachments` in agentcli.py); these mirror its count and size limits.

export const ATTACHMENT_MAX_COUNT = 4;
export const ATTACHMENT_EDGE_PX = 1568;
export const ATTACHMENT_MAX_BYTES = 2 * 1024 * 1024;
// Larger source files are refused before decoding, which is the costly step.
const SOURCE_MAX_BYTES = 40 * 1024 * 1024;
const JPEG_QUALITIES = [0.85, 0.7];
// The note that replaces an attached image once newer images supersede it.
export const SENT_IMAGE_PLACEHOLDER = "[An image attached here was replaced by a newer one and is no longer sent.]";

export interface ChatAttachment {
  id: string;
  name: string;
  mimeType: "image/jpeg";
  /** Base64 JPEG bytes, as the chat request carries them. */
  data: string;
  dataUrl: string;
}

export type AttachmentProblem = "type" | "size" | "unreadable";

export class AttachmentError extends Error {
  constructor(readonly problem: AttachmentProblem, readonly fileName: string) {
    super(problem);
  }
}

interface Decoded {
  source: CanvasImageSource;
  width: number;
  height: number;
  close: () => void;
}

async function decode(file: Blob): Promise<Decoded> {
  if (typeof createImageBitmap === "function") {
    try {
      const bitmap = await createImageBitmap(file);
      return { source: bitmap, width: bitmap.width, height: bitmap.height, close: () => bitmap.close() };
    } catch {
      // Some browsers decode a format through <img> but not createImageBitmap.
    }
  }
  const url = URL.createObjectURL(file);
  try {
    const image = new Image();
    image.src = url;
    await image.decode();
    return { source: image, width: image.naturalWidth, height: image.naturalHeight, close: () => undefined };
  } finally {
    URL.revokeObjectURL(url);
  }
}

function canvasBlob(canvas: HTMLCanvasElement, quality: number): Promise<Blob | null> {
  return new Promise((resolve) => canvas.toBlob(resolve, "image/jpeg", quality));
}

function dataUrlOf(blob: Blob): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result));
    reader.onerror = () => reject(reader.error);
    reader.readAsDataURL(blob);
  });
}

let nextId = 0;

/** Decode, shrink and re-encode one file, or throw an AttachmentError. */
export async function prepareAttachment(file: File): Promise<ChatAttachment> {
  if (!file.type.startsWith("image/")) throw new AttachmentError("type", file.name);
  if (file.size > SOURCE_MAX_BYTES) throw new AttachmentError("size", file.name);
  let decoded: Decoded;
  try {
    decoded = await decode(file);
  } catch {
    throw new AttachmentError("unreadable", file.name);
  }
  try {
    if (!decoded.width || !decoded.height) throw new AttachmentError("unreadable", file.name);
    const scale = Math.min(1, ATTACHMENT_EDGE_PX / Math.max(decoded.width, decoded.height));
    const canvas = document.createElement("canvas");
    canvas.width = Math.max(1, Math.round(decoded.width * scale));
    canvas.height = Math.max(1, Math.round(decoded.height * scale));
    const context = canvas.getContext("2d");
    if (!context) throw new AttachmentError("unreadable", file.name);
    context.fillStyle = "#ffffff";
    context.fillRect(0, 0, canvas.width, canvas.height);
    context.drawImage(decoded.source, 0, 0, canvas.width, canvas.height);
    for (const quality of JPEG_QUALITIES) {
      const blob = await canvasBlob(canvas, quality);
      if (!blob) throw new AttachmentError("unreadable", file.name);
      if (blob.size > ATTACHMENT_MAX_BYTES) continue;
      const dataUrl = await dataUrlOf(blob);
      const comma = dataUrl.indexOf(",");
      if (!dataUrl.startsWith("data:image/jpeg;base64,") || comma < 0) {
        throw new AttachmentError("unreadable", file.name);
      }
      nextId += 1;
      return { id: `attachment-${nextId}`, name: file.name, mimeType: "image/jpeg", data: dataUrl.slice(comma + 1), dataUrl };
    }
    throw new AttachmentError("size", file.name);
  } finally {
    decoded.close();
  }
}

// Plain text that only names the image: Finder adds the file name and some
// browsers the image URL when an image is copied.
const IMAGE_REFERENCE = /^(?:https?:\/\/\S+|[^\n\t]+\.(?:png|jpe?g|gif|webp|heic|heif|bmp|tiff?|avif))$/i;

/** The image files in a paste or drop. A clipboard that also carries real text
 *  is a text paste: spreadsheet cells, for example, copy a picture as well. */
export function imageFiles(transfer: DataTransfer | null): File[] {
  if (!transfer) return [];
  const images = Array.from(transfer.files ?? []).filter((file) => file.type.startsWith("image/"));
  if (!images.length || !transfer.types.includes("text/plain")) return images;
  const text = transfer.getData("text/plain").trim();
  return !text || IMAGE_REFERENCE.test(text) ? images : [];
}

/** Replace the image parts of the message that carried attachments, so model
 *  memory keeps the text but no longer resends the image. */
export function withoutSentImages(messages: unknown[]): unknown[] {
  if (!messages.length) return messages;
  const [first, ...rest] = messages;
  if (!first || typeof first !== "object" || !Array.isArray((first as { content?: unknown }).content)) return messages;
  const message = first as { content: unknown[] };
  const content = message.content.map((part) => {
    const type = part && typeof part === "object" ? (part as { type?: unknown }).type : undefined;
    return type === "image" || type === "image_url" ? { type: "text", text: SENT_IMAGE_PLACEHOLDER } : part;
  });
  return [{ ...message, content }, ...rest];
}
