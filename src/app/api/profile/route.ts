import { json, readJson } from "@/lib/http";
import { updateProfileUseCase } from "@/lib/services/account";
import { protectedRequest } from "../_utils";

export async function GET(request: Request): Promise<Response> {
  return protectedRequest(request, (_db, user) => json({
    user: {
      userId: user.userId,
      email: user.email,
      displayName: user.displayName,
    },
  }));
}

export async function PATCH(request: Request): Promise<Response> {
  return protectedRequest(request, async (db, user) => {
    const updated = updateProfileUseCase(db, user.userId, await readJson(request));
    return json({
      user: {
        userId: updated.userId,
        email: updated.email,
        displayName: updated.displayName,
      },
    });
  });
}

