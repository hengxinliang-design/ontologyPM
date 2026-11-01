import { describe, expect, it } from "vitest";
import { parseInline, parseMarkdown } from "./markdown";

// A PM Assistant answer as granite-4.0-h-micro writes it (shape taken from a live answer, 2026-10-31).
const ANSWER = `**项目 TPL149 的预算消耗情况**

| 项目项 | 数据 |
|--------|------|
| **已报告的人天** | 0.00 天 |
| 预算利用率 | 未列出 |

**解读**
- 该项目目前 **未设置预算**。
- 已填报 \`0\` 天

1. **确认预算**：联系项目负责人。
2. **工时填报**：尽快填报。
---
> 资料不足的部分说明不知道`;

describe("assistant markdown", () => {
  it("parses the blocks of a model answer", () => {
    const blocks = parseMarkdown(ANSWER);
    expect(blocks.map(block => block.type)).toEqual(["paragraph", "table", "paragraph", "list", "list", "rule", "quote"]);
    const table = blocks[1];
    if (table.type !== "table") throw new Error("table expected");
    expect(table.header.map(cell => cell[0].text)).toEqual(["项目项", "数据"]);
    expect(table.rows[0][0]).toEqual([{ type: "strong", text: "已报告的人天" }]);
    const numbered = blocks[4];
    expect(numbered.type === "list" && numbered.ordered && numbered.start).toBe(1);
  });

  it("keeps HTML as text and pads short table rows", () => {
    expect(parseInline("<img src=x onerror=alert(1)> **粗体**")).toEqual([
      { type: "text", text: "<img src=x onerror=alert(1)> " }, { type: "strong", text: "粗体" }]);
    expect(parseInline("\\* 回款率 = a \\| b")).toEqual([{ type: "text", text: "* 回款率 = a | b" }]);
    const [table] = parseMarkdown("| a | b |\n|---|---|\n| 1 |");
    expect(table.type === "table" && table.rows[0].map(cell => cell.length)).toEqual([1, 0]);
  });

  it("joins consecutive lines into one paragraph and keeps fenced code verbatim", () => {
    const blocks = parseMarkdown("第一行\n第二行\n\n```\n| 不是表格 |\n**不加粗**\n```");
    expect(blocks[0]).toEqual({ type: "paragraph", lines: [[{ type: "text", text: "第一行" }], [{ type: "text", text: "第二行" }]] });
    expect(blocks[1]).toEqual({ type: "code", text: "| 不是表格 |\n**不加粗**" });
  });
});
