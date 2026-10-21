"""Build the web data files: districts.geojson and extorsion.json.

districts.geojson holds simplified IGN district polygons with a `ubigeo`
property. extorsion.json holds the month axis and per-district monthly counts.
Polygons are simplified until the gzipped GeoJSON fits under GZIP_LIMIT bytes.
"""

import gzip
import importlib.resources
import json
import math
from pathlib import Path
from typing import Any, cast

import duckdb
import httpx

from dep.http import RobotsCheckedClient
from dep.sidpol import IGN_URL, USER_AGENT

IGN_GEOMETRY_PAGE_SIZE = 500

GZIP_LIMIT = 3 * 1024 * 1024
DISTRICTS_NAME = "districts.geojson"
EXTORSION_NAME = "extorsion.json"

# The IGN district layer stores empty geometries for 040101 (Arequipa) and
# 040126 (Yanahuara). The patch polygons come from the OCHA COD-AB release of
# the same IGN boundaries.
PATCH_NAME = "ign_geometry_patch.geojson"


def _ring_area(ring: list[list[float]]) -> float:
    return (
        sum(
            ring[i][0] * ring[i + 1][1] - ring[i + 1][0] * ring[i][1]
            for i in range(len(ring) - 1)
        )
        / 2
    )


def _point_in_ring(point: list[float], ring: list[list[float]]) -> bool:
    x, y = point
    inside = False
    j = len(ring) - 1
    for i in range(len(ring)):
        xi, yi = ring[i]
        xj, yj = ring[j]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi) + xi:
            inside = not inside
        j = i
    return inside


def esri_rings_to_geometry(rings: list[list[list[float]]]) -> dict[str, Any]:
    """Convert Esri rings to a GeoJSON Polygon or MultiPolygon.

    Esri exteriors are clockwise and holes are counter-clockwise. GeoJSON
    wants the opposite, so every ring is reversed.
    """
    outers = [ring for ring in rings if _ring_area(ring) < 0]
    holes = [ring for ring in rings if _ring_area(ring) >= 0]
    polygons = [[list(reversed(outer))] for outer in outers]
    for hole in holes:
        for polygon in polygons:
            if _point_in_ring(hole[0], polygon[0]):
                polygon.append(list(reversed(hole)))
                break
        else:
            polygons.append([list(reversed(hole))])
    if not polygons:
        return {"type": "Polygon", "coordinates": []}
    if len(polygons) == 1:
        return {"type": "Polygon", "coordinates": polygons[0]}
    return {"type": "MultiPolygon", "coordinates": polygons}


def fetch_districts_geojson(client: RobotsCheckedClient, url: str) -> dict[str, Any]:
    features: list[dict[str, Any]] = []
    offset = 0
    while True:
        response = client.get(
            url,
            params={
                "where": "1=1",
                "outFields": "UBIGEO,NOMBDIST,NOMBPROV,NOMBDEP",
                "returnGeometry": "true",
                "outSR": 4326,
                "orderByFields": "OBJECTID",
                "resultOffset": offset,
                "resultRecordCount": IGN_GEOMETRY_PAGE_SIZE,
                "f": "json",
            },
        )
        response.raise_for_status()
        page = response.json()
        if "error" in page:
            raise RuntimeError(f"IGN FeatureServer error: {page['error']}")
        features.extend(
            {
                "type": "Feature",
                "properties": f["attributes"],
                "geometry": esri_rings_to_geometry(f["geometry"]["rings"]),
            }
            for f in page["features"]
        )
        if not page.get("exceededTransferLimit"):
            break
        offset += IGN_GEOMETRY_PAGE_SIZE
    return {"type": "FeatureCollection", "features": features}


def load_geojson_file(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("type") == "FeatureCollection":
        return cast("dict[str, Any]", data)
    if isinstance(data, dict) and data.get("geometryType") and "features" in data:
        return {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "properties": f["attributes"],
                    "geometry": esri_rings_to_geometry(f["geometry"]["rings"]),
                }
                for f in data["features"]
            ],
        }
    raise RuntimeError(f"{path} is neither GeoJSON nor an ArcGIS JSON response")


