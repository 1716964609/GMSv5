import { createHash, randomBytes } from "node:crypto";

import { AppError } from "../domain/errors.ts";

const VERIFIER_PATTERN = /^[A-Za-z0-9._~-]{43,128}$/;

export function createPkceVerifier(): string {
  return randomBytes(32).toString("base64url");
}

export function assertPkceVerifier(
  verifier: string | undefined,
): asserts verifier is string {
  if (!verifier || !VERIFIER_PATTERN.test(verifier)) {
    throw new AppError(
      400,
      "PKCE_VERIFIER_INVALID",
      "PKCE verifier is missing or invalid",
    );
  }
}

export function createPkceChallenge(verifier: string): string {
  assertPkceVerifier(verifier);

  return createHash("sha256")
    .update(verifier, "ascii")
    .digest("base64url");
}
