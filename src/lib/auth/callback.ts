import { AppError } from "../domain/errors.ts";
import { assertPkceVerifier } from "../security/pkce.ts";

export type OAuthCallbackInput = {
  code: string | null;
  state: string | null;
  expectedState: string | undefined;
  verifier: string | undefined;
};

export function validateOAuthCallback(input: OAuthCallbackInput): { code: string; verifier: string } {
  if (!input.code || !input.state || !input.expectedState || input.state !== input.expectedState) {
    throw new AppError(400, "AUTH_STATE_INVALID", "Authentication state is invalid or expired");
  }
  assertPkceVerifier(input.verifier);
  return { code: input.code, verifier: input.verifier };
}

export function authFailureLoginUrl(baseUrl: string): URL {
  const loginUrl = new URL("/login", baseUrl);
  loginUrl.searchParams.set("authError", "1");
  return loginUrl;
}
