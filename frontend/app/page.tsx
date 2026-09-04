import { redirect } from "next/navigation";

/**
 * FR-CAL-01 — the calendar is the primary view after sign-in, so the root is
 * simply a redirect to it. Middleware guards "/" already, which means an
 * unauthenticated visitor is sent to sign in before this component ever runs.
 */
export default function Home() {
  redirect("/calendar");
}
