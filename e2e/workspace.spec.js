// A visitor's own workspace on the demo, through the screens a first-time visitor uses: the setup
// card, the invite, a teammate who signs in and does the work, and the lead who approves and closes
// it. Then the people who must not see it: another visitor and the demo's shared admin.
//
// Needs the stack in demo mode (see demo.yml); skips otherwise. Visitors sign in with Google, which a
// test cannot do, so each gets a session made inside the API container, as the API tests do.
import { execFileSync } from "node:child_process";
import { randomUUID } from "node:crypto";
import { fileURLToPath } from "node:url";
import { expect, test } from "@playwright/test";

const LIVE = Boolean(process.env.BASE_URL) && !/localhost|127\.0\.0\.1/.test(process.env.BASE_URL);
test.skip(LIVE, "writes a workspace, so it runs on the local stack only");

const REPO = fileURLToPath(new URL("..", import.meta.url));
// The visitor's account as Google sign-in makes it on the demo (private), or the existing one.
const MINT = `
import os, sys, psycopg
from psycopg.rows import dict_row
from app.auth import issue_session
email, name = sys.argv[1], sys.argv[2]
with psycopg.connect(os.environ["DATABASE_URL"], row_factory=dict_row, autocommit=True) as conn:
    row = conn.execute("select id from users where email = %s", (email,)).fetchone() or conn.execute(
        "insert into users (email, name, private) values (%s, %s, true) returning id", (email, name)
    ).fetchone()
    print(issue_session(conn, row["id"]))
`;

async function visitor(browser, baseURL, email, name = "Visitor") {
  const cookie = execFileSync("docker", ["compose", "exec", "-T", "api", "python", "-c", MINT, email, name], {
    cwd: REPO,
    encoding: "utf8",
  }).trim();
  const context = await browser.newContext();
  await context.addCookies([{ name: "session", value: cookie, url: baseURL }]);
  return context.newPage();
}

async function demoAdmin(browser) {
  const page = await (await browser.newContext()).newPage();
  await page.goto("/login");
  expect((await page.request.post("/api/auth/dev-login", { data: { email: "ava.okafor@newtonite.dev" } })).status()).toBe(200);
  return page;
}

const prop = (page, name) => page.locator(".props dt", { hasText: name }).locator("xpath=following-sibling::dd[1]");
async function move(page, button, status) {
  const saved = page.waitForResponse((r) => /\/(transition|claim)$/.test(new URL(r.url()).pathname) && r.request().method() === "POST");
  await page.getByRole("button", { name: button, exact: true }).click();
  expect((await saved).ok()).toBe(true);
  await expect(prop(page, "Status")).toHaveText(status);
}

test("a visitor sets up a workspace, a teammate works an item through it, and nobody else can see it", async ({
  browser,
  baseURL,
  request,
}) => {
  test.setTimeout(120_000);
  const config = await (await request.get("/api/auth/config")).json();
  test.skip(!config.demo, "needs demo mode: docker compose -f docker-compose.yml -f e2e/demo.yml up -d api");

  const tag = randomUUID().slice(0, 8);
  const teamName = `Ops ${tag}`;
  const mateEmail = `mate-${tag}@example.com`;

  // A new visitor lands on an empty workspace and the three-step setup card.
  const lead = await visitor(browser, baseURL, `lead-${tag}@example.com`, "Workspace Lead");
  await lead.goto("/");
  const setup = lead.getByRole("region", { name: "Set up your workspace" });
  await expect(setup).toContainText("0 of 3 done");
  await expect(lead.locator(".demo-banner")).toContainText("Your workspace is private");

  // Step 1: a team. They lead it.
  await setup.getByLabel("Team name").fill(teamName);
  await setup.getByRole("button", { name: "Create team" }).click();
  await expect(setup).toContainText("1 of 3 done");

  // Step 2: a teammate by email. Nothing is emailed, so the lead gets a message to send.
  await setup.getByLabel("Email").fill(mateEmail);
  await setup.getByRole("button", { name: "Add", exact: true }).click();
  await expect(setup).toContainText("2 of 3 done");
  await expect(setup.getByLabel("Invite message")).toHaveValue(new RegExp(`${teamName}.*${mateEmail}`));

  // Step 3: an item, through the real dialog. It gets the team's first key and the card goes away.
  await setup.getByRole("button", { name: "New item" }).click();
  const dialog = lead.locator("dialog[open]");
  await dialog.getByLabel("Team").selectOption({ label: teamName });
  await dialog.getByLabel("Title").fill(`Workspace item ${tag}`);
  await dialog.getByRole("button", { name: "Create item" }).click();
  await lead.waitForURL(/\/items\/[0-9a-f-]{36}$/);
  const url = new URL(lead.url()).pathname;
  await expect(lead.locator(".crumb-key")).toHaveText(/^[A-Z]+\d*-1$/);
  await lead.goto("/");
  await expect(lead.getByRole("heading", { name: "Recent activity" })).toBeVisible();
  await expect(lead.getByRole("region", { name: "Set up your workspace" })).toHaveCount(0);

  // The teammate signs in to the account the invite made, finds the team, and does the work.
  const mate = await visitor(browser, baseURL, mateEmail);
  const me = await (await mate.request.get("/api/me")).json();
  expect(me.teams.map((t) => [t.name, t.role])).toEqual([[teamName, "member"]]);
  await mate.goto(url);
  await move(mate, "Claim", "In progress");
  await move(mate, "Request approval", "Awaiting approval");

  // The lead approves; the teammate resolves; the lead closes.
  await lead.goto(url);
  await move(lead, "Approve", "Approved");
  await mate.goto(url);
  await move(mate, "Resolve", "Resolved");
  await lead.goto(url);
  await move(lead, "Close", "Closed");

  // The teammate was told about the lead's moves, through the outbox like anyone else.
  await expect(async () => {
    const notes = (await (await mate.request.get("/api/notifications")).json()).notifications;
    const statuses = notes.filter((n) => n.title === `Workspace item ${tag}`).map((n) => n.after?.status);
    expect(statuses).toEqual(expect.arrayContaining(["approved", "closed"]));
  }).toPass({ timeout: 15_000 });

  // Another visitor and the demo's shared admin get the same answer as for an item that does not exist.
  for (const outsider of [await visitor(browser, baseURL, `other-${tag}@example.com`), await demoAdmin(browser)]) {
    expect((await outsider.request.get(`/api${url}`)).status()).toBe(404);
    await outsider.goto(url);
    await expect(outsider.getByText("There is no item here you can see.")).toBeVisible();
    const seen = (await (await outsider.request.get("/api/me")).json()).visible_teams.map((t) => t.name);
    expect(seen).not.toContain(teamName);
  }
});
