// Playwright + @axe-core/playwright smoke & WCAG 2.1 AA verification template.
// Executed in CI `e2e-a11y` job against a running QualiText stack (E2E_BASE_URL).
import { test, expect } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';

const BASE_URL = process.env.E2E_BASE_URL ?? 'http://localhost:5173';

test('login flow, main workspace navigation, and WCAG 2.1 AA accessibility check', async ({
  page,
}) => {
  await page.goto(BASE_URL);
  await expect(page.getByRole('heading', { name: /QualiText/i })).toBeVisible();

  const loginAxe = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa']).analyze();
  expect(loginAxe.violations).toEqual([]);

  await page.fill('#email', process.env.E2E_EMAIL ?? 'admin@qualitext.id');
  await page.fill('#password', process.env.E2E_PASSWORD ?? 'Admin123!');
  await page.getByRole('button', { name: /Masuk|Sign In/i }).click();

  await expect(page.getByRole('heading', { name: /Ringkasan|Overview/i })).toBeVisible();
  const dashboardAxe = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa']).analyze();
  expect(dashboardAxe.violations).toEqual([]);
});
