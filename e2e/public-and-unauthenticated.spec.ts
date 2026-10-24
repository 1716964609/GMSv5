import { expect, test } from "@playwright/test";

test.describe("GMS v5 public and unauthenticated access", () => {
  test("renders the landing page and navigates to login and registration", async ({ page }) => {
    await page.goto("/");
    await expect(page).toHaveTitle(/GMS v5/);
    await expect(page.getByRole("heading", { name: /専門用語を/ })).toBeVisible();
    await expect(page.getByText("Glossary Management System")).toBeVisible();

    await page.locator('nav[aria-label="公開認証メニュー"]').getByRole("link", { name: "ログイン" }).click();
    await expect(page).toHaveURL(/\/login$/);
    await expect(page.getByRole("heading", { name: "ログイン" })).toBeVisible();

    await page.getByRole("link", { name: "新規登録" }).click();
    await expect(page).toHaveURL(/\/register$/);
    await expect(page.getByRole("heading", { name: "新規アカウント登録" })).toBeVisible();
  });

  test("redirects unauthenticated users away from protected application screens", async ({ page }) => {
    for (const path of ["/glossary", "/translation", "/settings"]) {
      await page.goto(path);
      await expect(page).toHaveURL(/\/login$/);
      await expect(page.getByRole("heading", { name: "ログイン" })).toBeVisible();
    }
  });

  test("rejects an unauthenticated glossary API request", async ({ request }) => {
    const response = await request.get("/api/glossaries");
    expect(response.status()).toBe(401);
    await expect(response.json()).resolves.toMatchObject({
      error: { code: "UNAUTHENTICATED" },
    });
  });

  test("does not pretend Cognito is configured", async ({ request }) => {
    const response = await request.get("/api/auth/login");
    expect(response.status()).toBe(503);
    await expect(response.json()).resolves.toMatchObject({
      error: { code: "AUTH_NOT_CONFIGURED" },
    });
  });
});
