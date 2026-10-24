import json
import subprocess
import sys
import threading
from collections.abc import Iterator
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import duckdb
import pytest

from dep.inpe import age_band, offence_group

SERVER_PAGE_SIZE = 3
NAME = "NAME-SENTINEL"
DOCUMENT = 99887766
ADDRESS = "ADDRESS-SENTINEL"
PERSONAL = {
    "APELLIDOS_Y_NOMBRES_LIMPIO",
    "NRO_DOCUMENTO_IDENTIDAD",
    "DOMICILIO_PARTICULAR",
    "X",
    "Y",
}


def inmates(
    count: int,
    tipo: str,
    status: str,
    sex: str,
    age: int,
    place: tuple[str, str, str],
) -> list[dict[str, object]]:
    department, province, district = place
    return [
        {
            "ID": 1,
            "X": -77.0,
            "Y": -12.0,
            "TIPO_DELITO": tipo,
            "SITUACION_JURIDICA": status,
            "GENERO": sex,
            "EDAD": age,
            "DEPARTAMENTO_DOMICILIO": department,
            "PROVINCIA_DOMICILIO": province,
            "DISTRITO_DOMICILIO": district,
            "APELLIDOS_Y_NOMBRES_LIMPIO": NAME,
            "NRO_DOCUMENTO_IDENTIDAD": DOCUMENT,
            "DOMICILIO_PARTICULAR": ADDRESS,
        }
        for _ in range(count)
    ]


LIMA = ("LIMA", "LIMA", "LIMA")
INMATES = [
    *inmates(5, "EXTORSION", "SENTENCIADO", "HOMBRE", 25, LIMA),
    # Two districts below the minimum fold into one `otros` row of five.
    *inmates(3, "EXTORSION", "PROCESADO", "HOMBRE", 35, ("LIMA", "LIMA", "ATE")),
    *inmates(2, "EXTORSION", "PROCESADO", "HOMBRE", 35, ("LIMA", "LIMA", "COMAS")),
    # Two in all: the folded row is still under the minimum, so it is left out.
    *inmates(1, "EXTORSION", "PROCESADO", "MUJER", 61, ("PIURA", "PIURA", "PIURA")),
    *inmates(1, "EXTORSION", "PROCESADO", "MUJER", 61, ("PIURA", "SULLANA", "SULLANA")),
    *inmates(5, "ROBO Y HURTO", "SENTENCIADO", "HOMBRE", 17, ("CUSCO", "CUSCO", "SAN SEBASTIAN")),
    *inmates(1, "ROBO Y HURTO", "PROCESADO", "HOMBRE", 70, ("TUMBES", "TUMBES", "TUMBES")),
    *inmates(5, "HOMICIDIO", "PROCESADO", "MUJER", 50, ("ICA", "ICA", "ICA")),
    *inmates(5, "DELITOS SEXUALES", "SENTENCIADO", "HOMBRE", 45, ("TACNA", "TACNA", "TACNA")),
    *inmates(5, "TRAFICO ILICITO DE DROGAS", "SENTENCIADO", "MUJER", 18, ("LORETO", "MAYNAS", "IQUITOS")),
    # Two offences that both map to `otros` share one cell.
    *inmates(3, "LESIONES", "SENTENCIADO", "HOMBRE", 29, ("PUNO", "PUNO", "PUNO")),
    *inmates(2, "SECUESTRO", "SENTENCIADO", "HOMBRE", 29, ("PUNO", "PUNO", "PUNO")),
]  # fmt: skip

ALL_COLUMNS = (
    "grupo_delito, situacion_juridica, sexo, grupo_edad, "
    "departamento, provincia, distrito, internos"
)
EXTORTION_COLUMNS = ALL_COLUMNS.replace("grupo_delito, ", "")

ALL_ROWS = [
    ("extorsion", "sentenciado", "hombre", "18-29", "LIMA", "LIMA", "LIMA", 5),
    ("extorsion", "procesado", "hombre", "30-44", None, None, "otros", 5),
    ("robo", "sentenciado", "hombre", "0-17", "CUSCO", "CUSCO", "SAN SEBASTIAN", 5),
    ("homicidio", "procesado", "mujer", "45-59", "ICA", "ICA", "ICA", 5),
    ("sexual", "sentenciado", "hombre", "45-59", "TACNA", "TACNA", "TACNA", 5),
    ("drogas", "sentenciado", "mujer", "18-29", "LORETO", "MAYNAS", "IQUITOS", 5),
    ("otros", "sentenciado", "hombre", "18-29", "PUNO", "PUNO", "PUNO", 5),
]
EXTORTION_ROWS = [row[1:] for row in ALL_ROWS[:2]]


