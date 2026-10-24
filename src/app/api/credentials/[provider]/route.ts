import { json } from "@/lib/http";
import { deleteCredentialUseCase } from "@/lib/services/credentials";
import { protectedRequest } from "../../_utils";

type Context = { params: Promise<{ provider: string }> };

export async function DELETE(request: Request, context: Context): Promise<Response> {
  return protectedRequest(request, async (db, user) => {
    const { provider } = await context.params;
    deleteCredentialUseCase(db, user.userId, provider);
    return json({ deleted: true });
  });
}
