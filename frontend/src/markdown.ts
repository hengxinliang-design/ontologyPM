// A small Markdown subset for PM Assistant answers: headings, paragraphs, lists, tables, quotes, rules, code,
// **bold** and `code`. It returns plain data rendered as React text, so model output can never inject HTML;
// links and images are left as text on purpose.

export type Inline = { type: "text" | "strong" | "code"; text: string };

export type Block =
  | { type: "heading"; level: number; content: Inline[] }
  | { type: "paragraph"; lines: Inline[][] }
  | { type: "list"; ordered: boolean; start: number; items: Inline[][] }
  | { type: "table"; header: Inline[][]; rows: Inline[][][] }
  | { type: "quote"; lines: Inline[][] }
  | { type: "code"; text: string }
  | { type: "rule" };

const HEADING = /^(#{1,6})\s+(.*)$/;
const RULE = /^\s*([-*_])(\s*\1){2,}\s*$/;
const BULLET = /^\s*[-*+]\s+(.*)$/;
const NUMBERED = /^\s*(\d+)[.)]\s+(.*)$/;
const QUOTE = /^\s*>\s?(.*)$/;
const FENCE = /^\s*```/;
const TABLE_ROW = /^\s*\|.*\|\s*$/;
const TABLE_SEPARATOR = /^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$/;

const ESCAPED = /\\([\\`*_#|>\-+.!\[\]()])/g;

export function parseInline(text: string): Inline[] {
  const parts: Inline[] = [];
  for (const piece of text.split(/(\*\*[^*]+?\*\*|`[^`]+?`)/)) {
    if (!piece) continue;
    if (piece.length > 4 && piece.startsWith("**") && piece.endsWith("**")) parts.push({ type: "strong", text: piece.slice(2, -2).replace(ESCAPED, "$1") });
    else if (piece.length > 2 && piece.startsWith("`") && piece.endsWith("`")) parts.push({ type: "code", text: piece.slice(1, -1) });
    else parts.push({ type: "text", text: piece.replace(ESCAPED, "$1") });
  }
  return parts;
}

function cells(row: string): string[] {
  return row.trim().replace(/^\|/, "").replace(/\|$/, "").split("|").map(cell => cell.trim());
}

function startsBlock(line: string, next: string | undefined): boolean {
  return HEADING.test(line) || RULE.test(line) || BULLET.test(line) || NUMBERED.test(line) || QUOTE.test(line)
    || FENCE.test(line) || (TABLE_ROW.test(line) && next !== undefined && TABLE_SEPARATOR.test(next));
}

export function parseMarkdown(source: string): Block[] {
  const lines = source.replace(/\r\n?/g, "\n").split("\n");
  const blocks: Block[] = [];
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    if (!line.trim()) { i += 1; continue; }
    if (FENCE.test(line)) {
      const body: string[] = [];
      i += 1;
      while (i < lines.length && !FENCE.test(lines[i])) body.push(lines[i++]);
      i += 1;
      blocks.push({ type: "code", text: body.join("\n") });
      continue;
    }
    const heading = HEADING.exec(line);
    if (heading) {
      blocks.push({ type: "heading", level: heading[1].length, content: parseInline(heading[2].replace(/\s+#+\s*$/, "")) });
      i += 1;
      continue;
    }
    if (RULE.test(line)) { blocks.push({ type: "rule" }); i += 1; continue; }
    if (TABLE_ROW.test(line) && i + 1 < lines.length && TABLE_SEPARATOR.test(lines[i + 1])) {
      const header = cells(line);
      const rows: Inline[][][] = [];
      i += 2;
      while (i < lines.length && TABLE_ROW.test(lines[i])) {
        const row = cells(lines[i++]);
        rows.push(header.map((_, column) => parseInline(row[column] ?? "")));
      }
      blocks.push({ type: "table", header: header.map(parseInline), rows });
      continue;
    }
    const numbered = NUMBERED.exec(line);
    if (numbered || BULLET.test(line)) {
      const ordered = Boolean(numbered);
      const pattern = ordered ? NUMBERED : BULLET;
      const items: Inline[][] = [];
      while (i < lines.length && pattern.test(lines[i])) {
        const match = pattern.exec(lines[i++])!;
        items.push(parseInline(ordered ? match[2] : match[1]));
      }
      blocks.push({ type: "list", ordered, start: numbered ? Number(numbered[1]) : 1, items });
      continue;
    }
    if (QUOTE.test(line)) {
      const quoted: Inline[][] = [];
      while (i < lines.length && QUOTE.test(lines[i])) quoted.push(parseInline(QUOTE.exec(lines[i++])![1]));
      blocks.push({ type: "quote", lines: quoted });
      continue;
    }
    const paragraph: Inline[][] = [];
    while (i < lines.length && lines[i].trim() && (paragraph.length === 0 || !startsBlock(lines[i], lines[i + 1]))) {
      paragraph.push(parseInline(lines[i++].trim()));
    }
    blocks.push({ type: "paragraph", lines: paragraph });
  }
  return blocks;
}
