"""Reading a company's own website, safely — spec 009 FR-CRMAI-ENR-02,
NFR-CRMAI-01, -06; P-3; Q-30, Q-31; plan §2.1, CT-3.

The worker fetches addresses people typed, so it could be pointed at
internal services (OWASP: SSRF). Every request here, the first and each
redirect hop, is checked before it is sent:

1. the scheme is `http` or `https`, on the default port, with no user;
2. the host is on the company's own site (its host or one under it,
   `domain/crm/enrichment.same_site`);
3. the host is resolved once (`resolve`), and every address it resolves to
   must be public: private, loopback, link-local, multicast, reserved,
   unspecified and shared addresses are refused, IPv4 and IPv6, including
   IPv4 inside IPv6, and any IPv6 address outside 2000::/3 (site-local
   fec0::/10 included);
4. the connection goes to the address that was checked, never to a second
   lookup (against DNS rebinding), with the original host as `Host` and as
   the TLS SNI name (httpx's `sni_hostname` extension), so the certificate is
   still checked against the host;
5. redirects are followed here, not by httpx, each hop checked again from
   step 1; one to another site is refused.

At most 5 pages a company (the home page, then its about, company and
contact pages when the home page links to them, plan §5.7), 1 MB a page and
10 seconds a request (Q-31). Pages are turned into text with the standard
library's `html.parser` and returned in memory only: never stored, logged,
queued or put in an error (P-3). Each request logs one JSON line with the
job, the workspace, the status, the latency and the size, never an address
or content (NFR-CRMAI-06). `worker-crm-ai` alone runs this.
"""

from __future__ import annotations

import ipaddress
import json
import logging
import socket
import threading
import time
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Any, Final
from urllib.parse import urljoin, urlsplit

import httpx

from app.domain.crm import enrichment as rules

logger = logging.getLogger("nldw-task-master")

#: Q-31.
MAX_PAGES: Final = 5
MAX_BYTES: Final = 1_000_000
TIMEOUT_SECONDS: Final = 10.0
#: Redirect hops followed for one page.
MAX_REDIRECTS: Final = 3
#: The default port of each scheme allowed; any other port is refused.
PORTS: Final = {"http": 80, "https": 443}
#: What the reader asks for and accepts.
HTML_TYPES: Final = ("text/html", "application/xhtml+xml")
#: No compression: a compressed body is inflated chunk by chunk with no
#: output limit, so one small chunk could grow far past `MAX_BYTES` before
#: the cap is checked. A body sent compressed anyway is not read.
HEADERS: Final = {
    "Accept": "text/html",
    "Accept-Encoding": "identity",
    "User-Agent": "NunnariCRM/1.0 (company enrichment)",
}
#: The global unicast IPv6 range; any other IPv6 address is refused.
_GLOBAL_V6: Final = ipaddress.ip_network("2000::/3")
REDIRECTS: Final = (301, 302, 303, 307, 308)


class Refused(Exception):
    """The address is not one the worker may read (Q-30). Its message names
    the rule, never the address."""


class Unreadable(Exception):
    """Nothing could be read from the site. Its message names the kind of
    failure, never the address or content."""


@dataclass
class Site:
    """What was read: each page's path and text, and the LinkedIn company
    links the pages carry. In memory only."""

    pages: list[tuple[str, str]] = field(default_factory=list)
    links: list[str] = field(default_factory=list)


def _clock() -> float:
    """The monotonic clock; tests move it."""
    return time.monotonic()


def resolve(host: str, port: int) -> list[str]:
    """Every address `host` resolves to, in order, each once."""
    found: list[str] = []
    for *_, sockaddr in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM):
        address = str(sockaddr[0]).split("%", 1)[0]
        if address not in found:
            found.append(address)
    return found


def _client() -> httpx.Client:
    """No redirects followed, no proxy taken from the environment, and no
    connection kept for the next request, so each request opens its own
    (with its own host as SNI) and `_Deadline` can end it; tests replace it
    with a mock transport."""
    return httpx.Client(
        follow_redirects=False,
        trust_env=False,
        limits=httpx.Limits(max_keepalive_connections=0),
    )


