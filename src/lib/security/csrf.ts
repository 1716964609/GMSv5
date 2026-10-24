import { AppError } from "../domain/errors.ts";

const unsafeMethods = new Set([
  "POST",
  "PUT",
  "PATCH",
  "DELETE",
]);

export function assertSameOriginForMutation(
  request: Request,
  configuredOrigin = process.env.GMS_PUBLIC_ORIGIN,
): void {
  if (!unsafeMethods.has(request.method.toUpperCase())) {
    return;
  }

  let expected: string;

  try {
    if (!configuredOrigin) {
      throw new Error("Missing public origin");
    }

    const parsed = new URL(configuredOrigin);

    if (
      parsed.origin !== configuredOrigin ||
      !["http:", "https:"].includes(parsed.protocol)
    ) {
      throw new Error("Invalid public origin");
    }

    expected = parsed.origin;
  } catch {
    throw new AppError(
      503,
      "CSRF_NOT_CONFIGURED",
      "GMS_PUBLIC_ORIGIN must be a valid origin",
    );
  }

  if (request.headers.get("origin") !== expected) {
    throw new AppError(
      403,
      "CSRF_ORIGIN_INVALID",
      "Request origin is not allowed",
    );
  }

  const fetchSite = request.headers.get("sec-fetch-site");

  if (fetchSite && fetchSite !== "same-origin") {
    throw new AppError(
      403,
      "CSRF_FETCH_SITE_INVALID",
      "Cross-site mutations are not allowed",
    );
  }
}
