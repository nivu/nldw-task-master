import { createClient } from "@supabase/supabase-js";

/**
 * A plain anon Supabase client with no session handling.
 * Use this for public pages that only read publicly accessible data
 * and must never attempt to refresh an auth token from cookies.
 */
export function createAnonClient() {
  return createClient(
    process.env.NEXT_PUBLIC_SUPABASE_URL!,
    process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY!,
    {
      auth: {
        persistSession: false,
        autoRefreshToken: false,
        detectSessionInUrl: false,
      },
    }
  );
}
