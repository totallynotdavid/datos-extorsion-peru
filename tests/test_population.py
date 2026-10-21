import html
import subprocess
import sys
import zipfile
from pathlib import Path

import duckdb

WORKBOOK_ROWS: list[list[object]] = [
    ["POBLACIÓN PROYECTADA"],
    ["", "", 2018, 2019, 2020],
    ["000000", "PERÚ", 100000, 101000, 102000],
    ["010000", "AMAZONAS", 50000, 51000, 52000],
    [10202, "ARAMANGO 1/", "Distrito creado en 2020", 7000, 7100],
    ["150101", "LIMA", 300000, 301000, 302000],
]


def column_name(index: int) -> str:
    name = ""
    index += 1
    while index:
        index, digit = divmod(index - 1, 26)
        name = chr(65 + digit) + name
    return name


def _cell(ref: str, value: object) -> str:
    if isinstance(value, str):
        return (
            f'<c r="{ref}" t="inlineStr"><is><t xml:space="preserve">'
            f"{html.escape(value)}</t></is></c>"
        )
    return f'<c r="{ref}"><v>{value}</v></c>'


def write_workbook(path: Path, rows: list[list[object]]) -> Path:
    sheet_rows = "".join(
        f'<row r="{i + 1}">'
        + "".join(
            _cell(f"{column_name(j)}{i + 1}", value)
            for j, value in enumerate(row)
            if value != ""
        )
        + "</row>"
        for i, row in enumerate(rows)
    )
    sheet = (
        '<?xml version="1.0"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/'
        f'2006/main"><sheetData>{sheet_rows}</sheetData></worksheet>'
    )
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(
            "[Content_Types].xml",
            '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.'
            'org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.'
            'openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/xl/workbook.xml" ContentType="application/'
            'vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
            '<Override PartName="/xl/worksheets/sheet1.xml" ContentType='
            '"application/vnd.openxmlformats-officedocument.spreadsheetml.'
            'worksheet+xml"/></Types>',
        )
        zf.writestr(
            "_rels/.rels",
            '<?xml version="1.0"?><Relationships xmlns="http://schemas.'
            'openxmlformats.org/package/2006/relationships"><Relationship '
            'Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument'
            '/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
            "</Relationships>",
        )
        zf.writestr(
            "xl/workbook.xml",
            '<?xml version="1.0"?><workbook xmlns="http://schemas.openxml'
            '.formats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.'
            'openxmlformats.org/officeDocument/2006/relationships"><sheets>'
            '<sheet name="POB" sheetId="1" r:id="rId2"/></sheets></workbook>',
        )
        zf.writestr(
            "xl/_rels/workbook.xml.rels",
            '<?xml version="1.0"?><Relationships xmlns="http://schemas.'
            'openxmlformats.org/package/2006/relationships"><Relationship '
            'Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument'
            '/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
            "</Relationships>",
        )
        zf.writestr("xl/worksheets/sheet1.xml", sheet)
    return path


def run_population(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "dep", "population", *args],
        capture_output=True,
        text=True,
    )


def read_out(out_dir: Path) -> list[tuple[object, ...]]:
    return duckdb.execute(
        "SELECT ubigeo, anio, poblacion FROM "
        f"read_parquet('{out_dir}/poblacion_distrito_anio.parquet') "
        "ORDER BY ubigeo, anio"
    ).fetchall()


def test_population_writes_district_year_rows(tmp_path: Path) -> None:
    workbook = write_workbook(tmp_path / "poblacion.xlsx", WORKBOOK_ROWS)
    out_dir = tmp_path / "out"

    result = run_population("--xlsx-file", str(workbook), "--out-dir", str(out_dir))
    assert result.returncode == 0, result.stderr
    assert read_out(out_dir) == [
        ("010202", 2019, 7000),
        ("010202", 2020, 7100),
        ("150101", 2018, 300000),
        ("150101", 2019, 301000),
        ("150101", 2020, 302000),
    ]

    # INEI republishes the whole projection, so a rerun replaces the table.
    second = write_workbook(
        tmp_path / "poblacion2.xlsx",
        [["", "", 2018, 2019, 2020], ["150101", "LIMA", 1, 2, 3]],
    )
    result = run_population("--xlsx-file", str(second), "--out-dir", str(out_dir))
    assert result.returncode == 0, result.stderr
    assert read_out(out_dir) == [
        ("150101", 2018, 1),
        ("150101", 2019, 2),
        ("150101", 2020, 3),
    ]
