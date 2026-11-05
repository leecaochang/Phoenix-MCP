// Minimal, dependency-free Markdown to HTML renderer for agentCLI assistant
// output. Safety model: every piece of source text is HTML-escaped BEFORE any
// transform runs, and the transforms only ever insert a fixed whitelist of tags
// (never raw model text into an attribute or tag position), so model output
// cannot inject markup. Non-text control characters are stripped, and link hrefs
// are restricted to http(s) or root-relative with double quotes percent-encoded.
// It is intentionally small: it covers the common Markdown an assistant emits
// (headings, emphasis, code, lists, links, blockquotes, tables) and renders
// anything it does not recognise as plain text. The Guide also turns
// `guide:<topic id>`, `guide:<topic id>#<section>` and `guide:#<section>` links
// into in-panel links (`guideLinks`), and gives its tables what they need to
// scroll sideways or stack into one card per row (`stackTables`).

export interface MarkdownOptions {
  /** Render `[text](guide:<topic id>#<section>)` as a link the Guide follows itself. */
  guideLinks?: boolean;
  /**
   * Wrap each table in a box that scrolls sideways, and start each filled cell
   * after the first with its column's name, shown only when the rows stack.
   */
  stackTables?: boolean;
}
/**
 * A `guide:` link: a topic id (letters, digits, dot, dash and underscore), a
 * section anchor (lower-case words joined by dashes), or both.
 */
