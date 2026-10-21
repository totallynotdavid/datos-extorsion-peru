"""Collect candidate extortion articles from Peruvian news outlets.

The Parquet table holds article metadata. Raw article HTML goes to
`raw_path(raw_dir, source, url)` as UTF-8, whatever charset the site served.
A source that fails stops and leaves its rows as they were.
"""

import contextlib
import hashlib
import html
import json
import os
import re
import sys
import unicodedata
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlsplit

import duckdb
import httpx

from dep.http import RobotsCheckedClient

USER_AGENT = "datos-extorsion-peru/0.1 (+https://github.com/totallynotdavid/datos-extorsion-peru)"
MIN_DELAY = 1.5
OUTPUT_NAME = "news_candidates.parquet"
LIMA = timezone(timedelta(hours=-5))
WP_PAGE_SIZE = 100


_BUSINESS = (
    r"(?:negocio|local|tienda|bodega|comercio|comerciante|empresa|restaurante"
    r"|mercado|botica|minimarket|taller|ferreteria|discoteca|bar|chifa|galeria"
    r"|establecimiento)"
)
_THREAT = r"(?:amenaz\w*|intimid\w*|advertencia|extors\w*|atentad\w*|disparo\w*)"


def _near(a: str, b: str, window: int = 150) -> str:
    return rf"\b{a}.{{0,{window}}}\b{b}|\b{b}.{{0,{window}}}\b{a}"


# Patterns run on lowercase text with accents removed.
TERMS: dict[str, str] = {
    "extors": r"extors",
    "cobro de cupos": r"\bcobros?\s+de\s+cupos?\b",
    "cupo": r"\bcupos?\b",
    "gota a gota": _near(r"gota\s+a\s+gota", _THREAT),
    "marcaje": r"\bmarcaje\b",
    "explosivo": _near(r"explosiv\w*", _BUSINESS),
}
_TERM_PATTERNS = {
    name: re.compile(pattern, re.DOTALL) for name, pattern in TERMS.items()
}


class SourceError(RuntimeError):
    """A source cannot continue. The message names the URL."""


@dataclass(frozen=True)
class Article:
    url: str
    published: date
    title: str
    region: str | None
    text: str
    html: str


@dataclass(frozen=True)
class Page:
    title: str
    published: date
    text: str


def match_terms(text: str) -> list[str]:
    plain = unicodedata.normalize("NFKD", text.lower())
    plain = "".join(c for c in plain if not unicodedata.combining(c))
    return [name for name, pattern in _TERM_PATTERNS.items() if pattern.search(plain)]


def raw_path(raw_dir: Path, source: str, url: str) -> Path:
    digest = hashlib.sha256(url.encode()).hexdigest()[:16]
    return raw_dir / source / f"{digest}.html"


def local_date(value: str) -> date:
    """The calendar date in Lima. Naive timestamps are already local."""
    moment = datetime.fromisoformat(value.strip())
    if moment.tzinfo is not None:
        moment = moment.astimezone(LIMA)
    return moment.date()


def month_range(start: date, end: date) -> Iterator[tuple[int, int]]:
    year, month = start.year, start.month
    while (year, month) <= (end.year, end.month):
        yield year, month
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)


def _clean(text: str) -> str:
    return " ".join(text.split())


class _PageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.paragraphs: list[str] = []
        self.words: list[str] = []
        self.json_ld: list[Any] = []
        self.og_title: str | None = None
        self.doc_title = ""
        self._paragraph: list[str] | None = None
        self._script: list[str] | None = None
        self._in_title = False
        self._skip = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if tag == "p":
            self._paragraph = []
        elif tag == "script":
            if (attributes.get("type") or "").lower() == "application/ld+json":
                self._script = []
            else:
                self._skip = True
        elif tag == "style":
            self._skip = True
        elif tag == "title":
            self._in_title = True
        elif tag == "meta" and attributes.get("property") == "og:title":
            self.og_title = attributes.get("content")

    def handle_endtag(self, tag: str) -> None:
        if tag == "p" and self._paragraph is not None:
            text = _clean("".join(self._paragraph))
            if text:
                self.paragraphs.append(text)
            self._paragraph = None
        elif tag == "script":
            if self._script is not None:
                with contextlib.suppress(ValueError):
                    self.json_ld.append(json.loads("".join(self._script), strict=False))
                self._script = None
            self._skip = False
        elif tag == "style":
            self._skip = False
        elif tag == "title":
            self._in_title = False

    def handle_data(self, data: str) -> None:
        if self._script is not None:
            self._script.append(data)
        elif self._skip:
            return
        elif self._in_title:
            self.doc_title += data
        else:
            self.words.append(data)
            if self._paragraph is not None:
                self._paragraph.append(data)


