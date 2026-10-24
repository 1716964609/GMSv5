import { listCredentialSummaries } from "@/lib/db/repository";
import { json, readJson } from "@/lib/http";
import { saveCredentialUseCase } from "@/lib/services/credentials";
import { asRecord } from "@/lib/validation";
import { protectedRequest } from "../_utils";

export async function GET(request: Request): Promise<Response> {
  return protectedRequest(request, (db, user) => json({
    credentials: listCredentialSummaries(db, user.userId),
  }));
}

export async function POST(request: Request): Promise<Response> {
  return protectedRequest(request, async (db, user) => {
    const body = asRecord(await readJson(request), "credential");
    return json({
      credential: saveCredentialUseCase(db, user.userId, body.provider, body.apiKey),
    });
  });
}
