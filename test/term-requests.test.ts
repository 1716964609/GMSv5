import assert from "node:assert/strict";
import test from "node:test";

import { isAbortError, TermRequestCoordinator } from "../src/lib/translation/term-requests.ts";

test("new request cancels and invalidates the older request for the same glossary", () => {
  const requests = new TermRequestCoordinator();
  const oldRequest = requests.begin("glossary-a");
  const newRequest = requests.begin("glossary-a");
  assert.equal(oldRequest.signal.aborted, true);
  assert.equal(requests.isCurrent(oldRequest), false);
  assert.equal(requests.isCurrent(newRequest), true);
});

test("collapse cancellation invalidates late response tokens", () => {
  const requests = new TermRequestCoordinator();
  const token = requests.begin("glossary-a");
  requests.cancel("glossary-a");
  assert.equal(token.signal.aborted, true);
  assert.equal(requests.isCurrent(token), false);
});

test("abort errors are identified separately from API failures", () => {
  assert.equal(isAbortError(new DOMException("cancelled", "AbortError")), true);
  assert.equal(isAbortError(new Error("network error")), false);
});
