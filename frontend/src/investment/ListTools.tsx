import { ReactNode, useMemo, useState } from "react";
import { pageOf } from "./listQuery";

/** Search box, matching count and page buttons for one table; the page goes back to 1 when the search changes. */
export function useListQuery<T>(items: T[], textOf: (item: T) => string) {
  const [query, setQuery] = useState("");
  const [page, setPage] = useState(1);
  const result = useMemo(() => pageOf(items, query, page, textOf), [items, query, page, textOf]);
  return { ...result, query, setQuery: (value: string) => { setQuery(value); setPage(1); }, setPage };
}

export function ListToolbar({ query, setQuery, total, all, unit, placeholder, children }: {
  query: string; setQuery: (value: string) => void; total: number; all: number; unit: string; placeholder: string; children?: ReactNode;
}) {
  return <div className="list-tools">
    <input type="search" value={query} placeholder={placeholder} aria-label={placeholder} onChange={e => setQuery(e.target.value)} />
    {children}
    <span>{total === all ? `共 ${all} ${unit}` : `匹配 ${total} / ${all} ${unit}`}</span>
  </div>;
}

export function Pager({ page, pages, setPage }: { page: number; pages: number; setPage: (page: number) => void }) {
  if (pages <= 1) return null;
  return <div className="pagination">
    <button type="button" disabled={page <= 1} onClick={() => setPage(page - 1)}>上一页</button>
    <span>第 {page} / {pages} 页</span>
    <button type="button" disabled={page >= pages} onClick={() => setPage(page + 1)}>下一页</button>
  </div>;
}
