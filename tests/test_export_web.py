import json
import subprocess
import sys
from pathlib import Path

import duckdb

IGN_GEOJSON = {
    "type": "FeatureCollection",
    "features": [
        {
            "type": "Feature",
            "properties": {
                "UBIGEO": "150101",
                "NOMBDIST": "LIMA",
                "NOMBPROV": "LIMA",
                "NOMBDEP": "LIMA",
            },
            "geometry": {
                "type": "Polygon",
                "coordinates": [
                    [
                        [-77.1, -12.0],
                        [-76.9, -12.0],
                        [-76.9, -12.2],
                        [-77.1, -12.2],
                        [-77.1, -12.0],
                    ]
                ],
            },
        },
        {
            "type": "Feature",
            "properties": {
                "UBIGEO": "010202",
                "NOMBDIST": "ARAMANGO",
                "NOMBPROV": "BAGUA",
                "NOMBDEP": "AMAZONAS",
            },
            "geometry": {
                "type": "Polygon",
                "coordinates": [
                    [
                        [-78.5, -5.5],
                        [-78.3, -5.5],
                        [-78.3, -5.7],
                        [-78.5, -5.7],
                        [-78.5, -5.5],
                    ]
                ],
            },
        },
    ],
}


def write_parquet(path: Path) -> None:
    duckdb.execute(
        "COPY (SELECT * FROM (VALUES "
        "('010202', 'ARAMANGO', 'BAGUA', 'AMAZONAS', 2018, 1, 5), "
        "('010202', 'ARAMANGO', 'BAGUA', 'AMAZONAS', 2018, 3, 2), "
        "('150101', 'LIMA', 'LIMA', 'LIMA', 2018, 2, 3)) "
        "t(ubigeo, distrito, provincia, departamento, anio, mes, denuncias)) "
        f"TO '{path}' (FORMAT PARQUET)"
    )


def write_population(path: Path) -> None:
    duckdb.execute(
        "COPY (SELECT * FROM (VALUES "
        "('010202', 2018, 9000), "
        "('150101', 2018, 300000), "
        "('160405', 2018, 1000)) "
        "t(ubigeo, anio, poblacion)) "
        f"TO '{path}' (FORMAT PARQUET)"
    )


def test_export_web_writes_both_files(tmp_path: Path) -> None:
    parquet = tmp_path / "extorsion_distrito_mes.parquet"
    write_parquet(parquet)
    population = tmp_path / "poblacion_distrito_anio.parquet"
    write_population(population)
    ign = tmp_path / "ign.geojson"
    ign.write_text(json.dumps(IGN_GEOJSON), encoding="utf-8")
    out_dir = tmp_path / "data"

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "dep",
            "export-web",
            "--parquet",
            str(parquet),
            "--population",
            str(population),
            "--ign-file",
            str(ign),
            "--out-dir",
            str(out_dir),
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    # 160405 exists in the INEI table but not in the IGN catalog.
    assert "1 INEI district" in result.stdout

    districts = json.loads((out_dir / "districts.geojson").read_text())
    assert {f["properties"]["ubigeo"] for f in districts["features"]} == {
        "010202",
        "150101",
    }
    lima = next(
        f for f in districts["features"] if f["properties"]["ubigeo"] == "150101"
    )
    assert lima["properties"]["nombre"] == "LIMA"
    ring = lima["geometry"]["coordinates"][0]
    assert ring[0] == ring[-1]

    extorsion = json.loads((out_dir / "extorsion.json").read_text())
    assert extorsion["months"] == ["2018-01", "2018-02", "2018-03"]
    assert extorsion["rows"]["010202"] == [5, 0, 2]
    assert extorsion["rows"]["150101"] == [0, 3, 0]
    assert extorsion["population"] == {
        "010202": {"2018": 9000},
        "150101": {"2018": 300000},
    }


def run_export(
    parquet: Path, ign: Path, out_dir: Path
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "dep",
            "export-web",
            "--parquet",
            str(parquet),
            "--population",
            str(parquet.parent / "no_population.parquet"),
            "--ign-file",
            str(ign),
            "--out-dir",
            str(out_dir),
        ],
        capture_output=True,
        text=True,
    )


def empty_feature(ubigeo: str) -> dict[str, object]:
    return {
        "type": "Feature",
        "properties": {
            "UBIGEO": ubigeo,
            "NOMBDIST": "EMPTY",
            "NOMBPROV": "P",
            "NOMBDEP": "D",
        },
        "geometry": {"type": "Polygon", "coordinates": []},
    }


def test_export_web_patches_ign_empty_geometry(tmp_path: Path) -> None:
    parquet = tmp_path / "extorsion_distrito_mes.parquet"
    write_parquet(parquet)
    collection = json.loads(json.dumps(IGN_GEOJSON))
    # The IGN layer returns empty geometries for 040101 and 040126.
    collection["features"].append(empty_feature("040101"))
    ign = tmp_path / "ign.geojson"
    ign.write_text(json.dumps(collection), encoding="utf-8")
    out_dir = tmp_path / "data"

    result = run_export(parquet, ign, out_dir)
    assert result.returncode == 0, result.stderr

    districts = json.loads((out_dir / "districts.geojson").read_text())
    arequipa = next(
        f for f in districts["features"] if f["properties"]["ubigeo"] == "040101"
    )
    assert arequipa["geometry"]["coordinates"]


def test_export_web_fails_on_missing_ign_code(tmp_path: Path) -> None:
    parquet = tmp_path / "extorsion_distrito_mes.parquet"
    write_parquet(parquet)
    collection = json.loads(json.dumps(IGN_GEOJSON))
    collection["features"].append(empty_feature("999999"))
    ign = tmp_path / "ign.geojson"
    ign.write_text(json.dumps(collection), encoding="utf-8")

    result = run_export(parquet, ign, tmp_path / "data")
    assert result.returncode != 0
    assert "999999" in result.stderr
