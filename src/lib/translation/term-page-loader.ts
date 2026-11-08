import { MAX_TRANSLATION_TERMS } from "../validation.ts";
import { aborted, type TermFetchConcurrency, type TermFetchScheduler } from "./term-fetch-scheduler.ts";

type Page<T> = {
  items: T[];
  page: number;
  pageSize: number;
  total: number;
  hasNextPage: boolean;
  termSetVersion?: string;
};

export class TermSelectionLimitError extends Error {
  constructor() {
    super("この用語集をすべて選択すると、10,000件の上限を超えます。既存の選択は変更していません。");
    this.name = "TermSelectionLimitError";
  }
}

export type TermPageLoadOptions<P> = {
  scheduler: TermFetchScheduler;
  concurrency?: TermFetchConcurrency;
  cachedPages?: ReadonlyMap<number, P>;
  validateVersion: (version: string, signal: AbortSignal) => Promise<{ termSetVersion: string }>;
};

export async function fetchAllPages<T extends { termId: string }, P extends Page<T>>(
  fetchPage: (page: number, signal: AbortSignal, expectedVersion?: string) => Promise<P>,
  signal: AbortSignal,
  options: TermPageLoadOptions<P>,
): Promise<P[]> {
  const concurrency = options.concurrency ?? 2;
  if (![1, 2, 4].includes(concurrency)) throw new Error("Invalid Term fetch concurrency");
  const operation = new AbortController();
  const cancel = () => operation.abort();
  signal.addEventListener("abort", cancel, { once: true });
  if (signal.aborted) operation.abort();
  const request = <R>(task: () => Promise<R>) => options.scheduler.run(operation.signal, task);

  try {
    // Always establish T0 on the server, even with a complete cached collection.
    const first = await request(() => fetchPage(1, operation.signal));
    const version = first.termSetVersion;
    if (typeof version !== "string" || !version || !Number.isSafeInteger(first.total) || first.total < 0 ||
        !Number.isSafeInteger(first.pageSize) || first.pageSize < 1 || first.pageSize > 100) {
      throw new Error("用語ページのメタデータが不正です");
    }
    if (first.total > MAX_TRANSLATION_TERMS) throw new TermSelectionLimitError();
    const totalPages = Math.max(1, Math.ceil(first.total / first.pageSize));
    function valid(page: P, number: number): boolean {
      return page.page === number && page.termSetVersion === version && page.total === first.total &&
        page.pageSize === first.pageSize && page.hasNextPage === (number < totalPages) &&
        Array.isArray(page.items) && page.items.length === Math.min(first.pageSize, Math.max(0, first.total - (number - 1) * first.pageSize)) &&
        page.items.every((item) => item && typeof item.termId === "string" && item.termId.length > 0) &&
        new Set(page.items.map((item) => item.termId)).size === page.items.length;
    }
    if (!valid(first, 1)) throw new Error("用語ページの内容が不正です");
    const pages = new Map<number, P>([[1, first]]);
    let nextPage = 2;
    let failure: unknown;
    let failed = false;
    async function worker() {
      try {
        while (!operation.signal.aborted) {
          const number = nextPage++;
          if (number > totalPages) return;
          const cached = options.cachedPages?.get(number);
          const page = cached && valid(cached, number) ? cached : await request(() => fetchPage(number, operation.signal, version));
          if (operation.signal.aborted) throw aborted();
          if (!valid(page, number)) throw new Error("用語ページの番号・改訂または内容が一致しません");
          pages.set(number, page);
        }
      } catch (error) {
        if (!failed) { failed = true; failure = error; }
        operation.abort();
      }
    }
    await Promise.all(Array.from({ length: Math.min(concurrency, totalPages - 1) }, worker));
    if (failed) throw failure;
    if (operation.signal.aborted) throw aborted();
    const ordered = Array.from({ length: totalPages }, (_, index) => pages.get(index + 1)!);
    const ids = ordered.flatMap((page) => page.items.map((item) => item.termId));
    if (ids.length !== first.total || new Set(ids).size !== ids.length) throw new Error("用語ページに重複または欠落があります");
    const final = await request(() => options.validateVersion(version, operation.signal));
    if (final.termSetVersion !== version) throw new Error("用語集の改訂が一致しません");
    if (operation.signal.aborted) throw aborted();
    return ordered;
  } finally {
    operation.abort();
    signal.removeEventListener("abort", cancel);
  }
}
