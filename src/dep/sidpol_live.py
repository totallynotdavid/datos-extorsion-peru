"""Aggregate extortion reports from Mininter's live SIDPOL ArcGIS layer.

Every run replaces both output tables. The layer also carries coordinates, but
most are the interior point of a police sector that Mininter filled in, so the
run never requests them and never writes them.
"""

from pathlib import Path
from typing import Any

import duckdb
import httpx

from dep.http import RobotsCheckedClient
from dep.sidpol import OUTPUT_NAME as CSV_TABLE_NAME
from dep.sidpol import USER_AGENT

LAYER_URL = (
    "https://services6.arcgis.com/lMIZrqiJkpM748BR/arcgis/rest/services/"
    "SIDPOL_DELITOS_TOTAL/FeatureServer/1/query"
)
WHERE = "subtipo_hecho = 'EXTORSION'"
FIELDS = (
    "ubigeo_hecho_delito",
    "fecha_hora_hecho",
    "turno_hecho",
    "modalidad_hecho",
    "cod_cpnp_hecho",
    "comisaria_hecho",
    "ESTADO_COORD",
)
PAGE_SIZE = 2000
DELAY = 1.5
DIFFERENCE_THRESHOLD = 0.10
DISTRICT_TABLE_NAME = "extorsion_live_distrito_mes_turno.parquet"
SECTOR_TABLE_NAME = "extorsion_live_sector_mes.parquet"


def _query(client: RobotsCheckedClient, url: str, **params: Any) -> dict[str, Any]:
    try:
        response = client.get(url, params={"where": WHERE, "f": "json", **params})
    except httpx.HTTPError as exc:
        raise RuntimeError(f"{url}: {exc}") from exc
    if response.status_code >= 400:
        raise RuntimeError(f"{url}: HTTP {response.status_code}")
    body = response.json()
    if "error" in body:
        raise RuntimeError(f"{url}: ArcGIS error {body['error']}")
    return dict(body)


def fetch_count(client: RobotsCheckedClient, url: str) -> int:
    return int(_query(client, url, returnCountOnly="true")["count"])


def load_reports(
    client: RobotsCheckedClient, url: str, con: duckdb.DuckDBPyConnection
) -> int:
    """Page through the layer in OBJECTID order into the `reports` table."""
    con.execute(
        "CREATE TABLE reports (ubigeo VARCHAR, fecha_ms BIGINT, turno VARCHAR, "
        "modalidad VARCHAR, cod_cpnp VARCHAR, comisaria VARCHAR, "
        "estado_coord VARCHAR)"
    )
    offset = 0
    while True:
        page = _query(
            client,
            url,
            outFields=",".join(FIELDS),
            returnGeometry="false",
            orderByFields="OBJECTID",
            resultOffset=offset,
            resultRecordCount=PAGE_SIZE,
        )
        features = page["features"]
        con.executemany(
            "INSERT INTO reports VALUES (?, ?, ?, ?, ?, ?, ?)",
            [tuple(f["attributes"].get(name) for name in FIELDS) for f in features],
        )
        offset += len(features)
        print(f"read {offset} rows")
        if not features or not page.get("exceededTransferLimit"):
            return offset


def _write_tables(
    con: duckdb.DuckDBPyConnection, district_path: Path, sector_path: Path
) -> None:
    district_path.parent.mkdir(parents=True, exist_ok=True)
    con.execute(
        """
        CREATE TABLE dated AS
        SELECT lpad(trim(ubigeo), 6, '0') AS ubigeo,
               CAST(year(epoch_ms(fecha_ms)) AS INTEGER) AS anio,
               CAST(month(epoch_ms(fecha_ms)) AS INTEGER) AS mes,
               lower(trim(turno)) AS turno,
               lpad(trim(cod_cpnp), 4, '0') AS cod_cpnp,
               trim(comisaria) AS comisaria
        FROM reports
        WHERE fecha_ms IS NOT NULL
        """
    )
    con.execute(
        """
        COPY (
            SELECT ubigeo, anio, mes, turno, CAST(COUNT(*) AS INTEGER) AS denuncias
            FROM dated
            GROUP BY ubigeo, anio, mes, turno
            ORDER BY ubigeo, anio, mes, turno
        ) TO ? (FORMAT PARQUET)
        """,
        [str(district_path)],
    )
    con.execute(
        """
        COPY (
            SELECT cod_cpnp,
                   min(comisaria) AS comisaria,
                   anio,
                   mes,
                   CAST(COUNT(*) AS INTEGER) AS denuncias
            FROM dated
            GROUP BY cod_cpnp, anio, mes
            ORDER BY cod_cpnp, anio, mes
        ) TO ? (FORMAT PARQUET)
        """,
        [str(sector_path)],
    )


