import { checkReadiness } from "@/lib/health/readiness";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export function GET(): Response {
  const result = checkReadiness(
    process.env.GMS_DATABASE_PATH,
    process.env.GMS_BUILD_SHA,
  );

  return Response.json(result, {
    status: result.status === "ready" ? 200 : 503,
    headers: {
      "Cache-Control": "no-store, max-age=0",
    },
  });
}
