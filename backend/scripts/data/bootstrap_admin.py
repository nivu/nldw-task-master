"""Create the very first admin account.

Run ONCE against a fresh deployment, then never again.

WHY THIS EXISTS. FR-AUTH-02 forbids self-registration, and every route that
creates an account requires an existing admin (FR-AUTH-03). A freshly deployed
database therefore has nobody who can sign in and no way to make anybody —
including the person who just deployed it. `supabase/seed.sql` solves this in
development, but it is development fixtures and is deliberately never applied
to a hosted project.

This script is the sanctioned way out of that, and it is a script rather than
an endpoint on purpose: an HTTP route that creates an admin without
authentication is a permanent hole in the product, whereas this needs the
service-role key and shell access to the deployment.

Usage:

    cd backend
    SUPABASE_URL=https://<ref>.supabase.co \\
    SUPABASE_SERVICE_ROLE_KEY=<service-role-key> \\
    uv run python scripts/data/bootstrap_admin.py \\
        --email vinita@nunnari.com --name "Vinita"

The password is read from the terminal without echoing, or from
BOOTSTRAP_ADMIN_PASSWORD if this is being run somewhere non-interactive.

It refuses to run if an active admin already exists. That is the safety
property that matters: this script can create the first admin and can never
quietly create a second one.
"""

from __future__ import annotations

import argparse
import getpass
import os
import sys

# Importable without the app's settings module, which requires a full .env.
from supabase import create_client

MIN_PASSWORD_LENGTH = 12


def main() -> int:
    parser = argparse.ArgumentParser(description="Create the first admin account.")
    parser.add_argument("--email", required=True, help="Sign-in address for the admin")
    parser.add_argument("--name", required=True, help="Display name, e.g. 'Vinita'")
    args = parser.parse_args()

    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    if not url or not key:
        print("SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must both be set.", file=sys.stderr)
        return 1

    print(f"Target: {url}")
    supabase = create_client(url, key)

    # Refuse if the job is already done. Running this twice by accident should
    # be inert, not a second privileged account nobody remembers creating.
    existing = (
        supabase.table("profiles")
        .select("email")
        .eq("role", "admin")
        .eq("is_active", True)
        .execute()
    )
    if existing.data:
        print("\nThis deployment already has an admin:", file=sys.stderr)
        for row in existing.data:
            print(f"  {row['email']}", file=sys.stderr)
        print(
            "\nRefusing to create another. Further accounts are created from the "
            "admin panel, which is the audited path (FR-ADMIN-02).",
            file=sys.stderr,
        )
        return 1

    password = os.environ.get("BOOTSTRAP_ADMIN_PASSWORD")
    if not password:
        password = getpass.getpass(f"Password for {args.email}: ")
        if password != getpass.getpass("Confirm: "):
            print("Those did not match.", file=sys.stderr)
            return 1

    if len(password) < MIN_PASSWORD_LENGTH:
        # Stricter than the 8 the API enforces for ordinary accounts. This one
        # can create and deactivate every other account in the company.
        print(
            f"The first admin's password must be at least {MIN_PASSWORD_LENGTH} characters.",
            file=sys.stderr,
        )
        return 1

    # Supabase Auth owns the password and hashes it (FR-AUTH-07). It is never
    # written to any table this product controls, and never logged.
    created = supabase.auth.admin.create_user(
        {
            "email": args.email,
            "password": password,
            "email_confirm": True,  # nobody exists to send a confirmation to
            "user_metadata": {"display_name": args.name},
        }
    )
    user_id = str(created.user.id)

    try:
        supabase.table("profiles").insert(
            {
                "id": user_id,
                "email": args.email,
                "display_name": args.name,
                "role": "admin",
                # An admin has no lead. Q-05: their own leave is approved by
                # another admin, and until there is one, not at all.
                "lead_id": None,
            }
        ).execute()
    except Exception:
        # An auth user with no profile can sign in and is then refused by
        # deps.current_user, which looks exactly like a broken login. Roll back.
        supabase.auth.admin.delete_user(user_id)
        raise

    supabase.table("audit_log").insert(
        {
            "actor_id": user_id,
            "actor_label": "system",
            "action": "admin.bootstrapped",
            "target_table": "profiles",
            "target_id": user_id,
            "after": {"email": args.email, "role": "admin", "via": "bootstrap_admin.py"},
        }
    ).execute()

    print(f"\nCreated admin {args.email} ({user_id}).")
    print("\nNext, signed in as this account:")
    print("  1. Admin -> Allowances: set the real monthly figures (spec Q-01).")
    print("     Nobody can book anything until an allowance exists.")
    print("  2. Admin -> People: create everyone else and assign their leads.")
    print("  3. Admin -> Holidays: declare the year's holidays.")
    print("  4. Admin -> Backfill: record leave already taken this month (A-21).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
