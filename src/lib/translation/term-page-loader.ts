import type { PagedItems } from "./term-selection.ts";

export async function fetchAllPages<T, P extends PagedItems<T>>(
  fetchPage: (page: number, signal: AbortSignal) => Promise<P>,
  signal: AbortSignal,
  cachedPages: ReadonlyMap<number, P> = new Map(),
): Promise<P[]> {
  const pages: P[] = [];
  let pageNumber = 1;

  while (true) {
    if (signal.aborted) throw new DOMException("The operation was aborted", "AbortError");
    const page = cachedPages.get(pageNumber) ?? await fetchPage(pageNumber, signal);
    if (signal.aborted) throw new DOMException("The operation was aborted", "AbortError");
    if (page.page !== pageNumber) {
      throw new Error("用語ページの番号が要求と一致しません");
    }
    pages.push(page);
    if (!page.hasNextPage) return pages;
    pageNumber += 1;
  }
}
