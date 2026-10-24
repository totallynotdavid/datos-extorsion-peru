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


def write_shift_table(path: Path) -> None:
    duckdb.execute(
        "COPY (SELECT * FROM (VALUES "
        "('010202', 2018, 1, 'mañana', 4), "
        "('010202', 2018, 1, 'noche', 5), "
        "('150101', 2018, 2, 'tarde', 7)) "
        "t(ubigeo, anio, mes, turno, denuncias)) "
        f"TO '{path}' (FORMAT PARQUET)"
    )


def write_victim_tables(sex_age: Path, occupation: Path) -> None:
    duckdb.execute(
        "COPY (SELECT * FROM (VALUES "
        "('extorsion', 2024, 1, 'femenino', '18-29', 4), "
        "('extorsion', 2024, 1, 'masculino', '30-44', 6), "
        "('extorsion', 2023, 1, 'masculino', '30-44', 9), "
        "('todos', 2024, 1, 'femenino', '18-29', 20)) "
        "t(delito, anio, mes, sexo, banda_edad, denuncias)) "
        f"TO '{sex_age}' (FORMAT PARQUET)"
    )
    duckdb.execute(
        "COPY (SELECT * FROM (VALUES "
        "('extorsion', 2024, 1, 'estudiante', 7), "
        "('extorsion', 2024, 1, 'otro', 3), "
        "('todos', 2024, 1, 'comercio_independiente', 20)) "
        "t(delito, anio, mes, ocupacion, denuncias)) "
        f"TO '{occupation}' (FORMAT PARQUET)"
    )


def object_keys(value: object) -> set[str]:
    if isinstance(value, dict):
        return set(value) | set().union(*(object_keys(item) for item in value.values()))
    if isinstance(value, list):
        return set().union(*(object_keys(item) for item in value))
    return set()


def test_export_web_writes_both_files(tmp_path: Path) -> None:
    parquet = tmp_path / "extorsion_distrito_mes.parquet"
    write_parquet(parquet)
    population = tmp_path / "poblacion_distrito_anio.parquet"
    write_population(population)
    shifts = tmp_path / "extorsion_live_distrito_mes_turno.parquet"
    write_shift_table(shifts)
    victim_sex_age = tmp_path / "victimas_sexo_edad_mes.parquet"
    victim_occupation = tmp_path / "victimas_ocupacion_mes.parquet"
    write_victim_tables(victim_sex_age, victim_occupation)
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
            "--shift-parquet",
            str(shifts),
            "--victim-sex-age-parquet",
            str(victim_sex_age),
            "--victim-occupation-parquet",
            str(victim_occupation),
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

    shifts_json = json.loads((out_dir / "turnos.json").read_text())
    assert shifts_json == {
        "months": ["2018-01", "2018-02"],
        "rows": [
            {
                "ubigeo": "010202",
                "mes": "2018-01",
                "turno": "noche",
                "denuncias": 5,
            },
            {
                "ubigeo": "150101",
                "mes": "2018-02",
                "turno": "tarde",
                "denuncias": 7,
            },
        ],
    }
    victims = json.loads((out_dir / "victimas.json").read_text())
    assert victims == {
        "year": 2024,
        "sex_age": [{"sexo": "masculino", "banda_edad": "30-44", "denuncias": 6}],
        "occupation": [{"ocupacion": "estudiante", "denuncias": 7}],
    }
    assert all(row["denuncias"] >= 5 for row in shifts_json["rows"])
    assert all(row["denuncias"] >= 5 for row in victims["sex_age"])
    assert all(row["denuncias"] >= 5 for row in victims["occupation"])
    assert object_keys(shifts_json) <= {
        "months",
        "rows",
        "ubigeo",
        "mes",
        "turno",
        "denuncias",
    }
    assert object_keys(victims) <= {
        "year",
        "sex_age",
        "sexo",
        "banda_edad",
        "denuncias",
        "occupation",
        "ocupacion",
    }


def run_export(
    parquet: Path, ign: Path, out_dir: Path
) -> subprocess.CompletedProcess[str]:
    shifts = parquet.parent / "extorsion_live_distrito_mes_turno.parquet"
    write_shift_table(shifts)
    victim_sex_age = parquet.parent / "victimas_sexo_edad_mes.parquet"
    victim_occupation = parquet.parent / "victimas_ocupacion_mes.parquet"
    write_victim_tables(victim_sex_age, victim_occupation)
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
            "--shift-parquet",
            str(shifts),
            "--victim-sex-age-parquet",
            str(victim_sex_age),
            "--victim-occupation-parquet",
            str(victim_occupation),
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
