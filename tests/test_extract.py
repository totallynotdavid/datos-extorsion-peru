import json
import os
import subprocess
import sys
import threading
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import duckdb
import pytest

from dep.news import raw_path


@dataclass
class Api:
    responses: list[tuple[int, dict[str, Any]]]
    requests: list[dict[str, Any]] = field(default_factory=list)
    base: str = ""


@pytest.fixture
def api() -> Iterator[Api]:
    state = Api([])

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            length = int(self.headers["Content-Length"] or 0)
            state.requests.append(json.loads(self.rfile.read(length)))
            status, body = state.responses.pop(0)
            encoded = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def log_message(self, format: str, *args: object) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    state.base = f"http://127.0.0.1:{server.server_port}"
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield state
    server.shutdown()
    server.server_close()


def tool_reply(events: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "id": "msg_test",
        "type": "message",
        "role": "assistant",
        "model": "claude-haiku-5-5",
        "content": [
            {
                "type": "tool_use",
                "id": "tool_test",
                "name": "extract_extortion_events",
                "input": {"events": events, "article_text": "must be dropped"},
            }
        ],
        "stop_reason": "tool_use",
        "stop_sequence": None,
        "usage": {"input_tokens": 1, "output_tokens": 1},
    }


def event(event_date: str) -> dict[str, Any]:
    return {
        "event_date": event_date,
        "district": "Los Olivos",
        "province": "Lima",
        "department": "Lima",
        "victim_type": "transporte",
        "method": "cobro_de_cupo",
        "gang": "Los Malditos",
        "confidence": "high",
    }


def write_inputs(tmp_path: Path) -> tuple[Path, Path, list[str]]:
    candidates = tmp_path / "out" / "news_candidates.parquet"
    raw_dir = tmp_path / "raw"
    urls = [
        "https://example.test/two-events",
        "https://example.test/no-events",
    ]
    duckdb.execute(
        "CREATE TABLE candidates (url VARCHAR, source VARCHAR, published DATE)"
    )
    duckdb.executemany(
        "INSERT INTO candidates VALUES (?, ?, ?)",
        [
            (urls[0], "correo", date(2026, 10, 21)),
            (urls[1], "exitosa", date(2026, 10, 22)),
        ],
    )
    candidates.parent.mkdir(parents=True)
    duckdb.execute(
        "COPY candidates TO ? (FORMAT PARQUET)",
        [str(candidates)],
    )
    duckdb.execute("DROP TABLE candidates")

    first = (
        '<html><head><script type="application/ld+json">'
        + json.dumps(
            {
                "@type": "NewsArticle",
                "headline": "Two events",
                "datePublished": "2026-10-21",
                "articleBody": "UNIQUE_ARTICLE_TEXT_PERSON_NAME_AND_QUOTE",
            }
        )
        + "</script></head></html>"
    )
    second = "<html><body><p>UNIQUE_ARTICLE_TEXT_WITH_NO_EVENT</p></body></html>"
    raw_path(raw_dir, "correo", urls[0]).parent.mkdir(parents=True)
    raw_path(raw_dir, "correo", urls[0]).write_text(first, encoding="utf-8")
    raw_path(raw_dir, "exitosa", urls[1]).parent.mkdir(parents=True)
    raw_path(raw_dir, "exitosa", urls[1]).write_text(second, encoding="utf-8")
    return candidates, raw_dir, urls


def run_extract(
    api: Api, candidates: Path, raw_dir: Path, out_dir: Path, *args: str
) -> subprocess.CompletedProcess[str]:
    env = {
        **os.environ,
        "ANTHROPIC_API_KEY": "test-key",
        "ANTHROPIC_BASE_URL": api.base,
    }
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "dep",
            "extract",
            "--candidates",
            str(candidates),
            "--raw-dir",
            str(raw_dir),
            "--out-dir",
            str(out_dir),
            *args,
        ],
        capture_output=True,
        text=True,
        env=env,
    )


