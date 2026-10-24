import { createHmac, timingSafeEqual } from "node:crypto";

import { AppError } from "../domain/errors.ts";
import type { User } from "../domain/types.ts";
import { findUserById } from "../db/repository.ts";
import type { DatabaseSync } from "node:sqlite";

export const SESSION_COOKIE = "gms_session";
export const AUTH_STATE_COOKIE = "gms_auth_state";

type SessionPayload = {
  userId: string;
  email: string;
  expiresAt: number;
};

function secret(): string {
  const value = process.env.SESSION_SECRET;
  if (!value || value.length < 32) {
    throw new AppError(
      503,
      "SESSION_NOT_CONFIGURED",
      "Session secret is not configured",
    );
  }

  return value;
}

function encode(value: string): string {
  return Buffer.from(value, "utf8").toString("base64url");
}

function decode(value: string): string {
  return Buffer.from(value, "base64url").toString("utf8");
}

function sign(value: string): string {
  return createHmac("sha256", secret()).update(value).digest("base64url");
}

export function createSessionValue(user: Pick<User, "userId" | "email">): string {
  const payload: SessionPayload = {
    userId: user.userId,
    email: user.email,
    expiresAt: Date.now() + 8 * 60 * 60 * 1000,
  };
  const encoded = encode(JSON.stringify(payload));
  return `${encoded}.${sign(encoded)}`;
}

export function verifySessionValue(value: string | undefined): SessionPayload | null {
  if (!value) {
    return null;
  }

  const [encoded, signature] = value.split(".");
  if (!encoded || !signature) {
    return null;
  }

  const expected = sign(encoded);
  const expectedBuffer = Buffer.from(expected);
  const actualBuffer = Buffer.from(signature);
  if (
    expectedBuffer.length !== actualBuffer.length ||
    !timingSafeEqual(expectedBuffer, actualBuffer)
  ) {
    return null;
  }

  try {
    const payload = JSON.parse(decode(encoded)) as SessionPayload;
    if (
      typeof payload.userId !== "string" ||
      typeof payload.email !== "string" ||
      typeof payload.expiresAt !== "number" ||
      payload.expiresAt <= Date.now()
    ) {
      return null;
    }

    return payload;
  } catch {
    return null;
  }
}

export function requestCookieValue(request: Request, name: string): string | undefined {
  const header = request.headers.get("cookie") ?? "";
  for (const part of header.split(";")) {
    const [key, ...value] = part.trim().split("=");
    if (key === name) {
      return value.join("=");
    }
  }

  return undefined;
}

export function sessionCookieOptions(maxAge = 8 * 60 * 60): {
  httpOnly: true;
  sameSite: "lax";
  secure: boolean;
  path: "/";
  maxAge: number;
} {
  return {
    httpOnly: true,
    sameSite: "lax",
    secure: process.env.NODE_ENV === "production",
    path: "/",
    maxAge,
  };
}

export function authenticatedUser(db: DatabaseSync, request: Request): User {
  const payload = verifySessionValue(requestCookieValue(request, SESSION_COOKIE));
  if (!payload) {
    throw new AppError(401, "UNAUTHENTICATED", "An active login session is required");
  }

  const user = findUserById(db, payload.userId);
  if (!user || user.accountStatus !== "ACTIVE") {
    throw new AppError(401, "UNAUTHENTICATED", "An active login session is required");
  }

  return user;
}
