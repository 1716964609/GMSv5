import { NextResponse } from "next/server";

import { changeCognitoPassword } from "@/lib/auth/cognito";
import { AppError } from "@/lib/domain/errors";
import { json, readJson } from "@/lib/http";
import { asRecord, requiredString } from "@/lib/validation";
import { protectedRequest } from "../../_utils";

export async function GET(request: Request): Promise<Response> {
  return protectedRequest(request, () => {
    const destination = process.env.COGNITO_PASSWORD_CHANGE_URL;
    if (!destination) {
      throw new AppError(
        503,
        "PASSWORD_CHANGE_NOT_CONFIGURED",
        "Configure the Cognito password change flow before starting it",
      );
    }
    return NextResponse.redirect(destination);
  });
}

export async function POST(request: Request): Promise<Response> {
  return protectedRequest(request, async (_db, user) => {
    const body = asRecord(await readJson(request), "password change");
    const currentPassword = requiredString(body.currentPassword, "currentPassword", { maxLength: 1_000 });
    const newPassword = requiredString(body.newPassword, "newPassword", { maxLength: 1_000 });
    if (currentPassword === newPassword) {
      throw new AppError(400, "PASSWORD_UNCHANGED", "New password must differ from the current password");
    }
    await changeCognitoPassword(user.email, currentPassword, newPassword);
    return json({ changed: true });
  });
}
