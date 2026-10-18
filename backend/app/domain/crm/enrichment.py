"""Company enrichment's rules — spec 009 FR-CRMAI-ENR-01 to -06, -08,
FR-CRMAI-LLM-04; Q-30, Q-32, Q-33, Q-37; data-model §3.2.1, §3.5; plan §5.7,
CT-3.

Pure. Which site a company is enriched from (`site`: its website's host,
else its primary domain, as `https://host/`, the idempotency key with the
company, Q-33), which addresses count as the same site (`same_site`, for the
pages read and the redirects followed, Q-30), the prompt's parts, how an
answer becomes the stored values (`values_from`: the industry and employee
band matched to the workspace's options by label, LinkedIn kept only as a
`linkedin.com/company/` page), and what each value may do to the company's
own field: be promoted while the field is empty (`promotable`), or wait as
an `enrichment_conflict` item while it holds something else (`conflicts`,
`items`, FR-CRMAI-ENR-04, -05). Nothing here writes a person-entered field.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable, Mapping, Sequence
from typing import Any, Final
from urllib.parse import urlparse

from app.domain.crm import review as review_rules

#: What the job records in the usage log and the job log.
JOB: Final = "enrichment"
#: The phase switch it runs behind (FR-CRMAI-FLAG-01).
PHASE: Final = "2"
SOURCE_KIND: Final = "enrichment"

#: FR-CRMAI-ENR-06 — why an enrichment failed, besides FR-CRMAI-LLM-03's
#: model reasons. A host the URL policy refuses is "did not respond" (D-2).
NO_RESPONSE: Final = "Website did not respond"
NO_INFO: Final = "No company information found"

#: The pages read after the home page, when the home page links to them
#: (plan §5.7).
CANDIDATE_PATHS: Final = ("/about", "/about-us", "/company", "/contact")
#: The most page text one page puts in the prompt: a large site cannot
#: inflate the prompt past what a model call takes (Q-31).
TEXT_LIMIT: Final = 20_000

#: The enriched values, in the order spec §7.2 lists them.
VALUE_KEYS: Final = (
    "industry",
    "sub_industry",
    "employee_band",
    "city",
    "country",
    "description",
    "linkedin",
    "products",
)
#: Each value that promotes, and the company field it promotes to (§7.2).
PROMOTES: Final = {
    "industry": "industry_id",
    "employee_band": "size_id",
    "city": "city",
    "country": "country",
    "description": "description",
    "linkedin": "linkedin",
}
#: The workspace lists the select values match against.
INDUSTRY_LIST: Final = "company.industry"
SIZE_LIST: Final = "company.size"

#: The job's instruction, after the fixed rules every prompt carries.
INSTRUCTION: Final = (
    "Describe one company from the text of its own website. You are given the "
    "workspace's industry and size options and the pages read. Answer with: industry, "
    "exactly one of the industry options when one fits, else the industry in at most "
    "three words; sub_industry in at most five words; employee_band, exactly one of the "
    "size options when the site says how many people work there, else null; city and "
    "country of the head office; description, one plain sentence of at most 300 "
    "characters on what the company does; linkedin, the company's LinkedIn company page "
    "link only when the site gives one; products, at most eight products or services, "
    "each in at most 60 characters. Use null (or an empty list) for anything the pages "
    "do not say. Never guess; paraphrase, never quote the pages."
)


# ---------------------------------------------------------------------------
# The site — FR-CRMAI-ENR-01, -08; Q-32, Q-33
# ---------------------------------------------------------------------------

#: A host name the CRM accepts as a domain (029's `crm_company_domains` CHECK).
_DOMAIN: Final = re.compile(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$")
#: The host of a URL typed with or without a scheme: 064's trigger uses the
#: same pattern, so the two always agree.
_HOST: Final = re.compile(r"^(?:[A-Za-z][A-Za-z0-9+.-]*://)?(?:[^@/?#]*@)?([^/:?#]+)")


def host_of(url: Any) -> str | None:
    """The host of a typed website, lower-cased, or None when it is not a
    domain name."""
    if not isinstance(url, str):
        return None
    found = _HOST.match(url.strip(" "))
    if not found:
        return None
    host = found.group(1).lower().rstrip(".")
    return host if _DOMAIN.match(host) else None


def site(website: Any, primary_domain: str | None) -> str | None:
    """The site a company is enriched from: `https://<website host>/`, else
    `https://<primary domain>/`, else None (nothing to read)."""
    primary = website.get("primary") if isinstance(website, Mapping) else None
    host = host_of(primary.get("url") if isinstance(primary, Mapping) else None)
    if host is None and primary_domain and _DOMAIN.match(primary_domain):
        host = primary_domain
    return f"https://{host}/" if host else None


#: Second-level suffixes under which a name is registered one label deeper
#: (acme.co.in, acme.edu.au): the CRM's markets, without a suffix list.
_SECOND_LEVEL: Final = frozenset(
    {
        "ac.in",
        "co.in",
        "edu.in",
        "gov.in",
        "net.in",
        "org.in",
        "res.in",
        "com.au",
        "edu.au",
        "gov.au",
        "net.au",
        "org.au",
        "ac.uk",
        "co.uk",
        "gov.uk",
        "org.uk",
        "co.nz",
        "com.sg",
        "edu.sg",
    }
)


def registrable(host: str) -> str:
    """The registered name a host belongs to: `about.acme.co.in` →
    `acme.co.in`, `www.acme.com` → `acme.com`."""
    labels = host.lower().rstrip(".").split(".")
    keep = 3 if len(labels) >= 3 and ".".join(labels[-2:]) in _SECOND_LEVEL else 2
    return ".".join(labels[-keep:])


def same_site(host: str, other: str) -> bool:
    """Q-30 — two hosts of one registered name (`acme.in`, `www.acme.in`)."""
    return bool(host) and bool(other) and registrable(host) == registrable(other)


# ---------------------------------------------------------------------------
# The prompt — FR-CRMAI-LLM-04
# ---------------------------------------------------------------------------


def prompt_parts(
    pages: Sequence[tuple[str, str]],
    links: Sequence[str],
    industries: Sequence[Mapping[str, Any]],
    sizes: Sequence[Mapping[str, Any]],
) -> list[tuple[str, str]]:
    """The options, then each page's text as quoted data (`(path, text)`),
    then the LinkedIn company links the pages carry."""
    options = "\n".join(
        [
            "Industry options: " + ("; ".join(o["label"] for o in industries) or "none"),
            "Size options: " + ("; ".join(o["label"] for o in sizes) or "none"),
        ]
    )
    parts = [("options", options)]
    parts += [(f"page {path}", text[:TEXT_LIMIT]) for path, text in pages]
    if links:
        parts.append(("links", "\n".join(links)))
    return parts


# ---------------------------------------------------------------------------
# Values — FR-CRMAI-ENR-03, -04; plan §5.7
# ---------------------------------------------------------------------------


def _fold(text: Any) -> str:
    return " ".join(str(text or "").split()).casefold()


def _active(options: Iterable[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    return [o for o in options if o.get("is_active", True)]


def match_option(text: Any, options: Sequence[Mapping[str, Any]]) -> Mapping[str, Any] | None:
    """The active option whose label is the text, case-insensitively."""
    wanted = _fold(text)
    if not wanted:
        return None
    return next((o for o in _active(options) if _fold(o["label"]) == wanted), None)


_NUMBER: Final = re.compile(r"\d[\d,]*")


def _bounds(text: str) -> tuple[int, float] | None:
    """`51-200` → (51, 200); `1000+` → (1000, inf); `about 300` → (300, 300)."""
    numbers = [int(n.replace(",", "")) for n in _NUMBER.findall(text)]
    if not numbers:
        return None
    if "+" in text or len(numbers) == 1 and re.search(r"(?i)\bover|more than|above", text):
        return numbers[0], float("inf")
    return numbers[0], float(numbers[1] if len(numbers) > 1 else numbers[0])


def size_option(band: Any, options: Sequence[Mapping[str, Any]]) -> Mapping[str, Any] | None:
    """The workspace's size band for an employee band: its label, else the
    band whose range holds the band's lower bound."""
    found = match_option(band, options)
    if found or not isinstance(band, str):
        return found
    wanted = _bounds(band)
    if wanted is None:
        return None
    for option in _active(options):
        span = _bounds(option["label"])
        if span and span[0] <= wanted[0] <= span[1]:
            return option
    return None


