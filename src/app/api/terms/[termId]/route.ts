import { deleteTerm, findTermForOwner } from "@/lib/db/repository";
import { json, readJson } from "@/lib/http";
import { updateTermUseCase } from "@/lib/services/glossary";
import { protectedRequest } from "../../_utils";

type Context = { params: Promise<{ termId: string }> };

export async function GET(request: Request, context: Context): Promise<Response> {
  return protectedRequest(request, async (db, user) => {
    const { termId } = await context.params;
    const term = findTermForOwner(db, user.userId, termId);
    return term ? json(term) : json({ error: { code: "TERM_NOT_FOUND", message: "Term was not found" } }, 404);
  });
}

export async function PATCH(request: Request, context: Context): Promise<Response> {
  return protectedRequest(request, async (db, user) => {
    const { termId } = await context.params;
    return json(updateTermUseCase(db, user.userId, await readJson(request), termId));
  });
}

export async function DELETE(request: Request, context: Context): Promise<Response> {
  return protectedRequest(request, async (db, user) => {
    const { termId } = await context.params;
    deleteTerm(db, user.userId, termId);
    return json({ deleted: true });
  });
}
