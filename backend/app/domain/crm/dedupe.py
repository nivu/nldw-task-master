"""Duplicates — spec 007 FR-CRM-DUP-01, -02.

Two kinds, with two answers:
- **Exact**: a company domain (primary or additional) or a person's primary
  email already used in the workspace. Refused 409, naming the record, even
  when it is in the Trash, because restoring it would otherwise clash
  (FR-CRM-DUP-01).
- **Similar**: names alike by trigram similarity at or above `THRESHOLD`.
  Shown before saving; the person may continue (FR-CRM-DUP-02). The
  similarity itself is computed by Postgres (`crm_similar`, 029).

Pure: normalising what a person typed, and the sentences.
"""

from __future__ import annotations

import re
from typing import Final

#: pg_trgm similarity at or above which two names are shown as possible
#: duplicates (029 `crm_similar`).
THRESHOLD: Final = 0.4

_DOMAIN = re.compile(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$")


def normalise_domain(text: str) -> str | None:
    """'https://www.Acme.com/about' → 'acme.com'. None if it is not a domain.

    People paste addresses, not domains, so the scheme, a leading `www.`, any
    path, port, user part and trailing dot are dropped before the shape is
    checked against the database's own CHECK (029).
    """
    value = text.strip().lower()
    value = re.sub(r"^[a-z][a-z0-9+.-]*://", "", value)
    value = value.split("/", 1)[0].split("?", 1)[0].split("#", 1)[0]
    value = value.rsplit("@", 1)[-1].split(":", 1)[0].rstrip(".")
    if value.startswith("www."):
        value = value[4:]
    if len(value) > 253 or not _DOMAIN.match(value):
        return None
    return value


def normalise_email(text: str) -> str:
    """Emails are matched and stored in lower case (029 CHECK)."""
    return text.strip().lower()


def domain_clash(domain: str, company: str, *, in_trash: bool) -> str:
    """FR-CRM-DUP-01 — one sentence naming the company that has it."""
    where = " (in Trash)" if in_trash else ""
    return f"{domain} already belongs to {company}{where}."


def email_clash(email: str, person: str, *, in_trash: bool) -> str:
    where = " (in Trash)" if in_trash else ""
    return f"{email} is already the email of {person}{where}."


def repeated_domain(domain: str) -> str:
    return f"{domain} is listed twice; give each domain once."


def full_name(first: str | None, last: str | None) -> str:
    return " ".join(part for part in ((first or "").strip(), (last or "").strip()) if part)