_LINKEDIN_PATH: Final = re.compile(r"^/company/([A-Za-z0-9_.%-]+)/?")


def linkedin_company(url: Any) -> str | None:
    """A LinkedIn company page as `https://www.linkedin.com/company/<name>/`,
    or None for any other link (plan §5.7)."""
    if not isinstance(url, str) or not url.strip():
        return None
    text = url.strip()
    parsed = urlparse(text if "://" in text else f"https://{text}")
    host = (parsed.hostname or "").lower()
    if parsed.scheme not in ("http", "https"):
        return None
    if host != "linkedin.com" and not host.endswith(".linkedin.com"):
        return None
    found = _LINKEDIN_PATH.match(parsed.path)
    return f"https://www.linkedin.com/company/{found.group(1)}/" if found else None


def _text(value: Any) -> str | None:
    text = " ".join(str(value).split()) if isinstance(value, str) else ""
    return text or None


def values_from(
    answer: Mapping[str, Any],
    industries: Sequence[Mapping[str, Any]],
    sizes: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """The stored values from a validated, masked answer. An industry or a
    band that matches an option is stored as that option's label; one that
    does not is kept as written, enriched only."""
    out: dict[str, Any] = {k: _text(answer.get(k)) for k in VALUE_KEYS if k != "products"}
    industry = match_option(out["industry"], industries)
    if industry:
        out["industry"] = industry["label"]
    band = size_option(out["employee_band"], sizes)
    if band:
        out["employee_band"] = band["label"]
    out["linkedin"] = linkedin_company(out["linkedin"])
    out["products"] = [t for p in answer.get("products") or [] if (t := _text(p))][:8]
    return out


def usable(values: Mapping[str, Any]) -> bool:
    """FR-CRMAI-ENR-06 — anything at all was found."""
    return any(values.get(k) for k in VALUE_KEYS)


def promotions(
    values: Mapping[str, Any],
    industries: Sequence[Mapping[str, Any]],
    sizes: Sequence[Mapping[str, Any]],
) -> dict[str, tuple[Any, str]]:
    """Each company field an enriched value could fill: `{field: (value,
    label)}`, the value as `PATCH /crm/companies/{id}` takes it. An industry
    with no matching option offers nothing (FR-CRMAI-ENR-04)."""
    out: dict[str, tuple[Any, str]] = {}
    industry = match_option(values.get("industry"), industries)
    if industry:
        out["industry_id"] = (industry["id"], industry["label"])
    band = size_option(values.get("employee_band"), sizes)
    if band:
        out["size_id"] = (band["id"], band["label"])
    for key in ("city", "country", "description"):
        if values.get(key):
            out[key] = (values[key], values[key])
    if values.get("linkedin"):
        out["linkedin"] = (
            {"primary": {"url": values["linkedin"]}, "others": []},
            values["linkedin"],
        )
    return out


def _linkedin_url(value: Any) -> str | None:
    primary = value.get("primary") if isinstance(value, Mapping) else None
    url = primary.get("url") if isinstance(primary, Mapping) else None
    return url if isinstance(url, str) and url.strip() else None


def filled(field: str, current: Any) -> bool:
    """Whether the company's own field holds something."""
    if field == "linkedin":
        return _linkedin_url(current) is not None
    return current not in (None, "") and not (isinstance(current, str) and not current.strip())


def same(field: str, current: Any, value: Any) -> bool:
    """Whether the company's field already says what the enriched value does."""
    if field in ("industry_id", "size_id"):
        return current == value
    if field == "linkedin":
        mine = _linkedin_url(current)
        return bool(mine) and (linkedin_company(mine) or mine) == _linkedin_url(value)
    return _fold(current) == _fold(value)


def promotable(
    company: Mapping[str, Any],
    values: Mapping[str, Any],
    industries: Sequence[Mapping[str, Any]],
    sizes: Sequence[Mapping[str, Any]],
) -> dict[str, bool]:
    """FR-CRMAI-ENR-04 — for each field an enriched value maps to: true when
    the company's own field is empty, so one click fills it."""
    return {
        field: not filled(field, company.get(field))
        for field in promotions(values, industries, sizes)
    }


def conflicts(
    company: Mapping[str, Any],
    values: Mapping[str, Any],
    industries: Sequence[Mapping[str, Any]],
    sizes: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """FR-CRMAI-ENR-05 — the fields already filled with something else:
    `[{field, value, label, current}]`."""
    out = []
    for field, (value, label) in promotions(values, industries, sizes).items():
        current = company.get(field)
        if filled(field, current) and not same(field, current, value):
            out.append({"field": field, "value": value, "label": label, "current": current})
    return out


def items(
    company: Mapping[str, Any],
    found: Sequence[Mapping[str, Any]],
    *,
    website: str,
    model: str,
    generated_at: str,
    roles: Mapping[str, str],
    active: set[str],
) -> list[dict[str, Any]]:
    """One `enrichment_conflict` item per conflicting field, for the company
    owner (FR-CRMAI-REV-02), recording the field's value now for the
    staleness check (FR-CRMAI-REV-04). Its version is the site and the
    value, so a dismissed item is not made again until either changes."""
    source_key = f"enrichment:{company['id']}"
    owner = review_rules.owner_for(
        "enrichment_conflict", roles=roles, active=active, company_owner=company.get("owner_id")
    )
    out = []
    for conflict in found:
        field = conflict["field"]
        version = hashlib.sha256(
            json.dumps([website, field, conflict["value"]], sort_keys=True).encode()
        ).hexdigest()[:16]
        out.append(
            {
                "type": "enrichment_conflict",
                "owner_id": owner,
                "record_type": "company",
                "record_id": company["id"],
                "suggestion": {
                    "field": field,
                    "value": conflict["value"],
                    "label": conflict["label"],
                },
                "current_values": {field: conflict["current"]},
                "source_kind": SOURCE_KIND,
                "source_key": source_key,
                "source_version": version,
                "source_link": {"record_type": "company", "record_id": company["id"]},
                "dedupe_key": review_rules.dedupe_key(
                    SOURCE_KIND,
                    source_key,
                    "enrichment_conflict",
                    "company",
                    company["id"],
                    fingerprint=field,
                ),
                "model": model,
                "generated_at": generated_at,
            }
        )
    return out
