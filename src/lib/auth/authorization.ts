import { authorizationUrl, createAuthState } from "./cognito.ts";
import {
  createPkceChallenge,
  createPkceVerifier,
} from "../security/pkce.ts";

export function prepareAuthorization(
  flow: "login" | "signup",
) {
  const state = createAuthState();
  const verifier = createPkceVerifier();
  const challenge = createPkceChallenge(verifier);

  return {
    state,
    verifier,
    redirectUrl: authorizationUrl(flow, state, challenge),
  };
}
