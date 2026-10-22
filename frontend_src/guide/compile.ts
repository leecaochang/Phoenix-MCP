/**
 * Guide topics: one Markdown file per topic, opening with a small JSON header
 * between two `---` lines, compiled by the build into the JSON the panel
 * loads. Compiling checks every header, that ids are unique, that each
 * `guide:` link names a topic, and that each `{{label:<key>}}` placeholder
 * names a catalog key, so a broken topic fails the build instead of the panel.
 *
 * Topic ids say what a topic explains: `workspace.<workspace id>` for a
 * workspace overview and `window.<window id>` for one window.
 */

export const GUIDE_FORMAT = 1;

export interface GuideTopic {
  title: string;
  /** Markdown, with `guide:` links and `{{label:<key>}}` placeholders. */
  body: string;
}

export interface GuideBundle {
  format: number;
  language: string;
  topics: Record<string, GuideTopic>;
}

export const TOPIC_ID = /^(workspace|window|wizard|topic)\.[A-Za-z0-9][A-Za-z0-9_-]*$/;
/** A link to another topic: `[text](guide:<topic id>)`. */
export const GUIDE_LINK = /\]\(guide:([^)\s]*)\)/g;
/** A UI label taken from the panel's catalog: `{{label:<key>}}`. */
export const LABEL = /\{\{label:([^}]*)\}\}/g;
export const LABEL_KEY = /^[A-Za-z][A-Za-z0-9_]*(\.[A-Za-z0-9_]+)+$/;

/** One topic file: its id, title and body, or the reason it cannot be read. */
export function parseTopic(source: string): { id: string; title: string; body: string } | string {
  const text = source.replace(/\r\n/g, "\n");
  const match = /^---\n([\s\S]*?)\n---\n?([\s\S]*)$/.exec(text);
  if (!match) return "missing the JSON header between --- lines";
  let header: unknown;
  try { header = JSON.parse(match[1]); } catch { return "the header is not valid JSON"; }
  if (!header || typeof header !== "object" || Array.isArray(header)) return "the header is not a JSON object";
  const { id, title, ...rest } = header as Record<string, unknown>;
  if (typeof id !== "string" || !TOPIC_ID.test(id)) return `bad topic id ${JSON.stringify(id)}`;
  if (typeof title !== "string" || !title.trim()) return "missing a title";
  const unknown = Object.keys(rest);
  if (unknown.length) return `unknown header fields: ${unknown.join(", ")}`;
  const body = match[2].trim();
  if (!body) return "the topic has no text";
  return { id, title: title.trim(), body };
}

/** Compile one language's topic files; `problems` lists everything that would break in the panel. */
export function compileGuide(
  language: string,
  files: ReadonlyArray<{ file: string; source: string }>,
  labelExists: (key: string) => boolean,
): { bundle: GuideBundle; problems: string[] } {
  const topics: Record<string, GuideTopic> = {};
  const problems: string[] = [];
  const from = new Map<string, string>();
  for (const { file, source } of files) {
    const topic = parseTopic(source);
    if (typeof topic === "string") { problems.push(`${file}: ${topic}`); continue; }
    if (from.has(topic.id)) { problems.push(`${file}: topic ${topic.id} is also defined in ${from.get(topic.id)}`); continue; }
    from.set(topic.id, file);
    topics[topic.id] = { title: topic.title, body: topic.body };
  }
  for (const [id, topic] of Object.entries(topics)) {
    const file = from.get(id);
    for (const [, target] of topic.body.matchAll(GUIDE_LINK)) {
      if (!topics[target]) problems.push(`${file}: link to unknown topic ${JSON.stringify(target)}`);
    }
    for (const [, key] of topic.body.matchAll(LABEL)) {
      if (!LABEL_KEY.test(key) || !labelExists(key)) problems.push(`${file}: unknown label ${JSON.stringify(key)}`);
    }
  }
  return { bundle: { format: GUIDE_FORMAT, language, topics }, problems };
}
