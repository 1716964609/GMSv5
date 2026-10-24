import { NextResponse } from "next/server";

import { logoutUrl } from "@/lib/auth/cognito";
import { SESSION_COOKIE, sessionCookieOptions } from "@/lib/auth/session";
import { assertSameOriginForMutation } from "@/lib/security/csrf";
import { publicRequest } from "../../_utils";

export async function POST(request: Request): Promise<Response> {
  return publicRequest(() => {
    assertSameOriginForMutation(request);

    const destination = process.env.COGNITO_DOMAIN
      ? logoutUrl()
      : new URL("/", request.url).toString();

    const response = NextResponse.redirect(destination, {
      status: 303,
    });

    response.cookies.set(
      SESSION_COOKIE,
      "",
      sessionCookieOptions(0),
    );

    return response;
  });
}
