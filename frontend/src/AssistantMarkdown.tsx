import { Fragment } from "react";
import { parseMarkdown, type Inline } from "./markdown";

function Spans({ parts }: { parts: Inline[] }) {
  return <>{parts.map((part, index) => part.type === "strong" ? <strong key={index}>{part.text}</strong>
    : part.type === "code" ? <code key={index}>{part.text}</code> : <Fragment key={index}>{part.text}</Fragment>)}</>;
}

function Lines({ lines }: { lines: Inline[][] }) {
  return <>{lines.map((line, index) => <Fragment key={index}>{index > 0 && <br />}<Spans parts={line} /></Fragment>)}</>;
}

/** Renders model output written in Markdown as React elements (never as HTML). */
export function Markdown({ text, className = "" }: { text: string; className?: string }) {
  return <div className={`markdown ${className}`.trim()}>{parseMarkdown(text).map((block, index) => {
    switch (block.type) {
      case "heading": return <p key={index} className={`markdown-heading level-${Math.min(block.level, 4)}`}><Spans parts={block.content} /></p>;
      case "paragraph": return <p key={index}><Lines lines={block.lines} /></p>;
      case "quote": return <blockquote key={index}><Lines lines={block.lines} /></blockquote>;
      case "code": return <pre key={index}><code>{block.text}</code></pre>;
      case "rule": return <hr key={index} />;
      case "list": {
        const items = block.items.map((item, itemIndex) => <li key={itemIndex}><Spans parts={item} /></li>);
        return block.ordered ? <ol key={index} start={block.start}>{items}</ol> : <ul key={index}>{items}</ul>;
      }
      case "table": return <div key={index} className="markdown-table"><table>
        <thead><tr>{block.header.map((cell, column) => <th key={column}><Spans parts={cell} /></th>)}</tr></thead>
        <tbody>{block.rows.map((row, rowIndex) => <tr key={rowIndex}>{row.map((cell, column) => <td key={column}><Spans parts={cell} /></td>)}</tr>)}</tbody>
      </table></div>;
    }
  })}</div>;
}
