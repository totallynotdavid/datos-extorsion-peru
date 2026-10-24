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

from dep.callao import DATASET_ID, age_band, occupation_group

HEADER = (
    "FECHA_CORTE;FECHA_REGISTRO;ID_DOC_DENUNCIA;UBIGEO;DEPARTAMENTO;PROVINCIA;"
    "DISTRITO;TIPO_DE_DENUNCIA;SITUACION_DENUNCIA;TIPO;SUBTIPO;MODALIDAD;"
    "FECHA_HECHO;HORA_HECHO;UBICACION;DESCRIPCION;FECHA_NACIMIENTO;EDAD_PERSONA;"
    "SEXO;ESTADO_CIVIL;GRADO_INSTRUCCION;OCUPACION;PAIS_NATAL;MES;LONGITUD;LATITUD"
)
# None of these may reach an output file or a log line.
SECRETS = [
    "DOCSENTINEL",
    "UBICSENTINEL",
    "DESCSENTINEL",
    "19870615",
    "1987-06-15",
    "-77.123456",
    "-12.654321",
]


def csv_row(
    fecha: str,
    subtipo: str,
    modalidad: str,
    edad: int,
    sexo: str,
    ocupacion: str,
    tipo: str = "PATRIMONIO (DELITO)",
) -> str:
    return ";".join(
        [
            "20240401",
            fecha,
            "DOCSENTINEL",
            "070101",
            "CALLAO",
            "CALLAO",
            "CALLAO",
            "DENUNCIA",
            "PENDIENTE",
            tipo,
            subtipo,
            modalidad,
            fecha,
            "1830",
            "UBICSENTINEL AV. LIMA 123",
            "DESCSENTINEL COMISARIA",
            "19870615",
            str(edad),
            sexo,
            "SOLTERO",
            "SUPERIOR",
            ocupacion,
            "PERU",
            fecha[:6],
            "-77.123456",
            "-12.654321",
        ]
    )


def extortion(fecha: str, edad: int, sexo: str, ocupacion: str, n: int) -> list[str]:
    return [csv_row(fecha, "EXTORSION", "EXTORSION", edad, sexo, ocupacion)] * n


def build_csv(rows: list[str]) -> bytes:
    return ("\r\n".join([HEADER, *rows]) + "\r\n").encode("utf-8-sig")


MARCH = "20240315"
APRIL = "20240410"

EXTORTION_ROWS = [
    *extortion(MARCH, 35, "MASCULINO", "OBRERO", 6),
    # Six reports form one folded `otros` row that meets the threshold.
    *extortion(MARCH, 20, "FEMENINO", "ESTUDIANTE", 2),
    *[csv_row(MARCH, "EXTORSION", "CHANTAJE", 70, "FEMENINO", "AMA DE CASA")] * 2,
    *extortion(MARCH, 10, "MASCULINO", "NO INDICA", 2),
    # April's two reports fold below the threshold and are not published.
    *extortion(APRIL, 50, "FEMENINO", "COMERCIANTE", 2),
    # The sexual-blackmail subtype remains in all-crimes counts only.
    *[
        csv_row(
            MARCH,
            "VIOLACION DE LA LIBERTAD SEXUAL",
            "CHANTAJE SEXUAL",
            25,
            "FEMENINO",
            "ESTUDIANTE",
            "LIBERTAD (DELITO)",
        )
    ]
    * 5,
]
OTHER_ROWS = [
    *[csv_row(MARCH, "HURTO", "HURTO", 40, "FEMENINO", "COMERCIANTE")] * 10,
    *[csv_row("20221201", "HURTO", "HURTO", 40, "FEMENINO", "COMERCIANTE")] * 3,
    csv_row("20010101", "ROBO", "ROBO", 40, "MASCULINO", "CHOFER"),
]


@dataclass
class Catalog:
    csv: bytes = field(default_factory=lambda: build_csv(EXTORTION_ROWS + OTHER_ROWS))
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
                            {"format": "docx", "url": f"{base}/files/metadata.docx"},
                            {"format": "csv", "url": f"{base}/files/FEBRERO2024.csv"},
                            {
                                "format": ".xlsx",
                                "url": f"{base}/files/diccionario.xlsx",
                            },
                        ]
                    }
                }
                self._send(200, json.dumps(record).encode())
            elif parts.path == "/files/FEBRERO2024.csv":
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


