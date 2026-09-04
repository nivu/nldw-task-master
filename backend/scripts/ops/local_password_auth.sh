#!/usr/bin/env sh
#
# Turn password sign-in back on FOR LOCAL DEVELOPMENT ONLY.
#
# FR-AUTH-08 makes Google the only way into the portal, and
# `[auth.email] enable_signup` in supabase/config.toml is the switch that
# enforces it. That file is pushed to production wholesale, and the CLI's
# `env()` substitution does not work on boolean fields, so this one value
# cannot differ between local and production.
#
# It therefore holds the PRODUCTION-correct value, and this script flips it for
# local work — because local development and the 32 browser tests sign in as
# the seeded accounts with a password, and Google OAuth cannot be driven by a
# headless test.
#
# The direction matters. Editing a committed file means the change appears in
# `git status`, so it announces itself before it can be pushed by accident; and
# forgetting to run this breaks a local test run, whereas the opposite default
# would mean forgetting to re-harden a production that quietly accepts
# passwords. The trap points at a broken test, not at a security hole.
#
#   ./scripts/ops/local_password_auth.sh on    # then: supabase stop && supabase start
#   ./scripts/ops/local_password_auth.sh off   # restore before committing
#   ./scripts/ops/local_password_auth.sh status
#
# NEVER run `supabase config push` while this is on.

set -e

CONFIG="$(cd "$(dirname "$0")/../../.." && pwd)/supabase/config.toml"
[ -f "$CONFIG" ] || { echo "cannot find $CONFIG" >&2; exit 1; }

# The [auth.email] block's enable_signup. There are three enable_signup lines
# in the file — the global one, this one, and the anonymous one — so match on
# the block rather than the key.
current() {
  awk '/^\[auth\.email\]/{f=1} f && /^enable_signup/{print $3; exit}' "$CONFIG"
}

set_to() {
  awk -v want="$1" '
    /^\[auth\.email\]/ {f=1}
    f && /^enable_signup/ && !done { print "enable_signup = " want; done=1; next }
    { print }
  ' "$CONFIG" > "$CONFIG.tmp" && mv "$CONFIG.tmp" "$CONFIG"
}

case "${1:-status}" in
  on)
    set_to true
    echo "Password sign-in ENABLED for local development."
    echo
    echo "  supabase stop && supabase start   # the flag is read at startup"
    echo
    echo "This edited a committed file. Run '$0 off' before you commit,"
    echo "and never 'supabase config push' while it is on."
    ;;
  off)
    set_to false
    echo "Password sign-in disabled — config.toml is production-correct again."
    ;;
  status)
    v="$(current)"
    if [ "$v" = "true" ]; then
      echo "LOCAL OVERRIDE IS ON — config.toml allows password sign-in."
      echo "Do not commit this, and do not run 'supabase config push'."
      exit 1
    fi
    echo "config.toml is production-correct: password sign-in disabled (FR-AUTH-08)."
    ;;
  *)
    echo "usage: $0 [on|off|status]" >&2
    exit 1
    ;;
esac
