"""Build victim-profile tables from the Callao regional government's SIDPOL extract.

The extract is one row per reported fact with the sex, age and occupation of
the person the report relates to. Only the columns in COLUMNS are read: the
others identify a person or a place (document id, birth date, address,
description, coordinates), so they are dropped at the read step and never held
in memory or written. Every run replaces both output tables.
"""

import re
import tempfile
from pathlib import Path

import duckdb
import httpx

from dep.http import RobotsCheckedClient
from dep.sidpol import (
    CATALOG_URL,
    USER_AGENT,
    download_csv,
    resolve_dataset_csv_url,
)

DATASET_ID = (
    "registros-de-hechos-delictivos-en-la-provincia-constitucional-del-callao-"
    "gobierno-regional"
)
DELAY = 1.5
COLUMNS = ("FECHA_HECHO", "MODALIDAD", "SUBTIPO", "EDAD_PERSONA", "SEXO", "OCUPACION")
FIRST_YEAR = 2023
MIN_CELL = 5
EXTORTION_SUBTYPE = "EXTORSION"
EXTORTION_MODALITIES = ("EXTORSION", "CHANTAJE")
SEX_AGE_TABLE_NAME = "victimas_sexo_edad_mes.parquet"
OCCUPATION_TABLE_NAME = "victimas_ocupacion_mes.parquet"
MISSING = "sin_dato"
FOLDED = "otros"
UNMAPPED = "otro"
SEXES = {"FEMENINO": "femenino", "MASCULINO": "masculino"}

# The first matching group wins. Keep specific groups above general ones, such
# as security above transport. Patterns start at a word boundary because the
# extract truncates long values, such as INDEPENDIENT.
OCCUPATION_GROUPS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("hogar", ("AMA DE CASA", "SERVICIO DOMESTICO", "CUIDADORA")),
    ("estudiante", ("ESTUDIANTE", "ESCOLAR", "UNIVERSITARI", "ALUMN")),
    ("sin_empleo_o_jubilado", ("DESEMPLEADO", "JUBILADO", "PENSIONISTA")),
    (
        "seguridad_y_fuerzas",
        (
            "SEGURIDAD",
            "AGENTE DE SEGURIDAD",
            "VIGILANTE",
            "SERENAZGO",
            "GUARDIAN",
            "PNP",
            "POLICIA",
            "RETIRADO PNP",
            "MILITAR",
            "SUB ?OFICIAL",
            "SO BRIG",
            "TNTE",
            "OFICIAL",
        ),
    ),
    (
        "profesional_tecnico",
        (
            "DOCENTE",
            "PROFESOR",
            "ABOGAD",
            "INGENIER",
            "MEDICO",
            "ENFERMER",
            "LIC ENFERMERIA",
            "ODONTOLOG",
            "PSICOLOG",
            "CONTADOR",
            "FARMACEUTIC",
            "OBSTETRIZ",
            "TERAPIST",
            "TECNIC",
            "TECNOLOG",
            "ANALISTA",
            "DISE[ÑN]ADOR",
            "COMUNICADOR",
            "MUSICO",
            "CANTANTE",
        ),
    ),
    (
        "transporte",
        (
            "CHOFER",
            "CONDUCTOR",
            "MOTOTAXI",
            "TAXISTA",
            "DELIVERY",
            "MOTORIZADO",
            "COBRADOR",
            "TRANSPORT",
            "AUXILIAR DE TRANSPORTE",
        ),
    ),
    (
        "obrero_oficio",
        (
            "OBRERO",
            "OPERARIO",
            "OPERADOR",
            "CARPINT",
            "SOLDAD",
            "MECANIC",
            "ALBA[ÑN]IL",
            "ELECTRIC",
            "PINTOR",
            "CERRAJ",
            "COSTUR",
            "ESTIBADOR",
            "PESCADOR",
            "AGRICULTOR",
            "CONSTRUCCION",
            "AYUDANTE",
            "RECICLADOR",
            "ARTESANO",
            "BARBERO",
            "MANICURISTA",
            "PEDICURE",
            "COSMETOLOG",
            "CARNICERO",
            "COCINERO",
            "REPOSTERA",
            "MOZO",
            "PLANCHADOR",
            "LUMINOTECNICO",
            "MAQUINARIA",
            "MANTENIMIENTO",
            "PORTERA",
        ),
    ),
    (
        "comercio_independiente",
        (
            "INDEPENDIENT",
            "TRABAJO INDEPENDIENT",
            "COMERCIANTE",
            "VENDEDOR",
            "NEGOCIANTE",
            "EMPRENDEDOR",
            "EMPRESARI",
            "TELEVENTAS",
            "MERCADERISTA",
            "PROMOTORA",
            "AGENTE DE VENTAS",
            "ASESOR(?:A)? DE VENTAS",
            "JEFE DE VENTAS",
        ),
    ),
    (
        "empleado_administrativo",
        (
            "EMPLEAD",
            "ADMINISTRAD",
            "ASISTENTE",
            "SECRETARI",
            "CAJER",
            "COORDINADOR",
            "SUPERVISOR",
            "FISCALIZADOR",
            "EJECUTIV",
            "ASESOR",
            "ATENCION AL CLIENTE",
            "TELEOPERAD",
            "DESPACHADOR",
            "AGENTE DE ADUANA",
            "AUXILIAR",
            "ENCARGADO",
        ),
    ),
)
_OCCUPATION_PATTERNS = tuple(
    (group, re.compile(r"\b(?:" + "|".join(patterns) + ")"))
    for group, patterns in OCCUPATION_GROUPS
)


