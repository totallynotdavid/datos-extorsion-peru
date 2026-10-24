import json
import subprocess
import sys
import threading
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import duckdb
import pytest

SERVER_PAGE_SIZE = 2


def report(
    ubigeo: str, when: str, turno: str, cod: str, name: str
) -> dict[str, object]:
    millis = int(datetime.fromisoformat(when).replace(tzinfo=UTC).timestamp() * 1000)
    return {
        "ubigeo_hecho_delito": ubigeo,
        "fecha_hora_hecho": millis,
        "turno_hecho": turno,
        "modalidad_hecho": "EXTORSION",
        "cod_cpnp_hecho": cod,
        "comisaria_hecho": name,
        "ESTADO_COORD": "CERO_AMBAS",
    }


REPORTS = [
    report("150101", "2025-01-03T10:00", "mañana", "0101", "CPNP LIMA"),
    report("150101", "2025-01-09T22:00", "noche", "0101", "CPNP LIMA"),
    report("150101", "2025-01-20T23:00", "noche", "0101", "CPNP LIMA"),
    report("150101", "2025-01-21T01:00", "madrugada", "0101", "CPNP LIMA"),
    report("10202", "2025-01-05T15:00", "tarde", "0202", "CPNP ARAMANGO"),
    report("10202", "2025-02-05T15:00", "tarde", "0202", "CPNP ARAMANGO"),
]

# The `dep sidpol` table agrees with the layer except Aramango in February
# (1 in the layer, 2 here) and Lima in December 2024, a month the layer lacks.
CSV_ROWS = [
    ("010202", 2025, 1, 1),
    ("010202", 2025, 2, 2),
    ("150101", 2025, 1, 4),
    ("150101", 2024, 12, 9),
]


@dataclass
class Layer:
    reports: list[dict[str, object]] = field(default_factory=lambda: list(REPORTS))
    robots: tuple[int, str] = (403, "Invalid URL")
    fail_page: tuple[int, str] | None = None
    queries: list[dict[str, list[str]]] = field(default_factory=list)


@pytest.fixture
def layer() -> Iterator[tuple[Layer, str]]:
    state = Layer()

    class Handler(BaseHTTPRequestHandler):
        def _send(self, status: int, body: str) -> None:
            data = body.encode()
            self.send_response(status)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self) -> None:
            parts = urlsplit(self.path)
            if parts.path == "/robots.txt":
                self._send(*state.robots)
                return
            params = parse_qs(parts.query)
            state.queries.append(params)
            if params.get("returnCountOnly") == ["true"]:
                self._send(200, json.dumps({"count": len(state.reports)}))
                return
            offset = int(params["resultOffset"][0])
            if state.fail_page and offset > 0:
                self._send(*state.fail_page)
                return
            chunk = state.reports[offset : offset + SERVER_PAGE_SIZE]
            page = {
                "features": [{"attributes": r} for r in chunk],
                "exceededTransferLimit": offset + SERVER_PAGE_SIZE < len(state.reports),
            }
            self._send(200, json.dumps(page))

        def log_message(self, format: str, *args: object) -> None:
            pass

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield state, f"http://127.0.0.1:{httpd.server_port}/layer/query"
    httpd.shutdown()
    thread.join()


def write_csv_table(out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect()
    con.execute(
        "CREATE TABLE t (ubigeo VARCHAR, anio INTEGER, mes INTEGER, denuncias INTEGER)"
    )
    con.executemany("INSERT INTO t VALUES (?, ?, ?, ?)", CSV_ROWS)
    con.execute(
        "COPY t TO ? (FORMAT PARQUET)",
        [str(out_dir / "extorsion_distrito_mes.parquet")],
    )
    con.close()


def run_live(url: str, out_dir: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "dep",
            "sidpol-live",
            "--url",
            url,
            "--out-dir",
            str(out_dir),
            "--delay",
            "0",
        ],
        capture_output=True,
        text=True,
    )


def read_parquet(path: Path, columns: str) -> list[tuple[object, ...]]:
    return duckdb.execute(
        f"SELECT {columns} FROM read_parquet('{path}') ORDER BY ALL"
    ).fetchall()


