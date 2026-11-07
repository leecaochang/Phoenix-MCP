import { describe, it, expect } from "vitest";
import { renderMarkdown, stripControlChars, flagsUnsafeContent } from "../utils/markdown";

const NUL = String.fromCharCode(0);

describe("output sanitization", () => {
  it("strips non-text control characters but keeps tab/newline", () => {
    expect(stripControlChars("a" + NUL + "bc")).toBe("abc");
    expect(stripControlChars("line1\nline2\tend")).toBe("line1\nline2\tend");
  });

  it("flags active-content markup and control chars", () => {
    expect(flagsUnsafeContent("<script>alert(1)</script>")).toBe(true);
    expect(flagsUnsafeContent("<img src=x onerror=go()>")).toBe(true);
    expect(flagsUnsafeContent("click javascript:evil()")).toBe(true);
    expect(flagsUnsafeContent("has a " + NUL + " null")).toBe(true);
    expect(flagsUnsafeContent("just normal text about lights")).toBe(false);
  });

  it("renderMarkdown strips control chars and escapes markup", () => {
    const html = renderMarkdown("safe " + NUL + " <script>bad()</script>");
    expect(html).not.toContain(NUL);
    expect(html).not.toContain("<script>");
    expect(html).toContain("&lt;script&gt;");
  });
});