def age_band(age: int | None) -> str:
    if age is None or age < 0:
        return MISSING
    if age < 18:
        return "0-17"
    if age < 30:
        return "18-29"
    if age < 45:
        return "30-44"
    if age < 60:
        return "45-59"
    return "60+"


def occupation_group(text: str | None) -> str:
    value = (text or "").strip().upper()
    if value in ("", "NO INDICA"):
        return MISSING
    for group, pattern in _OCCUPATION_PATTERNS:
        if pattern.search(value):
            return group
    return UNMAPPED


def _load_facts(con: duckdb.DuckDBPyConnection, csv_path: Path) -> None:
    """Read the extract into `facts`, one row per reported fact in range, with
    the sex, age band and occupation group already coded. The run log counts
    what is left out."""
    try:
        con.execute(
            f"""
            CREATE TABLE raw AS
            WITH extract AS (
                SELECT {", ".join(COLUMNS)}
                FROM read_csv(?, delim = ';', header = true, all_varchar = true)
            )
            SELECT CAST(try_strptime(trim(FECHA_HECHO), '%Y%m%d') AS DATE) AS fecha,
                   upper(trim(MODALIDAD)) AS modalidad,
                   upper(trim(SUBTIPO)) AS subtipo,
                   try_cast(trim(EDAD_PERSONA) AS INTEGER) AS edad,
                   upper(trim(SEXO)) AS sexo,
                   upper(trim(OCUPACION)) AS ocupacion
            FROM extract
            """,
            [str(csv_path)],
        )
    except duckdb.Error as exc:
        raise RuntimeError(f"{csv_path}: {exc}") from exc
    total, undated, early = con.execute(
        "SELECT COUNT(*), COUNT(*) FILTER (WHERE fecha IS NULL), "
        "COUNT(*) FILTER (WHERE year(fecha) < ?) FROM raw",
        [FIRST_YEAR],
    ).fetchone() or (0, 0, 0)
    print(f"read {total} rows")
    print(f"dropped {early} rows dated before {FIRST_YEAR}")
    if undated:
        print(f"dropped {undated} rows with no readable date")
    occupations = con.execute(
        "SELECT DISTINCT ocupacion FROM raw WHERE ocupacion IS NOT NULL"
    ).fetchall()
    con.execute("CREATE TABLE occupations (ocupacion VARCHAR, grupo VARCHAR)")
    con.executemany(
        "INSERT INTO occupations VALUES (?, ?)",
        [(o[0], occupation_group(o[0])) for o in occupations],
    )
    ages = con.execute(
        "SELECT DISTINCT edad FROM raw WHERE edad IS NOT NULL"
    ).fetchall()
    con.execute("CREATE TABLE ages (edad INTEGER, banda_edad VARCHAR)")
    con.executemany(
        "INSERT INTO ages VALUES (?, ?)",
        [(a[0], age_band(a[0])) for a in ages],
    )
    con.execute(
        f"""
        CREATE TABLE facts AS
        SELECT CAST(year(fecha) AS INTEGER) AS anio,
               CAST(month(fecha) AS INTEGER) AS mes,
               subtipo = '{EXTORTION_SUBTYPE}'
                   AND modalidad IN {EXTORTION_MODALITIES} AS extorsion,
               CASE sexo {" ".join(f"WHEN '{k}' THEN '{v}'" for k, v in SEXES.items())}
                    ELSE '{MISSING}' END AS sexo,
               coalesce(banda_edad, '{MISSING}') AS banda_edad,
               coalesce(grupo, '{MISSING}') AS ocupacion
        FROM raw LEFT JOIN occupations USING (ocupacion)
             LEFT JOIN ages USING (edad)
        WHERE year(fecha) >= ?
        """,
        [FIRST_YEAR],
    )
    kept, extortion = con.execute(
        "SELECT COUNT(*), COUNT(*) FILTER (WHERE extorsion) FROM facts"
    ).fetchone() or (0, 0)
    print(f"kept {kept} rows, {extortion} of them extortion")


