import type { DatabaseSync } from "node:sqlite";

import { withDatabaseAsync } from "@/lib/db/client";
import { authenticatedUser } from "@/lib/auth/session";
import { assertSameOriginForMutation } from "@/lib/security/csrf";
import { errorResponse } from "@/lib/http";
import type { User } from "@/lib/domain/types";

export async function protectedRequest(
  request: Request,
  callback: (db: DatabaseSync, user: User) => Promise<Response> | Response,
): Promise<Response> {
  try {
    return await withDatabaseAsync(async (db) => {
      const user = authenticatedUser(db, request);
      assertSameOriginForMutation(request);
      return callback(db, user);
    });
  } catch (error) {
    return errorResponse(error);
  }
}

export async function publicRequest(
  callback: () => Promise<Response> | Response,
): Promise<Response> {
  try {
    return await callback();
  } catch (error) {
    return errorResponse(error);
  }
}
