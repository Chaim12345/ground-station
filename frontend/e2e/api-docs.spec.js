import { execFileSync } from 'node:child_process';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { expect, test } from '@playwright/test';

const backendDir = fileURLToPath(new URL('../../backend/', import.meta.url));
const swaggerDir = fileURLToPath(new URL('../node_modules/swagger-ui-dist/', import.meta.url));
const testToken = 'gs_pat_playwright_smoke';

test.use({ storageState: { cookies: [], origins: [] }, colorScheme: 'light' });

function loadDocsFromBackend() {
  // Read the real ASGI routes without starting the app's hardware-oriented lifespan.
  const script = `
import asyncio
import contextlib
import io
import json

with contextlib.redirect_stdout(io.StringIO()):
    import httpx
    from server.startup import app

    async def read_docs():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            docs = await client.get("/api/docs")
            schema = await client.get("/api/openapi.json")
            docs.raise_for_status()
            schema.raise_for_status()
            return {"html": docs.text, "schema": schema.json()}

    result = asyncio.run(read_docs())

print(json.dumps(result))
`;
  return JSON.parse(execFileSync(`${backendDir}/venv/bin/python`, ['-c', script], {
    cwd: backendDir,
    env: { ...process.env, ALEMBIC_CONTEXT: '1' },
    encoding: 'utf8',
    maxBuffer: 10 * 1024 * 1024,
    timeout: 30000,
  }));
}

test('API docs show public calls, authorize a token, and remember dark mode', async ({ page }) => {
  const origin = new URL(test.info().project.use.baseURL).origin;
  let authorization;

  if (!process.env.BASE_URL) {
    const { html, schema } = loadDocsFromBackend();
    await page.route(`${origin}/api/docs`, (route) => route.fulfill({
      contentType: 'text/html', body: html,
    }));
    await page.route(`${origin}/api/openapi.json`, (route) => route.fulfill({
      contentType: 'application/json', body: JSON.stringify(schema),
    }));
  }
  await page.route(`${origin}/api/v1/me`, (route) => {
    authorization = route.request().headers().authorization;
    return route.fulfill({
      status: authorization === `Bearer ${testToken}` ? 200 : 401,
      contentType: 'application/json',
      body: JSON.stringify({ user_id: 'playwright-user', scopes: ['station:read'] }),
    });
  });
  // Serve the same Swagger UI major version locally so CI does not need a CDN.
  await page.route('**/swagger-ui-bundle.js', (route) => route.fulfill({
    contentType: 'application/javascript; charset=utf-8',
    body: readFileSync(`${swaggerDir}/swagger-ui-bundle.js`, 'utf8'),
  }));
  await page.route('**/swagger-ui.css', (route) => route.fulfill({
    contentType: 'text/css; charset=utf-8',
    body: readFileSync(`${swaggerDir}/swagger-ui.css`, 'utf8'),
  }));
  await page.route('**/favicon.png', (route) => route.fulfill({ status: 204 }));

  await page.goto('/api/docs');
  await expect(page.locator('.swagger-ui .opblock')).toHaveCount(12);
  await expect(page.getByText('/api/v1/me', { exact: true })).toBeVisible();
  await expect(page.getByText('/api/v1/events', { exact: true })).toBeVisible();
  await expect(page.getByText('/api/auth/login', { exact: true })).toHaveCount(0);

  const toggle = page.getByRole('button', { name: 'Dark mode' });
  await expect(page.locator('html')).toHaveAttribute('data-docs-theme', 'light');
  await toggle.click();
  await expect(page.locator('html')).toHaveAttribute('data-docs-theme', 'dark');
  await page.reload();
  await expect(page.locator('html')).toHaveAttribute('data-docs-theme', 'dark');
  await expect(toggle).toHaveAttribute('aria-pressed', 'true');

  await page.locator('.swagger-ui .auth-wrapper button.authorize').click();
  await page.locator('.swagger-ui .modal-ux input').fill(testToken);
  await page.locator('.swagger-ui .modal-ux button.authorize').click();
  await page.locator('.swagger-ui .modal-ux button.btn-done').click();

  const meOperation = page.locator('.swagger-ui .opblock').filter({ hasText: '/api/v1/me' });
  await meOperation.locator('.opblock-summary').click();
  await meOperation.getByRole('button', { name: 'Try it out' }).click();
  await meOperation.getByRole('button', { name: 'Execute' }).click();
  await expect.poll(() => authorization).toBe(`Bearer ${testToken}`);
  await expect(meOperation.locator('.responses-wrapper')).toContainText('playwright-user');
});