describe("renderMarkdown", () => {
  it("renders common inline and block markdown", () => {
    expect(renderMarkdown("**bold** and *italic*")).toContain("<strong>bold</strong>");
    expect(renderMarkdown("**bold** and *italic*")).toContain("<em>italic</em>");
    expect(renderMarkdown("use `get_state`")).toContain("<code>get_state</code>");
    expect(renderMarkdown("# Heading")).toContain("<h1>Heading</h1>");
    const list = renderMarkdown("- one\n- two");
    expect(list).toContain("<ul>");
    expect(list).toContain("<li>one</li>");
    expect(list).toContain("<li>two</li>");
    const ol = renderMarkdown("1. first\n2. second");
    expect(ol).toContain("<ol>");
    expect(ol).toContain("<li>first</li>");
  });

  it("renders fenced code blocks with escaped contents", () => {
    const html = renderMarkdown("```\nlight.turn_on <x>\n```");
    expect(html).toContain("<pre><code>");
    expect(html).toContain("light.turn_on &lt;x&gt;");
  });

  it("allows only http(s)/relative links and renders others as text", () => {
    expect(renderMarkdown("[docs](https://example.com)"))
      .toContain('<a href="https://example.com" target="_blank" rel="noopener noreferrer">docs</a>');
    expect(renderMarkdown("[x](javascript:alert(1))")).not.toContain("<a ");
    expect(renderMarkdown("[x](javascript:alert(1))")).toContain("x");
    // A root-relative path is fine, but a scheme-relative "//host" resolves to
    // an external https:// host in the browser and must NOT be linkified.
    expect(renderMarkdown("[ok](/config/dashboard)")).toContain("<a ");
    expect(renderMarkdown("[evil](//evil.example)")).not.toContain("<a ");
    expect(renderMarkdown("[evil](//evil.example)")).toContain("evil");
  });

  it("escapes raw HTML so model output cannot inject markup", () => {
    const html = renderMarkdown("<img src=x onerror=alert(1)> and <script>bad()</script>");
    expect(html).not.toContain("<img");
    expect(html).not.toContain("<script>");
    expect(html).toContain("&lt;img");
    expect(html).toContain("&lt;script&gt;");
  });

  it("strips quotes from link hrefs to prevent attribute breakout", () => {
    const html = renderMarkdown('[x](/a"onmouseover="alert(1))');
    expect(html).not.toContain('onmouseover="alert');
  });

  it("renders a GFM table into a real table (not one run-together paragraph)", () => {
    const src = [
      "| Entity ID | State |",
      "|-----------|-------|",
      "| light.a | on |",
      "| light.b | off |",
    ].join("\n");
    const html = renderMarkdown(src);
    expect(html).toContain("<table>");
    expect(html).toContain("<th>Entity ID</th>");
    expect(html).toContain("<th>State</th>");
    expect(html).toContain("<td>light.a</td>");
    expect(html).toContain("<td>off</td>");
    // The rows must not be collapsed into a single <p> with pipes.
    expect(html).not.toContain("<p>| light.a");
    // Cell content still gets inline formatting and escaping.
    expect(renderMarkdown("| A |\n|---|\n| `code` |")).toContain("<code>code</code>");
  });

  it("labels a Guide table's cells for stacking and wraps it to scroll, leaving chat tables bare", () => {
    const src = "| Field | Type | Notes |\n|---|---|---|\n| `kind` | string | |";
    const html = renderMarkdown(src, { stackTables: true });
    expect(html.startsWith('<div class="phx-md-table"><table>')).toBe(true);
    // The first cell is the card's heading and an empty cell gets no label.
    expect(html).toContain('<td><code>kind</code></td><td><span class="phx-cell-label">Type</span><span>string</span></td><td></td>');
    expect(renderMarkdown(src)).not.toContain("phx-cell-label");
    expect(renderMarkdown(src)).not.toContain("phx-md-table");
  });

  it("still renders intended emphasis", () => {
    expect(renderMarkdown("this is **bold** here")).toContain("<strong>bold</strong>");
    expect(renderMarkdown("this is _italic_ here")).toContain("<em>italic</em>");
    expect(renderMarkdown("this is *italic* here")).toContain("<em>italic</em>");
    expect(renderMarkdown("(__strong__)")).toContain("<strong>strong</strong>");
  });

  it("does NOT emphasize literal underscores/asterisks in ordinary text", () => {
    // Entity ids and identifiers with intra-word underscores must stay literal.
    const a = renderMarkdown("turn on sensor.living_room_temp and light.turn_on");
    expect(a).not.toContain("<em>");
    expect(a).toContain("living_room_temp");
    expect(a).toContain("light.turn_on");
    // Arithmetic-style asterisks/underscores must not become emphasis.
    expect(renderMarkdown("a * b * c")).not.toContain("<em>");
    expect(renderMarkdown("2*3*4 = 24")).not.toContain("<em>");
    // A word char right after the closing marker means it is not emphasis.
    expect(renderMarkdown("m**a**n")).not.toContain("<strong>");
  });

  it("keeps a lone dashed line as an <hr>, not a table separator", () => {
    const html = renderMarkdown("above\n\n---\n\nbelow");
    expect(html).toContain("<hr />");
    expect(html).not.toContain("<table>");
  });
});

it("rejects slash-backslash network paths while retaining local and HTTPS links", () => {
  expect(renderMarkdown("[bad](/\\evil.example/login)")).not.toContain("href=");
  expect(renderMarkdown("[local](/config/entities)")).toContain('href="/config/entities"');
  expect(renderMarkdown("[remote](https://example.com/path)")).toContain('href="https://example.com/path"');
});

