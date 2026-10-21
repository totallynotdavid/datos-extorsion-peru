import html
import itertools
import json
import socket
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import duckdb
import pytest

from dep.news import USER_AGENT, raw_path

WP_PAGE_SIZE = 2


@dataclass
class Reply:
    body: bytes
    status: int = 200
    content_type: str = "text/html; charset=utf-8"
    headers: dict[str, str] = field(default_factory=dict)


Route = Reply | Callable[[dict[str, list[str]]], Reply]


class Site:
    """One local server standing in for all four outlets, one path prefix each."""

    def __init__(self) -> None:
        self.routes: dict[str, Route] = {}
        self.requests: list[tuple[float, str, str]] = []
        self.base = ""

    def add(self, path: str, body: str, **kwargs: object) -> None:
        encoding = str(kwargs.pop("encoding", "utf-8"))
        self.routes[path] = Reply(body.encode(encoding), **kwargs)  # type: ignore[arg-type]

    def paths(self) -> list[str]:
        return [path for _, path, _ in self.requests]


def json_ld(kind: str, title: str, published: str | None, body: str | None) -> str:
    data: dict[str, str] = {"@type": kind, "headline": title}
    if published:
        data["datePublished"] = published
    if body:
        data["articleBody"] = body
    return f'<script type="application/ld+json">{json.dumps(data)}</script>'


def page(
    title: str,
    published: str | None,
    paragraphs: list[str],
    kind: str = "NewsArticle",
    body: str | None = None,
) -> str:
    paragraph_html = "".join(f"<p>{p}</p>" for p in paragraphs)
    return (
        "<html><head>"
        f'<meta property="og:title" content="{html.escape(title)}">'
        f"{json_ld(kind, title, published, body)}</head>"
        f"<body><p>menu</p>{paragraph_html}</body></html>"
    )


def sitemap(*urls: str) -> str:
    entries = "".join(f"<url><loc>{u}</loc></url>" for u in urls)
    return f'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{entries}</urlset>'


def listing(*items: tuple[str, str]) -> str:
    stories = "".join(
        '<div class="story-item"><p class="story-item__date">'
        f'{day}</p><a class="story-item__title" href="{href}">headline</a></div>'
        for href, day in items
    )
    return f"<html><body>{stories}</body></html>"


def wp_post(link: str, date: str, title: str, content: str) -> dict[str, object]:
    return {
        "link": link,
        "date": date,
        "title": {"rendered": title},
        "content": {"rendered": content},
    }