def _district_ubigeo(props: dict[str, Any]) -> str:
    return str(props.get("UBIGEO") or props.get("ubigeo") or "").zfill(6)


def apply_geometry_patch(collection: dict[str, Any]) -> None:
    """Fill empty district geometries from the bundled patch file."""
    patch = {
        f["properties"]["ubigeo"]: f["geometry"]
        for f in json.loads(
            importlib.resources.files("dep")
            .joinpath(PATCH_NAME)
            .read_text(encoding="utf-8")
        )["features"]
    }
    for feature in collection["features"]:
        geometry = feature.get("geometry") or {}
        if geometry.get("coordinates"):
            continue
        ubigeo = _district_ubigeo(feature.get("properties") or {})
        if ubigeo in patch:
            feature["geometry"] = patch[ubigeo]


def normalize_features(collection: dict[str, Any]) -> dict[str, Any]:
    """Keep only the properties the web app reads, lowercased."""
    features = []
    for feature in collection["features"]:
        if not feature["geometry"].get("coordinates"):
            continue
        props = feature.get("properties") or {}
        features.append(
            {
                "type": "Feature",
                "properties": {
                    "ubigeo": _district_ubigeo(props),
                    "nombre": props.get("NOMBDIST") or props.get("nombre"),
                    "provincia": props.get("NOMBPROV") or props.get("provincia"),
                    "departamento": props.get("NOMBDEP") or props.get("departamento"),
                },
                "geometry": feature["geometry"],
            }
        )
    return {"type": "FeatureCollection", "features": features}


def _perpendicular_distance(
    point: list[float], start: list[float], end: list[float]
) -> float:
    if start == end:
        return math.dist(point, start)
    (x, y), (x1, y1), (x2, y2) = point, start, end
    return abs((y2 - y1) * x - (x2 - x1) * y + x2 * y1 - y2 * x1) / math.hypot(
        y2 - y1, x2 - x1
    )


def _simplify_ring(ring: list[list[float]], tolerance: float) -> list[list[float]]:
    """Ramer-Douglas-Peucker on a closed ring. The closing point is preserved."""
    if len(ring) < 5 or tolerance <= 0:
        return ring
    open_ring = ring[:-1]
    keep = [True] * len(open_ring)
    stack = [(0, len(open_ring) - 1)]
    while stack:
        first, last = stack.pop()
        if last - first < 2:
            continue
        start = open_ring[first]
        end = open_ring[last]
        farthest, distance = first, 0.0
        for i in range(first + 1, last):
            d = _perpendicular_distance(open_ring[i], start, end)
            if d > distance:
                farthest, distance = i, d
        if distance > tolerance:
            stack.extend([(first, farthest), (farthest, last)])
        else:
            for i in range(first + 1, last):
                keep[i] = False
    simplified = [pt for pt, k in zip(open_ring, keep, strict=True) if k]
    if len(simplified) < 3:
        return ring
    simplified.append(list(simplified[0]))
    return simplified


def simplify_collection(collection: dict[str, Any], tolerance: float) -> None:
    for feature in collection["features"]:
        geometry = feature["geometry"]
        if geometry["type"] == "Polygon":
            geometry["coordinates"] = [
                _simplify_ring(ring, tolerance) for ring in geometry["coordinates"]
            ]
        elif geometry["type"] == "MultiPolygon":
            geometry["coordinates"] = [
                [_simplify_ring(ring, tolerance) for ring in polygon]
                for polygon in geometry["coordinates"]
            ]


def _quantize(collection: dict[str, Any], digits: int = 5) -> None:
    def round_coords(coords: Any) -> Any:
        if not coords:
            return coords
        if isinstance(coords[0], (int, float)):
            return [round(c, digits) for c in coords]
        return [round_coords(c) for c in coords]

    for feature in collection["features"]:
        feature["geometry"]["coordinates"] = round_coords(
            feature["geometry"]["coordinates"]
        )


