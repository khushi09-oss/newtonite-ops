// Read-only layout check of every screen, as each demo person, at desktop and phone width: no text
// drawn over other text, no sideways page scroll, no button or tab too narrow for its label. Safe
// against the live demo. Set SHOTS=<dir> to also save a screenshot of every screen.
import { expect, test } from "@playwright/test";

const PEOPLE = ["Admin", "Risk lead", "Risk member", "Compliance lead"];
const WIDTHS = [
  { name: "desktop", width: 1440, height: 900 },
  { name: "phone", width: 390, height: 844 },
];

// Runs in the page. Every visible piece of text as rectangles, cut down to what its scrolling or
// clipping ancestors actually show, so a title cut off with an ellipsis is not counted as overlap.
function findProblems() {
  const root = document.querySelector("dialog[open]") || document.body;
  const clipOf = new Map();
  function clip(el) {
    if (clipOf.has(el)) return clipOf.get(el);
    let box = { l: -Infinity, t: -Infinity, r: Infinity, b: Infinity };
    for (let a = el; a && a !== document.documentElement; a = a.parentElement) {
      const cs = getComputedStyle(a);
      if (cs.overflowX !== "visible" || cs.overflowY !== "visible") {
        const r = a.getBoundingClientRect();
        box = { l: Math.max(box.l, r.left), t: Math.max(box.t, r.top), r: Math.min(box.r, r.right), b: Math.min(box.b, r.bottom) };
      }
    }
    clipOf.set(el, box);
    return box;
  }
  const visible = (el) => {
    for (let a = el; a; a = a.parentElement) {
      const cs = getComputedStyle(a);
      if (cs.display === "none" || cs.visibility === "hidden" || cs.opacity === "0") return false;
      // A closed <details> hides everything but its summary without changing those styles.
      const fold = a.parentElement;
      if (fold?.tagName === "DETAILS" && !fold.open && a.tagName !== "SUMMARY") return false;
    }
    return true;
  };
  const boxes = [];
  const seen = new Set();
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  while (walker.nextNode()) {
    const node = walker.currentNode;
    const text = node.textContent.trim();
    const el = node.parentElement;
    if (!text || !el || ["OPTION", "SCRIPT", "STYLE", "TEXTAREA"].includes(el.tagName) || !visible(el)) continue;
    // SVG text is measured per <text> element, once, since its pieces share one box.
    const svg = el instanceof SVGElement;
    if (svg && seen.has(el)) continue;
    seen.add(el);
    const range = document.createRange();
    range.selectNodeContents(node);
    const c = clip(el);
    for (const r of svg ? [el.getBoundingClientRect()] : range.getClientRects()) {
      const b = { l: Math.max(r.left, c.l), t: Math.max(r.top, c.t), r: Math.min(r.right, c.r), b: Math.min(r.bottom, c.b) };
      if (b.r - b.l > 1 && b.b - b.t > 1) boxes.push({ ...b, text: (svg ? el.textContent : text).trim().slice(0, 40), node: svg ? el : node });
    }
  }
  const overlaps = [];
  for (let i = 0; i < boxes.length; i++) {
    for (let j = i + 1; j < boxes.length; j++) {
      const a = boxes[i], b = boxes[j];
      if (a.node === b.node) continue;
      const w = Math.min(a.r, b.r) - Math.max(a.l, b.l);
      const h = Math.min(a.b, b.b) - Math.max(a.t, b.t);
      if (w <= 0 || h <= 0) continue;
      const smaller = Math.min((a.r - a.l) * (a.b - a.t), (b.r - b.l) * (b.b - b.t));
      // Line boxes of neighbouring lines touch by a pixel or two; real overlap covers much more.
      if (w * h > 0.2 * smaller && h > 3) overlaps.push(`"${a.text}" over "${b.text}"`);
    }
  }
  const cramped = [...root.querySelectorAll("button, a.button, .tab, .chip, .nav-link")]
    .filter((el) => visible(el) && el.scrollWidth > el.clientWidth + 1)
    .map((el) => `"${el.textContent.trim().slice(0, 30)}" does not fit`);
  const sideways = document.documentElement.scrollWidth > window.innerWidth + 1 ? [`page is ${document.documentElement.scrollWidth}px wide`] : [];
  return [...new Set([...overlaps, ...cramped, ...sideways])];
}

