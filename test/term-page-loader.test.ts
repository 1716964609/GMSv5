import assert from "node:assert/strict";
import test from "node:test";
import { setImmediate as tick } from "node:timers/promises";

import { fetchAllPages, TermSelectionLimitError } from "../src/lib/translation/term-page-loader.ts";
import { aborted, TermFetchScheduler, termFetchConcurrency, type TermFetchConcurrency } from "../src/lib/translation/term-fetch-scheduler.ts";

type TestPage = { items: { termId: string }[]; page: number; total: number; pageSize: number; hasNextPage: boolean; termSetVersion?: string };

function page(number: number, total = 600, version = "v1", prefix = "term"): TestPage {
  return {
    page: number, total, pageSize: 100, termSetVersion: version, hasNextPage: number * 100 < total,
    items: Array.from({ length: Math.min(100, Math.max(0, total - (number - 1) * 100)) }, (_, i) => ({ termId: `${prefix}-${(number - 1) * 100 + i}` })),
  };
}
function options() {
  return { scheduler: new TermFetchScheduler(), validateVersion: async (version: string) => ({ termSetVersion: version }) };
}
function deferred() {
  let resolve!: () => void;
  const promise = new Promise<void>((done) => { resolve = done; });
  return { promise, resolve };
}

for (const concurrency of [1, 2, 4] as TermFetchConcurrency[]) {
  test(`parallel ${concurrency} retrieves identical ordered IDs within its worker limit`, async () => {
    let active = 0;
    let peak = 0;
    const completed: number[] = [];
    const third = deferred();
    const result = await fetchAllPages(async (number, signal, version) => {
      active += 1; peak = Math.max(peak, active);
      try {
        assert.equal(version, number === 1 ? undefined : "v1");
        if (number === 2 && concurrency > 1) await third.promise;
        await tick();
        if (signal.aborted) throw aborted();
        completed.push(number);
        if (number === 3) third.resolve();
        return page(number);
      } finally { active -= 1; }
    }, new AbortController().signal, { ...options(), concurrency });
    assert.equal(peak, concurrency);
    assert.deepEqual(result.map((p) => p.page), [1, 2, 3, 4, 5, 6]);
    assert.deepEqual(result.flatMap((p) => p.items).map((term) => term.termId), Array.from({ length: 600 }, (_, i) => `term-${i}`));
    if (concurrency > 1) assert.ok(completed.indexOf(3) < completed.indexOf(2));
  });
}

test("only same-version validated cache is reused; first and final always contact server", async () => {
  const cached = new Map([[1, page(1, 400)], [2, page(2, 400)], [3, page(3, 400, "old")], [4, { ...page(4, 400), termSetVersion: undefined }]]);
  const calls: number[] = [];
  let finalCalls = 0;
  await fetchAllPages(async (number) => { calls.push(number); return page(number, 400); }, new AbortController().signal, {
    ...options(), cachedPages: cached,
    validateVersion: async (version) => { finalCalls++; return { termSetVersion: version }; },
  });
  assert.deepEqual(calls.sort(), [1, 3, 4]);
  assert.equal(finalCalls, 1);
});

test("complete cache still requires first-page and final-version validation", async () => {
  const calls: number[] = [];
  let validations = 0;
  await fetchAllPages(async (number) => { calls.push(number); return page(number, 200); }, new AbortController().signal, {
    ...options(), cachedPages: new Map([[1, page(1, 200)], [2, page(2, 200)]]),
    validateVersion: async () => { validations++; throw new Error("changed after last page"); },
  }).then(() => assert.fail("must fail"), (error: Error) => assert.match(error.message, /changed/));
  assert.deepEqual(calls, [1]);
  assert.equal(validations, 1);
});

test("a failed page aborts sibling workers and never returns a partial collection", async () => {
  let siblingAborted = false;
  await assert.rejects(fetchAllPages(async (number, signal) => {
    if (number === 2) throw new Error("page failed");
    if (number > 1) { await tick(); siblingAborted ||= signal.aborted; }
    return page(number);
  }, new AbortController().signal, { ...options(), concurrency: 4 }), /page failed/);
  assert.equal(siblingAborted, true);
});

