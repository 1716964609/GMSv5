export type SelectAllResult = {
  status: "selected" | "cleared" | "limit-exceeded";
  selectedIds: Set<string>;
};

export function toggleTermSelection(
  selectedIds: ReadonlySet<string>,
  termId: string,
  maxTerms = 10_000,
): SelectAllResult {
  const next = new Set(selectedIds);
  if (next.has(termId)) {
    next.delete(termId);
    return { status: "cleared", selectedIds: next };
  }
  if (next.size >= maxTerms) {
    return { status: "limit-exceeded", selectedIds: new Set(selectedIds) };
  }
  next.add(termId);
  return { status: "selected", selectedIds: next };
}

export function toggleGlossarySelection(
  selectedIds: ReadonlySet<string>,
  glossaryTermIds: Iterable<string>,
  maxTerms = 10_000,
): SelectAllResult {
  const glossaryIds = new Set(glossaryTermIds);
  const allSelected = glossaryIds.size > 0 && [...glossaryIds].every((id) => selectedIds.has(id));

  if (allSelected) {
    const next = new Set(selectedIds);
    for (const termId of glossaryIds) next.delete(termId);
    return { status: "cleared", selectedIds: next };
  }

  const union = new Set([...selectedIds, ...glossaryIds]);
  if (union.size > maxTerms) {
    return { status: "limit-exceeded", selectedIds: new Set(selectedIds) };
  }
  return { status: "selected", selectedIds: union };
}

export type PagedItems<T> = {
  items: T[];
  page: number;
  hasNextPage: boolean;
};

export function collectCompletePages<T>(
  pages: ReadonlyMap<number, PagedItems<T>>,
): T[] | null {
  const items: T[] = [];
  let pageNumber = 1;

  while (true) {
    const page = pages.get(pageNumber);
    if (!page || page.page !== pageNumber) return null;
    items.push(...page.items);
    if (!page.hasNextPage) return items;
    pageNumber += 1;
  }
}

export function allPagesLoaded<T>(pages: ReadonlyMap<number, PagedItems<T>>): boolean {
  return collectCompletePages(pages) !== null;
}

export function toggleGlossaryExpansion(
  expanded: ReadonlySet<string>,
  glossaryId: string,
): Set<string> {
  const next = new Set(expanded);
  if (next.has(glossaryId)) next.delete(glossaryId);
  else next.add(glossaryId);
  return next;
}
