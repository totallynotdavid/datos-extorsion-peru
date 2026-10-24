"""Build the district x month extortion table from SIDPOL denuncias.

Counts are revised after publication, so every run replaces the whole output
table.
"""

import json
import tempfile
from collections.abc import Iterable
from pathlib import Path
from typing import Any, cast

import duckdb
import httpx

from dep.http import RobotsCheckedClient

USER_AGENT = "datos-extorsion-peru/0.1"
CATALOG_URL = "https://www.datosabiertos.gob.pe/api/3/action/package_show"
DATASET_ID = "denuncias-policiales-registradas-nivel-nacional-mininter"
CUMULATIVE_NAME_PREFIX = "DataSet Denuncias Policiales - Enero 2018 a"
IGN_URL = (
    "https://www.idep.gob.pe/geoportal/rest/services/"
    "DATOS_GEOESPACIALES/L%C3%8DMITES/FeatureServer/5/query"
)
IGN_PAGE_SIZE = 1000
OUTPUT_NAME = "extorsion_distrito_mes.parquet"


def resolve_csv_url(client: RobotsCheckedClient) -> str:
    """Return the download URL of the cumulative denuncias resource.

    The published file name changes with each release. The URL is resolved
    from the catalog record instead of hardcoded.
    """
    response = client.get(CATALOG_URL, params={"id": DATASET_ID})
    response.raise_for_status()
    result = response.json()["result"]
    dataset = result[0] if isinstance(result, list) else result
    resources = dataset["resources"]
    for resource in resources:
        if resource.get("format") == "csv" and resource.get("name", "").startswith(
            CUMULATIVE_NAME_PREFIX
        ):
            return str(resource["url"])
    raise RuntimeError(f"No cumulative denuncias CSV found in dataset {DATASET_ID}")


def resolve_dataset_csv_url(
    client: RobotsCheckedClient, catalog_url: str, dataset_id: str
) -> str:
    """Return the download URL of the one CSV resource of a catalog dataset.

    The published file name changes with each release, so the URL comes from
    the catalog record.
    """
    response = client.get(catalog_url, params={"id": dataset_id})
    response.raise_for_status()
    result = response.json()["result"]
    dataset = result[0] if isinstance(result, list) else result
    urls = [
        str(resource["url"])
        for resource in dataset["resources"]
        if str(resource.get("format", "")).lower().lstrip(".") == "csv"
    ]
    if len(urls) != 1:
        raise RuntimeError(
            f"expected one CSV resource in dataset {dataset_id}, found {len(urls)}"
        )
    return urls[0]


def download_csv(client: RobotsCheckedClient, url: str, dest: Path) -> None:
    with client.stream(url) as response:
        response.raise_for_status()
        with dest.open("wb") as fh:
            for chunk in response.iter_bytes():
                fh.write(chunk)


def fetch_districts(client: RobotsCheckedClient, url: str) -> list[dict[str, Any]]:
    districts: list[dict[str, Any]] = []
    offset = 0
    while True:
        response = client.get(
            url,
            params={
                "where": "1=1",
                "outFields": "UBIGEO,NOMBDIST,NOMBPROV,NOMBDEP",
                "returnGeometry": "false",
                "orderByFields": "OBJECTID",
                "resultOffset": offset,
                "resultRecordCount": IGN_PAGE_SIZE,
                "f": "json",
            },
        )
        response.raise_for_status()
        page = response.json()
        if "error" in page:
            raise RuntimeError(f"IGN FeatureServer error: {page['error']}")
        districts.extend(f["attributes"] for f in page["features"])
        if not page.get("exceededTransferLimit"):
            return districts
        offset += IGN_PAGE_SIZE


def load_districts_file(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, dict) and "features" in data:
        return cast("list[dict[str, Any]]", [f["attributes"] for f in data["features"]])
    return cast("list[dict[str, Any]]", data)


def build_table(
    csv_path: Path, districts: Iterable[dict[str, Any]], out_path: Path
) -> None:
    con = duckdb.connect()
    con.execute(
        "CREATE TABLE districts (ubigeo VARCHAR, distrito VARCHAR, "
        "provincia VARCHAR, departamento VARCHAR)"
    )
    con.executemany(
        "INSERT INTO districts VALUES (?, ?, ?, ?)",
        [
            (str(d["UBIGEO"]).zfill(6), d["NOMBDIST"], d["NOMBPROV"], d["NOMBDEP"])
            for d in districts
        ],
    )
    con.execute(
        """
        CREATE TABLE counts AS
        SELECT lpad(trim(UBIGEO_HECHO), 6, '0') AS ubigeo,
               CAST(ANIO AS INTEGER) AS anio,
               CAST(MES AS INTEGER) AS mes,
               CAST(SUM(CAST(cantidad AS INTEGER)) AS INTEGER) AS denuncias
        FROM read_csv(?, header = true, all_varchar = true)
        WHERE P_MODALIDADES = 'Extorsión'
        GROUP BY ubigeo, anio, mes
        """,
        [str(csv_path)],
    )
    unmatched = con.execute(
        "SELECT COUNT(*) FROM counts c LEFT JOIN districts d "
        "ON c.ubigeo = d.ubigeo WHERE d.ubigeo IS NULL"
    ).fetchone()
    assert unmatched is not None
    if unmatched[0]:
        print(
            f"warning: {unmatched[0]} district-month rows have a UBIGEO "
            "absent from the IGN catalog",
        )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    con.execute(
        """
        COPY (
            SELECT c.ubigeo,
                   d.distrito,
                   d.provincia,
                   d.departamento,
                   c.anio,
                   c.mes,
                   c.denuncias
            FROM counts c
            LEFT JOIN districts d ON c.ubigeo = d.ubigeo
            ORDER BY c.ubigeo, c.anio, c.mes
        ) TO ? (FORMAT PARQUET)
        """,
        [str(out_path)],
    )
    con.close()


def run(
    out_dir: Path,
    csv_file: Path | None = None,
    ign_file: Path | None = None,
    csv_url: str | None = None,
    ign_url: str | None = None,
) -> Path:
    with tempfile.TemporaryDirectory() as work_dir:
        with httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=60) as raw_client:
            client = RobotsCheckedClient(raw_client, USER_AGENT)
            if csv_file is not None:
                csv_path = csv_file
            else:
                url = csv_url or resolve_csv_url(client)
                csv_path = Path(work_dir) / "denuncias.csv"
                print(f"downloading {url}")
                download_csv(client, url, csv_path)
            if ign_file is not None:
                districts = load_districts_file(ign_file)
            else:
                districts = fetch_districts(client, ign_url or IGN_URL)
        out_path = out_dir / OUTPUT_NAME
        build_table(csv_path, districts, out_path)
    return out_path
