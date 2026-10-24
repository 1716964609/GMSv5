import { NextResponse } from "next/server";

import { prepareAuthorization } from "./authorization.ts";
import {
  AUTH_STATE_COOKIE,
  PKCE_VERIFIER_COOKIE,
  sessionCookieOptions,
} from "./session.ts";

export function startAuthorization(
  flow: "login" | "signup",
): NextResponse {
  const { state, verifier, redirectUrl } =
    prepareAuthorization(flow);

  const response = NextResponse.redirect(redirectUrl);
  const options = sessionCookieOptions(600);

  response.cookies.set(AUTH_STATE_COOKIE, state, options);
  response.cookies.set(PKCE_VERIFIER_COOKIE, verifier, options);

  return response;
}