test("abort during final validation cannot return a successful collection", async () => {
  const controller = new AbortController();
  await assert.rejects(fetchAllPages(async (number) => page(number, 100), controller.signal, {
    ...options(), validateVersion: async (version) => {
      controller.abort();
      return { termSetVersion: version };
    },
  }), { name: "AbortError" });
});

test("queued cancellation never starts a request and active cancellation holds the permit until settled", async () => {
  const scheduler = new TermFetchScheduler(1);
  const gate = deferred();
  const activeController = new AbortController();
  const waitingController = new AbortController();
  const first = scheduler.run(activeController.signal, async () => { await gate.promise; return 1; });
  const waiting = scheduler.run(waitingController.signal, async () => { assert.fail("canceled queued task started"); });
  const firstRejected = assert.rejects(first, { name: "AbortError" });
  const waitingRejected = assert.rejects(waiting, { name: "AbortError" });
  let thirdStarted = false;
  const third = scheduler.run(new AbortController().signal, async () => { thirdStarted = true; return 3; });
  waitingController.abort(); activeController.abort();
  await waitingRejected;
  await tick();
  assert.equal(thirdStarted, false);
  gate.resolve();
  await firstRejected;
  assert.equal(await third, 3);
});

test("multiple Glossaries and a normal page share at most four active requests", async () => {
  const scheduler = new TermFetchScheduler();
  let active = 0; let peak = 0;
  async function work<T>(value: T): Promise<T> {
    active++; peak = Math.max(peak, active);
    try { await tick(); return value; } finally { active--; }
  }
  const bulk = (prefix: string) => fetchAllPages(async (number) => work(page(number, 700, "v1", prefix)), new AbortController().signal, {
    scheduler, concurrency: 4, validateVersion: async (version) => work({ termSetVersion: version }),
  });
  const results = await Promise.all([bulk("a"), bulk("b"), scheduler.run(new AbortController().signal, () => work("normal page"))]);
  assert.equal(peak, 4);
  assert.equal(results[0].flatMap((p) => p.items).length, 700);
  assert.equal(results[1].flatMap((p) => p.items).length, 700);
  assert.equal(results[2], "normal page");
});

test("rejects bad page number, version, total, terminator and duplicate IDs", async () => {
  for (const corrupt of [
    { page: 3 }, { termSetVersion: "changed" }, { total: 299 }, { hasNextPage: true },
    { items: page(1, 200).items }, { items: [] },
  ]) {
    await assert.rejects(fetchAllPages(async (number) => number === 1 ? page(1, 200) : { ...page(2, 200), ...corrupt }, new AbortController().signal, options()));
  }
});

test("permits exactly 10,000 IDs and rejects 10,001 without extra page fetches", async () => {
  const result = await fetchAllPages(async (number) => page(number, 10_000), new AbortController().signal, options());
  assert.equal(result.flatMap((p) => p.items).length, 10_000);
  let calls = 0;
  await assert.rejects(fetchAllPages(async (number) => { calls++; return page(number, 10_001); }, new AbortController().signal, options()), TermSelectionLimitError);
  assert.equal(calls, 1);
});

test("pre-abort starts no work; scheduler recovers after a parsing failure", async () => {
  const controller = new AbortController(); controller.abort();
  await assert.rejects(fetchAllPages(async () => { assert.fail("must not fetch"); }, controller.signal, options()), { name: "AbortError" });
  const scheduler = new TermFetchScheduler(1);
  await assert.rejects(scheduler.run(new AbortController().signal, async () => { throw new SyntaxError("invalid JSON"); }));
  assert.equal(await scheduler.run(new AbortController().signal, async () => 1), 1);
  assert.deepEqual(["1", "2", "4", "99", undefined].map(termFetchConcurrency), [1, 2, 4, 2, 2]);
});
