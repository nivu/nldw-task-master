import { defineConfig, devices } from "@playwright/test";

/**
 * End-to-end tests against a real browser.
 *
 * These need the whole stack up — Supabase, the FastAPI backend, and this app —
 * because what they are checking is precisely the things unit tests cannot see:
 * that the route guards actually guard, that a lead's roster genuinely does not
 * carry a reason, and that the calendar renders a bookable day as bookable.
 *
 *   npx supabase start && npx supabase db reset   # schema + the seeded people
 *   cd backend && uv run uvicorn app.main:app --port 8000
 *   cd frontend && pnpm build && pnpm test:e2e
 *
 * The tests sign in as the seeded accounts and write real rows, so run them
 * against a local database and expect them to leave bookings behind. `db reset`
 * puts it back.
 */
export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false, // the specs share seeded accounts and write real rows
  forbidOnly: Boolean(process.env.CI),
  retries: process.env.CI ? 1 : 0,
  workers: 1,
  reporter: process.env.CI ? "github" : "list",

  use: {
    baseURL: process.env.E2E_BASE_URL ?? "http://localhost:3100",
    trace: "on-first-retry",
    screenshot: "only-on-failure",
  },

  projects: [
    // NFR-01 — leave is often marked from bed or in transit, so the phone
    // viewport is a first-class target rather than an afterthought.
    { name: "mobile", use: { ...devices["Pixel 7"] } },
    { name: "desktop", use: { ...devices["Desktop Chrome"] } },
  ],

  // Builds and serves its OWN server, on its own port, every run.
  //
  // Three deliberate choices, each from something that actually went wrong:
  //
  //   `pnpm build &&` — `next start` serves the asset manifest it booted with.
  //   Rebuild while it is running and it keeps serving chunk names that no
  //   longer exist, so every stylesheet 404s and the whole app renders
  //   unstyled. Tests then fail for reasons that have nothing to do with the
  //   code, and a dialog positioned `fixed` lands at the bottom of the
  //   document instead of the middle of the screen.
  //
  //   port 3100 — so a `pnpm dev` server on 3000 is neither killed nor,
  //   worse, silently tested against.
  //
  //   `reuseExistingServer: false` — reuse is what let the stale server above
  //   be trusted. A slower start is worth a result that means something.
  webServer: {
    command: "pnpm build && pnpm start -p 3100",
    url: "http://localhost:3100/auth/login",
    reuseExistingServer: false,
    timeout: 180_000,
  },
});
