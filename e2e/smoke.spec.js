// Read-only: signs in as each demo person and opens every screen, changing nothing, so it is safe
// to run against the shared live demo after a deploy.
import { expect, test } from "@playwright/test";

const PEOPLE = ["Admin", "Risk lead", "Risk member", "Compliance lead"];

for (const label of PEOPLE) {
  test(`${label} can open every screen without changing anything`, async ({ page }) => {
    const writes = [];
    page.on("request", (r) => {
      const path = new URL(r.url()).pathname;
      if (r.method() !== "GET" && path !== "/api/auth/dev-login") writes.push(`${r.method()} ${path}`);
    });

    await page.goto("/login");
    await page.getByRole("button", { name: label }).click();
    await page.waitForURL((url) => new URL(url).pathname === "/");
    await expect(page.getByText("Assigned to you")).toBeVisible();

    await page.goto("/inbox");
    await expect(page.getByRole("tab", { name: /Assigned to me/ })).toBeVisible();

    await page.goto("/items");
    await expect(page.locator(".group-head").first()).toBeVisible();
    await page.locator(".irow").first().click();
    await expect(page.getByRole("heading", { name: "Next step" })).toBeVisible();

    await page.goto("/notifications");
    await expect(page.getByRole("tab", { name: "All" })).toBeVisible();

    if (label === "Admin") {
      await page.goto("/admin");
      await expect(page.locator(".people-row").nth(1)).toBeVisible();
    }

    expect(writes).toEqual([]);
  });
}

test("the privacy page opens without signing in", async ({ page }) => {
  await page.goto("/privacy");
  await expect(page.getByRole("heading", { name: "Privacy" })).toBeVisible();
});
