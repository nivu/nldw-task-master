import { redirect } from "next/navigation";

/**
 * The root sends a signed-in person to Home — what needs them today. The
 * calendar (FR-CAL-01) is one tap away and still the first item in the
 * navigation. Middleware guards "/" already, so an unauthenticated visitor is
 * sent to sign in before this component ever runs.
 */
export default function Root() {
  redirect("/home");
}