describe("inline markers around addresses and code", () => {
  it("leaves a link's address alone", () => {
    expect(renderMarkdown("[x](https://a/_b_/c)")).toContain('href="https://a/_b_/c"');
    expect(renderMarkdown("[x](https://a/*b*/c)")).toContain('href="https://a/*b*/c"');
    expect(renderMarkdown("[x](https://a/foo-_bar_)")).toContain('href="https://a/foo-_bar_"');
  });

  it("leaves a bare address alone, and still sees emphasis around it", () => {
    expect(renderMarkdown("https://a/_b_")).toBe("<p>https://a/_b_</p>");
    expect(renderMarkdown("**https://a.com** and _https://b.com/x_"))
      .toBe("<p><strong>https://a.com</strong> and <em>https://b.com/x</em></p>");
    expect(renderMarkdown("see https://a/b_c_d, then stop.")).toBe("<p>see https://a/b_c_d, then stop.</p>");
  });

  it("leaves code alone", () => {
    expect(renderMarkdown("`a _b_ *c*`")).toBe("<p><code>a _b_ *c*</code></p>");
  });

  it("still renders markers inside a link's text, and keeps them inside the link", () => {
    expect(renderMarkdown("[**b**](https://a)")).toContain("><strong>b</strong></a>");
    expect(renderMarkdown("[`c`](https://a)")).toContain("><code>c</code></a>");
    expect(renderMarkdown("**[a](https://b)**")).toMatch(/^<p><strong><a [^>]*>a<\/a><\/strong><\/p>$/);
  });

  it("keeps an IPv6 host in a link", () => {
    expect(renderMarkdown("[ha](http://[fe80::1]:8123/x)")).toContain('href="http://[fe80::1]:8123/x"');
  });

  it("keeps brackets in a link's query", () => {
    expect(renderMarkdown("[x](https://a/?filter[name]=1)")).toContain('href="https://a/?filter[name]=1"');
  });

  it("finds a link only where its text and address are whole", () => {
    const href = (source: string) => [...renderMarkdown(source).matchAll(/href="([^"]*)"/g)].map((match) => match[1]);
    expect(href("[a](https://b) and [c](https://d)")).toEqual(["https://b", "https://d"]);
    expect(href("[a][b](https://c)")).toEqual(["https://c"]);
    expect(href("[[a](https://b)")).toEqual(["https://b"]);
    expect(href("[a](https://b(c)d)")).toEqual(["https://b(c"]);
    expect(href("[a](javascript:1) [b](https://c)")).toEqual(["https://c"]);
    expect(href("[a](https://b c) [d](https://e)")).toEqual(["https://e"]);
    expect(href("[a](https://b)c](https://d)")).toEqual(["https://b"]);
    expect(renderMarkdown("[a](https://b)c](https://d)")).toContain(">a</a>c](https://d)");
    expect(renderMarkdown("[](https://b)")).not.toContain("<a ");
    expect(renderMarkdown("[a]()")).toBe("<p>[a]()</p>");
    expect(renderMarkdown("a](https://b)")).not.toContain("<a ");
    expect(renderMarkdown("[a]b](https://c)")).not.toContain("<a ");
  });

  it("cannot put held markup in an href, where its quotes would end the attribute", () => {
    const html = renderMarkdown('[x](http://a/`"onx=1`)');
    expect(html).not.toContain("<a ");
    const box = document.createElement("div");
    box.innerHTML = html;
    expect(box.querySelector("[onx]")).toBeNull();
  });

  it("gives no way to reach a held marker from the source", () => {
    const forged = `a ${String.fromCharCode(1)}0${String.fromCharCode(2)} \`b\``;
    expect(renderMarkdown(forged)).toBe("<p>a 0 <code>b</code></p>");
  });
});

