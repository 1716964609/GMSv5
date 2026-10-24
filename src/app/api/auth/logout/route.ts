import { NextResponse } from "next/server";

import { logoutUrl } from "@/lib/auth/cognito";
import { SESSION_COOKIE, sessionCookieOptions } from "@/lib/auth/session";
import { publicRequest } from "../../_utils";

export async function GET(request: Request): Promise<Response> {
  return publicRequest(async () => {
    const destination = process.env.COGNITO_DOMAIN ? logoutUrl() : new URL("/", request.url).toString();
    const response = NextResponse.redirect(destination);
    response.cookies.set(SESSION_COOKIE, "", sessionCookieOptions(0));
    return response;
  });
}