def write_districts(collection: dict[str, Any], out_path: Path) -> set[str]:
    """Write the simplified polygons and return the exported UBIGEO codes."""
    tolerance = 0.0005
    while True:
        candidate = normalize_features(collection)
        simplify_collection(candidate, tolerance)
        _quantize(candidate)
        payload = json.dumps(candidate, separators=(",", ":")).encode("utf-8")
        if len(gzip.compress(payload)) <= GZIP_LIMIT or tolerance >= 0.1:
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_bytes(payload)
            return {str(f["properties"]["ubigeo"]) for f in candidate["features"]}
        tolerance *= 4


def _month_axis(bounds: tuple[int, int, int, int]) -> list[str]:
    min_year, min_month, max_year, max_month = bounds
    months = []
    year, month = min_year, min_month
    while (year, month) <= (max_year, max_month):
        months.append(f"{year:04d}-{month:02d}")
        month += 1
        if month == 13:
            year, month = year + 1, 1
    return months


def load_population(path: Path) -> dict[str, dict[str, int]]:
    con = duckdb.connect()
    out: dict[str, dict[str, int]] = {}
    for ubigeo, anio, poblacion in con.execute(
        "SELECT ubigeo, anio, poblacion FROM read_parquet(?)", [str(path)]
    ).fetchall():
        out.setdefault(str(ubigeo), {})[str(anio)] = int(poblacion)
    con.close()
    return out


def write_extorsion(
    parquet_path: Path,
    out_path: Path,
    population: dict[str, dict[str, int]] | None = None,
) -> dict[str, Any]:
    con = duckdb.connect()
    first = con.execute(
        "SELECT anio, mes FROM read_parquet(?) ORDER BY anio, mes LIMIT 1",
        [str(parquet_path)],
    ).fetchone()
    last = con.execute(
        "SELECT anio, mes FROM read_parquet(?) ORDER BY anio DESC, mes DESC LIMIT 1",
        [str(parquet_path)],
    ).fetchone()
    if first is None or last is None or first[0] is None or last[0] is None:
        con.close()
        raise RuntimeError(f"{parquet_path} has no rows")
    months = _month_axis((first[0], first[1], last[0], last[1]))
    rows: dict[str, list[int]] = {}
    for ubigeo, anio, mes, denuncias in con.execute(
        "SELECT ubigeo, anio, mes, denuncias FROM read_parquet(?) "
        "WHERE ubigeo IS NOT NULL ORDER BY ubigeo, anio, mes",
        [str(parquet_path)],
    ).fetchall():
        index = (anio - first[0]) * 12 + (mes - first[1])
        rows.setdefault(str(ubigeo), [0] * len(months))[index] = int(denuncias)
    con.close()
    payload: dict[str, Any] = {"months": months, "rows": rows}
    if population is not None:
        payload["population"] = population
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
    return payload


def run(
    out_dir: Path,
    parquet_path: Path,
    population_path: Path | None = None,
    ign_file: Path | None = None,
    ign_url: str | None = None,
) -> None:
    if ign_file is not None:
        collection = load_geojson_file(ign_file)
    else:
        with httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=60) as raw_client:
            collection = fetch_districts_geojson(
                RobotsCheckedClient(raw_client, USER_AGENT), ign_url or IGN_URL
            )
    apply_geometry_patch(collection)
    ign_codes = {
        _district_ubigeo(props)
        for f in collection["features"]
        if (props := f.get("properties") or {}).get("UBIGEO") or props.get("ubigeo")
    }
    kept = write_districts(collection, out_dir / DISTRICTS_NAME)
    missing = ign_codes - kept
    if missing:
        raise RuntimeError(
            "IGN districts missing from districts.geojson: "
            + ", ".join(sorted(missing))
        )

    population: dict[str, dict[str, int]] | None = None
    if population_path is not None and population_path.exists():
        population = load_population(population_path)
        dropped = len(set(population) - kept)
        population = {
            ubigeo: years for ubigeo, years in population.items() if ubigeo in kept
        }
        if dropped:
            print(
                f"warning: dropped {dropped} INEI districts absent from "
                "the IGN catalog",
            )
    elif population_path is not None:
        print(
            f"warning: {population_path} not found; extorsion.json has no "
            "population data",
        )
    write_extorsion(parquet_path, out_dir / EXTORSION_NAME, population)