def run_callao(catalog_url: str, out_dir: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "dep",
            "callao",
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


def test_callao_writes_victim_tables_without_identifying_columns(
    catalog: tuple[Catalog, str], tmp_path: Path
) -> None:
    state, catalog_url = catalog
    out_dir = tmp_path / "out"
    sex_age = out_dir / "victimas_sexo_edad_mes.parquet"
    occupation = out_dir / "victimas_ocupacion_mes.parquet"

    result = run_callao(catalog_url, out_dir)

    assert result.returncode == 0, result.stderr
    assert "/files/FEBRERO2024.csv" in state.requests
    assert "dropped 4 rows dated before 2023" in result.stdout
    assert "kept 29 rows, 14 of them extortion" in result.stdout

    assert duckdb.execute(
        f"SELECT name FROM parquet_schema('{sex_age}') WHERE name <> 'duckdb_schema'"
    ).fetchall() == [
        ("delito",),
        ("anio",),
        ("mes",),
        ("sexo",),
        ("banda_edad",),
        ("denuncias",),
    ]
    assert read_parquet(sex_age) == [
        ("extorsion", 2024, 3, "masculino", "30-44", 6),
        ("extorsion", 2024, 3, "otros", "otros", 6),
        ("todos", 2024, 3, "femenino", "18-29", 7),
        ("todos", 2024, 3, "femenino", "30-44", 10),
        ("todos", 2024, 3, "masculino", "30-44", 6),
    ]
    assert read_parquet(occupation) == [
        ("extorsion", 2024, 3, "obrero_oficio", 6),
        ("extorsion", 2024, 3, "otros", 6),
        ("todos", 2024, 3, "comercio_independiente", 10),
        ("todos", 2024, 3, "estudiante", 7),
        ("todos", 2024, 3, "obrero_oficio", 6),
    ]
    # April's two extortion rows and the cells of March's all-crimes rows that
    # fold to under 5 are counted in the log and left out of the tables.
    assert "14 reports in cells under 5 folded into otros, 8 of them" in result.stdout
    published = b"".join(p.read_bytes() for p in (sex_age, occupation))
    everything = published + (result.stdout + result.stderr).encode()
    for secret in SECRETS:
        assert secret.encode() not in everything, secret


def test_callao_replaces_the_tables_on_every_run(
    catalog: tuple[Catalog, str], tmp_path: Path
) -> None:
    state, catalog_url = catalog
    out_dir = tmp_path / "out"
    assert run_callao(catalog_url, out_dir).returncode == 0

    state.csv = build_csv(OTHER_ROWS)
    result = run_callao(catalog_url, out_dir)

    assert result.returncode == 0, result.stderr
    assert {
        row[0] for row in read_parquet(out_dir / "victimas_sexo_edad_mes.parquet")
    } == {"todos"}


@pytest.mark.parametrize(
    ("age", "band"),
    [
        (0, "0-17"),
        (17, "0-17"),
        (18, "18-29"),
        (29, "18-29"),
        (30, "30-44"),
        (44, "30-44"),
        (45, "45-59"),
        (59, "45-59"),
        (60, "60+"),
        (124, "60+"),
        (None, "sin_dato"),
        (-1, "sin_dato"),
    ],
)
def test_age_band_edges(age: int | None, band: str) -> None:
    assert age_band(age) == band


@pytest.mark.parametrize(
    ("value", "group"),
    [
        ("NO INDICA", "sin_dato"),
        ("", "sin_dato"),
        (None, "sin_dato"),
        ("AMA DE CASA", "hogar"),
        ("ESTUDIANTE", "estudiante"),
        ("JUBILADO", "sin_empleo_o_jubilado"),
        ("SERENAZGO MOTORIZADO", "seguridad_y_fuerzas"),
        ("PNP EN RETIRO", "seguridad_y_fuerzas"),
        ("TECNICO EN ENFERMERIA", "profesional_tecnico"),
        ("MOTOTAXISTA", "transporte"),
        ("OBRERO INDEPENDIENTE", "obrero_oficio"),
        ("TRABAJO INDEPENDIENT", "comercio_independiente"),
        ("EMPLEADA DE LA MDV", "empleado_administrativo"),
        ("T MG", "otro"),
        ("EVENTOS INFANTILES", "otro"),
    ],
)
def test_occupation_group_mapping(value: str | None, group: str) -> None:
    assert occupation_group(value) == group
