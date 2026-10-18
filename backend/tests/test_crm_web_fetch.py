"""The website reader — spec 009 FR-CRMAI-ENR-02, NFR-CRMAI-01, -06; Q-30,
Q-31; plan CT-3; spec §15 "A site address resolving to a private network is
refused":

- private, loopback, link-local, shared, multicast and unspecified addresses
  are refused, IPv4 and IPv6, IPv4 inside IPv6 included, and so is a host
  with any such answer;
- the connection goes to the address that was checked, with the host as
  `Host` and SNI: a second, different DNS answer (rebinding) cannot move it;
- redirects are followed only within the same site, each hop checked again;
- at most 5 pages, 1 MB a page, 10 seconds a request;
- one structured log line per request, never an address or content.

Every test runs on `FakeWeb` (`tests/crm_ai_fakes.py`): no DNS, no network.
"""

from __future__ import annotations

import itertools
import logging
import socket

import pytest
from crm_ai_fakes import PUBLIC, FakeWeb

from app.services.crm import web_fetch

HOME = "https://acme.in/"
PAGE = "<html><head><title>Acme Tools</title></head><body><p>We make tools.</p></body></html>"


class _Stream:
    """httpcore's network stream, as `_Deadline` sees it."""

    def __init__(self, sock: socket.socket) -> None:
        self.sock = sock

    def get_extra_info(self, info: str):
        return self.sock if info == "socket" else None


@pytest.fixture
def web(monkeypatch) -> FakeWeb:
    return FakeWeb().install(monkeypatch)


def _read(site: str = HOME) -> web_fetch.Site:
    return web_fetch.read_site(site, job="enrichment", workspace="labs")


PRIVATE = [
    "10.1.2.3",
    "172.16.0.9",
    "192.168.1.1",
    "127.0.0.1",
    "169.254.169.254",
    "100.64.0.1",
    "0.0.0.0",
    "224.0.0.1",
    "255.255.255.255",
    "::1",
    "::",
    "fe80::1",
    "fc00::1",
    "fd12:3456::1",
    "ff02::1",
    "::ffff:127.0.0.1",
    "::ffff:10.0.0.1",
    "2002:7f00:1::1",
]


class TestPrivateAddressesAreRefused:
    """FR-CRMAI-ENR-02, Q-30."""

    @pytest.mark.parametrize("address", PRIVATE)
    def test_web_fetch_a_non_public_address_is_not_public(self, address):
        assert web_fetch.public(address) is False

    @pytest.mark.parametrize("address", ["fec0::1", "fec0:1234::5", "fef0::1", "100::1"])
    def test_web_fetch_an_ipv6_address_outside_global_unicast_is_not_public(self, address):
        """Site-local fec0::/10 counts as global to Python; only 2000::/3 is
        global unicast."""
        assert web_fetch.public(address) is False

    def test_web_fetch_a_host_resolving_to_a_site_local_address_is_unread(self, web):
        web.answer("acme.in", ["fec0::1"])
        web.page("acme.in", "/", PAGE)
        with pytest.raises(web_fetch.Unreadable):
            _read()
        assert web.requests == []

    @pytest.mark.parametrize("address", [PUBLIC, "2606:4700:4700::1111", "8.8.8.8"])
    def test_web_fetch_a_public_address_is_public(self, address):
        assert web_fetch.public(address) is True

    @pytest.mark.parametrize("address", PRIVATE)
    def test_web_fetch_a_host_resolving_to_it_is_refused_unread(self, web, address):
        web.answer("acme.in", [address])
        web.page("acme.in", "/", PAGE)
        with pytest.raises(web_fetch.Unreadable):
            _read()
        assert web.requests == []

    def test_web_fetch_one_private_answer_among_public_ones_refuses(self, web):
        web.answer("acme.in", [PUBLIC, "10.0.0.5"])
        web.page("acme.in", "/", PAGE)
        with pytest.raises(web_fetch.Unreadable):
            _read()
        assert web.requests == []

    def test_web_fetch_a_bare_address_is_refused(self, web):
        web.answer("127.0.0.1", [PUBLIC])
        with pytest.raises(web_fetch.Unreadable):
            _read("https://127.0.0.1/")
        assert web.requests == [] and web.lookups == []

    def test_web_fetch_a_host_that_does_not_resolve_is_unread(self, web):
        with pytest.raises(web_fetch.Unreadable):
            _read()
        assert web.requests == []


