import subprocess
import sys
from datetime import date
from pathlib import Path
from typing import Any

import duckdb

DAY = date(2026, 10, 20)
OTHER_DAY = date(2026, 10, 21)

COLUMNS = (
    "url, source, published, event_date, district, province, department, "
    "victim_type, method, gang, confidence, model, extracted_at, has_event"
)


def row(
    url: str,
    published: date,
    event_date: date | None,
    district: str | None,
    victim_type: str | None,
    gang: str | None,
    confidence: str | None,
    has_event: bool = True,
) -> tuple[Any, ...]:
    return (
        url,
        "correo",
        published,
        event_date,
        district,
        "Lima",
        "Lima",
        victim_type,
        "cobro_de_cupo",
        gang,
        confidence,
        "claude-haiku-5-5",
        None,
        has_event,
    )


def write_events(path: Path, rows: list[tuple[Any, ...]]) -> None:
    con = duckdb.connect()
    con.execute(
        "CREATE TABLE events (url VARCHAR, source VARCHAR, published DATE, "
        "event_date DATE, district VARCHAR, province VARCHAR, department VARCHAR, "
        "victim_type VARCHAR, method VARCHAR, gang VARCHAR, confidence VARCHAR, "
        "model VARCHAR, extracted_at TIMESTAMPTZ, has_event BOOLEAN)"
    )
    con.executemany(
        f"INSERT INTO events ({COLUMNS}) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        rows,
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    con.execute("COPY events TO ? (FORMAT PARQUET)", [str(path)])
    con.close()


def run_dedup(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "dep", "dedup", *args],
        capture_output=True,
        text=True,
        check=False,
    )


def test_dedup_merges_one_event_across_articles(tmp_path: Path) -> None:
    events = tmp_path / "in" / "news_events.parquet"
    out_dir = tmp_path / "out"
    write_events(
        events,
        [
            # Two articles on one event: accents and case fold, earliest
            # published and highest confidence win.
            row(
                "https://a.test/1",
                date(2026, 10, 22),
                DAY,
                "LOS OLIVOS ",
                "negocio",
                "los malditos",
                "low",
            ),
            row(
                "https://b.test/2",
                date(2026, 10, 21),
                DAY,
                "Los Olivos",
                "negocio",
                "Los Malditos",
                "high",
            ),
            # Same date, district and type but different gangs: two events.
            row(
                "https://a.test/3",
                DAY,
                DAY,
                "Comas",
                "transporte",
                "Los Pulpos",
                "medium",
            ),
            row(
                "https://a.test/4",
                DAY,
                DAY,
                "Comas",
                "transporte",
                "La Jauría",
                "medium",
            ),
            # A null gang joins the only gang named for the group.
            row("https://a.test/5", DAY, DAY, "Ate", "vivienda", None, "medium"),
            row(
                "https://a.test/6",
                OTHER_DAY,
                DAY,
                "Ate",
                "vivienda",
                "Los Injertos del Cono",
                "low",
            ),
            # A null gang beside two named gangs could join either: it stays alone.
            row("https://a.test/7", DAY, DAY, "Rímac", "persona", "Los Pulpos", "low"),
            row("https://a.test/8", DAY, DAY, "Rimac", "persona", "La Jauría", "low"),
            row("https://a.test/9", DAY, DAY, "Rimac", "persona", None, "low"),
            # Different victim type: a different event.
            row("https://a.test/10", DAY, DAY, "Ate", "negocio", None, "low"),
            # Null date or district: never merged, flagged unplaced.
            row("https://a.test/11", DAY, None, "Lima", "otro", None, "low"),
            row("https://a.test/12", DAY, None, "Lima", "otro", None, "low"),
            row("https://a.test/13", DAY, DAY, None, "otro", None, "low"),
            row("https://a.test/14", DAY, DAY, None, "otro", None, "low"),
            # An article without an event is not an event.
            row("https://a.test/15", DAY, None, None, None, None, None, False),
        ],
    )

    result = run_dedup("--events", str(events), "--out-dir", str(out_dir))

    assert result.returncode == 0, result.stderr
    out_path = out_dir / "news_events_dedup.parquet"
    assert result.stdout.strip() == str(out_path)
    table = duckdb.execute(
        "SELECT district, victim_type, gang, confidence, n_sources, urls, "
        "published, unplaced FROM read_parquet(?) "
        "ORDER BY unplaced, district, victim_type, gang, urls",
        [str(out_path)],
    ).fetchall()
    assert table == [
        ("Ate", "negocio", None, "low", 1, ["https://a.test/10"], DAY, False),
        (
            "Ate",
            "vivienda",
            "Los Injertos del Cono",
            "medium",
            2,
            ["https://a.test/5", "https://a.test/6"],
            DAY,
            False,
        ),
        (
            "Comas",
            "transporte",
            "La Jauría",
            "medium",
            1,
            ["https://a.test/4"],
            DAY,
            False,
        ),
        (
            "Comas",
            "transporte",
            "Los Pulpos",
            "medium",
            1,
            ["https://a.test/3"],
            DAY,
            False,
        ),
        (
            "Los Olivos",
            "negocio",
            "Los Malditos",
            "high",
            2,
            ["https://a.test/1", "https://b.test/2"],
            date(2026, 10, 21),
            False,
        ),
        ("Rimac", "persona", "La Jauría", "low", 1, ["https://a.test/8"], DAY, False),
        ("Rimac", "persona", None, "low", 1, ["https://a.test/9"], DAY, False),
        ("Rímac", "persona", "Los Pulpos", "low", 1, ["https://a.test/7"], DAY, False),
        ("Lima", "otro", None, "low", 1, ["https://a.test/11"], DAY, True),
        ("Lima", "otro", None, "low", 1, ["https://a.test/12"], DAY, True),
        (None, "otro", None, "low", 1, ["https://a.test/13"], DAY, True),
        (None, "otro", None, "low", 1, ["https://a.test/14"], DAY, True),
    ]
    columns = {
        name
        for (name, *_rest) in duckdb.execute(
            "DESCRIBE SELECT * FROM read_parquet(?)", [str(out_path)]
        ).fetchall()
    }
    assert not columns & {"url", "source", "text", "title", "extracted_at"}


def test_dedup_replaces_the_previous_output(tmp_path: Path) -> None:
    events = tmp_path / "news_events.parquet"
    write_events(
        events,
        [row("https://a.test/1", DAY, DAY, "Ate", "negocio", None, "low")],
    )
    assert (
        run_dedup("--events", str(events), "--out-dir", str(tmp_path)).returncode == 0
    )
    write_events(
        events,
        [row("https://a.test/2", DAY, DAY, "Comas", "negocio", None, "low")],
    )

    result = run_dedup("--events", str(events), "--out-dir", str(tmp_path))

    assert result.returncode == 0, result.stderr
    urls = duckdb.execute(
        "SELECT urls FROM read_parquet(?)",
        [str(tmp_path / "news_events_dedup.parquet")],
    ).fetchall()
    assert urls == [(["https://a.test/2"],)]


def test_dedup_reports_a_missing_input(tmp_path: Path) -> None:
    result = run_dedup(
        "--events", str(tmp_path / "none.parquet"), "--out-dir", str(tmp_path)
    )

    assert result.returncode != 0
    assert "dep: error: cannot read events" in result.stderr
    assert not (tmp_path / "news_events_dedup.parquet").exists()
