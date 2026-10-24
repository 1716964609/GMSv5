import {
  createHmac,
  createPublicKey,
  randomBytes,
  verify as verifySignature,
} from "node:crypto";

import { AppError } from "../domain/errors.ts";

type CognitoConfig = {
  domain: string;
  clientId: string;
  clientSecret?: string;
  issuer: string;
  redirectUri: string;
  logoutUri: string;
};

type IdTokenClaims = {
  sub: string;
  email: string;
  email_verified?: boolean;
  name?: string;
  exp: number;
  iss: string;
  aud: string;
  token_use?: string;
};

function requiredEnv(name: string): string {
  const value = process.env[name];
  if (!value) {
    throw new AppError(503, "AUTH_NOT_CONFIGURED", `${name} is not configured`);
  }
  return value;
}

function trimTrailingSlash(value: string): string {
  return value.replace(/\/+$/, "");
}

export function cognitoConfig(): CognitoConfig {
  const domain = trimTrailingSlash(requiredEnv("COGNITO_DOMAIN"));
  const clientId = requiredEnv("COGNITO_CLIENT_ID");
  const issuer = trimTrailingSlash(
    process.env.COGNITO_ISSUER ?? `${domain}`,
  );
  const redirectUri = requiredEnv("COGNITO_REDIRECT_URI");
  const logoutUri = process.env.COGNITO_LOGOUT_URI ?? redirectUri.replace(/\/api\/auth\/callback$/, "/");

  return {
    domain,
    clientId,
    clientSecret: process.env.COGNITO_CLIENT_SECRET,
    issuer,
    redirectUri,
    logoutUri,
  };
}

export function createAuthState(): string {
  return randomBytes(32).toString("base64url");
}

export function authorizationUrl(
  flow: "login" | "signup" | "reset",
  state: string,
): string {
  const config = cognitoConfig();
  const path = flow === "reset" ? "/forgotPassword" : "/oauth2/authorize";
  const url = new URL(path, `${config.domain}/`);
  url.searchParams.set("client_id", config.clientId);
  url.searchParams.set("response_type", "code");
  url.searchParams.set("scope", "openid email profile");
  url.searchParams.set("redirect_uri", config.redirectUri);
  url.searchParams.set("state", state);
  if (flow === "signup") {
    url.searchParams.set("screen_hint", "signup");
  }
  return url.toString();
}

export function logoutUrl(): string {
  const config = cognitoConfig();
  const url = new URL("/logout", `${config.domain}/`);
  url.searchParams.set("client_id", config.clientId);
  url.searchParams.set("logout_uri", config.logoutUri);
  return url.toString();
}

function decodeBase64Url(value: string): Buffer {
  return Buffer.from(value, "base64url");
}

function decodeJson<T>(value: string): T {
  return JSON.parse(decodeBase64Url(value).toString("utf8")) as T;
}

async function exchangeCode(code: string): Promise<{ id_token: string }> {
  const config = cognitoConfig();
  const body = new URLSearchParams({
    grant_type: "authorization_code",
    client_id: config.clientId,
    code,
    redirect_uri: config.redirectUri,
  });
  if (config.clientSecret) {
    body.set("client_secret", config.clientSecret);
  }

  const response = await fetch(`${config.domain}/oauth2/token`, {
    method: "POST",
    headers: { "content-type": "application/x-www-form-urlencoded" },
    body,
    cache: "no-store",
  });
  if (!response.ok) {
    throw new AppError(401, "AUTHORIZATION_CODE_INVALID", "Authentication code could not be exchanged");
  }
  const payload = (await response.json()) as { id_token?: string };
  if (!payload.id_token) {
    throw new AppError(401, "ID_TOKEN_MISSING", "Authentication response did not include an ID token");
  }
  return { id_token: payload.id_token };
}