class TestTheCheckedAddressIsConnectedTo:
    """FR-CRMAI-ENR-02 — no second lookup, against DNS rebinding."""

    def test_web_fetch_connects_to_the_checked_address_with_host_and_sni(self, web):
        web.site("acme.in", {"/": PAGE})
        site = _read()
        assert site.pages == [("/", "Acme Tools\nWe make tools.")]
        assert web.requests == [
            {
                "address": PUBLIC,
                "host": "acme.in",
                "sni": "acme.in",
                "path": "/",
                "accept_encoding": "identity",
            }
        ]

    def test_web_fetch_a_rebinding_answer_cannot_move_the_connection(self, web):
        # The first lookup is public; any later one says loopback.
        web.answer("acme.in", [PUBLIC], ["127.0.0.1"])
        web.page("acme.in", "/", '<a href="/about">About</a>' + PAGE)
        web.page("acme.in", "/about", "<p>About us</p>")
        site = _read()
        # One lookup per request: the home page was read from the checked
        # address; the about page's own lookup was refused, never connected.
        assert web.lookups == ["acme.in", "acme.in"]
        assert [r["address"] for r in web.requests] == [PUBLIC]
        assert [path for path, _ in site.pages] == ["/"]


class TestRedirects:
    """FR-CRMAI-ENR-02 — same-site only, each hop checked again."""

    def test_web_fetch_a_same_site_redirect_is_followed_and_checked(self, web):
        web.site("acme.in", {})
        web.page("acme.in", "/", status=301, headers={"location": "https://www.acme.in/home"})
        web.site("www.acme.in", {"/home": PAGE})
        site = _read()
        assert web.lookups == ["acme.in", "www.acme.in"]
        assert [(r["host"], r["path"]) for r in web.requests] == [
            ("acme.in", "/"),
            ("www.acme.in", "/home"),
        ]
        assert site.pages[0][1].startswith("Acme Tools")

    def test_web_fetch_a_cross_site_redirect_is_refused(self, web):
        web.site("acme.in", {})
        web.page("acme.in", "/", status=302, headers={"location": "https://evil.example/"})
        web.site("evil.example", {"/": PAGE})
        with pytest.raises(web_fetch.Unreadable):
            _read()
        assert [r["host"] for r in web.requests] == ["acme.in"]
        assert "evil.example" not in web.lookups

    @pytest.mark.parametrize(
        ("home", "other"),
        [
            ("acme.co.za", "rival.co.za"),
            ("acme.github.io", "attacker.github.io"),
            ("shop.acme.com", "acme.com"),
        ],
    )
    def test_web_fetch_a_redirect_beside_the_site_under_a_shared_suffix_is_refused(
        self, web, home, other
    ):
        web.site(home, {})
        web.page(home, "/", status=302, headers={"location": f"https://{other}/"})
        web.site(other, {"/": PAGE})
        with pytest.raises(web_fetch.Unreadable):
            _read(f"https://{home}/")
        assert [r["host"] for r in web.requests] == [home]
        assert other not in web.lookups

    def test_web_fetch_a_link_beside_the_site_is_not_read(self, web):
        web.site("acme.co.za", {"/": '<a href="https://rival.co.za/about">x</a>' + PAGE})
        web.site("rival.co.za", {"/about": "<p>Rival</p>"})
        _read("https://acme.co.za/")
        assert [r["host"] for r in web.requests] == ["acme.co.za"]

    def test_web_fetch_a_redirect_to_a_private_host_on_the_site_is_refused(self, web):
        web.site("acme.in", {})
        web.page("acme.in", "/", status=307, headers={"location": "https://intranet.acme.in/"})
        web.answer("intranet.acme.in", ["10.0.0.7"])
        web.page("intranet.acme.in", "/", PAGE)
        with pytest.raises(web_fetch.Unreadable):
            _read()
        assert [r["host"] for r in web.requests] == ["acme.in"]

    @pytest.mark.parametrize(
        "location", ["https://acme.in:8443/", "ftp://acme.in/", "https://user@acme.in/"]
    )
    def test_web_fetch_another_port_scheme_or_a_user_is_refused(self, web, location):
        web.site("acme.in", {})
        web.page("acme.in", "/", status=302, headers={"location": location})
        with pytest.raises(web_fetch.Unreadable):
            _read()
        assert len(web.requests) == 1

    def test_web_fetch_too_many_redirects_read_nothing(self, web):
        web.site("acme.in", {})
        web.page("acme.in", "/", status=302, headers={"location": "/a"})
        web.page("acme.in", "/a", status=302, headers={"location": "/b"})
        web.page("acme.in", "/b", status=302, headers={"location": "/c"})
        web.page("acme.in", "/c", status=302, headers={"location": "/d"})
        web.page("acme.in", "/d", PAGE)
        with pytest.raises(web_fetch.Unreadable):
            _read()
        assert len(web.requests) == web_fetch.MAX_REDIRECTS + 1


