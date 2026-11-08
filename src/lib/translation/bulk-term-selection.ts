import { fetchAllPages, type TermPageLoadOptions } from "./term-page-loader.ts";
import { toggleGlossarySelection } from "./term-selection.ts";

type TermPageLike = {
  items: Array<{ termId: string }>; page: number; pageSize: number; total: number;
  hasNextPage: boolean; termSetVersion?: string;
};

/** No selection mutation until every asynchronous check has succeeded. */
export async function executeSelectAll<P extends TermPageLike>(
  fetchPage: (page: number, signal: AbortSignal, version?: string) => Promise<P>,
  signal: AbortSignal,
  options: TermPageLoadOptions<P> & {
    isCurrent: () => boolean;
    getSelected: () => ReadonlySet<string>;
    commit: (ids: Set<string>) => void;
  },
) {
  const pages = await fetchAllPages(fetchPage, signal, options);
  if (signal.aborted || !options.isCurrent()) return { status: "stale" as const };
  const result = toggleGlossarySelection(options.getSelected(), pages.flatMap((page) => page.items.map((term) => term.termId)));
  if (result.status !== "limit-exceeded") options.commit(result.selectedIds);
  return { status: "complete" as const, pages, selectionStatus: result.status };
}
