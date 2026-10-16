import { defineConfig } from "@playwright/test";

// Runs against the local stack by default; set BASE_URL to try the live demo instead.
export default defineConfig({
  testDir: ".",
  timeout: 60_000,
  retries: 0,
  reporter: [["list"]],
  use: {
    baseURL: process.env.BASE_URL || "http://localhost:5173",
    trace: "retain-on-failure",
  },
});