class TestCaps:
    """Q-31 — 5 pages, 1 MB a page, 10 seconds a request."""

    def test_web_fetch_the_limits(self):
        assert (web_fetch.MAX_PAGES, web_fetch.MAX_BYTES, web_fetch.TIMEOUT_SECONDS) == (
            5,
            1_000_000,
            10.0,
        )

    def test_web_fetch_at_most_five_pages_and_only_the_linked_candidates(self, web):
        links = "".join(
            f'<a href="{p}">x</a>'
            for p in ("/about", "/about-us/", "/company", "/contact", "/careers", "/blog")
        )
        pages = {"/": links + PAGE}
        pages.update({p: f"<p>{p}</p>" for p in ("/about", "/about-us/", "/company", "/contact")})
        web.site("acme.in", pages)
        web.page("acme.in", "/careers", "<p>careers</p>")
        site = _read()
        assert [p for p, _ in site.pages] == ["/", "/about", "/about-us/", "/company", "/contact"]
        assert len(web.requests) == 5

    def test_web_fetch_a_page_not_linked_from_home_is_not_read(self, web):
        web.site("acme.in", {"/": PAGE, "/about": "<p>About</p>"})
        _read()
        assert [r["path"] for r in web.requests] == ["/"]

    def test_web_fetch_a_link_to_another_site_is_not_followed(self, web):
        web.site("acme.in", {"/": '<a href="https://other.in/about">x</a>' + PAGE})
        web.site("other.in", {"/about": "<p>Other</p>"})
        _read()
        assert [r["host"] for r in web.requests] == ["acme.in"]

    def test_web_fetch_asks_for_an_uncompressed_page(self, web):
        """A compressed body is inflated before the 1 MB cap is checked, so
        one small chunk could grow to tens of MB: none is asked for."""
        web.site("acme.in", {"/": PAGE})
        _read()
        assert [r["accept_encoding"] for r in web.requests] == ["identity"]

    @pytest.mark.parametrize("encoding", ["gzip", "deflate", "br"])
    def test_web_fetch_a_page_compressed_anyway_is_unread(self, web, encoding):
        web.answer("acme.in", [PUBLIC])
        web.page("acme.in", "/", headers={"content-encoding": encoding}, chunks=iter([b"x" * 10]))
        with pytest.raises(web_fetch.Unreadable):
            _read()
        assert web.pulled == 0

    def test_web_fetch_a_page_is_read_to_one_megabyte_at_most(self, web):
        chunk = b"<p>" + b"a" * 65_533
        web.answer("acme.in", [PUBLIC])
        web.page("acme.in", "/", chunks=itertools.repeat(chunk, 50))
        site = _read()
        assert web.pulled <= web_fetch.MAX_BYTES + len(chunk)
        assert len(site.pages[0][1]) <= web_fetch.MAX_BYTES

    def test_web_fetch_a_slow_page_is_dropped_after_ten_seconds(self, web):
        web.answer("acme.in", [PUBLIC])
        web.tick = 4.0
        web.page("acme.in", "/", chunks=itertools.repeat(b"<p>slow</p>", 1000))
        with pytest.raises(web_fetch.Unreadable):
            _read()
        assert web.pulled <= 4 * len(b"<p>slow</p>")

    def test_web_fetch_a_page_whose_answer_comes_after_ten_seconds_is_dropped(self, web):
        # A redirect that arrives late is not followed either.
        web.answer("acme.in", [PUBLIC])
        web.page("acme.in", "/", status=302, headers={"location": "/home"}, wait=10.5)
        web.page("acme.in", "/home", PAGE)
        with pytest.raises(web_fetch.Unreadable):
            _read()
        assert [r["path"] for r in web.requests] == ["/"]

    def test_web_fetch_the_deadline_shuts_a_trickling_connection(self):
        # httpx's timeouts count each read; the deadline ends the whole request.
        mine, theirs = socket.socketpair()
        stream = _Stream(mine)
        try:
            with web_fetch._Deadline(0.05) as watch:
                watch.trace("connection.connect_tcp.complete", {"return_value": stream})
                mine.settimeout(5)
                assert mine.recv(10) == b""
            assert watch.fired
        finally:
            mine.close()
            theirs.close()

    def test_web_fetch_the_deadline_ended_in_time_does_nothing(self):
        mine, theirs = socket.socketpair()
        try:
            with web_fetch._Deadline(5) as watch:
                watch.trace("connection.connect_tcp.complete", {"return_value": _Stream(mine)})
                theirs.sendall(b"ok")
                assert mine.recv(10) == b"ok"
            assert not watch.fired
        finally:
            mine.close()
            theirs.close()

    @pytest.mark.parametrize(
        ("status", "kind"), [(200, "application/pdf"), (500, "text/html"), (404, "text/html")]
    )
    def test_web_fetch_a_non_html_or_failed_page_is_unread(self, web, status, kind):
        web.answer("acme.in", [PUBLIC])
        web.page("acme.in", "/", PAGE, status=status, headers={"content-type": kind})
        with pytest.raises(web_fetch.Unreadable):
            _read()


