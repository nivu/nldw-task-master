import { createServerClient } from "@supabase/ssr";
import { NextResponse, type NextRequest } from "next/server";

// ---------------------------------------------------------------------------
// Route configuration — the only part of this file a new project should edit.
// ---------------------------------------------------------------------------

/**
 * Prefixes that skip auth entirely. No token refresh is attempted, so these
 * stay fast and never redirect.
 *
 * FR-AUTH-01 requires authentication on every page except sign-in, so this
 * list holds exactly that one route. There is no public marketing page and no
 * signup route — accounts are created by an admin (FR-AUTH-02).
 */
const PUBLIC_ROUTES = ["/auth/"];

/**
 * Prefixes that require a signed-in user.
 *
 * "/" is included and matched exactly: the root immediately redirects to the
 * calendar, which is the primary view after sign-in (FR-CAL-01), so an
 * unauthenticated visitor must be stopped here rather than bounced twice.
 */
const PROTECTED_ROUTES = [
  "/calendar",
  "/team",
  "/approvals",
  "/admin",
  "/account",
  "/",
];

/** Where unauthenticated users are sent. */
const LOGIN_PATH = "/auth/login";

// ---------------------------------------------------------------------------

function matches(pathname: string, routes: string[]): boolean {
  return routes.some((route) =>
    route === "/" ? pathname === "/" : pathname.startsWith(route)
  );
}

export async function updateSession(request: NextRequest) {
  let supabaseResponse = NextResponse.next({ request });

  const pathname = request.nextUrl.pathname;

  // Public routes: skip auth entirely (no token work needed)
  if (matches(pathname, PUBLIC_ROUTES)) {
    return supabaseResponse;
  }

  const supabase = createServerClient(
    process.env.NEXT_PUBLIC_SUPABASE_URL!,
    process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY!,
    {
      cookies: {
        getAll() {
          return request.cookies.getAll();
        },
        setAll(cookiesToSet) {
          // Forward updated cookies to the request so downstream server
          // components see the refreshed tokens.
          cookiesToSet.forEach(({ name, value }) =>
            request.cookies.set(name, value)
          );
          // Rebuild supabaseResponse so its Set-Cookie headers carry the
          // updated (or cleared) tokens.  Every call to setAll replaces the
          // previous response; the final value is what gets returned.
          supabaseResponse = NextResponse.next({ request });
          cookiesToSet.forEach(({ name, value, options }) =>
            supabaseResponse.cookies.set(name, value, options)
          );
        },
      },
    }
  );

  let user = null;
  try {
    const { data, error } = await supabase.auth.getUser();
    if (!error) {
      user = data.user;
    }
    // On auth failure (invalid/expired refresh token) the Supabase auth
    // library has already called setAll() internally to clear the bad
    // cookies.  supabaseResponse now carries the deletion headers.
    // We must NOT discard supabaseResponse when redirecting — see below.
  } catch {
    // Network or unexpected error — treat as unauthenticated.
  }

  // Build a redirect response that also carries any Set-Cookie headers
  // accumulated in supabaseResponse (e.g. the cleared auth cookies after a
  // token-refresh failure).  Without this, the bad refresh token stays in
  // the browser and every subsequent request logs the same AuthApiError.
  function redirectTo(dest: string): NextResponse {
    const url = request.nextUrl.clone();
    const [destPath, search] = dest.split("?");
    url.pathname = destPath;
    url.search = search ? `?${search}` : "";
    const res = NextResponse.redirect(url);
    supabaseResponse.cookies.getAll().forEach((c) =>
      res.cookies.set(c.name, c.value, c)
    );
    return res;
  }

  if (matches(pathname, PROTECTED_ROUTES) && !user) {
    return redirectTo(LOGIN_PATH);
  }

  return supabaseResponse;
}
