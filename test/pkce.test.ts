import assert from "node:assert/strict";
import { describe, it } from "node:test";

import {
  assertPkceVerifier,
  createPkceVerifier,
  createPkceChallenge,
} from "../src/lib/security/pkce.ts";

describe("OAuth 2.0 PKCE S256", () => {
  it("matches the RFC 7636 S256 test vector", () => {
    const verifier =
      "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk";

    assert.equal(
      createPkceChallenge(verifier),
      "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM",
    );
  });

  it("generates independent valid verifiers", () => {
    const first = createPkceVerifier();
    const second = createPkceVerifier();

    assertPkceVerifier(first);
    assertPkceVerifier(second);

    assert.notEqual(first, second);
    assert.notEqual(
      createPkceChallenge(first),
      createPkceChallenge(second),
    );
  });

  it("rejects missing or invalid verifiers", () => {
    for (const value of [undefined, "", "short", "invalid value"]) {
      assert.throws(() => assertPkceVerifier(value));
    }
  });
});
