import { NextResponse } from "next/server";

import { authenticateCallback, cognitoConfig } from "@/lib/auth/cognito";
import { authFailureLoginUrl, validateOAuthCallback } from "@/lib/auth/callback";
import { upsertExternalUser } from "@/lib/db/repository";
import { withDatabase } from "@/lib/db/client";
import { createSessionValue, AUTH_STATE_COOKIE, PKCE_VERIFIER_COOKIE, SESSION_COOKIE, requestCookieValue, sessionCookieOptions } from "@/lib/auth/session";

function clearAuthFlowCookies(response: NextResponse): void {
  response.cookies.delete(AUTH_STATE_COOKIE);
  response.cookies.delete(PKCE_VERIFIER_COOKIE);
}

function authFailureResponse(request: Request): NextResponse {
  let baseUrl = request.url;
  try {
    baseUrl = cognitoConfig().redirectUri;
  } catch {
    // Keep the failure path available even when the provider configuration is unavailable.
  }

  const response = NextResponse.redirect(authFailureLoginUrl(baseUrl), 303);
  clearAuthFlowCookies(response);
  response.headers.set("Cache-Control", "no-store");
  response.headers.set("Referrer-Policy", "no-referrer");
  return response;
}

export async function GET(request: Request): Promise<Response> {
  try {
    const url = new URL(request.url);
    const { code, verifier } = validateOAuthCallback({
      code: url.searchParams.get("code"),
      state: url.searchParams.get("state"),
      expectedState: requestCookieValue(request, AUTH_STATE_COOKIE),
      verifier: requestCookieValue(request, PKCE_VERIFIER_COOKIE),
    });

    const claims = await authenticateCallback(code, verifier);
    const user = withDatabase((db) =>
      upsertExternalUser(db, {
        authSubject: claims.sub,
        email: claims.email.toLowerCase(),
        emailVerified: claims.email_verified === true,
        displayName: claims.name ?? null,
      }),
    );
    if (user.accountStatus !== "ACTIVE") {
      const response = NextResponse.redirect(new URL("/?error=email_not_verified", cognitoConfig().redirectUri));
      clearAuthFlowCookies(response);
      return response;
    }

    const response = NextResponse.redirect(new URL("/glossary", cognitoConfig().redirectUri));
    response.cookies.set(SESSION_COOKIE, createSessionValue(user), sessionCookieOptions());
    clearAuthFlowCookies(response);
    return response;
  } catch {
    return authFailureResponse(request);
  }
}