const GUIDE_TOPIC_RE = /^guide:([A-Za-z0-9][A-Za-z0-9._-]*)?(?:#([a-z0-9]+(?:-[a-z0-9]+)*))?$/;

// Non-text control characters (keep tab \x09, newline \x0A, carriage return
// \x0D). Built via RegExp() with escaped strings so no literal control bytes
// live in this source file.
const CONTROL_CLASS = "\\x00-\\x08\\x0B\\x0C\\x0E-\\x1F\\x7F";
// eslint-disable-next-line no-control-regex
const CONTROL_RE = new RegExp("[" + CONTROL_CLASS + "]", "g");

export function stripControlChars(s: string): string {
  // Model output should be text; NUL and other C0 controls have no legitimate
  // place and can corrupt rendering, so they are removed before display.
  return s.replace(CONTROL_RE, "");
}

// True if the RAW model output contains active-content markup (script/iframe/
// event handlers/javascript: URLs) or control characters. renderMarkdown always
// neutralizes such content by escaping it to plain text; this flag lets the UI
// tell the user that neutralization happened, so a reply full of literal tags is
// not mistaken for a bug.
// eslint-disable-next-line no-control-regex
const UNSAFE_RE = new RegExp(
  "<\\s*(?:script|iframe|object|embed|link|style|meta|svg|math|base)\\b" +
    "|\\son[a-z]+\\s*=|javascript:|data:text/html|[" + CONTROL_CLASS + "]",
  "i",
);

export function flagsUnsafeContent(raw: string): boolean {
  return UNSAFE_RE.test(raw);
}

function esc(s: string): string {
  return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

// Inline transforms. Input MUST already be HTML-escaped.
//
// Emphasis is boundary-aware so literal underscores/asterisks in ordinary output
// (entity ids like sensor.living_room_temp, or "a * b") are NOT mistaken for
// markdown. A marker only opens/closes emphasis at a word boundary: the run must
// be preceded by start-of-string or a non-word, non-marker character, its content
// must not begin or end with whitespace, and it must be followed by a non-word
// character or end-of-string. This mirrors the GFM rule that emphasis markers
// flanked by alphanumerics on the inside edge do not delimit, so intra-word
// underscores (the common case) stay literal.
function emphasize(s: string): string {
  s = s.replace(/(^|[^\w*])\*\*(\S(?:[^*]*?\S)?)\*\*(?!\w)/g, "$1<strong>$2</strong>");
  s = s.replace(/(^|[^\w_])__(\S(?:[^_]*?\S)?)__(?!\w)/g, "$1<strong>$2</strong>");
  s = s.replace(/(^|[^\w*])\*(\S(?:[^*]*?\S)?)\*(?!\w)/g, "$1<em>$2</em>");
  return s.replace(/(^|[^\w_])_(\S(?:[^_]*?\S)?)_(?!\w)/g, "$1<em>$2</em>");
}

// A piece of finished markup held out of the text while emphasis runs. Control
// characters are stripped from the source before any line gets here, so the
// text cannot contain a held marker of its own.
// eslint-disable-next-line no-control-regex
const HELD_RE = /\x01(\d+)\x02/g;

// A link's text and address both stop at "[", which bounds the work from each
// opening bracket by the next one: a line of brackets stays linear. The only
// "[" an address may hold is the one around an IPv6 host. An address cannot hold
// a held marker either: that markup would land inside the href attribute.
// eslint-disable-next-line no-control-regex
const LINK_RE = /\[([^[\]]+)\]\(((?:[^)\s[\x01]|\[[0-9A-Fa-f:.]*\])+)\)/g;

// A bare address, to keep its markers literal. It is left as text, not made a link.
const BARE_URL_RE = /https?:\/\/\S+/g;
const URL_TAIL = "*_.,;:!?)]";

function inline(s: string, opts: MarkdownOptions = {}): string {
  const held: string[] = [];
  const hold = (html: string): string => `\x01${held.push(html) - 1}\x02`;
  s = s.replace(/`([^`]+)`/g, (_m, c: string) => hold(`<code>${c}</code>`));
  s = s.replace(LINK_RE, (_m, text: string, url: string) => {
    // Absolute http(s), or a root-relative path that is NOT scheme-relative:
    // "//evil.example" starts with "/" but browsers resolve it as an external
    // https:// host, so a bare "/" allow-rule would let it through. Require a
    // single leading slash not followed by a slash or backslash.
    if (/^(https?:\/\/|\/(?![\/\\]))/i.test(url)) {
      const safe = url.replace(/"/g, "%22");
      return hold(`<a href="${safe}" target="_blank" rel="noopener noreferrer">${emphasize(text)}</a>`);
    }
    const topic = opts.guideLinks ? GUIDE_TOPIC_RE.exec(url) : null;
    if (topic && (topic[1] || topic[2])) {
      const to = topic[1] ? ` data-guide-topic="${topic[1]}"` : "";
      const at = topic[2] ? ` data-guide-anchor="${topic[2]}"` : "";
      return hold(`<a href="#" class="phx-guide-link"${to}${at}>${emphasize(text)}</a>`);
    }
    return text;
  });
  s = s.replace(BARE_URL_RE, (url) => {
    // Closing emphasis or punctuation after an address is not part of it.
    let end = url.length;
    while (end > 0 && URL_TAIL.includes(url[end - 1])) end -= 1;
    return hold(url.slice(0, end)) + url.slice(end);
  });
  const restore = (text: string): string => text.replace(HELD_RE, (_m, i: string) => restore(held[Number(i)]));
  return restore(emphasize(s));
}

// GFM table detection. A separator row is dashes (optionally colon-aligned)
// between pipes; it must contain a pipe so a lone "---" stays an <hr>. The line
// is trimmed first: runs of spaces at its ends would make this pattern quadratic.
const TABLE_SEP_RE = /^\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)*\|?$/;
function isTableSep(line: string): boolean {
  return line.includes("|") && TABLE_SEP_RE.test(line.trim());
}
function splitTableRow(line: string): string[] {
  let s = line.trim();
  if (s.startsWith("|")) s = s.slice(1);
  if (s.endsWith("|")) s = s.slice(0, -1);
  return s.split("|").map((c) => c.trim());
}
function renderTable(header: string[], rows: string[][], opts: MarkdownOptions): string {
  const names = header.map((h) => inline(esc(h), opts));
  const th = names.map((h) => `<th>${h}</th>`).join("");
  const body = rows.map((r) => {
    const cells: string[] = [];
    for (let i = 0; i < header.length; i++) {
      const cell = inline(esc(r[i] ?? ""), opts);
      const labeled = opts.stackTables && i > 0 && cell;
      cells.push(labeled ? `<td><span class="phx-cell-label">${names[i]}</span><span>${cell}</span></td>` : `<td>${cell}</td>`);
    }
    return `<tr>${cells.join("")}</tr>`;
  }).join("");
  const table = `<table><thead><tr>${th}</tr></thead><tbody>${body}</tbody></table>`;
  return opts.stackTables ? `<div class="phx-md-table">${table}</div>` : table;
}

export function renderMarkdown(src: string, opts: MarkdownOptions = {}): string {
  if (!src) return "";
  const lines = stripControlChars(src).replace(/\r\n/g, "\n").split("\n");
  const out: string[] = [];
  let inCode = false;
  let codeBuf: string[] = [];
  let listType: "ul" | "ol" | null = null;
  let para: string[] = [];

  const closeList = () => { if (listType) { out.push(`</${listType}>`); listType = null; } };
  const flushPara = () => {
    if (para.length) { out.push(`<p>${inline(esc(para.join(" ")), opts)}</p>`); para = []; }
  };

  for (let li = 0; li < lines.length; li++) {
    const line = lines[li];
    const fence = line.match(/^```/);
    if (fence) {
      if (!inCode) { flushPara(); closeList(); inCode = true; codeBuf = []; }
      else { out.push(`<pre><code>${esc(codeBuf.join("\n"))}</code></pre>`); inCode = false; }
      continue;
    }
    if (inCode) { codeBuf.push(line); continue; }

    // Table: a header row with pipes immediately followed by a separator row.
    if (line.includes("|") && li + 1 < lines.length && isTableSep(lines[li + 1])) {
      flushPara(); closeList();
      const header = splitTableRow(line);
      const rows: string[][] = [];
      let j = li + 2;
      for (; j < lines.length; j++) {
        const r = lines[j];
        if (/^\s*$/.test(r) || !r.includes("|")) break;
        rows.push(splitTableRow(r));
      }
      out.push(renderTable(header, rows, opts));
      li = j - 1;
      continue;
    }

    if (/^\s*$/.test(line)) { flushPara(); closeList(); continue; }

    const h = line.match(/^(#{1,6})\s+(.*)$/);
    if (h) { flushPara(); closeList(); const lvl = h[1].length; out.push(`<h${lvl}>${inline(esc(h[2]), opts)}</h${lvl}>`); continue; }

    if (/^(-{3,}|\*{3,}|_{3,})\s*$/.test(line)) { flushPara(); closeList(); out.push("<hr />"); continue; }

    const bq = line.match(/^>\s?(.*)$/);
    if (bq) { flushPara(); closeList(); out.push(`<blockquote>${inline(esc(bq[1]), opts)}</blockquote>`); continue; }

    const ul = line.match(/^\s*[-*+]\s+(.*)$/);
    if (ul) { flushPara(); if (listType !== "ul") { closeList(); out.push("<ul>"); listType = "ul"; } out.push(`<li>${inline(esc(ul[1]), opts)}</li>`); continue; }

    const ol = line.match(/^\s*\d+\.\s+(.*)$/);
    if (ol) { flushPara(); if (listType !== "ol") { closeList(); out.push("<ol>"); listType = "ol"; } out.push(`<li>${inline(esc(ol[1]), opts)}</li>`); continue; }

    closeList();
    para.push(line);
  }

  if (inCode) out.push(`<pre><code>${esc(codeBuf.join("\n"))}</code></pre>`);
  flushPara();
  closeList();
  return out.join("\n");
}