def _summary(con: duckdb.DuckDBPyConnection, column: str) -> str:
    rows = con.execute(
        f"SELECT coalesce({column}, '(vacío)'), COUNT(*) FROM reports "
        "GROUP BY 1 ORDER BY 2 DESC, 1"
    ).fetchall()
    return ", ".join(f"{name} {n}" for name, n in rows)


def compare_with_csv(
    con: duckdb.DuckDBPyConnection, csv_table: Path
) -> list[tuple[str, int, int, int, int]]:
    """District-months where the layer and the CSV table differ by more than
    10% of the larger count. Only months both sources hold are compared, and a
    district missing from one source counts as zero there."""
    return con.execute(
        """
        WITH live AS (
            SELECT ubigeo, anio, mes, CAST(COUNT(*) AS INTEGER) AS n
            FROM dated GROUP BY ubigeo, anio, mes
        ),
        csv AS (
            SELECT ubigeo, anio, mes, denuncias AS n FROM read_parquet(?)
        ),
        shared AS (
            SELECT anio, mes FROM live INTERSECT SELECT anio, mes FROM csv
        ),
        joined AS (
            SELECT ubigeo, anio, mes, coalesce(l.n, 0) AS live, coalesce(c.n, 0) AS csv
            FROM (SELECT * FROM live SEMI JOIN shared USING (anio, mes)) l
            FULL JOIN (SELECT * FROM csv SEMI JOIN shared USING (anio, mes)) c
            USING (ubigeo, anio, mes)
        )
        SELECT ubigeo, anio, mes, live, csv
        FROM joined
        WHERE abs(live - csv) > ? * greatest(live, csv)
        ORDER BY abs(live - csv) DESC, ubigeo, anio, mes
        """,
        [str(csv_table), DIFFERENCE_THRESHOLD],
    ).fetchall()


def run(
    out_dir: Path,
    url: str = LAYER_URL,
    csv_table: Path | None = None,
    delay: float = DELAY,
) -> tuple[Path, Path]:
    district_path = out_dir / DISTRICT_TABLE_NAME
    sector_path = out_dir / SECTOR_TABLE_NAME
    csv_table = csv_table or out_dir / CSV_TABLE_NAME
    con = duckdb.connect()
    with httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=60) as raw_client:
        client = RobotsCheckedClient(
            raw_client, USER_AGENT, delay, open_on_robots_error=True
        )
        expected = fetch_count(client, url)
        print(f"layer reports {expected} rows where {WHERE}")
        loaded = load_reports(client, url, con)
    if loaded != expected:
        print(f"warning: read {loaded} rows but the layer counted {expected}")
    print(f"modalidad: {_summary(con, 'modalidad')}")
    print(f"ESTADO_COORD: {_summary(con, 'estado_coord')}")
    undated = con.execute(
        "SELECT COUNT(*) FROM reports WHERE fecha_ms IS NULL"
    ).fetchone()
    assert undated is not None
    if undated[0]:
        print(f"warning: {undated[0]} rows have no date and are left out")
    _write_tables(con, district_path, sector_path)
    if csv_table.exists():
        _print_comparison(compare_with_csv(con, csv_table), csv_table)
    else:
        print(f"no comparison: {csv_table} not found, run `dep sidpol` first")
    con.close()
    return district_path, sector_path


def _print_comparison(
    rows: list[tuple[str, int, int, int, int]], csv_table: Path
) -> None:
    print(
        f"{len(rows)} district-months differ by more than "
        f"{DIFFERENCE_THRESHOLD:.0%} from {csv_table}"
    )
    if not rows:
        return
    print(f"{'ubigeo':<8}{'anio':>6}{'mes':>5}{'layer':>8}{'csv':>8}")
    for ubigeo, anio, mes, live, csv in rows:
        print(f"{ubigeo:<8}{anio:>6}{mes:>5}{live:>8}{csv:>8}")
