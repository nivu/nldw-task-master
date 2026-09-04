import { NextResponse } from "next/server";
import { createClient } from "@/lib/supabase/server";

/**
 * OAuth return leg — FR-AUTH-08/09.
 *
 * Supabase sends the browser here after Google. Two things can arrive:
 *
 *   ?code=...                       success; exchange it for a session
 *   ?error=...&error_code=...       Supabase refused, and says why
 *
 * The refusal case is the one that matters and the one the first version got
 * wrong. It only looked for `code`, so every failure became a flat "that
 * sign-in did not complete, try again" — which is exactly wrong advice when
 * the cause is that the person has no portal account. Retrying cannot help,
 * and they have no way to learn that.
 *
 * `signup_disabled` is not a malfunction here. It is FR-AUTH-09 working:
 * Google authenticates, it does not authorise. Somebody with a perfectly valid
 * Google account who is not in the portal must be told to ask an admin, not
 * told to try again.
 */

/** Turns Supabase's error codes into something worth reading. */
function explain(errorCode: string | null, description: string | null): string {
  switch (errorCode) {
    case "signup_disabled":
      return (
        "Google signed you in, but that address has no portal account. " +
        "Accounts are created by an admin — ask one to add you, and check " +
        "you used the same address they set up."
      );
    case "access_denied":
      return "You cancelled the Google sign-in. Nothing was changed.";
    case "provider_email_needs_verification":
      return "That Google account's email is not verified. Verify it with Google, then try again.";
    default:
      // Supabase's own description is usually clearer than anything generic,
      // so prefer it and fall back only when it is absent.
      return description?.trim()
        ? `Google sign-in failed: ${description.trim()}`
        : "Google sign-in did not complete. Please try again.";
  }
}

export async function GET(request: Request) {
  const { searchParams, origin } = new URL(request.url);

  const error = searchParams.get("error");
  const errorCode = searchParams.get("error_code");
  const description = searchParams.get("error_description");

  if (error || errorCode) {
    const reason = explain(errorCode, description);
    return NextResponse.redirect(
      `${origin}/auth/login?reason=${encodeURIComponent(reason)}`
    );
  }

  const code = searchParams.get("code");
  const next = searchParams.get("next") ?? "/calendar";

  if (code) {
    const supabase = await createClient();
    const { error: exchangeError } = await supabase.auth.exchangeCodeForSession(code);
    if (!exchangeError) {
      return NextResponse.redirect(`${origin}${next}`);
    }
    return NextResponse.redirect(
      `${origin}/auth/login?reason=${encodeURIComponent(
        `Google sign-in failed: ${exchangeError.message}`
      )}`
    );
  }

  return NextResponse.redirect(
    `${origin}/auth/login?reason=${encodeURIComponent(
      "Google sign-in did not complete. Please try again."
    )}`
  );
}