def _article_ld(blocks: list[Any]) -> dict[str, Any] | None:
    for block in blocks:
        for node in block if isinstance(block, list) else [block]:
            if not isinstance(node, dict):
                continue
            graph = node.get("@graph")
            for item in [node, *(graph if isinstance(graph, list) else [])]:
                kinds = item.get("@type") if isinstance(item, dict) else None
                kinds = kinds if isinstance(kinds, list) else [kinds]
                if any(
                    isinstance(k, str) and k.endswith("Article") for k in kinds
                ) and item.get("datePublished"):
                    return item  # type: ignore[no-any-return]
    return None


def parse_page(url: str, markup: str) -> Page:
    """Read title, publication date and text from a full article page.

    The date comes from JSON-LD `datePublished`. The text is JSON-LD
    `articleBody` when the page has one, and the `<p>` text otherwise.
    """
    parser = _PageParser()
    parser.feed(markup)
    ld = _article_ld(parser.json_ld)
    if ld is None:
        raise SourceError(f"{url}: no JSON-LD datePublished")
    try:
        published = local_date(str(ld["datePublished"]))
    except ValueError as exc:
        raise SourceError(
            f"{url}: unreadable datePublished {ld['datePublished']!r}"
        ) from exc
    title = _clean(parser.og_title or str(ld.get("headline") or "") or parser.doc_title)
    if not title:
        raise SourceError(f"{url}: no title")
    body = ld.get("articleBody")
    text = (
        _clean(body) if isinstance(body, str) and body else " ".join(parser.paragraphs)
    )
    return Page(title=title, published=published, text=text)


def parse_fragment(markup: str) -> str:
    parser = _PageParser()
    parser.feed(markup)
    return " ".join(parser.paragraphs) or _clean(" ".join(parser.words))


class _TagListing(HTMLParser):
    """Collects (href, listed date) for each story on a Correo tag page."""

    def __init__(self) -> None:
        super().__init__()
        self.items: list[tuple[str, date | None]] = []
        self._date: date | None = None
        self._in_date = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        classes = (dict(attrs).get("class") or "").split()
        if tag == "p" and "story-item__date" in classes:
            self._in_date = True
        elif tag == "a" and "story-item__title" in classes:
            href = dict(attrs).get("href")
            if href:
                self.items.append((href, self._date))
            self._date = None

    def handle_endtag(self, tag: str) -> None:
        if tag == "p":
            self._in_date = False

    def handle_data(self, data: str) -> None:
        if self._in_date:
            # The site does not zero-pad month and day: "2026-9-9".
            found = re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})", data.strip())
            try:
                self._date = date(*map(int, found.groups())) if found else None
            except ValueError:
                self._date = None


class Fetcher:
    """GET that turns any failure into a SourceError naming the URL."""

    def __init__(self, client: RobotsCheckedClient) -> None:
        self._client = client

    def get(self, url: str, params: dict[str, Any] | None = None) -> httpx.Response:
        response = self._send(url, params)
        if response is None:
            full = str(httpx.URL(url, params=params))
            raise SourceError(f"{full}: HTTP 404")
        return response

    def get_or_none(
        self, url: str, params: dict[str, Any] | None = None
    ) -> httpx.Response | None:
        """Return None on a 404. Any other failure still raises."""
        return self._send(url, params)

    def _send(
        self, url: str, params: dict[str, Any] | None = None
    ) -> httpx.Response | None:
        full = str(httpx.URL(url, params=params))
        try:
            response = self._client.get(full)
        except httpx.HTTPError as exc:
            raise SourceError(f"{full}: {exc}") from exc
        if response.status_code == 404:
            return None
        if response.status_code in (403, 429):
            raise SourceError(f"{full}: HTTP {response.status_code}, source stopped")
        if response.status_code != 200:
            raise SourceError(f"{full}: HTTP {response.status_code}")
        return response


