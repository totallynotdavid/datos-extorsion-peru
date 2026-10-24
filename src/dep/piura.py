"""Build the sentences table from the Piura judicial district's open data.

The extract is one row per person sentenced to an effective custodial penalty.
It carries no crime date and no crime place: the year is the sentence's and the
province is the court's. The table counts convictions, not incidents.

Only the columns in COLUMNS are read. The others identify a person (document
id, birth date, case number), so they are dropped at the read step and never
held in memory or written. A read error is reported without the library's
message, because that message can quote a row. Every run replaces the table.
"""

import re
import tempfile
import unicodedata
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
    "situación-jurídica-de-procesos-penales-con-penas-privativas-de-libertad-efectivas"
)
DELAY = 1.5
ENCODING = "latin-1"
COLUMNS = ("DELITO", "PROVINCIA", "FECHA_SENTENCIA")
MIN_CELL = 5
TABLE_NAME = "sentencias_piura_anio_provincia_delito.parquet"
FOLDED = "otros"
MISSING = "sin_dato"
OTHER = "other"

# The first matching group wins. Patterns run on the accent-free upper-case
# DELITO, which is free text: the court's registry spells one offence several
# ways. A DELITO that matches no pattern, or a generic aggravating clause that
# does not name its offence, is `other`.
OFFENCE_GROUPS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("extorsion", ("EXTORSION",)),
    (
        "sexual",
        (
            r"VIOLACION (?:SEXUAL|DE LA LIBERTAD SEXUAL|A PERSONA)",
            "ACTOS CONTRA EL PUDOR",
            "TOCAMIENTOS",
            "ACCESO CARNAL",
            "CONNOTACION SEXUAL",
            "LIBERTAD SEXUAL",
            "EXPLOTACION SEXUAL",
            "PROPOSICIONES",
            "PORNOGRAFIA",
            "PROXENETISMO",
            "PROSTITUCION",
        ),
    ),
    (
        "homicide",
        ("HOMICIDIO", "ASESINATO", "FEMINICIDIO", "PARRICIDIO", "SICARIATO"),
    ),
    (
        "drugs",
        (
            "DROGAS",
            "MICROCOMERCIALIZACION",
            "MICROPRODUCCION",
            "AMAPOLA",
            r"COCA\b",
            "INSUMOS QUIMICOS",
        ),
    ),
    ("robbery", (r"ROBO\b",)),
)
_OFFENCE_PATTERNS = tuple(
    (group, re.compile(r"\b(?:" + "|".join(patterns) + ")"))
    for group, patterns in OFFENCE_GROUPS
)


def offence_group(text: str | None) -> str:
    plain = unicodedata.normalize("NFD", (text or "").upper())
    value = "".join(c for c in plain if not unicodedata.combining(c))
    for group, pattern in _OFFENCE_PATTERNS:
        if pattern.search(value):
            return group
    return OTHER


def _load_sentences(con: duckdb.DuckDBPyConnection, csv_path: Path) -> None:
    """Read the extract into `sentences`, one row per sentence with a readable
    date, with the offence group already coded. The run log counts what is
    left out."""
    try:
        con.execute(
            f"""
            CREATE TABLE raw AS
            WITH extract AS (
                SELECT {", ".join(COLUMNS)}
                FROM read_csv(?, delim = ',', header = true, all_varchar = true,
                              encoding = '{ENCODING}')
            )
            SELECT trim(DELITO) AS delito,
                   coalesce(nullif(upper(trim(PROVINCIA)), ''), '{MISSING}')
                       AS court_province,
                   CAST(try_strptime(trim(FECHA_SENTENCIA), '%Y%m%d') AS DATE)
                       AS fecha
            FROM extract
            """,
            [str(csv_path)],
        )
    except duckdb.Error as exc:
        raise RuntimeError(
            f"{csv_path}: cannot read the CSV ({type(exc).__name__}); the "
            "message is withheld because it can quote a row"
        ) from None
    total, undated = con.execute(
        "SELECT COUNT(*), COUNT(*) FILTER (WHERE fecha IS NULL) FROM raw"
    ).fetchone() or (0, 0)
    print(f"read {total} rows")
    if undated:
        print(f"dropped {undated} rows with no readable sentence date")
    delitos = con.execute("SELECT DISTINCT delito FROM raw").fetchall()
    con.execute("CREATE TABLE offences (delito VARCHAR, offence_group VARCHAR)")
    con.executemany(
        "INSERT INTO offences VALUES (?, ?)",
        [(d[0], offence_group(d[0])) for d in delitos],
    )
    con.execute(
        """
        CREATE TABLE sentences AS
        SELECT CAST(year(fecha) AS INTEGER) AS sentence_year,
               court_province,
               offence_group
        FROM raw LEFT JOIN offences ON raw.delito IS NOT DISTINCT FROM offences.delito
        WHERE fecha IS NOT NULL
        """
    )
    kept, extortion = con.execute(
        "SELECT COUNT(*), COUNT(*) FILTER (WHERE offence_group = 'extorsion') "
        "FROM sentences"
    ).fetchone() or (0, 0)
    print(f"kept {kept} rows, {extortion} of them extortion")


