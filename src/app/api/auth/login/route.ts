import { startAuthorization } from "@/lib/auth/start";
import { publicRequest } from "../../_utils";

export async function GET(): Promise<Response> {
  return publicRequest(() => startAuthorization("login"));
}