class _Deadline:
    """Ends a request when its 10 seconds are up (Q-31). httpx's timeouts
    count each step and each read on its own, so a server answering a
    little at a time could otherwise hold the worker far longer. The
    connection is caught as it opens (httpcore's `trace` extension) and its
    socket shut down at the deadline, which fails the read in progress.
    Connecting and the TLS handshake each have half the time (httpx's own
    connect timeout), so they end in time too."""

    def __init__(self, seconds: float) -> None:
        self.fired = False
        self._stream: Any = None
        self._lock = threading.Lock()
        self._timer = threading.Timer(seconds, self._fire)
        self._timer.daemon = True

    def trace(self, event: str, info: dict[str, Any]) -> None:
        if event in ("connection.connect_tcp.complete", "connection.start_tls.complete"):
            with self._lock:
                self._stream = info.get("return_value")
                fired = self.fired
            if fired:
                self._fire()

    def _fire(self) -> None:
        with self._lock:
            self.fired = True
            stream = self._stream
        sock = stream.get_extra_info("socket") if stream is not None else None
        if sock is not None:
            try:
                # The plain socket's shutdown, also under TLS: it wakes a
                # blocked read; a socket already closed refuses it.
                socket.socket.shutdown(sock, socket.SHUT_RDWR)
            except OSError:
                pass

    def __enter__(self) -> _Deadline:
        self._timer.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self._timer.cancel()
        with self._lock:
            self._stream = None


def public(address: str) -> bool:
    """Whether the worker may connect to `address`: a global unicast
    address, also when an IPv6 address carries an IPv4 one."""
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address):
        inner = ip.ipv4_mapped or ip.sixtofour or (ip.teredo[1] if ip.teredo else None)
        if inner is not None and not public(str(inner)):
            return False
        # Site-local fec0::/10 counts as global to Python; only 2000::/3 is
        # global unicast. (An IPv4-mapped address was judged above.)
        if inner is None and (ip.is_site_local or ip not in _GLOBAL_V6):
            return False
    return bool(
        ip.is_global
        and not ip.is_multicast
        and not ip.is_private
        and not ip.is_loopback
        and not ip.is_link_local
        and not ip.is_reserved
        and not ip.is_unspecified
    )


def _checked(url: str, home: str) -> tuple[httpx.URL, str, str]:
    """`(the URL to connect to, the host, its path)` for a URL on the site
    of `home`, connecting to its one checked address; else `Refused`."""
    parts = urlsplit(url)
    scheme = parts.scheme.lower()
    if scheme not in PORTS:
        raise Refused("Only http and https are read.")
    if parts.username or parts.password:
        raise Refused("An address with a user name is not read.")
    try:
        port = parts.port or PORTS[scheme]
    except ValueError:
        raise Refused("The address has no valid port.") from None
    if port != PORTS[scheme]:
        raise Refused("Only the default port is read.")
    host = (parts.hostname or "").lower().rstrip(".")
    if not host or not rules.same_site(host, home):
        raise Refused("Only the company's own site is read.")
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        raise Refused("A bare address is not read.")
    try:
        addresses = resolve(host, port)
    except OSError:
        raise Refused("The host did not resolve.") from None
    if not addresses or not all(public(a) for a in addresses):
        raise Refused("The host is not a public address.")
    path = parts.path or "/"
    query = {"query": parts.query.encode()} if parts.query else {}
    target = httpx.URL(scheme=scheme, host=addresses[0], path=path, **query)
    return target, host, path


def _log(job: str, workspace: str, status: str, started: float, size: int) -> None:
    logger.info(
        json.dumps(
            {
                "event": "crm_web_fetch",
                "job": job,
                "workspace": workspace,
                "status": status,
                "latency_ms": int((_clock() - started) * 1000),
                "bytes": size,
            }
        )
    )


def _body(response: httpx.Response, deadline: float) -> bytes:
    """At most `MAX_BYTES` of the body, read before the deadline."""
    chunks: list[bytes] = []
    size = 0
    for chunk in response.iter_bytes():
        if _clock() > deadline:
            raise TimeoutError
        chunks.append(chunk)
        size += len(chunk)
        if size >= MAX_BYTES:
            break
    return b"".join(chunks)[:MAX_BYTES]


