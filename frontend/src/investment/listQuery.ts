// Search and paging for the investment page's tables (在途商机, 沉没成本复盘, 赢单项目的获客成本, 未建档代码).
export const PAGE_SIZE = 10;

export function matches(text: string, query: string): boolean {
  const words = query.trim().toLowerCase().split(/\s+/).filter(Boolean);
  const haystack = text.toLowerCase();
  return words.every(word => haystack.includes(word));
}

export function pageOf<T>(items: T[], query: string, page: number, textOf: (item: T) => string, pageSize = PAGE_SIZE) {
  const found = query.trim() ? items.filter(item => matches(textOf(item), query)) : items;
  const pages = Math.max(1, Math.ceil(found.length / pageSize));
  const current = Math.min(Math.max(1, page), pages);
  return { visible: found.slice((current - 1) * pageSize, current * pageSize), total: found.length, pages, page: current };
}
