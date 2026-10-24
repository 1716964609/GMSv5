import { NextResponse } from "next/server";

import { authenticateCallback } from "@/lib/auth/cognito";
import { upsertExternalUser } from "@/lib/db/repository";
import { withDatabase } from "@/lib/db/client";
import { createSessionValue, AUTH_STATE_COOKIE, SESSION_COOKIE, requestCookieValue, sessionCookieOptions } from "@/lib/auth/session";
import { AppError } from "@/lib/domain/errors";
import { publicRequest } from "../../_utils";

export async function GET(request: Request): Promise<Response> {
  return publicRequest(async () => {
    const url = new URL(request.url);
    const code = url.searchParams.get("code");
    const state = url.searchParams.get("state");
    const expectedState = requestCookieValue(request, AUTH_STATE_COOKIE);
    if (!code || !state || !expectedState || state !== expectedState) {
      throw new AppError(400, "AUTH_STATE_INVALID", "Authentication state is invalid or expired");
    }

    const claims = await authenticateCallback(code);
    const user = withDatabase((db) =>
      upsertExternalUser(db, {
        authSubject: claims.sub,
        email: claims.email.toLowerCase(),
        emailVerified: claims.email_verified === true,
        displayName: claims.name ?? null,
      }),
    );
    if (user.accountStatus !== "ACTIVE") {
      const response = NextResponse.redirect(new URL("/?error=email_not_verified", request.url));
      response.cookies.delete(AUTH_STATE_COOKIE);
      return response;
    }

    const response = NextResponse.redirect(new URL("/glossary", request.url));
    response.cookies.set(SESSION_COOKIE, createSessionValue(user), sessionCookieOptions());
    response.cookies.delete(AUTH_STATE_COOKIE);
    return response;
  });
}