def _publish(con: duckdb.DuckDBPyConnection, out_path: Path) -> None:
    """Write the counts of `sentences` by year, court province and offence
    group.

    A cell under MIN_CELL is folded into an `otros` province row of its group
    and year. A folded row still under MIN_CELL is not published, because its
    count alone would single out a few people. The run log counts both."""
    con.execute(
        f"""
        CREATE TABLE cells AS
        WITH counted AS (
            SELECT sentence_year, court_province, offence_group,
                   CAST(COUNT(*) AS INTEGER) AS sentences
            FROM sentences GROUP BY ALL
        )
        SELECT sentence_year, court_province, offence_group, sentences,
               FALSE AS folded
        FROM counted WHERE sentences >= {MIN_CELL}
        UNION ALL
        SELECT sentence_year, '{FOLDED}', offence_group,
               CAST(SUM(sentences) AS INTEGER), TRUE
        FROM counted WHERE sentences < {MIN_CELL}
        GROUP BY sentence_year, offence_group
        """
    )
    folded, suppressed = con.execute(
        "SELECT COALESCE(SUM(sentences) FILTER (WHERE folded), 0), "
        f"COALESCE(SUM(sentences) FILTER (WHERE folded AND sentences < {MIN_CELL}), 0)"
        " FROM cells"
    ).fetchone() or (0, 0)
    print(
        f"{out_path.name}: {folded} sentences in cells under {MIN_CELL} folded "
        f"into {FOLDED}, {suppressed} of them in {FOLDED} rows still under "
        f"{MIN_CELL} and not published"
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    con.execute(
        f"""
        COPY (
            SELECT sentence_year, court_province, offence_group, sentences
            FROM cells WHERE sentences >= {MIN_CELL}
            ORDER BY sentence_year, offence_group, court_province
        ) TO ? (FORMAT PARQUET)
        """,
        [str(out_path)],
    )


def build_table(csv_path: Path, out_dir: Path) -> Path:
    con = duckdb.connect()
    try:
        _load_sentences(con, csv_path)
        out_path = out_dir / TABLE_NAME
        _publish(con, out_path)
    finally:
        con.close()
    return out_path


def run(
    out_dir: Path,
    csv_file: Path | None = None,
    csv_url: str | None = None,
    catalog_url: str = CATALOG_URL,
    delay: float = DELAY,
) -> Path:
    if csv_file is not None:
        return build_table(csv_file, out_dir)
    with tempfile.TemporaryDirectory() as work_dir:
        with httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=60) as raw_client:
            client = RobotsCheckedClient(raw_client, USER_AGENT, delay)
            url = csv_url or resolve_dataset_csv_url(client, catalog_url, DATASET_ID)
            csv_path = Path(work_dir) / "piura.csv"
            print(f"downloading {url}")
            try:
                download_csv(client, url, csv_path)
            except httpx.HTTPError as exc:
                raise RuntimeError(f"{url}: {exc}") from exc
        return build_table(csv_path, out_dir)