class TestText:
    def test_web_fetch_scripts_and_styles_are_dropped_and_linkedin_kept(self, web):
        web.site(
            "acme.in",
            {
                "/": (
                    "<html><head><title>Acme</title><style>p{}</style></head><body>"
                    "<script>steal()</script><p>Tools &amp; dies</p>"
                    '<a href="https://www.linkedin.com/company/acme-tools/">LinkedIn</a>'
                    '<a href="https://www.linkedin.com/in/someone">Person</a>'
                    "</body></html>"
                )
            },
        )
        site = _read()
        assert site.pages == [("/", "Acme\nTools & dies\nLinkedIn\nPerson")]
        assert site.links == ["https://www.linkedin.com/company/acme-tools/"]


class TestLogging:
    """NFR-CRMAI-06 — one JSON line per request, no address, no content."""

    def test_web_fetch_one_line_per_page_with_no_content(self, web, caplog):
        caplog.set_level(logging.INFO)
        web.site("acme.in", {"/": '<a href="/about">x</a>' + PAGE, "/about": "<p>About us</p>"})
        _read()
        lines = [r.getMessage() for r in caplog.records if "crm_web_fetch" in r.getMessage()]
        assert len(lines) == 2
        for line in lines:
            assert '"status": "200"' in line and '"job": "enrichment"' in line
            for text in ("acme", PUBLIC, "tools", "About us"):
                assert text not in line.lower(), line