async function check(page, label, problems) {
  // Not "networkidle": the live-updates stream stays open on purpose.
  await page.waitForFunction(() => !document.querySelector(".page-loading, .group-note"));
  await page.waitForTimeout(400); // let charts and counts settle
  const found = await page.evaluate(findProblems);
  for (const p of found) problems.push(`${label}: ${p}`);
  if (process.env.SHOTS) await page.screenshot({ path: `${process.env.SHOTS}/${label.replace(/[^a-z0-9]+/gi, "-")}.png`, fullPage: true });
}

for (const size of WIDTHS) {
  test(`signed-out pages are laid out cleanly (${size.name})`, async ({ page }) => {
    await page.setViewportSize(size);
    const problems = [];
    for (const path of ["/login", "/privacy"]) {
      await page.goto(path);
      await check(page, `${size.name} signed out ${path}`, problems);
    }
    expect(problems).toEqual([]);
  });

  for (const who of PEOPLE) {
    test(`${who} sees every screen laid out cleanly (${size.name})`, async ({ page }) => {
      test.setTimeout(180_000);
      await page.setViewportSize(size);
      const writes = [];
      page.on("request", (r) => {
        // Only this app's own API counts; Google's sign-in script posts its own logs to Google.
        const path = new URL(r.url()).pathname;
        if (!path.startsWith("/api/") || new URL(r.url()).origin !== new URL(page.url()).origin) return;
        if (r.method() !== "GET" && path !== "/api/auth/dev-login") writes.push(`${r.method()} ${path}`);
      });
      await page.goto("/login");
      await page.getByRole("button", { name: who }).click();
      await page.waitForURL((url) => new URL(url).pathname === "/");
      const me = await (await page.request.get("/api/me")).json();
      const tag = `${size.name} ${who}`;
      const problems = [];
      const visit = async (path, name) => {
        await page.goto(path);
        await check(page, `${tag} ${name}`, problems);
      };

      await visit("/", "dashboard");
      for (const tab of ["assigned", "approvals", "unassigned_urgent", "stale"]) await visit(`/inbox?tab=${tab}`, `inbox ${tab}`);
      await visit("/items", "items");
      await visit("/items?overdue=true", "items overdue");
      await visit("/items?q=refund", "items search");
      if (me.teams[0]) await visit(`/teams/${me.teams[0].id}`, "team");
      await visit("/notifications", "notifications");
      await visit("/notifications?tab=unread", "notifications unread");

      // The item page, then the panels it opens. Opening them changes nothing.
      await page.goto("/items");
      await page.locator(".irow").first().click();
      await page.getByRole("heading", { name: "Next step" }).waitFor();
      const itemUrl = page.url();
      await check(page, `${tag} item`, problems);
      for (const name of ["Edit", "Reassign"]) {
        const button = page.getByRole("button", { name, exact: true });
        if (await button.count()) {
          await button.first().click();
          await check(page, `${tag} item ${name.toLowerCase()}`, problems);
          await page.goto(itemUrl);
          await page.getByRole("heading", { name: "Next step" }).waitFor();
        }
      }
      if (me.teams.some((t) => t.role !== "viewer") || me.is_admin) await visit("/items?new=1", "new item dialog");
      if (me.is_admin) {
        for (const tab of ["people", "teams", "jobs", "log"]) await visit(`/admin?tab=${tab}`, `admin ${tab}`);
        await page.getByRole("tab", { name: "People" }).click();
        await page.getByRole("button", { name: "Invite person" }).click();
        await check(page, `${tag} invite dialog`, problems);
      }

      expect(writes).toEqual([]);
      expect(problems).toEqual([]);
    });
  }
}
