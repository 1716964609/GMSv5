import { NextResponse } from "next/server";

import { verifyCognitoPassword } from "@/lib/auth/cognito";
import { requestDeletionUseCase } from "@/lib/services/account";
import { readJson } from "@/lib/http";
import { asRecord, parseBoolean } from "@/lib/validation";
import { AppError } from "@/lib/domain/errors";
import { requiredString } from "@/lib/validation";
import { SESSION_COOKIE, sessionCookieOptions } from "@/lib/auth/session";
import { protectedRequest } from "../../_utils";

export async function POST(request: Request): Promise<Response> {
  return protectedRequest(request, async (db, user) => {
    const body = asRecord(await readJson(request), "account deletion");
    parseBoolean(body.confirmed, "confirmed");
    if (body.confirmed !== true) {
      throw new AppError(400, "DELETION_CONFIRMATION_REQUIRED", "Account deletion must be explicitly confirmed");
    }
    const currentPassword = requiredString(body.currentPassword, "currentPassword", {
      maxLength: 1_000,
    });
    await verifyCognitoPassword(user.email, currentPassword);
    const deleted = requestDeletionUseCase(db, user.userId);
    const response = NextResponse.json(
      {
        accountStatus: deleted.accountStatus,
        deletionScheduledAt: deleted.deletionScheduledAt,
        recoveryAvailable: false,
      },
      { status: 202 },
    );
    response.cookies.set(SESSION_COOKIE, "", sessionCookieOptions(0));
    return response;
  });
}
