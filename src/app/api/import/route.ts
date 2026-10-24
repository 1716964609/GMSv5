import { json, readJson } from "@/lib/http";
import { importGlossaryUseCase, validateImportTarget } from "@/lib/services/import";
import { asRecord } from "@/lib/validation";
import { protectedRequest } from "../_utils";

export async function POST(request: Request): Promise<Response> {
  return protectedRequest(request, async (db, user) => {
    const body = asRecord(await readJson(request), "import request");
    const target = validateImportTarget(body.target);
    return json(
      importGlossaryUseCase(db, user.userId, {
        target,
        document: body.document,
      }),
      201,
    );
  });
}