def test_extracts_multiple_events_and_skips_processed_urls(
    api: Api, tmp_path: Path
) -> None:
    candidates, raw_dir, urls = write_inputs(tmp_path)
    api.responses = [
        (
            200,
            tool_reply(
                [
                    {
                        "event_date": "2026-10-20",
                        "district": "Los Olivos",
                        "province": "Lima",
                        "department": "Lima",
                        "victim_type": "transporte",
                        "method": "cobro_de_cupo",
                        "gang": "Los Malditos",
                        "confidence": "high",
                    },
                    {
                        "event_date": "2026-10-19",
                        "district": None,
                        "province": "Lima",
                        "department": "Lima",
                        "victim_type": "negocio",
                        "method": "amenaza",
                        "gang": None,
                        "confidence": "medium",
                    },
                ]
            ),
        ),
        (200, tool_reply([])),
    ]

    first = run_extract(api, candidates, raw_dir, tmp_path / "out")
    assert first.returncode == 0, first.stderr
    rows = duckdb.execute(
        "SELECT url, source, published, event_date, district, province, department, "
        "victim_type, method, gang, confidence, model, has_event "
        f"FROM read_parquet('{tmp_path / 'out' / 'news_events.parquet'}') "
        "WHERE has_event ORDER BY event_date"
    ).fetchall()
    assert rows == [
        (
            urls[0],
            "correo",
            date(2026, 10, 21),
            date(2026, 10, 19),
            None,
            "Lima",
            "Lima",
            "negocio",
            "amenaza",
            None,
            "medium",
            "claude-haiku-5-5",
            True,
        ),
        (
            urls[0],
            "correo",
            date(2026, 10, 21),
            date(2026, 10, 20),
            "Los Olivos",
            "Lima",
            "Lima",
            "transporte",
            "cobro_de_cupo",
            "Los Malditos",
            "high",
            "claude-haiku-5-5",
            True,
        ),
    ]
    assert duckdb.execute(
        "SELECT url, source, published, event_date, district, province, department, "
        "victim_type, method, gang, confidence, model, has_event "
        "FROM read_parquet(?) WHERE NOT has_event",
        [str(tmp_path / "out" / "news_events.parquet")],
    ).fetchall() == [
        (
            urls[1],
            "exitosa",
            date(2026, 10, 22),
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            "claude-haiku-5-5",
            False,
        )
    ]
    assert len(api.requests) == 2
    assert (
        b"UNIQUE_ARTICLE_TEXT_PERSON_NAME_AND_QUOTE"
        not in (tmp_path / "out" / "news_events.parquet").read_bytes()
    )

    api.responses = []
    skipped = run_extract(api, candidates, raw_dir, tmp_path / "out")
    assert skipped.returncode == 0, skipped.stderr
    assert len(api.requests) == 2


def test_zero_event_urls_count_done_and_limit_advances(
    api: Api, tmp_path: Path
) -> None:
    candidates, raw_dir, urls = write_inputs(tmp_path)
    api.responses = [(200, tool_reply([]))]

    first = run_extract(api, candidates, raw_dir, tmp_path / "out", "--limit", "1")
    assert first.returncode == 0, first.stderr
    assert duckdb.execute(
        "SELECT url, source, published, model, has_event FROM read_parquet(?)",
        [str(tmp_path / "out" / "news_events.parquet")],
    ).fetchall() == [(urls[0], "correo", date(2026, 10, 21), "claude-haiku-5-5", False)]

    api.responses = [(500, {"type": "error", "error": {"type": "api_error"}})]
    failed = run_extract(api, candidates, raw_dir, tmp_path / "out", "--limit", "1")
    assert failed.returncode != 0
    assert len(api.requests) == 2
    assert duckdb.execute(
        "SELECT url, has_event FROM read_parquet(?)",
        [str(tmp_path / "out" / "news_events.parquet")],
    ).fetchall() == [(urls[0], False)]

    api.responses = [(200, tool_reply([event("2026-10-22")]))]
    second = run_extract(api, candidates, raw_dir, tmp_path / "out", "--limit", "1")
    assert second.returncode == 0, second.stderr
    assert len(api.requests) == 3
    assert duckdb.execute(
        "SELECT url, has_event FROM read_parquet(?) ORDER BY url, has_event",
        [str(tmp_path / "out" / "news_events.parquet")],
    ).fetchall() == [(urls[1], True), (urls[0], False)]


