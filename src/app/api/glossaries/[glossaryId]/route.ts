import { deleteGlossary, findGlossaryForOwner } from "@/lib/db/repository";
import { json, readJson } from "@/lib/http";
import { updateGlossaryUseCase } from "@/lib/services/glossary";
import { protectedRequest } from "../../_utils";

type Context = { params: Promise<{ glossaryId: string }> };

export async function GET(request: Request, context: Context): Promise<Response> {
  return protectedRequest(request, async (db, user) => {
    const { glossaryId } = await context.params;
    const glossary = findGlossaryForOwner(db, user.userId, glossaryId);
    return glossary ? json(glossary) : json({ error: { code: "GLOSSARY_NOT_FOUND", message: "Glossary was not found" } }, 404);
  });
}

export async function PATCH(request: Request, context: Context): Promise<Response> {
  return protectedRequest(request, async (db, user) => {
    const { glossaryId } = await context.params;
    return json(updateGlossaryUseCase(db, user.userId, await readJson(request), glossaryId));
  });
}

export async function DELETE(request: Request, context: Context): Promise<Response> {
  return protectedRequest(request, async (db, user) => {
    const { glossaryId } = await context.params;
    deleteGlossary(db, user.userId, glossaryId);
    return json({ deleted: true });
  });
}