def fetch(
    client: httpx.Client, url: str, home: str, *, job: str, workspace: str
) -> tuple[str, str] | None:
    """One page as `(final URL, HTML)`, following same-site redirects, or
    None when it cannot be read. `Refused` when an address is refused."""
    for _ in range(MAX_REDIRECTS + 1):
        target, host, _path = _checked(url, home)
        started = _clock()
        deadline = started + TIMEOUT_SECONDS
        with _Deadline(TIMEOUT_SECONDS) as watch:
            request = client.build_request(
                "GET",
                target,
                headers={**HEADERS, "Host": host},
                extensions={
                    "sni_hostname": host,
                    "timeout": httpx.Timeout(
                        TIMEOUT_SECONDS, connect=TIMEOUT_SECONDS / 2
                    ).as_dict(),
                    "trace": watch.trace,
                },
            )
            try:
                response = client.send(request, stream=True)
            except httpx.HTTPError as exc:
                failed = "TimeoutError" if watch.fired else type(exc).__name__
                _log(job, workspace, failed, started, 0)
                return None
            try:
                if _clock() > deadline:
                    # The answer came after the deadline.
                    _log(job, workspace, "TimeoutError", started, 0)
                    return None
                if response.status_code in REDIRECTS:
                    _log(job, workspace, str(response.status_code), started, 0)
                    location = response.headers.get("location")
                    if not location:
                        return None
                    url = urljoin(f"{target.scheme}://{host}{_path}", location)
                    continue
                kind = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
                encoding = response.headers.get("content-encoding", "").strip().lower()
                if (
                    response.status_code != 200
                    or kind not in HTML_TYPES
                    or encoding not in ("", "identity")
                ):
                    _log(job, workspace, str(response.status_code), started, 0)
                    return None
                try:
                    body = _body(response, deadline)
                except (TimeoutError, httpx.HTTPError) as exc:
                    failed = "TimeoutError" if watch.fired else type(exc).__name__
                    _log(job, workspace, failed, started, 0)
                    return None
                _log(job, workspace, "200", started, len(body))
                text = body.decode(response.encoding or "utf-8", errors="replace")
                return f"{target.scheme}://{host}{_path}", text
            finally:
                response.close()
    return None


class _Text(HTMLParser):
    """A page's visible text and its links, nothing else."""

    SKIP = frozenset({"script", "style", "noscript", "template", "svg", "head", "iframe"})

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.links: list[str] = []
        self._skipping = 0
        self._title = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self.SKIP:
            self._skipping += 1
        if tag == "title":
            self._title = True
        if tag == "a":
            href = dict(attrs).get("href")
            if href:
                self.links.append(href)

    def handle_endtag(self, tag: str) -> None:
        if tag in self.SKIP and self._skipping:
            self._skipping -= 1
        if tag == "title":
            self._title = False

    def handle_data(self, data: str) -> None:
        if (not self._skipping or self._title) and data.strip():
            self.parts.append(" ".join(data.split()))


def page_text(html: str) -> tuple[str, list[str]]:
    """`(text, links)` of one page."""
    parser = _Text()
    try:
        parser.feed(html)
        parser.close()
    except Exception:  # noqa: BLE001 - a broken page gives what was read
        pass
    return "\n".join(parser.parts), parser.links


def read_site(website: str, *, job: str, workspace: str) -> Site:
    """The company's own site, at most `MAX_PAGES` pages. `Unreadable` when
    the home page cannot be read, its address refused included."""
    home = urlsplit(website).hostname or ""
    out = Site()
    with _client() as client:
        try:
            first = fetch(client, website, home, job=job, workspace=workspace)
        except Refused as refused:
            _log(job, workspace, "refused", _clock(), 0)
            raise Unreadable("The site's address is not read.") from refused
        if first is None:
            raise Unreadable("The site did not answer.")
        final, html = first
        text, hrefs = page_text(html)
        out.pages.append(("/", text))
        more = _candidates(final, hrefs, home)
        for url in more[: MAX_PAGES - 1]:
            try:
                page = fetch(client, url, home, job=job, workspace=workspace)
            except Refused:
                _log(job, workspace, "refused", _clock(), 0)
                continue
            if page is None:
                continue
            text, links = page_text(page[1])
            out.pages.append((urlsplit(page[0]).path or "/", text))
            hrefs += links
        out.links = _linkedin(hrefs)
    return out


def _candidates(base: str, hrefs: list[str], home: str) -> list[str]:
    """The about, company and contact pages the home page links to, on the
    same site, each once, in `CANDIDATE_PATHS` order."""
    found: dict[str, str] = {}
    for href in hrefs:
        url = urljoin(base, href.strip())
        parts = urlsplit(url)
        path = (parts.path or "/").rstrip("/").lower() or "/"
        host = (parts.hostname or "").lower()
        if path in rules.CANDIDATE_PATHS and path not in found and rules.same_site(host, home):
            found[path] = f"{parts.scheme}://{parts.netloc}{parts.path}"
    return [found[p] for p in rules.CANDIDATE_PATHS if p in found]


def _linkedin(hrefs: list[str]) -> list[str]:
    """The LinkedIn company pages linked, each once."""
    out: list[str] = []
    for href in hrefs:
        page = rules.linkedin_company(href)
        if page and page not in out:
            out.append(page)
    return out[:5]