describe("hostile input stays inert", () => {
  const ALLOWED = new Set(["P", "STRONG", "EM", "CODE", "PRE", "A", "TABLE", "THEAD", "TBODY", "TR", "TH", "TD",
    "H1", "H2", "H3", "H4", "H5", "H6", "UL", "OL", "LI", "BLOCKQUOTE", "HR", "SPAN", "DIV"]);
  const PAYLOADS = [
    "<img src=x onerror=alert(1)>", "[x](javascript:alert(1))", "[x](JaVaScRiPt:alert(1))",
    "[x](data:text/html,<script>alert(1)</script>)", '[x](http://a"onmouseover="alert(1))',
    "[x](http://a'onmouseover='alert(1))", "[x](//evil.example)", "[x](/\\evil.example)", "[x](\\\\evil.example)",
    '[x](http://a/`"onx=1`)', '[**"x**](http://a)', "| a | b |\n|---|---|\n| <script>1</script> | [q](javascript:1) |",
    "# <svg onload=1>", "> <iframe src=javascript:1>", "```\n</code></pre><script>1</script>\n```", '[a](http://x_y_"z_)',
    '[a](http://x/*"*)', "*[a*](http://x)", '_"onclick=x_', "[x](http://a&quot;onclick=alert(1))", '<a href="javascript:1">x</a>',
    "[x](https://a)<script>", "[<b>](https://a)", '[x](/foo"><script>alert(1)</script>)', '[x](/foo\n"onclick=1)',
  ];
  const FRAGMENTS = ["*", "_", "`", "[", "]", "(", ")", '"', "'", "<", ">", "javascript:", "http://a", "/x", " ", "\n", "|", "---",
    "**", "__", "&", "#", "onerror=", "<img src=x ", "\\"];

  /** A fixed sequence, so a failure repeats. */
  function generated(count: number): string[] {
    let seed = 20261105;
    const next = (limit: number) => { seed = (seed * 48271) % 2147483647; return seed % limit; };
    return Array.from({ length: count }, () => Array.from({ length: 3 + next(14) }, () => FRAGMENTS[next(FRAGMENTS.length)]).join(""));
  }

  it("yields only allow-listed tags, attributes and addresses", () => {
    const bad: string[] = [];
    for (const source of [...PAYLOADS, ...generated(400)]) {
      for (const options of [{}, { guideLinks: true, stackTables: true }]) {
        const box = new DOMParser().parseFromString(`<div>${renderMarkdown(source, options)}</div>`, "text/html");
        for (const el of box.body.querySelectorAll("div *")) {
          if (!ALLOWED.has(el.tagName)) bad.push(`${el.tagName}: ${source}`);
          for (const attr of el.attributes) {
            const onLink = el.tagName === "A" && ["href", "target", "rel", "data-guide-topic", "data-guide-anchor"].includes(attr.name);
            if (!onLink && attr.name !== "class") bad.push(`${attr.name} on ${el.tagName}: ${source}`);
            if (attr.name === "href" && !/^(https?:\/\/|\/(?![/\\])|#$)/i.test(attr.value)) bad.push(`href ${attr.value}: ${source}`);
          }
        }
      }
    }
    expect(bad).toEqual([]);
    // Hundreds of DOM parses, which a loaded runner can stretch past the default limit.
  }, 30_000);
});

describe("hostile input stays cheap", () => {
  const SIZE = 200_000;
  it.each([
    ["opening brackets", "[".repeat(SIZE)],
    ["link openings", "[x](".repeat(SIZE / 4)],
    ["link openings with an address", "[x](a".repeat(SIZE / 5)],
    ["IPv6 openings", "[x](http://[a:".repeat(SIZE / 14)],
    ["an unclosed IPv6 host", `[x](http://[${"a:".repeat(SIZE / 2)}`],
    ["markers", "*a ".repeat(SIZE / 3)],
    ["backticks", "`a".repeat(SIZE / 2)],
    ["a marker-heavy address tail", `http://${"_.".repeat(SIZE / 2)}`],
    ["spaces before a table separator", `a|b\n${" ".repeat(SIZE)}|`],
    ["spaces in a table header", `${" ".repeat(SIZE)}|x\n${" ".repeat(SIZE)}|-x`],
  ])("%s", (_name, source) => {
    const started = performance.now();
    renderMarkdown(source);
    expect(performance.now() - started).toBeLessThan(1000);
  });

  /** The fastest of a few runs: load only ever slows a run, so this is the closest to the cost. */
  const cost = (source: string) => Math.min(...Array.from({ length: 5 }, () => {
    const started = performance.now();
    renderMarkdown(source);
    return performance.now() - started;
  }));

  // Four times the input costs about four times the work when linear and sixteen when quadratic.
  it.each([
    ["hex link openings", "[a]("],
    ["digit link openings", "[1]("],
    ["hex openings with an address", "[a](b"],
    ["bare link middles", "]("],
    ["openings that end at a space", "[a](b "],
    ["an open bracket after each opening", "[a](b["],
    ["held code in each address", "[a](`c`"],
    ["a closed bracket in each address", "[a](b[c]"],
  ])("%s grow linearly", (_name, unit) => {
    const small = unit.repeat(20_000 / unit.length);
    const large = unit.repeat(80_000 / unit.length);
    expect(cost(large)).toBeLessThan(9 * Math.max(cost(small), 2));
  }, 120_000);

  describe("tables", () => {
    /** A header of `columns` cells over `rows` rows of one empty cell each. */
    const padded = (columns: number, rows: number) => `${"|".repeat(columns + 1)}\n|-\n${"|\n".repeat(rows)}`;
    const cells = (html: string) => (html.match(/<t[dh]>/g) ?? []).length;

    it("draws a short row only as far as it goes", () => {
      const html = renderMarkdown("| a | b | c |\n|---|---|---|\n| 1 |\n| 1 | 2 | 3 | 4 |");
      expect(html).toContain("<tr><td>1</td></tr>");
      expect(html).toContain("<tr><td>1</td><td>2</td><td>3</td></tr>");
    });

    it("keeps its cost to its source when a wide header sits over short rows", () => {
      const html = renderMarkdown(padded(64, 4_000));
      expect(html).toContain("<table>");
      expect(cells(html)).toBe(64 + 4_000);
      expect(html.length).toBeLessThan(20 * 8_000);
    });

    it.each([
      ["too many columns", padded(65, 1)],
      ["too many cells", `a|b\n-|-\n${"1|2\n".repeat(5_000)}`],
    ])("falls back to escaped text with %s", (_name, source) => {
      const html = renderMarkdown(`${source}\nafter <b>`);
      expect(html.startsWith("<pre><code>")).toBe(true);
      expect(html).not.toContain("<table>");
      expect(html).toContain("after &lt;b&gt;");
    });

    it("draws a table right at both limits", () => {
      expect(renderMarkdown(padded(64, 1))).toContain("<table>");
      expect(renderMarkdown(`a|b\n-|-\n${"1|2\n".repeat(4_999)}`)).toContain("<table>");
    });

    it("escapes what it falls back to", () => {
      const html = renderMarkdown(`<img src=x onerror=y>${"|".repeat(70)}\n|-\n|`);
      expect(html).toContain("&lt;img");
      expect(html).not.toContain("<img");
    });

    // A row that drew every column would make each of these quadratic.
    it.each([
      ["a header of pipes over rows of pipes", (n: number) => `${"|".repeat(n / 4)}\n|-\n${"|\n".repeat(n / 4)}`],
      ["a header at the column limit over empty rows", (n: number) => padded(64, n / 2)],
    ])("%s grow linearly and stay small", (_name, build) => {
      const small = build(20_000);
      const large = build(80_000);
      expect(cost(large)).toBeLessThan(9 * Math.max(cost(small), 2));
      expect(renderMarkdown(large).length).toBeLessThan(10 * large.length);
    }, 120_000);

    it("stays quick for a header and rows of pipes", () => {
      const started = performance.now();
      renderMarkdown(`${"|".repeat(4_000)}\n|-\n${"|\n".repeat(4_000)}`);
      expect(performance.now() - started).toBeLessThan(1000);
    });

    it("does not repeat a block's cost in any other block shape", () => {
      const unit = [
        "> a\n", "- a\n1. b\n", "# a\n", "---\n", "```\n", "a|\n",
      ];
      for (const piece of unit) {
        const source = piece.repeat(20_000 / piece.length);
        expect(renderMarkdown(source).length, piece).toBeLessThan(40 * source.length);
      }
    });
  });
});
