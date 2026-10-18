// Two real browsers, two people, one item. The server rules are covered by the API tests; these check
// what each person actually sees. Uses the demo sign-in.
import { randomUUID } from "node:crypto";
import { expect, test } from "@playwright/test";

// These create items, and nothing can be deleted, so they would litter the shared live demo until the
// nightly reset. They run on the local stack only; smoke.spec.js is the read-only check for live.
const LIVE = Boolean(process.env.BASE_URL) && !/localhost|127\.0\.0\.1/.test(process.env.BASE_URL);
test.skip(LIVE, "writes items, so it runs on the local stack only");

const MEMBER = "Risk member";
const LEAD = "Risk lead";

async function signIn(browser, label, { live = true } = {}) {
  const context = await browser.newContext();
  const page = await context.newPage();
  // A tab whose live stream is down behaves like a stale tab: only polling, every 15 seconds.
  if (!live) await page.route("**/api/stream**", (route) => route.abort());
  await page.goto("/login");
  await page.getByRole("button", { name: label }).click();
  await page.waitForURL((url) => new URL(url).pathname === "/"); // signing in lands on the dashboard
  return { context, page };
}

async function newRiskItem(page, title) {
  const me = await (await page.request.get("/api/me")).json();
  const risk = me.teams.find((t) => t.name === "Risk");
  const res = await page.request.post("/api/items", {
    headers: { "Idempotency-Key": randomUUID() },
    data: { team_id: risk.id, kind: "task", title, priority: 2 },
  });
  expect(res.status()).toBe(201);
  return (await res.json()).id;
}

const prop = (page, name) => page.locator(".props dt", { hasText: name }).locator("xpath=following-sibling::dd[1]");

test("a stale edit is rolled back and the conflict strip names who changed what", async ({ browser }) => {
  const member = await signIn(browser, MEMBER, { live: false });
  const lead = await signIn(browser, LEAD);
  const id = await newRiskItem(member.page, `E2E stale edit ${randomUUID().slice(0, 8)}`);

  // The member's page must stay stale. With its live stream cut it still polls every 15 s, and on a
  // busy machine that poll can land mid-test and quietly fetch the new version. A fake clock does not
  // stop it, since fake time still runs. So until the conflict is shown, every reload of the item gets
  // the copy the member first saw, as a tab left open would. The 409 itself carries the new version.
  const itemUrl = `**/api/items/${id}`;
  let firstCopy;
  await member.page.route(itemUrl, async (route) => {
    if (route.request().method() !== "GET") return route.continue();
    firstCopy ??= await (await route.fetch()).text();
    return route.fulfill({ status: 200, contentType: "application/json", body: firstCopy });
  });
  await member.page.goto(`/items/${id}`);
  await lead.page.goto(`/items/${id}`);
  await expect(prop(member.page, "Version")).toHaveText("1");

  // The lead raises the priority. The member's tab still shows version 1.
  await lead.page.getByRole("button", { name: "Edit" }).click();
  await lead.page.locator(".edit-form select").first().selectOption({ label: "Urgent" });
  await lead.page.getByRole("button", { name: "Save changes" }).click();
  await expect(prop(lead.page, "Priority")).toContainText("Urgent");
  await expect(prop(member.page, "Version")).toHaveText("1");

  // The member acts on the stale copy: rolled back, and told exactly what happened.
  await member.page.getByRole("button", { name: "Triage" }).click();
  const strip = member.page.locator(".conflict");
  await expect(strip).toContainText("Changed by Finn Okafor");
  await expect(strip).toContainText("priority Normal → Urgent");
  await expect(strip).toContainText("Your change was not saved");
  await expect(prop(member.page, "Status")).toContainText("New");

  // Retry applies the same move on top of the new version, and the page reloads the real item again.
  await member.page.unroute(itemUrl);
  await strip.getByRole("button", { name: "Retry on the new version" }).click();
  await expect(member.page.locator(".conflict")).toHaveCount(0);
  await expect(prop(member.page, "Status")).toContainText("Triaged");
  await expect(prop(member.page, "Priority")).toContainText("Urgent"); // the lead's change survived

  await member.context.close();
  await lead.context.close();
});

test("two people claim the same item and exactly one of them wins", async ({ browser }) => {
  const member = await signIn(browser, MEMBER, { live: false });
  const lead = await signIn(browser, LEAD, { live: false });
  const id = await newRiskItem(member.page, `E2E claim race ${randomUUID().slice(0, 8)}`);
  await member.page.goto(`/items/${id}`);
  await lead.page.goto(`/items/${id}`);
  await expect(member.page.getByRole("button", { name: "Claim" })).toBeVisible();
  await expect(lead.page.getByRole("button", { name: "Claim" })).toBeVisible();

  await Promise.all([
    member.page.getByRole("button", { name: "Claim" }).click(),
    lead.page.getByRole("button", { name: "Claim" }).click(),
  ]);

  // Whoever lost sees a strip naming the winner; both pages end up agreeing on one assignee.
  const lost = await Promise.race([
    member.page.locator(".conflict").waitFor().then(() => ({ loser: member.page, winner: "Finn Okafor" })),
    lead.page.locator(".conflict").waitFor().then(() => ({ loser: lead.page, winner: "Dev Mehta" })),
  ]);
  await expect(lost.loser.locator(".conflict")).toContainText(`Changed by ${lost.winner}`);
  await expect(prop(member.page, "Assignee")).toContainText(lost.winner);
  await expect(prop(lead.page, "Assignee")).toContainText(lost.winner);

  await member.context.close();
  await lead.context.close();
});

test("a change by someone else appears on an open page without a reload", async ({ browser }) => {
  const member = await signIn(browser, MEMBER);
  const lead = await signIn(browser, LEAD);
  const id = await newRiskItem(member.page, `E2E live ${randomUUID().slice(0, 8)}`);
  await member.page.goto(`/items/${id}`);
  await expect(prop(member.page, "Version")).toHaveText("1");
  await member.page.waitForTimeout(2000); // let the live stream connect

  const note = `Live note ${randomUUID().slice(0, 8)}`;
  const posted = Date.now();
  const res = await lead.page.request.post(`/api/items/${id}/comments`, { data: { body: note } });
  expect(res.status()).toBe(201);

  // Polling runs once a minute while the stream is up, so seeing it within 5 s means the stream did it.
  await expect(member.page.getByText(note)).toBeVisible({ timeout: 5000 });
  console.log(`  the comment appeared ${((Date.now() - posted) / 1000).toFixed(1)} s after it was posted`);

  await member.context.close();
  await lead.context.close();
});
