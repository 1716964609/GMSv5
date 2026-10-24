import { json } from "@/lib/http";
import { protectedRequest } from "../../_utils";

export async function GET(request: Request): Promise<Response> {
  return protectedRequest(request, (_db, user) => json({ user }));
}
