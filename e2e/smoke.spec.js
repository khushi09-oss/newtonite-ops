// Read-only: signs in as each demo person and opens every screen, changing nothing, so it is safe
// to run against the shared live demo after a deploy.
import { expect, test } from "@playwright/test";

const PEOPLE = ["Admin", "Risk lead", "Risk member", "Compliance lead"];

for (const label of PEOPLE) {
  test(`${label} can open every screen without changing anything`, async ({ page }) => {
    const writes = [];
    page.on("request", (r) => {
      // Only this app's own API counts; Google's sign-in script posts its own logs to Google.
      const path = new URL(r.url()).pathname;
      if (!path.startsWith("/api/") || new URL(r.url()).origin !== new URL(page.url()).origin) return;
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

    // Every team list on screen is the server's visible_teams: the sidebar, the items filter, and the
    // New item dialog (only the teams you may create on).
    await page.goto("/items");
    await expect(page.locator(".group-head").first()).toBeVisible();
    const me = await (await page.request.get("/api/me")).json();
    const names = (teams) => teams.map((t) => t.name).sort();
    // A sidebar link reads "Risk" then its role; keep the name.
    const sidebar = await page.locator(".team-link").evaluateAll((links) =>
      links.map((a) => [...a.childNodes].filter((n) => n.nodeType === 3).map((n) => n.textContent).join("").trim()),
    );
    expect(sidebar.sort()).toEqual(names(me.visible_teams));
    const filter = page.locator(".filter-bar select[aria-label=Team] option:not([value=''])");
    // Options in a closed select have no rendered text, so read their text content.
    const texts = (loc) => loc.evaluateAll((els) => els.map((e) => e.textContent.trim()));
    expect((await texts(filter)).sort()).toEqual(names(me.visible_teams));
    const creatable = me.visible_teams.filter((t) => t.can_create);
    if (creatable.length) {
      await page.goto("/items?new=1");
      await page.locator("dialog[open] label:has-text('Team') select option").first().waitFor({ state: "attached" });
      const options = page.locator("dialog[open] label:has-text('Team') select option");
      expect((await texts(options)).sort()).toEqual(names(creatable));
      await page.keyboard.press("Escape");
    }

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
