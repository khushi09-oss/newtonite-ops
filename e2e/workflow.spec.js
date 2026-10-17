// The whole life of one item, driven through the UI by the people the build plan names: a member who
// does the work, the lead who approves and closes it, a viewer who can only follow, and someone off
// the team. Each step checks one requirement from docs/BUILD_PLAN.md. Writes items, so local only.
import { randomUUID } from "node:crypto";
import { expect, test } from "@playwright/test";

const LIVE = Boolean(process.env.BASE_URL) && !/localhost|127\.0\.0\.1/.test(process.env.BASE_URL);
test.skip(LIVE, "writes items, so it runs on the local stack only");

async function signIn(browser, { card, email }) {
  const context = await browser.newContext();
  const page = await context.newPage();
  await page.goto("/login");
  if (card) {
    await page.getByRole("button", { name: card }).click();
  } else {
    const res = await page.request.post("/api/auth/dev-login", { data: { email } });
    expect(res.status()).toBe(200);
    await page.goto("/");
  }
  await page.waitForURL((url) => new URL(url).pathname === "/");
  return page;
}

const prop = (page, name) => page.locator(".props dt", { hasText: name }).locator("xpath=following-sibling::dd[1]");
const hint = (page) => page.locator(".side-card").first().locator("p").first();
async function move(page, button, status) {
  await page.getByRole("button", { name: button, exact: true }).click();
  await expect(prop(page, "Status")).toHaveText(status);
}

test("an item goes from new to closed and back, and every rule on the way holds", async ({ browser }) => {
  test.setTimeout(120_000);
  const member = await signIn(browser, { card: "Risk member" });
  const lead = await signIn(browser, { card: "Risk lead" });
  const viewer = await signIn(browser, { card: "Compliance lead" }); // views Risk only

  // Create, through the real dialog. The item gets its team's next key and starts as New.
  const title = `Workflow ${randomUUID().slice(0, 8)}`;
  await member.goto("/items?new=1");
  const dialog = member.locator("dialog[open]");
  await dialog.getByLabel("Team").selectOption({ label: "Risk" });
  await dialog.getByLabel("Title").fill(title);
  // Urgent, so it sorts to the top of the lead's approvals tab, which shows the first 50.
  await dialog.getByLabel("Priority").selectOption({ label: "Urgent" });
  await dialog.getByRole("button", { name: "Create item" }).click();
  await member.waitForURL(/\/items\/[0-9a-f-]{36}$/);
  const url = new URL(member.url()).pathname;
  const key = await member.locator(".crumb-key").innerText();
  expect(key).toMatch(/^RSK-\d+$/);
  await expect(prop(member, "Status")).toHaveText("New");

  // Search by key finds it.
  await lead.goto(`/items?q=${key}`);
  await expect(lead.locator(".irow", { hasText: title })).toHaveCount(1);

  // A viewer sees it but is offered nothing, and is told why.
  await viewer.goto(url);
  await expect(hint(viewer)).toHaveText("You can follow this item but not change it.");
  await expect(viewer.getByRole("button", { name: "Claim", exact: true })).toHaveCount(0);

  // Someone off the team gets the same answer as for an item that does not exist.
  const outsider = await signIn(browser, { email: "ava.lindqvist@newtonite.dev" });
  const me = await (await outsider.request.get("/api/me")).json();
  expect(me.teams.map((t) => t.name)).not.toContain("Risk");
  expect((await outsider.request.get(`/api${url}`)).status()).toBe(404);

  // new -> triaged by a member, then the claim takes it to in progress with them as owner.
  await member.goto(url);
  await move(member, "Triage", "Triaged");
  await member.getByRole("button", { name: "Claim", exact: true }).click();
  await expect(prop(member, "Status")).toHaveText("In progress");
  await expect(prop(member, "Assignee")).toContainText("Dev Mehta");

  // The owner asks for approval. Nobody approves their own request, so they are not offered it.
  await move(member, "Request approval", "Awaiting approval");
  await expect(member.getByRole("button", { name: "Approve", exact: true })).toHaveCount(0);

  // The lead's inbox has it under approvals.
  await lead.goto("/inbox?tab=approvals");
  await expect(lead.locator(".irow", { hasText: title })).toHaveCount(1);

  // Rejecting needs a note: the button opens a note box instead of moving the item.
  await lead.goto(url);
  await expect(hint(lead)).toHaveText("Waiting for your approval. Approve it, or reject it with a note.");
  await lead.getByRole("button", { name: "Reject", exact: true }).click();
  await expect(lead.getByLabel("Note")).toBeVisible();
  await expect(lead.getByRole("button", { name: "Reject with note" })).toBeDisabled();
  await expect(prop(lead, "Status")).toHaveText("Awaiting approval");

  // The lead approves; the owner resolves; only the lead can close; the lead reopens.
  await lead.goto(url);
  await move(lead, "Approve", "Approved");
  await member.goto(url);
  await move(member, "Resolve", "Resolved");
  await expect(member.getByRole("button", { name: "Close", exact: true })).toHaveCount(0);
  await lead.goto(url);
  await move(lead, "Close", "Closed");
  await expect(hint(lead)).toHaveText("Closed. Reopen it if the problem comes back.");
  await move(lead, "Reopen", "Triaged");

  // A comment sent twice by a double click is stored once.
  await member.goto(url);
  const note = `Double ${randomUUID().slice(0, 6)}`;
  await member.getByLabel("Comment", { exact: true }).fill(note);
  await member.getByRole("button", { name: "Comment", exact: true }).dblclick();
  await expect(member.locator(".comment-card", { hasText: note })).toHaveCount(1);
  await member.reload();
  await expect(member.locator(".comment-card", { hasText: note })).toHaveCount(1);

  // History has one entry per change, in order, and the server agrees with the page.
  const item = await (await member.request.get(`/api${url}`)).json();
  const kinds = item.events.map((e) => e.kind).reverse();
  expect(kinds).toEqual(["created", "status", "claimed", "status", "status", "status", "status", "status", "comment"]);
  expect(item.version).toBe(kinds.length);

  // The owner was told about the lead's moves, through the outbox and the worker.
  await expect(async () => {
    const res = await member.request.get("/api/notifications");
    const mine = (await res.json()).notifications.filter((n) => n.title === title);
    expect(mine.map((n) => n.after?.status)).toEqual(expect.arrayContaining(["approved", "closed", "triaged"]));
  }).toPass({ timeout: 15_000 });
  await member.goto("/notifications");
  await expect(member.locator(".nrow", { hasText: title }).first()).toBeVisible();
});
