// Two quiet ways a screen used to change or keep data the person did not ask it to. Writes items,
// so local only.
import { randomUUID } from "node:crypto";
import { expect, test } from "@playwright/test";

const LIVE = Boolean(process.env.BASE_URL) && !/localhost|127\.0\.0\.1/.test(process.env.BASE_URL);
test.skip(LIVE, "writes items, so it runs on the local stack only");

async function signIn(page, card) {
  await page.goto("/login");
  await page.getByRole("button", { name: card }).click();
  await page.waitForURL((url) => new URL(url).pathname === "/");
}

test("editing only the title saves only the title, and the due date keeps its time", async ({ page }) => {
  await signIn(page, "Risk lead");
  const me = await (await page.request.get("/api/me")).json();
  const risk = me.teams.find((t) => t.name === "Risk");
  const due = "2026-11-05T14:30:00+00:00";
  const res = await page.request.post("/api/items", {
    headers: { "Idempotency-Key": randomUUID() },
    data: { team_id: risk.id, kind: "task", title: `Edit check ${randomUUID().slice(0, 8)}`, due_at: due },
  });
  const item = await res.json();

  await page.goto(`/items/${item.id}`);
  await page.getByRole("button", { name: "Edit", exact: true }).click();
  await page.locator(".edit-form input").first().fill(`${item.title} renamed`);
  const saved = page.waitForResponse((r) => r.request().method() === "PATCH");
  await page.getByRole("button", { name: "Save changes" }).click();
  expect(Object.keys((await saved).request().postDataJSON()).sort()).toEqual(["title", "version"]);

  const after = await (await page.request.get(`/api/items/${item.id}`)).json();
  expect(after.due_at).toBe(due);
  expect(Object.keys(after.events[0].after)).toEqual(["title"]);
});

test("a sidebar link replaces the search instead of the old search coming back", async ({ page }) => {
  await signIn(page, "Risk member");
  await page.goto("/items?q=refund");
  await expect(page.getByLabel("Search items")).toHaveValue("refund");
  await page.getByRole("navigation").getByRole("link", { name: "All items" }).click();
  await page.waitForTimeout(800); // longer than the search box's pause before it writes the URL
  expect(new URL(page.url()).searchParams.get("q")).toBeNull();
  await expect(page.getByLabel("Search items")).toHaveValue("");
});

test("closing an inline panel puts focus back on the button that opened it", async ({ page }) => {
  await signIn(page, "Risk lead");
  const me = await (await page.request.get("/api/me")).json();
  const risk = me.teams.find((t) => t.name === "Risk");
  const res = await page.request.post("/api/items", {
    headers: { "Idempotency-Key": randomUUID() },
    data: { team_id: risk.id, kind: "task", title: `Focus check ${randomUUID().slice(0, 8)}` },
  });
  const item = await res.json();
  await page.goto(`/items/${item.id}`);
  const claim = page.waitForResponse((r) => /\/claim$/.test(new URL(r.url()).pathname));
  await page.getByRole("button", { name: "Claim", exact: true }).first().click(); // so Reassign is offered
  await claim;

  const reassign = page.getByRole("button", { name: "Reassign" });
  await reassign.focus();
  await page.keyboard.press("Enter");
  await page.locator(".assign").getByRole("button", { name: "Cancel" }).click();
  await expect(reassign).toBeFocused();

  await page.getByRole("button", { name: "Edit", exact: true }).click();
  await page.locator(".edit-form").getByRole("button", { name: "Cancel" }).click();
  await expect(page.getByRole("button", { name: "Edit", exact: true })).toBeFocused();
});
