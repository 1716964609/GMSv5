import { createTermUseCase } from "@/lib/services/glossary";
import { readTermPageRequest } from "@/lib/services/term-pages";
import { json, readJson } from "@/lib/http";
import { protectedRequest } from "../../../_utils";

type Context = { params: Promise<{ glossaryId: string }> };

export async function GET(request: Request, context: Context): Promise<Response> {
  const response = await protectedRequest(request, async (db, user) => {
    const { glossaryId } = await context.params;
    return json(readTermPageRequest(db, user.userId, glossaryId, new URL(request.url)));
  });
  response.headers.set("Cache-Control", "private, no-store");
  return response;
}

export async function POST(request: Request, context: Context): Promise<Response> {
  return protectedRequest(request, async (db, user) => {
    const { glossaryId } = await context.params;
    return json(createTermUseCase(db, user.userId, await readJson(request), glossaryId), 201);
  });
}