def page_text(response: httpx.Response) -> str:
    """Decode with the charset the header names, then the one `<meta>` names."""
    charset = response.charset_encoding
    if charset is None:
        found = re.search(rb"charset=[\"']?([A-Za-z0-9_-]+)", response.content[:4096])
        charset = found.group(1).decode() if found else "utf-8"
    try:
        return response.content.decode(charset)
    except (LookupError, UnicodeDecodeError) as exc:
        raise SourceError(f"{response.url}: cannot decode as {charset}") from exc


def fetch_article(fetch: Fetcher, url: str, region: str | None) -> Article:
    response = fetch.get(url)
    markup = page_text(response)
    page = parse_page(url, markup)
    return Article(url, page.published, page.title, region, page.text, markup)


Collector = Callable[[Fetcher, str, date, date], Iterator[Article]]


def collect_exitosa(
    fetch: Fetcher, base: str, start: date, end: date
) -> Iterator[Article]:
    """Monthly sitemaps list every article. Only slugs with `extors` are fetched."""
    for year, month in month_range(start, end):
        sitemap = f"{base}/sitemapNoticias{year}{month:02d}.xml"
        response = fetch.get_or_none(sitemap)
        if response is None:
            print(f"warning: no sitemap at {sitemap}", file=sys.stderr)
            continue
        locs = re.findall(r"<loc>\s*([^<]+?)\s*</loc>", response.text)
        for url in dict.fromkeys(html.unescape(loc) for loc in locs):
            slug = urlsplit(url).path.rsplit("/", 1)[-1].lower()
            if "extors" in slug:
                yield fetch_article(fetch, url, region=None)


def collect_correo(
    fetch: Fetcher, base: str, start: date, end: date
) -> Iterator[Article]:
    """Walk the tag pages, newest first, until a 404 or a page older than start."""
    seen: set[str] = set()
    page = 1
    while True:
        url = f"{base}/noticias/extorsion/" + (f"{page}/" if page > 1 else "")
        response = fetch.get_or_none(url)
        if response is None:
            return
        listing = _TagListing()
        listing.feed(page_text(response))
        if not listing.items:
            return
        listed_dates = [d for _, d in listing.items if d is not None]
        if not listed_dates:
            raise SourceError(f"{url}: no story dates found on the tag page")
        for href, listed in listing.items:
            article_url = urljoin(url, href)
            if article_url in seen:
                continue
            seen.add(article_url)
            if listed is not None and not start <= listed <= end:
                continue
            found = re.search(r"/edicion/([^/]+)/", urlsplit(article_url).path)
            yield fetch_article(fetch, article_url, found.group(1) if found else None)
        if max(listed_dates) < start:
            return
        page += 1


def collect_wordpress(
    fetch: Fetcher, base: str, start: date, end: date
) -> Iterator[Article]:
    endpoint = f"{base}/wp-json/wp/v2/posts"
    # WordPress compares `after`/`before` against the post's datetime. Pad one
    # day so articles dated `start` and `end` count.
    after = (start - timedelta(days=1)).isoformat() + "T23:59:59"
    before = (end + timedelta(days=1)).isoformat() + "T00:00:00"
    page, total_pages = 1, 1
    while page <= total_pages:
        response = fetch.get(
            endpoint,
            params={
                "search": "extorsion",
                "after": after,
                "before": before,
                "per_page": WP_PAGE_SIZE,
                "page": page,
            },
        )
        if page == 1:
            try:
                total_pages = int(response.headers["X-WP-TotalPages"])
            except (KeyError, ValueError) as exc:
                raise SourceError(f"{response.url}: no X-WP-TotalPages") from exc
        try:
            posts = response.json()
        except ValueError as exc:
            raise SourceError(f"{response.url}: not JSON") from exc
        for post in posts:
            yield _wordpress_article(post, str(response.url))
        page += 1


