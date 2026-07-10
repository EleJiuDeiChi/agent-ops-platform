import { expect, test } from "@playwright/test";
import type { Page } from "@playwright/test";

interface SameOriginResponse {
  body: Record<string, unknown>;
  status: number;
  url: string;
}

async function fetchSameOriginJson(page: Page, path: string): Promise<SameOriginResponse> {
  return page.evaluate(async (requestPath) => {
    const response = await fetch(requestPath, { credentials: "include" });
    return {
      body: (await response.json()) as Record<string, unknown>,
      status: response.status,
      url: response.url
    };
  }, path);
}

test("shows the login surface before a session exists", async ({ page }) => {
  await page.goto("/");

  await expect(
    page.getByRole("heading", { name: "AI 原生服务器运维面板" })
  ).toBeVisible();
  await expect(page.getByLabel("账号")).toBeVisible();
  await expect(page.getByLabel("密码")).toBeVisible();
  await expect(page.getByRole("button", { name: /登录|直接进入/ })).toBeEnabled();
});

test("serves public probes and CSRF through the frontend origin", async ({ page }) => {
  test.skip(
    !process.env.PLAYWRIGHT_BASE_URL,
    "production same-origin checks require PLAYWRIGHT_BASE_URL"
  );
  await page.goto("/");
  const frontendOrigin = new URL(page.url()).origin;

  const checks = [
    { path: "/health/live", bodyKey: "status" },
    { path: "/version", bodyKey: "service" },
    { path: "/preflight", bodyKey: "status" },
    { path: "/api/csrf", bodyKey: "csrf_token" }
  ];

  for (const check of checks) {
    const response = await fetchSameOriginJson(page, check.path);
    expect(response.status, `${check.path} should be reachable`).toBe(200);
    expect(new URL(response.url).origin, `${check.path} should stay same-origin`).toBe(frontendOrigin);
    expect(response.body[check.bodyKey], `${check.path} should expose ${check.bodyKey}`).toBeTruthy();
  }
});

test("logs in through production and shows disabled capabilities", async ({ page }) => {
  const username = process.env.AIOPS_E2E_USERNAME;
  const password = process.env.AIOPS_E2E_PASSWORD;
  test.skip(
    !process.env.PLAYWRIGHT_BASE_URL || !username || !password,
    "authenticated production checks require external base URL and test credentials"
  );

  await page.goto("/");
  await page.getByLabel("账号").fill(username || "");
  await page.getByLabel("密码").fill(password || "");
  await page.getByRole("button", { name: /登录$/ }).click();

  await expect(
    page.getByRole("heading", { name: "不用懂命令，先让 AI 看一遍服务器" })
  ).toBeVisible();
  await expect(page.getByText(/未开放：/).first()).toBeVisible();
});
