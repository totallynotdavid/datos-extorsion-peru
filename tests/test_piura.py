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

from dep.piura import DATASET_ID, offence_group

HEADER = (
    "MES,SEDE,DEPARTAMENTO,PROVINCIA,DISTRITO,INSTANCIA_SENTENCIA,EXPEDIENTE,"
    "FECHA_INGRESO,ESPECIALIDAD,SUBESPECIALIDAD,PROCESO,DNI,FECHA_NACIMIENTO,EDAD,"
    "SEXO,TIPO_PARTE,UBIGEO_PARTE,GENERICO,DELITO,CONDICION,SITUACION_JURIDICA,"
    "FECHA_SENTENCIA,DIAS,PENA,AÑOS_SENTENCIA,MESES_SENTENCIA,DIAS_SENTENCIA,"
    "F_INICIO_PENA,F_TERMINO_PENA,FECHA_CORTE"
)
# None of these may reach an output file, a log line or an error.
SECRETS = [
    "70123456",
    "19870615",
    "1987-06-15",
    "00123-2021-0-2001-JR-PE-01",
    "EXPSENTINEL",
]


def csv_row(province: str, delito: str, sentenced: str) -> str:
    values = {
        "SEDE": "SEDE CENTRAL",
        "PROVINCIA": province,
        "EXPEDIENTE": "00123-2021-0-2001-JR-PE-01",
        "FECHA_INGRESO": "1987-06-15",
        "ESPECIALIDAD": "EXPSENTINEL",
        "DNI": "70123456",
        "FECHA_NACIMIENTO": "19870615",
        "SEXO": "M",
        "DELITO": delito,
        "FECHA_SENTENCIA": sentenced,
    }
    return ",".join(f'"{values.get(name, "x")}"' for name in HEADER.split(","))


def build_csv(rows: list[str]) -> bytes:
    return ("\r\n".join([HEADER, *rows]) + "\r\n").encode("latin-1")


def sentences(province: str, delito: str, sentenced: str, n: int) -> list[str]:
    return [csv_row(province, delito, sentenced)] * n


EXTORTION = "Extorsión."
ROWS = [
    *sentences("PIURA", EXTORTION, "20240315", 6),
    # Two cells of three fold into one `otros` row that meets the threshold.
    *sentences("PAITA", EXTORTION, "20240401", 3),
    *sentences("SECHURA", EXTORTION, "20240402", 3),
    # 2023's two sentences fold below the threshold and are not published.
    *sentences("PIURA", EXTORTION, "20231201", 2),
    # Two spellings of one offence share a cell.
    *sentences("PIURA", "Robo Agravado", "20240110", 4),
    *sentences("PIURA", "Robo.", "20240111", 2),
    *sentences("PIURA", "Feminicidio", "20240201", 5),
    *sentences("MORROPON", "Violación sexual de menor de edad", "20240202", 5),
    *sentences(
        "PIURA",
        "Promoción o favorecimiento al tráfico ilícito de drogas.",
        "20240203",
        5,
    ),
    *sentences("PIURA", "Hurto agravado.", "20240204", 5),
    *sentences("PIURA", "Robo Agravado", "", 2),
]


@dataclass
class Catalog:
    csv: bytes = field(default_factory=lambda: build_csv(ROWS))
    requests: list[str] = field(default_factory=list)


@pytest.fixture
def catalog() -> Iterator[tuple[Catalog, str]]:
    state = Catalog()
    base = ""

    class Handler(BaseHTTPRequestHandler):
        def _send(self, status: int, body: bytes) -> None:
            self.send_response(status)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:
            parts = urlsplit(self.path)
            state.requests.append(self.path)
            if parts.path == "/api/3/action/package_show":
                assert parse_qs(parts.query)["id"] == [DATASET_ID]
                record = {
                    "result": {
                        "resources": [
                            {"format": "csv", "url": f"{base}/files/sentencia.csv"},
                            {"format": ".docx", "url": f"{base}/files/meta.docx"},
                        ]
                    }
                }
                self._send(200, json.dumps(record).encode())
            elif parts.path == "/files/sentencia.csv":
                self._send(200, state.csv)
            else:
                self._send(404, b"")

        def log_message(self, format: str, *args: object) -> None:
            pass

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    base = f"http://127.0.0.1:{httpd.server_port}"
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield state, f"{base}/api/3/action/package_show"
    httpd.shutdown()
    thread.join()


