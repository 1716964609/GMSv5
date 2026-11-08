import assert from "node:assert/strict";
import test from "node:test";
import { setImmediate as tick } from "node:timers/promises";
import { executeSelectAll } from "../src/lib/translation/bulk-term-selection.ts";
import { TermFetchScheduler } from "../src/lib/translation/term-fetch-scheduler.ts";
import { TermRequestCoordinator } from "../src/lib/translation/term-requests.ts";

function page(number: number, total = 201, prefix = "t") {
  return {
    page: number, pageSize: 100, total, hasNextPage: number * 100 < total, termSetVersion: "revision",
    items: Array.from({ length: Math.min(100, Math.max(0, total - (number - 1) * 100)) }, (_, i) => ({ termId: `${prefix}-${(number - 1) * 100 + i}` })),
  };
}
const validateVersion = async (version: string) => ({ termSetVersion: version });

test("stale completed operation cannot change selection even if no AbortError is delivered", async () => {
  let current = true;
  const selected = new Set(["existing"]);
  const result = await executeSelectAll(async (number) => page(number), new AbortController().signal, {
    scheduler: new TermFetchScheduler(),
    validateVersion: async (version) => { current = false; return { termSetVersion: version }; },
    isCurrent: () => current, getSelected: () => selected,
    commit: () => assert.fail("stale selection committed"),
  });
  assert.equal(result.status, "stale");
  assert.deepEqual([...selected], ["existing"]);
});

test("new request generation aborts old late response without any selection change", async () => {
  const requests = new TermRequestCoordinator();
  const token = requests.begin("g");
  let commits = 0;
  const selected = new Set(["previous"]);
  await assert.rejects(executeSelectAll(async (number) => {
    const result = page(number);
    if (number === 2) requests.begin("g");
    await tick(); // deliberately ignore AbortSignal in this transport
    return result;
  }, token.signal, {
    scheduler: new TermFetchScheduler(), validateVersion,
    isCurrent: () => requests.isCurrent(token), getSelected: () => selected,
    commit: () => { commits++; },
  }), { name: "AbortError" });
  assert.equal(commits, 0); assert.deepEqual([...selected], ["previous"]);
});

test("failure preserves selections made by another operation while fetching", async () => {
  let selected = new Set(["previous"]);
  await assert.rejects(executeSelectAll(async (number) => {
    if (number === 2) { selected = new Set([...selected, "other-operation"]); throw new Error("network failure"); }
    return page(number);
  }, new AbortController().signal, {
    scheduler: new TermFetchScheduler(), validateVersion,
    isCurrent: () => true, getSelected: () => selected,
    commit: () => assert.fail("partial operation committed"),
  }), /network failure/);
  assert.deepEqual([...selected], ["previous", "other-operation"]);
});

test("two Glossary operations combine against the latest selection; the second union above 10,000 is rejected", async () => {
  const scheduler = new TermFetchScheduler();
  let selected = new Set(["previous"]);
  let commits = 0;
  const run = (prefix: string) => executeSelectAll(async (number) => { await tick(); return page(number, 5_000, prefix); }, new AbortController().signal, {
    scheduler, concurrency: 4, validateVersion, isCurrent: () => true,
    getSelected: () => selected, commit: (next) => { selected = next; commits++; },
  });
  const results = await Promise.all([run("a"), run("b")]);
  assert.equal(commits, 1); assert.equal(selected.size, 5_001); assert.ok(selected.has("previous"));
  assert.equal(results.filter((r) => r.status === "complete" && r.selectionStatus === "limit-exceeded").length, 1);
});

test("exactly 10,000 including an overlapping ID succeeds; a 10,001-ID union leaves selection intact", async () => {
  let selected = new Set(["t-0"]);
  const run = () => executeSelectAll(async (number) => page(number, 10_000), new AbortController().signal, {
    scheduler: new TermFetchScheduler(), concurrency: 4, validateVersion, isCurrent: () => true,
    getSelected: () => selected, commit: (next) => { selected = next; },
  });
  const allowed = await run();
  assert.equal(allowed.status === "complete" && allowed.selectionStatus, "selected");
  assert.equal(selected.size, 10_000);
  selected = new Set(["unrelated"]);
  const original = selected;
  const refused = await run();
  assert.equal(refused.status === "complete" && refused.selectionStatus, "limit-exceeded");
  assert.equal(selected, original); assert.deepEqual([...selected], ["unrelated"]);
});

test("Clear All waits for validation and only clears the target Glossary IDs", async () => {
  let selected = new Set(["t-0", "t-1", "other"]);
  let checked = false;
  const result = await executeSelectAll(async (number) => page(number, 2), new AbortController().signal, {
    scheduler: new TermFetchScheduler(),
    validateVersion: async (version) => { assert.equal(selected.size, 3); checked = true; return { termSetVersion: version }; },
    isCurrent: () => true, getSelected: () => selected,
    commit: (next) => { assert.equal(checked, true); selected = next; },
  });
  assert.equal(result.status === "complete" && result.selectionStatus, "cleared");
  assert.deepEqual([...selected], ["other"]);
});