@dataclass
class Layer:
    rows: list[dict[str, object]] = field(default_factory=lambda: list(INMATES))
    robots: tuple[int, str] = (403, "Invalid URL")
    fail_page: tuple[int, str] | None = None
    honors_out_fields: bool = True
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
                self._send(200, json.dumps({"count": len(state.rows)}))
                return
            offset = int(params["resultOffset"][0])
            if state.fail_page and offset > 0:
                self._send(*state.fail_page)
                return
            asked = params["outFields"][0].split(",")
            chunk = state.rows[offset : offset + SERVER_PAGE_SIZE]
            if state.honors_out_fields:
                chunk = [{k: v for k, v in r.items() if k in asked} for r in chunk]
            page = {
                "features": [{"attributes": r} for r in chunk],
                "exceededTransferLimit": offset + SERVER_PAGE_SIZE < len(state.rows),
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


def run_inpe(url: str, out_dir: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "dep",
            "inpe",
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
    return sorted(
        duckdb.execute(f"SELECT {columns} FROM read_parquet('{path}')").fetchall(),
        key=str,
    )


@pytest.mark.parametrize("robots", [(403, "Invalid URL"), (404, "nope"), (500, "boom")])
def test_inpe_writes_both_tables_from_a_whitelist_of_fields(
    layer: tuple[Layer, str], tmp_path: Path, robots: tuple[int, str]
) -> None:
    state, url = layer
    state.robots = robots
    out_dir = tmp_path / "out"

    result = run_inpe(url, out_dir)

    assert result.returncode == 0, result.stderr
    all_path = out_dir / "inpe_internos_perfil_distrito.parquet"
    extortion_path = out_dir / "inpe_extorsion_perfil_distrito.parquet"
    assert read_parquet(all_path, ALL_COLUMNS) == sorted(ALL_ROWS, key=str)
    assert read_parquet(extortion_path, EXTORTION_COLUMNS) == sorted(
        EXTORTION_ROWS, key=str
    )
    assert f"layer reports {len(INMATES)} rows" in result.stdout
    assert "inpe_internos_perfil_distrito.parquet: 3 inmates" in result.stdout
    assert "inpe_extorsion_perfil_distrito.parquet: 2 inmates" in result.stdout

    # One count, then one query per page.
    pages = -(-len(INMATES) // SERVER_PAGE_SIZE)
    assert len(state.queries) == 1 + pages
    for sent in state.queries:
        requested = set(sent["outFields"][0].split(","))
        assert "*" not in requested
        assert requested
        assert not requested & PERSONAL
        assert sent["returnGeometry"] == ["false"]
    assert state.queries[-1]["orderByFields"] == ["ObjectId"]

    # Folded districts are not published.
    districts = {row[0] for row in read_parquet(all_path, "distrito")}
    assert not {"ATE", "COMAS"} & districts
    # No personal value reaches the tables, the log or the file bytes.
    for text in (
        result.stdout,
        result.stderr,
        all_path.read_bytes().decode("latin-1"),
        extortion_path.read_bytes().decode("latin-1"),
    ):
        assert NAME not in text
        assert str(DOCUMENT) not in text
        assert ADDRESS not in text


def test_inpe_stops_when_the_server_returns_fields_outside_the_whitelist(
    layer: tuple[Layer, str], tmp_path: Path
) -> None:
    state, url = layer
    state.honors_out_fields = False
    out_dir = tmp_path / "out"

    result = run_inpe(url, out_dir)

    assert result.returncode != 0
    assert "outside the outFields whitelist" in result.stderr
    assert "NRO_DOCUMENTO_IDENTIDAD" in result.stderr
    assert NAME not in result.stderr + result.stdout
    assert len(state.queries) == 2  # the count, then the first page only
    assert not out_dir.exists()


def test_inpe_replaces_both_tables(layer: tuple[Layer, str], tmp_path: Path) -> None:
    state, url = layer
    out_dir = tmp_path / "out"
    assert run_inpe(url, out_dir).returncode == 0

    state.rows = inmates(6, "HOMICIDIO", "PROCESADO", "HOMBRE", 33, LIMA)
    second = run_inpe(url, out_dir)

    assert second.returncode == 0, second.stderr
    assert read_parquet(
        out_dir / "inpe_internos_perfil_distrito.parquet", "grupo_delito, internos"
    ) == [("homicidio", 6)]
    assert (
        read_parquet(out_dir / "inpe_extorsion_perfil_distrito.parquet", "internos")
        == []
    )


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
def test_inpe_stops_with_the_url_on_a_source_failure(
    layer: tuple[Layer, str],
    tmp_path: Path,
    robots: tuple[int, str],
    fail_page: tuple[int, str] | None,
    stopped_at: str,
) -> None:
    state, url = layer
    state.robots = robots
    state.fail_page = fail_page

    result = run_inpe(url, tmp_path / "out")

    assert result.returncode != 0
    assert stopped_at in result.stderr
    assert not (tmp_path / "out").exists()


@pytest.mark.parametrize(
    ("age", "band"),
    [
        (None, "sin dato"),
        (0, "0-17"),
        (17, "0-17"),
        (18, "18-29"),
        (29, "18-29"),
        (30, "30-44"),
        (44, "30-44"),
        (45, "45-59"),
        (59, "45-59"),
        (60, "60+"),
        (95, "60+"),
    ],
)
def test_age_band_edges(age: int | None, band: str) -> None:
    assert age_band(age) == band


@pytest.mark.parametrize(
    ("tipo", "group"),
    [
        ("EXTORSION", "extorsion"),
        (" extorsion ", "extorsion"),
        ("ROBO Y HURTO", "robo"),
        ("HOMICIDIO", "homicidio"),
        ("DELITOS SEXUALES", "sexual"),
        ("TRAFICO ILICITO DE DROGAS", "drogas"),
        ("TENENCIA O PRODUCCION ILEGAL DE ARMAS", "otros"),
        ("SECUESTRO", "otros"),
        (None, "otros"),
    ],
)
def test_offence_group(tipo: str | None, group: str) -> None:
    assert offence_group(tipo) == group