async function verifyIdToken(token: string, config: CognitoConfig): Promise<IdTokenClaims> {
  const parts = token.split(".");
  if (parts.length !== 3) {
    throw new AppError(401, "ID_TOKEN_INVALID", "ID token format is invalid");
  }
  const header = decodeJson<{ alg?: string; kid?: string }>(parts[0]);
  const claims = decodeJson<IdTokenClaims>(parts[1]);
  if (header.alg !== "RS256" || !header.kid) {
    throw new AppError(401, "ID_TOKEN_INVALID", "ID token signing algorithm is invalid");
  }
  if (
    claims.iss !== config.issuer ||
    claims.aud !== config.clientId ||
    claims.token_use !== undefined && claims.token_use !== "id" ||
    !claims.sub ||
    !claims.email ||
    claims.exp * 1000 <= Date.now()
  ) {
    throw new AppError(401, "ID_TOKEN_INVALID", "ID token claims are invalid");
  }

  const jwksResponse = await fetch(`${config.issuer}/.well-known/jwks.json`, {
    cache: "no-store",
  });
  if (!jwksResponse.ok) {
    throw new AppError(503, "JWKS_UNAVAILABLE", "Authentication signing keys are unavailable");
  }
  const jwks = (await jwksResponse.json()) as {
    keys?: Array<Record<string, unknown>>;
  };
  const jwk = jwks.keys?.find((key) => key.kid === header.kid);
  if (!jwk) {
    throw new AppError(401, "ID_TOKEN_INVALID", "ID token signing key is unknown");
  }

  const publicKey = createPublicKey({ key: jwk, format: "jwk" });
  const valid = verifySignature(
    "RSA-SHA256",
    Buffer.from(`${parts[0]}.${parts[1]}`),
    publicKey,
    decodeBase64Url(parts[2]),
  );
  if (!valid) {
    throw new AppError(401, "ID_TOKEN_INVALID", "ID token signature is invalid");
  }

  return claims;
}

export async function authenticateCallback(code: string): Promise<IdTokenClaims> {
  const config = cognitoConfig();
  const token = await exchangeCode(code);
  return verifyIdToken(token.id_token, config);
}

export async function verifyCognitoPassword(
  email: string,
  password: string,
): Promise<void> {
  await initiatePasswordAuth(email, password);
}

async function initiatePasswordAuth(
  email: string,
  password: string,
): Promise<string> {
  const config = cognitoConfig();
  const region = process.env.COGNITO_REGION;
  if (!region) {
    throw new AppError(503, "AUTH_NOT_CONFIGURED", "COGNITO_REGION is not configured");
  }

  const authParameters: Record<string, string> = {
    USERNAME: email,
    PASSWORD: password,
  };
  if (config.clientSecret) {
    authParameters.SECRET_HASH = createHmac("sha256", config.clientSecret)
      .update(`${email}${config.clientId}`)
      .digest("base64");
  }

  const response = await fetch(`https://cognito-idp.${region}.amazonaws.com/`, {
    method: "POST",
    headers: {
      "Content-Type": "application/x-amz-json-1.1",
      "X-Amz-Target": "AWSCognitoIdentityProviderService.InitiateAuth",
    },
    body: JSON.stringify({
      AuthFlow: "USER_PASSWORD_AUTH",
      ClientId: config.clientId,
      AuthParameters: authParameters,
    }),
    cache: "no-store",
  });
  if (!response.ok) {
    throw new AppError(401, "PASSWORD_INVALID", "Current password could not be verified");
  }
  const payload = (await response.json()) as {
    AuthenticationResult?: { AccessToken?: string };
  };
  const accessToken = payload.AuthenticationResult?.AccessToken;
  if (!accessToken) {
    throw new AppError(409, "PASSWORD_CHANGE_CHALLENGE", "Cognito requires an additional password step");
  }
  return accessToken;
}

export async function changeCognitoPassword(
  email: string,
  currentPassword: string,
  newPassword: string,
): Promise<void> {
  const accessToken = await initiatePasswordAuth(email, currentPassword);
  const region = requiredEnv("COGNITO_REGION");
  const response = await fetch(`https://cognito-idp.${region}.amazonaws.com/`, {
    method: "POST",
    headers: {
      "Content-Type": "application/x-amz-json-1.1",
      "X-Amz-Target": "AWSCognitoIdentityProviderService.ChangePassword",
    },
    body: JSON.stringify({
      AccessToken: accessToken,
      PreviousPassword: currentPassword,
      ProposedPassword: newPassword,
    }),
    cache: "no-store",
  });
  if (!response.ok) {
    throw new AppError(400, "PASSWORD_CHANGE_FAILED", "Cognito could not change the password");
  }
}
