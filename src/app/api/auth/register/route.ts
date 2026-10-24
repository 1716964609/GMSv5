import { NextResponse } from "next/server";

import { authorizationUrl, createAuthState } from "@/lib/auth/cognito";
import { AUTH_STATE_COOKIE, sessionCookieOptions } from "@/lib/auth/session";
import { publicRequest } from "../../_utils";

export async function GET(): Promise<Response> {
  return publicRequest(async () => {
    const state = createAuthState();
    const response = NextResponse.redirect(authorizationUrl("signup", state));
    response.cookies.set(AUTH_STATE_COOKIE, state, {
      ...sessionCookieOptions(600),
      sameSite: "lax",
    });
    return response;
  });
}
