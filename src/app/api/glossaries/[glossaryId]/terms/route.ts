import { createTermUseCase } from "@/lib/services/glossary";
import { listTerms } from "@/lib/db/repository";
import { json, readJson } from "@/lib/http";
import { positivePage } from "@/lib/validation";
import { protectedRequest } from "../../../_utils";

type Context = { params: Promise<{ glossaryId: string }> };

export async function GET(request: Request, context: Context): Promise<Response> {
  return protectedRequest(request, async (db, user) => {
    const { glossaryId } = await context.params;
    const page = positivePage(new URL(request.url).searchParams.get("page"));
    return json(listTerms(db, user.userId, glossaryId, page));
  });
}

export async function POST(request: Request, context: Context): Promise<Response> {
  return protectedRequest(request, async (db, user) => {
    const { glossaryId } = await context.params;
    return json(createTermUseCase(db, user.userId, await readJson(request), glossaryId), 201);
  });
}
