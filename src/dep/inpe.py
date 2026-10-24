"""Aggregate INPE's prison population layer into profile tables.

The layer is one row per inmate and also carries names, identity document
numbers and home addresses. Those must never reach this machine, so every
request names the fields it wants in `outFields` and none of them is personal,
and the run stops if the server answers with any other field.

Every run replaces both output tables. A cell of fewer than `MIN_CELL` inmates
is folded into an `otros` district row of its offence group, legal status, sex
and age band. Where the folded cells still total fewer than `MIN_CELL`, they are
left out and the run prints how many inmates that is.
"""

from collections.abc import Iterable
from pathlib import Path
from typing import Any

import duckdb
import httpx

from dep.http import RobotsCheckedClient
from dep.sidpol import USER_AGENT

LAYER_URL = (
    "https://services5.arcgis.com/i1qB7n7Z27Xnip7G/arcgis/rest/services/"
    "MAPEO_2022/FeatureServer/0/query"
)
WHERE = "1=1"
FIELDS = (
    "TIPO_DELITO",
    "SITUACION_JURIDICA",
    "GENERO",
    "EDAD",
    "DEPARTAMENTO_DOMICILIO",
    "PROVINCIA_DOMICILIO",
    "DISTRITO_DOMICILIO",
)
PAGE_SIZE = 1000
DELAY = 1.5
MIN_CELL = 5
UNKNOWN = "sin dato"
OTHER_DISTRICT = "otros"
ALL_TABLE_NAME = "inpe_internos_perfil_distrito.parquet"
EXTORTION_TABLE_NAME = "inpe_extorsion_perfil_distrito.parquet"

OFFENCE_GROUPS = {
    "EXTORSION": "extorsion",
    "ROBO Y HURTO": "robo",
    "HOMICIDIO": "homicidio",
    "DELITOS SEXUALES": "sexual",
    "TRAFICO ILICITO DE DROGAS": "drogas",
}
OTHER_GROUP = "otros"
AGE_BANDS = ((18, "0-17"), (30, "18-29"), (45, "30-44"), (60, "45-59"))
OLDEST_BAND = "60+"


def offence_group(tipo_delito: str | None) -> str:
    return OFFENCE_GROUPS.get((tipo_delito or "").strip().upper(), OTHER_GROUP)


def age_band(age: int | None) -> str:
    if age is None:
        return UNKNOWN
    for upper, label in AGE_BANDS:
        if age < upper:
            return label
    return OLDEST_BAND


def _text(value: Any, lower: bool) -> str:
    text = str(value).strip() if value is not None else ""
    if not text:
        return UNKNOWN
    return text.lower() if lower else text.upper()


def _row(attributes: dict[str, Any]) -> tuple[str, ...]:
    return (
        offence_group(attributes.get("TIPO_DELITO")),
        _text(attributes.get("SITUACION_JURIDICA"), lower=True),
        _text(attributes.get("GENERO"), lower=True),
        age_band(attributes.get("EDAD")),
        _text(attributes.get("DEPARTAMENTO_DOMICILIO"), lower=False),
        _text(attributes.get("PROVINCIA_DOMICILIO"), lower=False),
        _text(attributes.get("DISTRITO_DOMICILIO"), lower=False),
    )


def _query(client: RobotsCheckedClient, url: str, **params: Any) -> dict[str, Any]:
    query = {
        "where": WHERE,
        "outFields": ",".join(FIELDS),
        "returnGeometry": "false",
        "f": "json",
        **params,
    }
    try:
        response = client.get(url, params=query)
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


def _check_fields(url: str, features: Iterable[dict[str, Any]]) -> None:
    extra = {name for f in features for name in f["attributes"]} - set(FIELDS)
    if extra:
        raise RuntimeError(
            f"{url}: the server returned fields outside the outFields "
            f"whitelist: {', '.join(sorted(extra))}"
        )


def load_inmates(
    client: RobotsCheckedClient, url: str, con: duckdb.DuckDBPyConnection
) -> int:
    """Page through the layer in ObjectId order into the `inmates` table."""
    con.execute(
        "CREATE TABLE inmates (grupo_delito VARCHAR, situacion_juridica VARCHAR, "
        "sexo VARCHAR, grupo_edad VARCHAR, departamento VARCHAR, "
        "provincia VARCHAR, distrito VARCHAR)"
    )
    offset = 0
    while True:
        page = _query(
            client,
            url,
            orderByFields="ObjectId",
            resultOffset=offset,
            resultRecordCount=PAGE_SIZE,
        )
        features = page["features"]
        _check_fields(url, features)
        con.executemany(
            "INSERT INTO inmates VALUES (?, ?, ?, ?, ?, ?, ?)",
            [_row(f["attributes"]) for f in features],
        )
        offset += len(features)
        print(f"read {offset} rows")
        if not features or not page.get("exceededTransferLimit"):
            return offset


def _write_table(
    con: duckdb.DuckDBPyConnection, path: Path, group_column: bool, where: str
) -> int:
    """Write the cells of `inmates` matching `where`, folding the small ones.
    Returns how many inmates were left out because the fold was still small."""
    group = "grupo_delito, " if group_column else ""
    con.execute(
        f"""
        CREATE OR REPLACE TEMP TABLE cells AS
        SELECT {group}situacion_juridica, sexo, grupo_edad,
               departamento, provincia, distrito, COUNT(*) AS n
        FROM inmates
        WHERE {where}
        GROUP BY ALL
        """
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    con.execute(
        f"""
        COPY (
            SELECT {group}situacion_juridica, sexo, grupo_edad,
                   departamento, provincia, distrito,
                   CAST(n AS INTEGER) AS internos
            FROM cells WHERE n >= $min
            UNION ALL
            SELECT {group}situacion_juridica, sexo, grupo_edad,
                   NULL, NULL, '{OTHER_DISTRICT}', CAST(SUM(n) AS INTEGER)
            FROM cells WHERE n < $min
            GROUP BY {group}situacion_juridica, sexo, grupo_edad
            HAVING SUM(n) >= $min
            ORDER BY ALL
        ) TO $path (FORMAT PARQUET)
        """,
        {"min": MIN_CELL, "path": str(path)},
    )
    left_out = con.execute(
        f"""
        SELECT coalesce(SUM(folded), 0) FROM (
            SELECT SUM(n) AS folded FROM cells WHERE n < $min
            GROUP BY {group}situacion_juridica, sexo, grupo_edad
            HAVING SUM(n) < $min
        )
        """,
        {"min": MIN_CELL},
    ).fetchone()
    assert left_out is not None
    return int(left_out[0])


def run(out_dir: Path, url: str = LAYER_URL, delay: float = DELAY) -> tuple[Path, Path]:
    all_path = out_dir / ALL_TABLE_NAME
    extortion_path = out_dir / EXTORTION_TABLE_NAME
    con = duckdb.connect()
    with httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=60) as raw_client:
        client = RobotsCheckedClient(
            raw_client, USER_AGENT, delay, open_on_robots_error=True
        )
        expected = fetch_count(client, url)
        print(f"layer reports {expected} rows")
        loaded = load_inmates(client, url, con)
    if loaded != expected:
        print(f"warning: read {loaded} rows but the layer counted {expected}")
    for path, group_column, where in (
        (all_path, True, "TRUE"),
        (extortion_path, False, "grupo_delito = 'extorsion'"),
    ):
        left_out = _write_table(con, path, group_column, where)
        if left_out:
            print(f"{path.name}: {left_out} inmates in cells under {MIN_CELL} left out")
    con.close()
    return all_path, extortion_path
