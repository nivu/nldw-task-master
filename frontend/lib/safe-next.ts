/**
 * Where to send someone after signing in: a path on this site only.
 *
 * `next` arrives in the query string, so anyone can write it. A path starting
 * `//` or `/\` is read by browsers as another host (`//evil.test`), which
 * would make the sign-in an open redirect; anything else is answered "/".
 */
export function safeNext(next: string | null): string {
  if (!next || !next.startsWith("/") || next.startsWith("//") || next.startsWith("/\\")) {
    return "/";
  }
  return next;
}
