/**
 * An Agent Chat error or notice in the reader's language.
 *
 * The backend sends, and the conversation log stores, the English message
 * together with the code and params that produced it. The live chat and the
 * Conversations window both render the catalog entry for the code when this
 * bundle has one, and the English message otherwise.
 */

import { hasMessage, t, type Params } from "../i18n";

function paramsOf(value: unknown): Params | undefined {
  if (!value || typeof value !== "object" || Array.isArray(value)) return undefined;
  const out: Params = {};
  for (const [key, item] of Object.entries(value as Record<string, unknown>)) {
    if (typeof item === "string" || typeof item === "number") out[key] = item;
  }
  return out;
}

function noticeKey(code: unknown): string | null {
  const key = typeof code === "string" && code ? `agentchat.notice.${code}` : "";
  return key && hasMessage(key) ? key : null;
}

/**
 * The failure detail spliced into a retry line or a retry-exhausted error. The
 * server sends `detail_code` only when the detail is Phoenix's own sentence, so
 * that sentence is translated; a provider's own words stay verbatim.
 */
export function failureDetailText(detail: string, detailCode: unknown): string {
  const key = noticeKey(detailCode);
  return key ? t(key) : detail;
}

export function chatNoticeText(code: unknown, message: string, params?: unknown): string {
  const key = noticeKey(code);
  if (!key) return message;
  const values = paramsOf(params);
  if (values && typeof values.detail === "string") values.detail = failureDetailText(values.detail, values.detail_code);
  return t(key, values);
}
