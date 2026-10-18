"""Company enrichment's rules — spec 009 FR-CRMAI-ENR-01, -03 to -06, -08,
FR-CRMAI-LLM-04; Q-30, Q-32, Q-33; data-model §3.2.1, §3.5; plan §5.7.

Pure functions of `domain/crm/enrichment.py`: the site a company is read
from, which hosts are the same site, how an answer maps to the workspace's
industry and size options, which LinkedIn links are kept, and when a value
may be promoted or waits as an `enrichment_conflict` item instead.
"""

from __future__ import annotations

import pytest

from app.domain.crm import enrichment as rules
from app.domain.crm import review as review_rules

INDUSTRIES = [
    {"id": "i-mfg", "label": "Manufacturing", "is_active": True},
    {"id": "i-edu", "label": "Education", "is_active": True},
    {"id": "i-old", "label": "Textiles", "is_active": False},
]
SIZES = [
    {"id": f"s-{label}", "label": label, "is_active": True}
    for label in ("1-10", "11-50", "51-200", "201-1000", "1000+")
]
ANSWER = {
    "industry": "manufacturing",
    "sub_industry": "Machine tools",
    "employee_band": "51-200",
    "city": "Coimbatore",
    "country": "India",
    "description": "Acme makes precision machine tools.",
    "linkedin": "https://in.linkedin.com/company/acme-tools?trk=x",
    "products": ["Lathes", " CNC mills "],
}


def _values(**changes) -> dict:
    return rules.values_from({**ANSWER, **changes}, INDUSTRIES, SIZES)


class TestTheSite:
    """FR-CRMAI-ENR-01, -08; Q-32, Q-33."""

    @pytest.mark.parametrize(
        ("url", "site"),
        [
            ("https://www.Acme.in/about?x=1", "https://www.acme.in/"),
            ("acme.in", "https://acme.in/"),
            ("http://acme.in:8080/", "https://acme.in/"),
            ("https://user@acme.co.in/", "https://acme.co.in/"),
            ("  https://acme.in./  ", "https://acme.in/"),
        ],
    )
    def test_enrichment_the_websites_host_is_the_site(self, url, site):
        assert rules.site({"primary": {"url": url}}, "other.in") == site

    @pytest.mark.parametrize(
        "website", [None, {}, {"primary": None}, {"primary": {"url": "not a url"}}]
    )
    def test_enrichment_without_a_usable_website_the_primary_domain(self, website):
        assert rules.site(website, "acme.in") == "https://acme.in/"

    def test_enrichment_neither_is_no_site(self):
        assert rules.site(None, None) is None
        assert rules.site({"primary": {"url": "https://bad host/"}}, None) is None

    @pytest.mark.parametrize(
        ("a", "b", "same"),
        [
            ("acme.in", "www.acme.in", True),
            ("about.acme.co.in", "acme.co.in", True),
            ("acme.co.in", "other.co.in", False),
            ("acme.com.au", "shop.acme.com.au", True),
            ("acme.in", "acme.in.evil.com", False),
            ("acme.in", "evilacme.in", False),
            ("acme.in", "", False),
        ],
    )
    def test_enrichment_the_same_site(self, a, b, same):
        assert rules.same_site(a, b) is same


class TestValues:
    """FR-CRMAI-ENR-03, -04; plan §5.7."""

    def test_enrichment_the_industry_and_band_take_the_options_labels(self):
        values = _values()
        assert values["industry"] == "Manufacturing"
        assert values["employee_band"] == "51-200"
        assert values["products"] == ["Lathes", "CNC mills"]

    def test_enrichment_an_industry_with_no_option_is_kept_as_written(self):
        values = _values(industry="Aerospace")
        assert values["industry"] == "Aerospace"
        assert "industry_id" not in rules.promotions(values, INDUSTRIES, SIZES)

    def test_enrichment_an_inactive_option_does_not_match(self):
        assert rules.match_option("Textiles", INDUSTRIES) is None

    @pytest.mark.parametrize(
        ("band", "label"),
        [
            ("51-200", "51-200"),
            ("about 300 employees", "201-1000"),
            ("200-500", "51-200"),
            ("5000+", "1000+"),
            ("over 2000", "1000+"),
            ("a small team", None),
        ],
    )
    def test_enrichment_the_employee_band_maps_to_a_size(self, band, label):
        found = rules.size_option(band, SIZES)
        assert (found or {}).get("label") == label

    @pytest.mark.parametrize(
        ("url", "kept"),
        [
            (
                "https://in.linkedin.com/company/acme-tools?trk=x",
                "https://www.linkedin.com/company/acme-tools/",
            ),
            ("linkedin.com/company/acme", "https://www.linkedin.com/company/acme/"),
            ("https://www.linkedin.com/in/priya", None),
            ("https://linkedin.com.evil.in/company/acme", None),
            ("https://acme.in/linkedin", None),
            ("javascript:alert(1)", None),
        ],
    )
    def test_enrichment_linkedin_only_as_a_company_page(self, url, kept):
        assert rules.linkedin_company(url) == kept

    def test_enrichment_nothing_found_is_not_usable(self):
        empty = rules.values_from(
            {k: None for k in rules.VALUE_KEYS if k != "products"} | {"products": []},
            INDUSTRIES,
            SIZES,
        )
        assert rules.usable(empty) is False
        assert rules.usable(_values()) is True


