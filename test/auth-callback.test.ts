import assert from "node:assert/strict";
import test from "node:test";

import { authFailureLoginUrl, validateOAuthCallback } from "../src/lib/auth/callback.ts";

const verifier = "a".repeat(43);

test("OAuth callback accepts matching state and a valid PKCE verifier", () => {
  assert.deepEqual(validateOAuthCallback({ code: "one-time-code", state: "expected", expectedState: "expected", verifier }), {
    code: "one-time-code",
    verifier,
  });
});

test("OAuth callback rejects missing, mismatched, or replayed state", () => {
  for (const input of [
    { code: "code", state: null, expectedState: "state", verifier },
    { code: "code", state: "wrong", expectedState: "state", verifier },
    { code: "code", state: "state", expectedState: undefined, verifier },
  ]) {
    assert.throws(() => validateOAuthCallback(input), { code: "AUTH_STATE_INVALID" });
  }
});

test("OAuth callback rejects missing or malformed PKCE verifier", () => {
  assert.throws(() => validateOAuthCallback({ code: "code", state: "state", expectedState: "state", verifier: undefined }), { code: "PKCE_VERIFIER_INVALID" });
  assert.throws(() => validateOAuthCallback({ code: "code", state: "state", expectedState: "state", verifier: "short" }), { code: "PKCE_VERIFIER_INVALID" });
});

test("authentication recovery URL is fixed to the login page", () => {
  const url = authFailureLoginUrl("https://app.example/api/auth/callback?returnTo=https://evil.example");
  assert.equal(url.origin, "https://app.example");
  assert.equal(url.pathname, "/login");
  assert.equal(url.search, "?authError=1");
});
