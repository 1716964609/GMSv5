import assert from "node:assert/strict";
import test from "node:test";

import { fetchAllPages } from "../src/lib/translation/term-page-loader.ts";

test("fetchAllPages loads sequentially and reuses cached pages", async () => {
  const calls: number[] = [];
  const cached = { items: ["cached"], page: 1, hasNextPage: true };
  const pages = await fetchAllPages(async (page) => {
    calls.push(page);
    return { items: [`page-${page}`], page, hasNextPage: page < 3 };
  }, new AbortController().signal, new Map([[1, cached]]));
  assert.deepEqual(calls, [2, 3]);
  assert.deepEqual(pages.map((page) => page.items[0]), ["cached", "page-2", "page-3"]);
});

test("fetchAllPages rejects a failed page without returning a partial result", async () => {
  const calls: number[] = [];
  await assert.rejects(fetchAllPages(async (page) => {
    calls.push(page);
    if (page === 2) throw new Error("page failed");
    return { items: ["first"], page, hasNextPage: true };
  }, new AbortController().signal), /page failed/);
  assert.deepEqual(calls, [1, 2]);
});

test("fetchAllPages observes cancellation before requesting another page", async () => {
  const controller = new AbortController();
  const calls: number[] = [];
  await assert.rejects(fetchAllPages(async (page) => {
    calls.push(page);
    controller.abort();
    return { items: ["first"], page, hasNextPage: true };
  }, controller.signal), { name: "AbortError" });
  assert.deepEqual(calls, [1]);
});
