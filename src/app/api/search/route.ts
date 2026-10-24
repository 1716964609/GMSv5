import { searchTerms } from "@/lib/db/repository";
import { json } from "@/lib/http";
import { protectedRequest } from "../_utils";

export async function GET(request: Request): Promise<Response> {
  return protectedRequest(request, (db, user) => {
    const keyword = new URL(request.url).searchParams.get("q") ?? "";
    return json({ items: searchTerms(db, user.userId, keyword.trim()) });
  });
}
