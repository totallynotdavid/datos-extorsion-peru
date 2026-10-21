"""Build the district x year population table from the INEI projections.

The workbook lists projected population at June 30 of each year. District
rows are UBIGEO codes not ending in "00"; cells for years before a district
existed hold text such as "Distrito creado en 2021" and are skipped. INEI
republishes the whole projection, so each run replaces the output table.
"""

import re
import tempfile
from pathlib import Path
from typing import cast

import duckdb
import httpx
from python_calamine import load_workbook

from dep.http import RobotsCheckedClient
from dep.sidpol import USER_AGENT

WORKBOOK_URL = (
    "https://cdn.www.gob.pe/uploads/document/file/8261096/"
    "6894980-peru-poblacion-total-proyectada-al-30-de-junio-de-cada-ano"
    "-segun-departamento-provincia-y-distrito-2018-2026.xlsx"
)
OUTPUT_NAME = "poblacion_distrito_anio.parquet"

_CODE = re.compile(r"\d{1,6}")
_MIN_YEAR = 1900
_MAX_YEAR = 2100


def _ubigeo(value: object) -> str | None:
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    if isinstance(value, int) and not isinstance(value, bool):
        return str(value).zfill(6)
    if isinstance(value, str) and _CODE.fullmatch(value.strip()):
        return value.strip().zfill(6)
    return None


def parse_workbook(path: Path) -> list[tuple[str, int, int]]:
    """Return (ubigeo, anio, poblacion) rows for district codes.

    The year header row is the first row holding two or more year numbers; its
    columns name the year of each population cell. Non-numeric cells, the Peru
    total (000000) and department/province rows (codes ending in 00) are
    skipped.
    """
    sheet = load_workbook(str(path)).get_sheet_by_index(0)
    rows: list[list[object]] = cast("list[list[object]]", sheet.to_python())

    years: dict[int, int] = {}
    header = -1
    for i, row in enumerate(rows):
        cols = {
            j: int(v)
            for j, v in enumerate(row)
            if isinstance(v, (int, float))
            and not isinstance(v, bool)
            and float(v).is_integer()
            and _MIN_YEAR <= v <= _MAX_YEAR
        }
        if len(cols) >= 2:
            years, header = cols, i
            break
    if header < 0:
        raise RuntimeError(f"{path} has no year header row")

    out: list[tuple[str, int, int]] = []
    for row in rows[header + 1 :]:
        if not row:
            continue
        code = _ubigeo(row[0])
        if code is None or code == "000000" or code.endswith("00"):
            continue
        for col, year in years.items():
            if col >= len(row):
                continue
            value = row[col]
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                out.append((code, year, int(value)))
    return out


def write_table(rows: list[tuple[str, int, int]], out_path: Path) -> None:
    con = duckdb.connect()
    con.execute(
        "CREATE TABLE population (ubigeo VARCHAR, anio INTEGER, poblacion INTEGER)"
    )
    con.executemany("INSERT INTO population VALUES (?, ?, ?)", rows)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    con.execute(
        "COPY (SELECT * FROM population ORDER BY ubigeo, anio) TO ? (FORMAT PARQUET)",
        [str(out_path)],
    )
    con.close()


def run(
    out_dir: Path,
    xlsx_file: Path | None = None,
    xlsx_url: str | None = None,
) -> Path:
    with tempfile.TemporaryDirectory() as work_dir:
        if xlsx_file is not None:
            path = xlsx_file
        else:
            url = xlsx_url or WORKBOOK_URL
            path = Path(work_dir) / "poblacion.xlsx"
            print(f"downloading {url}")
            with httpx.Client(
                headers={"User-Agent": USER_AGENT}, timeout=60
            ) as raw_client:
                client = RobotsCheckedClient(raw_client, USER_AGENT)
                with client.stream(url) as response:
                    response.raise_for_status()
                    with path.open("wb") as fh:
                        for chunk in response.iter_bytes():
                            fh.write(chunk)
        rows = parse_workbook(path)
    out_path = out_dir / OUTPUT_NAME
    write_table(rows, out_path)
    return out_path
