import assert from "node:assert/strict";
import { after, describe, it } from "node:test";

import {
  authenticateCallback,
} from "../src/lib/auth/cognito.ts";
import {
  prepareAuthorization,
} from "../src/lib/auth/authorization.ts";
import {
  createPkceChallenge,
  createPkceVerifier,
} from "../src/lib/security/pkce.ts";
import { AppError } from "../src/lib/domain/errors.ts";

const originalEnvironment = {
  COGNITO_DOMAIN: process.env.COGNITO_DOMAIN,
  COGNITO_CLIENT_ID: process.env.COGNITO_CLIENT_ID,
  COGNITO_CLIENT_SECRET: process.env.COGNITO_CLIENT_SECRET,
  COGNITO_REDIRECT_URI: process.env.COGNITO_REDIRECT_URI,
};

process.env.COGNITO_DOMAIN =
  "https://example.auth.ap-northeast-1.amazoncognito.com";
process.env.COGNITO_CLIENT_ID = "test-client";
process.env.COGNITO_CLIENT_SECRET = "test-client-secret";
process.env.COGNITO_REDIRECT_URI =
  "http://localhost:3100/api/auth/callback";

after(() => {
  for (const [key, value] of Object.entries(originalEnvironment)) {
    if (value === undefined) {
      delete process.env[key];
    } else {
      process.env[key] = value;
    }
  }
});

describe("Cognito PKCE integration", () => {
  for (const flow of ["login", "signup"] as const) {
    it(`prepares ${flow} challenge and matching verifier`, () => {
      const { redirectUrl, state, verifier } =
        prepareAuthorization(flow);

      const url = new URL(redirectUrl);

      assert.ok(verifier);
      assert.ok(state);

      assert.equal(
        url.searchParams.get("response_type"),
        "code",
      );

      assert.equal(
        url.searchParams.get("code_challenge_method"),
        "S256",
      );

      assert.equal(
        url.searchParams.get("code_challenge"),
        createPkceChallenge(verifier),
      );

      assert.equal(
        url.searchParams.get("state"),
        state,
      );

      assert.equal(
        url.searchParams.get("redirect_uri"),
        process.env.COGNITO_REDIRECT_URI,
      );

      assert.equal(
        url.searchParams.has("code_verifier"),
        false,
      );
    });
  }

  it("sends the correct verifier during token exchange", async () => {
    const verifier = createPkceVerifier();
    const originalFetch = globalThis.fetch;

    let requestBody = "";
    let callCount = 0;

    try {
      globalThis.fetch = async (_input, init) => {
        callCount += 1;
        requestBody = String(init?.body);

        return new Response("Mock rejection", {
          status: 400,
        });
      };

      await assert.rejects(
        authenticateCallback("mock-code", verifier),
        (error: unknown) =>
          error instanceof AppError &&
          error.code === "AUTHORIZATION_CODE_INVALID",
      );

      const body = new URLSearchParams(requestBody);

      assert.equal(callCount, 1);
      assert.equal(body.get("code"), "mock-code");
      assert.equal(body.get("code_verifier"), verifier);
      assert.equal(body.get("grant_type"), "authorization_code");
      assert.equal(body.get("client_id"), "test-client");
      assert.equal(
        body.get("client_secret"),
        "test-client-secret",
      );
    } finally {
      globalThis.fetch = originalFetch;
    }
  });

  it("rejects invalid verifier before network access", async () => {
    const originalFetch = globalThis.fetch;
    let calls = 0;

    try {
      globalThis.fetch = async () => {
        calls += 1;
        throw new Error("Unexpected network request");
      };

      await assert.rejects(
        authenticateCallback("mock-code", "invalid"),
        (error: unknown) =>
          error instanceof AppError &&
          error.code === "PKCE_VERIFIER_INVALID",
      );

      assert.equal(calls, 0);
    } finally {
      globalThis.fetch = originalFetch;
    }
  });
});