def test_force_reprocesses_existing_url(api: Api, tmp_path: Path) -> None:
    candidates, raw_dir, _ = write_inputs(tmp_path)
    api.responses = [(200, tool_reply([])), (200, tool_reply([]))]
    first = run_extract(api, candidates, raw_dir, tmp_path / "out", "--limit", "1")
    assert first.returncode == 0, first.stderr
    forced = run_extract(
        api, candidates, raw_dir, tmp_path / "out", "--limit", "1", "--force"
    )
    assert forced.returncode == 0, forced.stderr
    assert len(api.requests) == 2


def test_invalid_reply_names_url_and_stops(api: Api, tmp_path: Path) -> None:
    candidates, raw_dir, urls = write_inputs(tmp_path)
    api.responses = [(200, tool_reply([{"method": "not-valid"}]))]

    result = run_extract(api, candidates, raw_dir, tmp_path / "out", "--limit", "1")

    assert result.returncode != 0
    assert urls[0] in result.stderr
    assert "invalid structured reply" in result.stderr


def test_invalid_event_date_names_url_and_stops(api: Api, tmp_path: Path) -> None:
    candidates, raw_dir, urls = write_inputs(tmp_path)
    invalid = event("relative-date")
    api.responses = [(200, tool_reply([invalid]))]

    result = run_extract(api, candidates, raw_dir, tmp_path / "out", "--limit", "1")

    assert result.returncode != 0
    assert urls[0] in result.stderr
    assert "event_date must be an ISO date or null" in result.stderr


def test_rate_limit_stops_without_retry(api: Api, tmp_path: Path) -> None:
    candidates, raw_dir, urls = write_inputs(tmp_path)
    api.responses = [(429, {"type": "error", "error": {"type": "rate_limit_error"}})]

    result = run_extract(api, candidates, raw_dir, tmp_path / "out")

    assert result.returncode != 0
    assert urls[0] in result.stderr
    assert "HTTP 429" in result.stderr
    assert len(api.requests) == 1


def test_server_error_keeps_successful_articles(api: Api, tmp_path: Path) -> None:
    candidates, raw_dir, urls = write_inputs(tmp_path)
    api.responses = [
        (200, tool_reply([event("2026-10-20")])),
        (500, {"type": "error", "error": {"type": "api_error"}}),
    ]

    result = run_extract(api, candidates, raw_dir, tmp_path / "out")

    assert result.returncode != 0
    assert urls[1] in result.stderr
    assert "HTTP 500" in result.stderr
    assert duckdb.execute(
        "SELECT url, has_event FROM read_parquet(?)",
        [str(tmp_path / "out" / "news_events.parquet")],
    ).fetchall() == [(urls[0], True)]


def test_article_text_is_capped_at_8000_characters(api: Api, tmp_path: Path) -> None:
    candidates, raw_dir, _ = write_inputs(tmp_path)
    raw_path(raw_dir, "correo", "https://example.test/two-events").write_text(
        '<html><head><script type="application/ld+json">'
        + json.dumps(
            {
                "@type": "NewsArticle",
                "headline": "Long article",
                "datePublished": "2026-10-21",
                "articleBody": "X" * 9_000,
            }
        )
        + "</script></head></html>",
        encoding="utf-8",
    )
    api.responses = [(200, tool_reply([]))]

    result = run_extract(api, candidates, raw_dir, tmp_path / "out", "--limit", "1")

    assert result.returncode == 0, result.stderr
    article_text = api.requests[0]["messages"][0]["content"].split(
        "Article text:\n", 1
    )[1]
    assert article_text == "X" * 8_000


def test_missing_key_is_loud(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    result = subprocess.run(
        [sys.executable, "-m", "dep", "extract", "--out-dir", str(tmp_path)],
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "ANTHROPIC_API_KEY is required" in result.stderr
