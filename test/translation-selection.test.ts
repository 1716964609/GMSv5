import assert from "node:assert/strict";
import test from "node:test";

import {
  collectCompletePages,
  toggleGlossarySelection,
  toggleTermSelection,
} from "../src/lib/translation/term-selection.ts";

test("individual selection toggles a Term ID without mutating the prior set", () => {
  const original = new Set(["existing"]);
  const selected = toggleTermSelection(original, "new");
  assert.deepEqual([...selected.selectedIds], ["existing", "new"]);
  assert.deepEqual([...original], ["existing"]);
  assert.equal(toggleTermSelection(selected.selectedIds, "new").status, "cleared");
});

test("Select All counts the union of IDs and deduplicates overlap", () => {
  const result = toggleGlossarySelection(new Set(["shared", "other"]), ["shared", "shared", "added"], 3);
  assert.equal(result.status, "selected");
  assert.deepEqual(new Set(result.selectedIds), new Set(["shared", "other", "added"]));
});

test("Select All over the limit leaves existing selection unchanged", () => {
  const current = new Set(["a", "b"]);
  const result = toggleGlossarySelection(current, ["c", "d"], 3);
  assert.equal(result.status, "limit-exceeded");
  assert.deepEqual(result.selectedIds, current);
  assert.deepEqual(current, new Set(["a", "b"]));
});

test("complete-page collection rejects gaps and returns a complete sequence", () => {
  const page = (number: number, hasNextPage: boolean) => ({ items: [number], page: number, hasNextPage });
  assert.equal(collectCompletePages(new Map([[1, page(1, true)], [3, page(3, false)]])), null);
  assert.deepEqual(collectCompletePages(new Map([[1, page(1, true)], [2, page(2, false)]])), [1, 2]);
});