def wp_route(posts: list[dict[str, object]]) -> Callable[[dict[str, list[str]]], Reply]:
    def route(query: dict[str, list[str]]) -> Reply:
        assert query["search"] == ["extorsion"]
        # The collector asks for 100 posts a page. The fixture answers in pages
        # of WP_PAGE_SIZE to exercise the paging loop.
        assert query["per_page"] == ["100"]
        after, before = query["after"][0], query["before"][0]
        hits = [p for p in posts if after < str(p["date"]) < before]
        page_number = int(query["page"][0])
        pages = max(1, -(-len(hits) // WP_PAGE_SIZE))
        if page_number > pages:
            return Reply(b"{}", status=400, content_type="application/json")
        chunk = hits[(page_number - 1) * WP_PAGE_SIZE : page_number * WP_PAGE_SIZE]
        return Reply(
            json.dumps(chunk).encode(),
            content_type="application/json",
            headers={"X-WP-TotalPages": str(pages)},
        )

    return route


@pytest.fixture
def site() -> Iterator[Site]:
    state = Site()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            parts = urlsplit(self.path)
            state.requests.append(
                (time.monotonic(), parts.path, self.headers.get("User-Agent", ""))
            )
            route = state.routes.get(parts.path)
            if route is None:
                reply = Reply(b"not found", status=404)
            elif isinstance(route, Reply):
                reply = route
            else:
                reply = route(parse_qs(parts.query))
            self.send_response(reply.status)
            self.send_header("Content-Type", reply.content_type)
            self.send_header("Content-Length", str(len(reply.body)))
            for name, value in reply.headers.items():
                self.send_header(name, value)
            self.end_headers()
            self.wfile.write(reply.body)

        def log_message(self, format: str, *args: object) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    state.base = f"http://127.0.0.1:{server.server_port}"
    state.add("/robots.txt", "User-agent: *\nDisallow: /private/\n")
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield state
    server.shutdown()
    server.server_close()


def fill_site(site: Site) -> None:
    """September 2026 for Exitosa, Correo, El Tiempo and Sin Fronteras."""
    base = site.base
    exitosa = f"{base}/exitosa/crimen/extorsionadores-queman-combi-n1"
    exitosa_other = f"{base}/exitosa/deportes/clasico-termina-empate-n2"
    site.add("/exitosa/sitemapNoticias202609.xml", sitemap(exitosa, exitosa_other))
    site.add(
        "/exitosa/crimen/extorsionadores-queman-combi-n1",
        page(
            "Extorsionadores queman combi en Los Olivos: ñandú",
            "2026-09-28T06:11:00-05:00",
            ["Lo mataron por no pagar el cupo. Policía investigó la extorsión."],
        ),
        content_type="text/html; charset=ISO-8859-1",
        encoding="iso-8859-1",
    )
    site.add("/exitosa/deportes/clasico-termina-empate-n2", "never fetched")

    site.add(
        "/correo/noticias/extorsion/",
        listing(
            (
                "/correo/edicion/lima/sicarios-matan-a-comerciante-noticia/",
                "2026-9-29",
            ),
            ("/correo/edicion/la-libertad/lluvias-en-trujillo-noticia/", "2026-09-29"),
            (
                "/correo/edicion/lambayeque/cobran-cupos-en-chiclayo-noticia/",
                "2026-09-12",
            ),
            ("/correo/edicion/lima/agosto-antiguo-noticia/", "2026-08-31"),
        ),
    )
    site.add(
        "/correo/noticias/extorsion/2/",
        listing(("/correo/edicion/lima/julio-antiguo-noticia/", "2026-07-15")),
    )
    site.add(
        "/correo/edicion/lima/sicarios-matan-a-comerciante-noticia/",
        page(
            "Sicarios matan a comerciante",
            "2026-09-30T02:30:00.000Z",
            [],
            kind="ReportageNewsArticle",
            body="Policía investiga un caso de extorsión.",
        ),
    )
    site.add(
        "/correo/edicion/la-libertad/lluvias-en-trujillo-noticia/",
        page(
            "Lluvias en Trujillo",
            "2026-09-29T09:00:00.000Z",
            [],
            kind="ReportageNewsArticle",
            body="Se esperan lluvias ligeras durante la semana.",
        ),
    )
    site.add(
        "/correo/edicion/lambayeque/cobran-cupos-en-chiclayo-noticia/",
        page(
            "Cobran cupos en Chiclayo",
            "2026-09-12T15:00:00.000Z",
            [],
            kind="ReportageNewsArticle",
            body="Una banda exige el cobro de cupos a transportistas.",
        ),
    )

    site.routes["/eltiempo/wp-json/wp/v2/posts"] = wp_route(
        [
            wp_post(
                f"{base}/eltiempo/local/extorsionan-a-bodeguero/",
                "2026-09-10T08:00:00",
                "Extorsionan a bodeguero &amp; hijo",
                "<p>Le exigen dinero bajo amenaza.</p>",
            )
        ]
    )
    site.routes["/sinfronteras/wp-json/wp/v2/posts"] = wp_route(
        [
            wp_post(
                f"{base}/sinfronteras/policiales/extorsion-en-tumbes/",
                "2026-09-30T23:30:00",
                "Extorsión en Tumbes",
                "<p>Detienen a presunto extorsionador.</p>",
            )
        ]
    )


def run_news(
    site: Site, tmp_path: Path, *args: str
) -> subprocess.CompletedProcess[str]:
    urls = [
        f"{name}={site.base}/{name}"
        for name in ("exitosa", "correo", "eltiempo", "sinfronteras")
    ]
    command = [
        sys.executable,
        "-m",
        "dep",
        "news",
        "--out-dir",
        str(tmp_path / "out"),
        "--raw-dir",
        str(tmp_path / "raw"),
    ]
    for url in urls:
        command += ["--source-url", url]
    return subprocess.run([*command, *args], capture_output=True, text=True)


def read_rows(tmp_path: Path) -> list[tuple[object, ...]]:
    return duckdb.execute(
        "SELECT source, published, region, matched, url "
        f"FROM read_parquet('{tmp_path}/out/news_candidates.parquet') "
        "ORDER BY url"
    ).fetchall()


def test_collects_candidates_from_each_source(site: Site, tmp_path: Path) -> None:
    fill_site(site)

    result = run_news(site, tmp_path, "--from", "2026-09", "--to", "2026-09")

    assert result.returncode == 0, result.stderr
    base = site.base
    assert read_rows(tmp_path) == [
        (
            "correo",
            date(2026, 9, 12),
            "lambayeque",
            ["cobro de cupos", "cupo"],
            f"{base}/correo/edicion/lambayeque/cobran-cupos-en-chiclayo-noticia/",
        ),
        (
            "correo",
            date(2026, 9, 29),
            "lima",
            ["extors"],
            f"{base}/correo/edicion/lima/sicarios-matan-a-comerciante-noticia/",
        ),
        (
            "eltiempo",
            date(2026, 9, 10),
            None,
            ["extors"],
            f"{base}/eltiempo/local/extorsionan-a-bodeguero/",
        ),
        (
            "exitosa",
            date(2026, 9, 28),
            None,
            ["extors", "cupo"],
            f"{base}/exitosa/crimen/extorsionadores-queman-combi-n1",
        ),
        (
            "sinfronteras",
            date(2026, 9, 30),
            None,
            ["extors"],
            f"{base}/sinfronteras/policiales/extorsion-en-tumbes/",
        ),
    ]

    columns = duckdb.execute(
        f"DESCRIBE SELECT * FROM read_parquet('{tmp_path}/out/news_candidates.parquet')"
    ).fetchall()
    assert [c[0] for c in columns] == [
        "url",
        "source",
        "published",
        "region",
        "matched",
        "fetched_at",
    ]

    kept = f"{base}/exitosa/crimen/extorsionadores-queman-combi-n1"
    assert "ñandú" in raw_path(tmp_path / "raw", "exitosa", kept).read_text(
        encoding="utf-8"
    )
    dropped = f"{base}/correo/edicion/la-libertad/lluvias-en-trujillo-noticia/"
    assert not raw_path(tmp_path / "raw", "correo", dropped).exists()
    assert len(list((tmp_path / "raw").rglob("*.html"))) == 5

    assert "/exitosa/deportes/clasico-termina-empate-n2" not in site.paths()
    # A listed date outside the range is not fetched.
    assert "/correo/edicion/lima/agosto-antiguo-noticia/" not in site.paths()
    # A tag page whose newest story predates the range ends the walk.
    assert "/correo/noticias/extorsion/2/" in site.paths()
    assert "/correo/noticias/extorsion/3/" not in site.paths()
    assert {agent for _, _, agent in site.requests} == {USER_AGENT}
    times = [t for t, _, _ in site.requests]
    assert min(b - a for a, b in itertools.pairwise(times)) >= 1.45


def test_term_filter_over_paged_wordpress_results(site: Site, tmp_path: Path) -> None:
    cases = [
        ("Cobran cupos", "Cobro de cupos a mototaxistas.", ["cobro de cupos", "cupo"]),
        (
            "Préstamos 1",
            "Prestamistas gota a gota amenazan a deudores.",
            ["gota a gota"],
        ),
        ("Préstamos 2", "El gota a gota crece en Sullana.", None),
        ("Ataque", "Lanzan un explosivo contra una bodega.", ["explosivo"]),
        ("Hallazgo", "Hallan un explosivo en una cantera.", None),
        ("Reglaje", "Hacen marcaje a transportistas.", ["marcaje"]),
        ("Lluvias", "Se esperan lluvias ligeras en Piura.", None),
    ]
    site.routes["/eltiempo/wp-json/wp/v2/posts"] = wp_route(
        [
            wp_post(
                f"{site.base}/eltiempo/nota-{n}/",
                f"2026-09-0{n + 1}T10:00:00",
                title,
                f"<p>{body}</p>",
            )
            for n, (title, body, _) in enumerate(cases)
        ]
    )

    result = run_news(
        site, tmp_path, "--from", "2026-09", "--to", "2026-09", "--source", "eltiempo"
    )

    assert result.returncode == 0, result.stderr
    assert {row[4]: row[3] for row in read_rows(tmp_path)} == {
        f"{site.base}/eltiempo/nota-{n}/": matched
        for n, (_, _, matched) in enumerate(cases)
        if matched
    }
    assert site.paths().count("/eltiempo/wp-json/wp/v2/posts") == 4


def test_page_without_date_fails_loudly_and_other_sources_still_write(
    site: Site, tmp_path: Path
) -> None:
    fill_site(site)
    dateless = "/edicion/lima/sin-fecha-extorsion-noticia/"
    site.add(
        "/correo/noticias/extorsion/",
        listing((f"/correo{dateless}", "2026-09-20")),
    )
    site.add(
        f"/correo{dateless}",
        page("Sin fecha", None, [], kind="ReportageNewsArticle", body="extorsión"),
    )

    result = run_news(
        site,
        tmp_path,
        "--from",
        "2026-09",
        "--to",
        "2026-09",
        "--source",
        "correo",
        "--source",
        "exitosa",
    )

    assert result.returncode != 0
    assert f"{site.base}/correo{dateless}" in result.stderr
    assert {row[0] for row in read_rows(tmp_path)} == {"exitosa"}


def test_rate_limit_stops_the_source_and_keeps_stored_rows(
    site: Site, tmp_path: Path
) -> None:
    fill_site(site)
    first = run_news(site, tmp_path, "--from", "2026-09", "--to", "2026-09")
    assert first.returncode == 0, first.stderr
    stored = read_rows(tmp_path)

    second_article = f"{site.base}/exitosa/crimen/extorsion-en-lima-n3"
    site.add(
        "/exitosa/sitemapNoticias202609.xml",
        sitemap(
            f"{site.base}/exitosa/crimen/extorsionadores-queman-combi-n1",
            second_article,
        ),
    )
    site.add("/exitosa/crimen/extorsionadores-queman-combi-n1", "slow down", status=429)
    site.add("/exitosa/crimen/extorsion-en-lima-n3", "unreached")
    site.requests.clear()

    result = run_news(site, tmp_path, "--from", "2026-09", "--to", "2026-09")

    assert result.returncode != 0
    assert (
        f"{site.base}/exitosa/crimen/extorsionadores-queman-combi-n1" in result.stderr
    )
    assert "429" in result.stderr
    assert site.paths().count("/exitosa/crimen/extorsionadores-queman-combi-n1") == 1
    assert "/exitosa/crimen/extorsion-en-lima-n3" not in site.paths()
    assert read_rows(tmp_path) == stored


def test_forbidden_stops_the_source(site: Site, tmp_path: Path) -> None:
    fill_site(site)
    site.add("/correo/noticias/extorsion/", "denied", status=403)

    result = run_news(
        site, tmp_path, "--from", "2026-09", "--to", "2026-09", "--source", "correo"
    )

    assert result.returncode != 0
    assert f"{site.base}/correo/noticias/extorsion/" in result.stderr
    assert site.paths().count("/correo/noticias/extorsion/") == 1


def test_robots_txt_is_obeyed(site: Site, tmp_path: Path) -> None:
    fill_site(site)
    site.add("/robots.txt", "User-agent: *\nDisallow: /exitosa/crimen/\n")

    result = run_news(
        site, tmp_path, "--from", "2026-09", "--to", "2026-09", "--source", "exitosa"
    )

    assert result.returncode != 0
    assert "/exitosa/crimen/extorsionadores-queman-combi-n1" in result.stderr
    assert "/exitosa/crimen/extorsionadores-queman-combi-n1" not in site.paths()


def test_rerun_replaces_the_range_and_keeps_other_months(
    site: Site, tmp_path: Path
) -> None:
    fill_site(site)
    october = f"{site.base}/exitosa/policial/extorsionan-a-taxista-n9"
    site.add("/exitosa/sitemapNoticias202610.xml", sitemap(october))
    site.add(
        "/exitosa/policial/extorsionan-a-taxista-n9",
        page("Extorsionan a taxista", "2026-10-02T09:00:00-05:00", ["extorsión"]),
    )

    for _ in range(2):
        result = run_news(site, tmp_path, "--from", "2026-09", "--to", "2026-09")
        assert result.returncode == 0, result.stderr
    assert len(read_rows(tmp_path)) == 5

    result = run_news(
        site, tmp_path, "--from", "2026-10", "--to", "2026-10", "--source", "exitosa"
    )
    assert result.returncode == 0, result.stderr
    assert len(read_rows(tmp_path)) == 6

    # The September sitemap now lists no article and the Correo tag page lost
    # one. Rerunning September drops their rows, and October stays.
    site.add("/exitosa/sitemapNoticias202609.xml", sitemap())
    site.add(
        "/correo/noticias/extorsion/",
        listing(
            (
                "/correo/edicion/lima/sicarios-matan-a-comerciante-noticia/",
                "2026-09-29",
            ),
        ),
    )
    result = run_news(site, tmp_path, "--from", "2026-09", "--to", "2026-09")
    assert result.returncode == 0, result.stderr
    rows = read_rows(tmp_path)
    assert [r[0] for r in rows] == ["correo", "eltiempo", "exitosa", "sinfronteras"]
    assert len({r[4] for r in rows}) == len(rows)


def test_month_range_is_validated(site: Site, tmp_path: Path) -> None:
    result = run_news(site, tmp_path, "--from", "2026-10", "--to", "2026-09")

    assert result.returncode != 0
    assert "--from is after --to" in result.stderr
    assert site.requests == []


def test_tag_page_without_story_dates_stops_before_fetching_articles(
    site: Site, tmp_path: Path
) -> None:
    fill_site(site)
    site.add(
        "/correo/noticias/extorsion/",
        listing(("/correo/edicion/lima/sicarios-matan-a-comerciante-noticia/", "")),
    )

    result = run_news(
        site, tmp_path, "--from", "2026-09", "--to", "2026-09", "--source", "correo"
    )

    assert result.returncode != 0
    assert f"{site.base}/correo/noticias/extorsion/" in result.stderr
    assert "/correo/edicion/lima/sicarios-matan-a-comerciante-noticia/" not in (
        site.paths()
    )


def test_repeated_source_is_collected_once(site: Site, tmp_path: Path) -> None:
    fill_site(site)

    result = run_news(
        site,
        tmp_path,
        "--from",
        "2026-09",
        "--to",
        "2026-09",
        "--source",
        "exitosa",
        "--source",
        "exitosa",
    )

    assert result.returncode == 0, result.stderr
    assert site.paths().count("/exitosa/sitemapNoticias202609.xml") == 1


def test_unreachable_host_error_names_robots_txt(tmp_path: Path) -> None:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        closed = probe.getsockname()[1]
    command = [
        sys.executable,
        "-m",
        "dep",
        "news",
        "--out-dir",
        str(tmp_path / "out"),
        "--raw-dir",
        str(tmp_path / "raw"),
        "--from",
        "2026-09",
        "--to",
        "2026-09",
        "--source",
        "exitosa",
        "--source-url",
        f"exitosa=http://127.0.0.1:{closed}",
    ]

    result = subprocess.run(command, capture_output=True, text=True)

    assert result.returncode != 0
    assert f"http://127.0.0.1:{closed}/robots.txt" in result.stderr
    assert "sitemapNoticias" not in result.stderr