@pytest.mark.parametrize("robots", [(403, "Invalid URL"), (404, "nope"), (500, "boom")])
def test_sidpol_live_writes_both_tables_and_lists_differences(
    layer: tuple[Layer, str], tmp_path: Path, robots: tuple[int, str]
) -> None:
    state, url = layer
    state.robots = robots
    out_dir = tmp_path / "out"
    write_csv_table(out_dir)

    result = run_live(url, out_dir)

    assert result.returncode == 0, result.stderr
    assert read_parquet(
        out_dir / "extorsion_live_distrito_mes_turno.parquet",
        "ubigeo, anio, mes, turno, denuncias",
    ) == [
        ("010202", 2025, 1, "tarde", 1),
        ("010202", 2025, 2, "tarde", 1),
        ("150101", 2025, 1, "madrugada", 1),
        ("150101", 2025, 1, "mañana", 1),
        ("150101", 2025, 1, "noche", 2),
    ]
    assert read_parquet(
        out_dir / "extorsion_live_sector_mes.parquet",
        "cod_cpnp, comisaria, anio, mes, denuncias",
    ) == [
        ("0101", "CPNP LIMA", 2025, 1, 4),
        ("0202", "CPNP ARAMANGO", 2025, 1, 1),
        ("0202", "CPNP ARAMANGO", 2025, 2, 1),
    ]
    assert f"layer reports {len(REPORTS)} rows" in result.stdout
    # Only Aramango February differs by more than 10%. December 2024 is a month
    # the layer does not cover, so it is not compared.
    assert "1 district-months differ" in result.stdout
    assert "010202    2025    2       1       2" in result.stdout
    assert "150101" not in result.stdout.split("differ")[1]

    sent = state.queries[-1]
    assert sent["where"] == ["subtipo_hecho = 'EXTORSION'"]
    assert sent["orderByFields"] == ["OBJECTID"]
    assert sent["returnGeometry"] == ["false"]
    requested = sent["outFields"][0].split(",")
    assert not {"direccion_hecho", "cuadra_hecho", "tipo_via_hecho"} & set(requested)
    assert not {"lat_hecho", "long_hecho"} & set(requested)
    assert len(state.queries) == 1 + 3  # count, then three pages of two


def test_sidpol_live_replaces_tables_and_skips_comparison_without_csv_table(
    layer: tuple[Layer, str], tmp_path: Path
) -> None:
    state, url = layer
    out_dir = tmp_path / "out"

    first = run_live(url, out_dir)
    assert first.returncode == 0, first.stderr
    assert "no comparison" in first.stdout

    state.reports = REPORTS[:1]
    second = run_live(url, out_dir)
    assert second.returncode == 0, second.stderr
    assert read_parquet(
        out_dir / "extorsion_live_distrito_mes_turno.parquet", "ubigeo, denuncias"
    ) == [("150101", 1)]


@pytest.mark.parametrize(
    ("robots", "fail_page", "stopped_at"),
    [
        pytest.param((429, "slow down"), None, "/robots.txt", id="robots-429"),
        pytest.param((404, ""), (429, "slow down"), "/layer/query", id="page-429"),
        pytest.param((404, ""), (503, "down"), "/layer/query", id="page-503"),
        pytest.param(
            (404, ""),
            (200, json.dumps({"error": {"code": 400, "message": "bad"}})),
            "/layer/query",
            id="arcgis-error-body",
        ),
    ],
)
def test_sidpol_live_stops_with_the_url_on_a_source_failure(
    layer: tuple[Layer, str],
    tmp_path: Path,
    robots: tuple[int, str],
    fail_page: tuple[int, str] | None,
    stopped_at: str,
) -> None:
    state, url = layer
    state.robots = robots
    state.fail_page = fail_page

    result = run_live(url, tmp_path / "out")

    assert result.returncode != 0
    assert stopped_at in result.stderr
    assert not (tmp_path / "out" / "extorsion_live_sector_mes.parquet").exists()
