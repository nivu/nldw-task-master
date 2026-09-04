import { type NextRequest } from "next/server";
import { updateSession } from "@/lib/supabase/middleware";

/**
 * Route guard and session refresh.
 *
 * Next.js 16 renamed this file convention from `middleware` to `proxy`; the
 * old name still works but warns on every build. The behaviour is unchanged —
 * which routes require a signed-in user is configured in
 * `lib/supabase/middleware.ts`, not here.
 */
export default async function proxy(request: NextRequest) {
  return await updateSession(request);
}

export const config = {
  matcher: [
    "/((?!_next/static|_next/image|favicon.ico|.*\\.(?:svg|png|jpg|jpeg|gif|webp)$).*)",
  ],
};