class TestPromoteOrConflict:
    """FR-CRMAI-ENR-04, -05."""

    def test_enrichment_empty_fields_are_promotable(self):
        values = _values()
        assert rules.promotable({"id": "c1"}, values, INDUSTRIES, SIZES) == {
            "industry_id": True,
            "size_id": True,
            "city": True,
            "country": True,
            "description": True,
            "linkedin": True,
        }
        assert rules.conflicts({"id": "c1"}, values, INDUSTRIES, SIZES) == []

    def test_enrichment_a_promoted_value_is_what_the_patch_takes(self):
        found = rules.promotions(_values(), INDUSTRIES, SIZES)
        assert found["industry_id"] == ("i-mfg", "Manufacturing")
        assert found["size_id"] == ("s-51-200", "51-200")
        assert found["linkedin"][0] == {
            "primary": {"url": "https://www.linkedin.com/company/acme-tools/"},
            "others": [],
        }

    def test_enrichment_a_field_with_the_same_value_is_neither(self):
        company = {
            "id": "c1",
            "industry_id": "i-mfg",
            "city": " coimbatore ",
            "linkedin": {"primary": {"url": "https://linkedin.com/company/acme-tools"}},
        }
        values = _values()
        offered = rules.promotable(company, values, INDUSTRIES, SIZES)
        assert offered["industry_id"] is False and offered["city"] is False
        assert offered["linkedin"] is False
        assert {c["field"] for c in rules.conflicts(company, values, INDUSTRIES, SIZES)} == set()

    def test_enrichment_a_field_with_another_value_is_a_conflict(self):
        company = {"id": "c1", "owner_id": "u1", "industry_id": "i-edu", "city": "Chennai"}
        found = rules.conflicts(company, _values(), INDUSTRIES, SIZES)
        assert [(c["field"], c["value"], c["label"], c["current"]) for c in found] == [
            ("industry_id", "i-mfg", "Manufacturing", "i-edu"),
            ("city", "Coimbatore", "Coimbatore", "Chennai"),
        ]
        assert rules.promotable(company, _values(), INDUSTRIES, SIZES)["city"] is False

    def test_enrichment_conflict_items_go_to_the_company_owner_one_per_field(self):
        company = {"id": "c1", "owner_id": "u1", "industry_id": "i-edu", "city": "Chennai"}
        found = rules.conflicts(company, _values(), INDUSTRIES, SIZES)
        made = rules.items(
            company,
            found,
            website="https://acme.in/",
            model="claude-sonnet-5-5",
            generated_at="2026-10-18T06:00:00+00:00",
            roles={"u1": "member"},
            active={"u1"},
        )
        assert [i["owner_id"] for i in made] == ["u1", "u1"]
        assert made[1]["suggestion"] == {
            "field": "city",
            "value": "Coimbatore",
            "label": "Coimbatore",
        }
        assert made[1]["current_values"] == {"city": "Chennai"}
        assert made[1]["dedupe_key"] == review_rules.dedupe_key(
            "enrichment",
            "enrichment:c1",
            "enrichment_conflict",
            "company",
            "c1",
            fingerprint="city",
        )
        assert len({i["dedupe_key"] for i in made}) == 2

    def test_enrichment_a_viewer_owner_sends_the_item_to_the_admins(self):
        company = {"id": "c1", "owner_id": "u1", "city": "Chennai"}
        [item] = rules.items(
            company,
            rules.conflicts(company, _values(), INDUSTRIES, SIZES),
            website="https://acme.in/",
            model="m",
            generated_at="t",
            roles={"u1": "viewer"},
            active={"u1"},
        )
        assert item["owner_id"] is None

    def test_enrichment_the_items_version_follows_the_site_and_value(self):
        company = {"id": "c1", "owner_id": "u1", "city": "Chennai"}

        def version(site, city):
            found = rules.conflicts(company, _values(city=city), INDUSTRIES, SIZES)
            [item] = rules.items(
                company, found, website=site, model="m", generated_at="t", roles={}, active=set()
            )
            return item["source_version"]

        assert version("https://acme.in/", "Coimbatore") == version(
            "https://acme.in/", "Coimbatore"
        )
        assert version("https://acme.in/", "Coimbatore") != version("https://acme.in/", "Madurai")
        assert version("https://acme.in/", "Coimbatore") != version(
            "https://www.acme.in/", "Coimbatore"
        )