def run_piura(catalog_url: str, out_dir: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "dep",
            "piura",
            "--catalog-url",
            catalog_url,
            "--out-dir",
            str(out_dir),
            "--delay",
            "0",
        ],
        capture_output=True,
        text=True,
    )


def read_parquet(path: Path) -> list[tuple[object, ...]]:
    return duckdb.execute(
        f"SELECT * FROM read_parquet('{path}') ORDER BY ALL"
    ).fetchall()


def test_piura_writes_the_sentences_table_without_identifying_values(
    catalog: tuple[Catalog, str], tmp_path: Path
) -> None:
    state, catalog_url = catalog
    out_dir = tmp_path / "out"
    table = out_dir / "sentencias_piura_anio_provincia_delito.parquet"

    result = run_piura(catalog_url, out_dir)

    assert result.returncode == 0, result.stderr
    assert "/files/sentencia.csv" in state.requests
    assert "dropped 2 rows with no readable sentence date" in result.stdout
    assert "kept 40 rows, 14 of them extortion" in result.stdout

    assert duckdb.execute(
        f"SELECT name FROM parquet_schema('{table}') WHERE name <> 'duckdb_schema'"
    ).fetchall() == [
        ("sentence_year",),
        ("court_province",),
        ("offence_group",),
        ("sentences",),
    ]
    assert read_parquet(table) == [
        (2024, "MORROPON", "sexual", 5),
        (2024, "PIURA", "drugs", 5),
        (2024, "PIURA", "extorsion", 6),
        (2024, "PIURA", "homicide", 5),
        (2024, "PIURA", "other", 5),
        (2024, "PIURA", "robbery", 6),
        (2024, "otros", "extorsion", 6),
    ]
    assert "8 sentences in cells under 5 folded into otros" in result.stdout
    assert ", 2 of them in otros rows still under 5" in result.stdout
    everything = table.read_bytes() + (result.stdout + result.stderr).encode()
    for secret in SECRETS:
        assert secret.encode() not in everything, secret


def test_piura_replaces_the_table_on_every_run(
    catalog: tuple[Catalog, str], tmp_path: Path
) -> None:
    state, catalog_url = catalog
    out_dir = tmp_path / "out"
    table = out_dir / "sentencias_piura_anio_provincia_delito.parquet"
    assert run_piura(catalog_url, out_dir).returncode == 0

    state.csv = build_csv(sentences("PIURA", "Robo.", "20250101", 5))
    result = run_piura(catalog_url, out_dir)

    assert result.returncode == 0, result.stderr
    assert read_parquet(table) == [(2025, "PIURA", "robbery", 5)]


def test_piura_unreadable_csv_fails_without_quoting_a_row(
    catalog: tuple[Catalog, str], tmp_path: Path
) -> None:
    state, catalog_url = catalog
    # The bad row comes after the library's dialect sample, so the library
    # reports it by quoting the whole row.
    filler = sentences("PIURA", "Robo.", "20240101", 21000)
    bad_row = csv_row("PIURA", "Robo.", "20240101") + ',"extra"'
    state.csv = build_csv([*filler, bad_row])

    result = run_piura(catalog_url, tmp_path / "out")

    assert result.returncode != 0
    assert "cannot read the CSV" in result.stderr
    for secret in SECRETS:
        assert secret not in result.stdout + result.stderr, secret


@pytest.mark.parametrize(
    ("delito", "group"),
    [
        ("Extorsión.", "extorsion"),
        ("Robo Agravado", "robbery"),
        ("Robo de ganado.", "robbery"),
        ("Hurto agravado.", "other"),
        ("Asesinato", "homicide"),
        ("Sicariato.", "homicide"),
        ("Violación sexual de menor de edad", "sexual"),
        ("Actos contra el pudor en menores", "sexual"),
        ("Violación de domicilio", "other"),
        ("Violación de las medidas sanitarias.", "other"),
        ("Formas agravadas de tráfico ilícito de drogas.", "drugs"),
        (
            "TRAFICO ILICITO DE DROGAS - resiembra de coca en predios erradicados.",
            "drugs",
        ),
        ("Tráfico ilegal de productos forestales maderables", "other"),
        ("La víctima tiene entre catorce y menos de dieciocho años.", "other"),
        ("NULL", "other"),
        (None, "other"),
    ],
)
def test_offence_group_mapping(delito: str | None, group: str) -> None:
    assert offence_group(delito) == group
