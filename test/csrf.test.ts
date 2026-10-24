import assert from "node:assert/strict";
import { describe, it } from "node:test";

import { assertSameOriginForMutation } from "../src/lib/security/csrf.ts";
import { AppError } from "../src/lib/domain/errors.ts";

const expected = "http://localhost:3100";

function request(
  method: string,
  origin?: string,
  fetchSite?: string,
): Request {
  const headers = new Headers();

  if (origin !== undefined) {
    headers.set("origin", origin);
  }

  if (fetchSite !== undefined) {
    headers.set("sec-fetch-site", fetchSite);
  }

  return new Request(
    "http://127.0.0.1:3000/api/glossaries",
    { method, headers },
  );
}

function rejects(
  operation: () => void,
  status: number,
): void {
  assert.throws(operation, (error: unknown) =>
    error instanceof AppError && error.status === status
  );
}

describe("CSRF origin protection", () => {
  it("allows legitimate same-origin mutations", () => {
    for (const method of ["POST", "PUT", "PATCH", "DELETE"]) {
      assert.doesNotThrow(() =>
        assertSameOriginForMutation(
          request(method, expected, "same-origin"),
          expected,
        ),
      );
    }
  });

  it("rejects cross-origin mutations", () => {
    rejects(() =>
      assertSameOriginForMutation(
        request("POST", "https://attacker.example", "cross-site"),
        expected,
      ), 403);
  });

  it("rejects missing or null origins", () => {
    for (const origin of [undefined, "null"]) {
      rejects(() =>
        assertSameOriginForMutation(request("POST", origin), expected),
        403,
      );
    }
  });

  it("rejects a conflicting Fetch Metadata header", () => {
    rejects(() =>
      assertSameOriginForMutation(
        request("POST", expected, "cross-site"),
        expected,
      ), 403);
  });

  it("does not block read-only GET requests", () => {
    assert.doesNotThrow(() =>
      assertSameOriginForMutation(request("GET"), expected),
    );
  });

  it("fails closed when the public origin is missing", () => {
    rejects(() =>
      assertSameOriginForMutation(
        request("POST", expected),
        "",
      ), 503);
  });
});