def _publish(
    con: duckdb.DuckDBPyConnection, dims: tuple[str, ...], out_path: Path
) -> None:
    """Write the counts of `facts` by month and `dims`, for extortion and for
    all crimes.

    A cell under MIN_CELL is folded into an `otros` row of its crime and month.
    A folded row still under MIN_CELL is not published, because its count
    alone would single out a few people. The run log counts both."""
    keys = ", ".join(("delito", "anio", "mes", *dims))
    folded_dims = ", ".join(f"'{FOLDED}' AS {d}" for d in dims)
    con.execute(
        f"""
        CREATE OR REPLACE TABLE cells AS
        WITH scoped AS (
            SELECT 'todos' AS delito, * FROM facts
            UNION ALL
            SELECT 'extorsion' AS delito, * FROM facts WHERE extorsion
        ),
        counted AS (
            SELECT {keys}, CAST(COUNT(*) AS INTEGER) AS denuncias
            FROM scoped GROUP BY {keys}
        )
        SELECT {keys}, denuncias, FALSE AS folded FROM counted
        WHERE denuncias >= {MIN_CELL}
        UNION ALL
        SELECT delito, anio, mes, {folded_dims},
               CAST(SUM(denuncias) AS INTEGER), TRUE
        FROM counted WHERE denuncias < {MIN_CELL}
        GROUP BY delito, anio, mes
        """
    )
    folded, suppressed = con.execute(
        "SELECT COALESCE(SUM(denuncias) FILTER (WHERE folded), 0), "
        f"COALESCE(SUM(denuncias) FILTER (WHERE folded AND denuncias < {MIN_CELL}), 0)"
        " FROM cells"
    ).fetchone() or (0, 0)
    print(
        f"{out_path.name}: {folded} reports in cells under {MIN_CELL} folded "
        f"into {FOLDED}, {suppressed} of them in {FOLDED} rows still under "
        f"{MIN_CELL} and not published"
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    con.execute(
        f"""
        COPY (
            SELECT {keys}, denuncias FROM cells
            WHERE denuncias >= {MIN_CELL}
            ORDER BY {keys}
        ) TO ? (FORMAT PARQUET)
        """,
        [str(out_path)],
    )


def build_tables(csv_path: Path, out_dir: Path) -> tuple[Path, Path]:
    con = duckdb.connect()
    try:
        _load_facts(con, csv_path)
        sex_age_path = out_dir / SEX_AGE_TABLE_NAME
        occupation_path = out_dir / OCCUPATION_TABLE_NAME
        _publish(con, ("sexo", "banda_edad"), sex_age_path)
        _publish(con, ("ocupacion",), occupation_path)
    finally:
        con.close()
    return sex_age_path, occupation_path


def run(
    out_dir: Path,
    csv_file: Path | None = None,
    csv_url: str | None = None,
    catalog_url: str = CATALOG_URL,
    delay: float = DELAY,
) -> tuple[Path, Path]:
    if csv_file is not None:
        return build_tables(csv_file, out_dir)
    with tempfile.TemporaryDirectory() as work_dir:
        with httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=60) as raw_client:
            client = RobotsCheckedClient(raw_client, USER_AGENT, delay)
            url = csv_url or resolve_dataset_csv_url(client, catalog_url, DATASET_ID)
            csv_path = Path(work_dir) / "callao.csv"
            print(f"downloading {url}")
            try:
                download_csv(client, url, csv_path)
            except httpx.HTTPError as exc:
                raise RuntimeError(f"{url}: {exc}") from exc
        return build_tables(csv_path, out_dir)
