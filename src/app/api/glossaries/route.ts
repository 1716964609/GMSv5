import { createGlossaryUseCase } from "@/lib/services/glossary";
import { listGlossaries } from "@/lib/db/repository";
import { json, readJson } from "@/lib/http";
import { positivePage } from "@/lib/validation";
import { protectedRequest } from "../_utils";

export async function GET(request: Request): Promise<Response> {
  return protectedRequest(request, (db, user) => {
    const page = positivePage(new URL(request.url).searchParams.get("page"));
    return json(listGlossaries(db, user.userId, page));
  });
}

export async function POST(request: Request): Promise<Response> {
  return protectedRequest(request, async (db, user) => {
    return json(createGlossaryUseCase(db, user.userId, await readJson(request)), 201);
  });
}