def _wordpress_article(post: dict[str, Any], endpoint: str) -> Article:
    url = str(post.get("link") or f"{endpoint} (post {post.get('id')})")
    try:
        markup = post["content"]["rendered"]
        title = _clean(parse_fragment(post["title"]["rendered"]))
        published = local_date(post["date"])
    except (KeyError, TypeError, ValueError) as exc:
        raise SourceError(
            f"{url}: post has no readable date, title or content"
        ) from exc
    if not title:
        raise SourceError(f"{url}: no title")
    return Article(url, published, title, None, parse_fragment(markup), markup)


@dataclass(frozen=True)
class Source:
    name: str
    base_url: str
    collect: Collector


SOURCES: dict[str, Source] = {
    source.name: source
    for source in (
        Source("exitosa", "https://www.exitosanoticias.pe", collect_exitosa),
        Source("correo", "https://diariocorreo.pe", collect_correo),
        Source("eltiempo", "https://eltiempo.pe", collect_wordpress),
        Source("sinfronteras", "https://diariosinfronteras.com.pe", collect_wordpress),
    )
}


@dataclass(frozen=True)
class Row:
    url: str
    source: str
    published: date
    region: str | None
    matched: list[str]
    fetched_at: datetime


def collect_source(
    source: Source,
    fetch: Fetcher,
    base_url: str,
    start: date,
    end: date,
    raw_dir: Path,
) -> list[Row]:
    rows: dict[str, Row] = {}
    for article in source.collect(fetch, base_url, start, end):
        if not start <= article.published <= end:
            continue
        matched = match_terms(f"{article.title}\n{article.text}")
        if not matched:
            continue
        path = raw_path(raw_dir, source.name, article.url)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(article.html, encoding="utf-8")
        rows[article.url] = Row(
            article.url,
            source.name,
            article.published,
            article.region,
            matched,
            datetime.now(UTC),
        )
    return list(rows.values())


def write_table(
    path: Path, rows: list[Row], sources: list[str], start: date, end: date
) -> None:
    """Replace the rows of `sources` published in the range, keep the rest.

    A URL already stored under another date or source is replaced too, so the
    URL stays a key.
    """
    con = duckdb.connect()
    con.execute(
        "CREATE TABLE news (url VARCHAR, source VARCHAR, published DATE, "
        "region VARCHAR, matched VARCHAR[], fetched_at TIMESTAMPTZ)"
    )
    if path.exists():
        con.execute("INSERT INTO news SELECT * FROM read_parquet(?)", [str(path)])
    con.execute(
        "DELETE FROM news WHERE list_contains(?, source) AND published BETWEEN ? AND ?",
        [sources, start, end],
    )
    con.execute(
        "DELETE FROM news WHERE list_contains(?, url)", [[row.url for row in rows]]
    )
    if rows:
        con.executemany(
            "INSERT INTO news VALUES (?, ?, ?, ?, ?, ?)",
            [
                (
                    r.url,
                    r.source,
                    r.published,
                    r.region,
                    r.matched,
                    r.fetched_at,
                )
                for r in rows
            ],
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(path.name + ".partial")
    con.execute(
        "COPY (SELECT * FROM news ORDER BY published, url) TO ? (FORMAT PARQUET)",
        [str(partial)],
    )
    con.close()
    os.replace(partial, path)


def run(
    out_dir: Path,
    raw_dir: Path,
    start: date,
    end: date,
    sources: list[str],
    base_urls: dict[str, str],
) -> Path:
    """Collect the range from each source and merge it into the output table.

    Rows from sources that finish are written even when other sources fail.
    Raises RuntimeError with one line per failed source.
    """
    rows: list[Row] = []
    done: list[str] = []
    errors: list[str] = []
    with httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=60) as raw_client:
        fetch = Fetcher(RobotsCheckedClient(raw_client, USER_AGENT, MIN_DELAY))
        for name in sources:
            source = SOURCES[name]
            base = base_urls.get(name, source.base_url).rstrip("/")
            try:
                found = collect_source(source, fetch, base, start, end, raw_dir)
            except RuntimeError as exc:
                errors.append(f"{name}: {exc}")
                continue
            print(f"{name}: {len(found)} candidates")
            rows.extend(found)
            done.append(name)
    out_path = out_dir / OUTPUT_NAME
    if done:
        write_table(out_path, rows, done, start, end)
    if errors:
        raise RuntimeError("\n".join(errors))
    return out_path
