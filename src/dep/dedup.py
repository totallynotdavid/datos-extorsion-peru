import os
from pathlib import Path

import duckdb

INPUT_NAME = "news_events.parquet"
OUTPUT_NAME = "news_events_dedup.parquet"


class DedupError(RuntimeError):
    pass


def _fold(column: str) -> str:
    return (
        f"NULLIF(regexp_replace(strip_accents(lower(trim({column}))), "
        "'\\s+', ' ', 'g'), '')"
    )


# An event is a cluster of rows that agree on date, folded district and victim
# type. Within that group each named gang is its own event. Rows without a gang
# join the group's only named gang; when the group names two or more gangs they
# could belong to either, so they stay together as an event without a gang.
# Rows missing a date or district never cluster: each is its own event.
_QUERY = f"""
WITH src AS (
    SELECT DISTINCT url, published, event_date, district, province, department,
        victim_type, method, gang, confidence
    FROM read_parquet(?)
    WHERE has_event
),
keyed AS (
    SELECT *,
        {_fold("district")} AS district_key,
        {_fold("gang")} AS gang_key,
        CASE confidence WHEN 'high' THEN 3 WHEN 'medium' THEN 2 ELSE 1 END
            AS confidence_rank
    FROM src
),
groups AS (
    SELECT event_date, district_key, victim_type,
        count(DISTINCT gang_key) AS gang_count, min(gang_key) AS only_gang
    FROM keyed
    WHERE event_date IS NOT NULL AND district_key IS NOT NULL
    GROUP BY ALL
),
clustered AS (
    SELECT k.*,
        g.event_date IS NULL AS unplaced,
        CASE
            WHEN g.event_date IS NULL THEN CAST(row_number() OVER () AS VARCHAR)
            ELSE concat_ws('|', k.event_date, k.district_key, k.victim_type,
                coalesce(k.gang_key, CASE WHEN g.gang_count = 1 THEN g.only_gang END,
                    ''))
        END AS cluster
    FROM keyed k
    LEFT JOIN groups g
        ON k.event_date = g.event_date
        AND k.district_key = g.district_key
        AND k.victim_type = g.victim_type
),
ranked AS (
    SELECT *, row_number() OVER (
        PARTITION BY cluster, unplaced
        ORDER BY confidence_rank DESC, published, url
    ) AS pick
    FROM clustered
)
SELECT
    event_date,
    arg_min(district, pick) AS district,
    arg_min(province, pick) AS province,
    arg_min(department, pick) AS department,
    victim_type,
    arg_min(method, pick) AS method,
    arg_min(gang, pick) FILTER (WHERE gang IS NOT NULL) AS gang,
    CASE max(confidence_rank) WHEN 3 THEN 'high' WHEN 2 THEN 'medium' ELSE 'low' END
        AS confidence,
    CAST(count(DISTINCT url) AS INTEGER) AS n_sources,
    list(DISTINCT url ORDER BY url) AS urls,
    min(published) AS published,
    unplaced
FROM ranked
GROUP BY cluster, unplaced, event_date, victim_type
ORDER BY event_date NULLS LAST, district, victim_type, gang, urls
"""


def run(events_path: Path, out_dir: Path) -> Path:
    if not events_path.exists():
        raise DedupError(f"cannot read events {events_path}: no such file")
    out_path = out_dir / OUTPUT_NAME
    out_dir.mkdir(parents=True, exist_ok=True)
    partial = out_path.with_name(out_path.name + ".partial")
    con = duckdb.connect()
    try:
        con.execute(f"CREATE TABLE merged AS {_QUERY}", [str(events_path)])
        con.execute("COPY merged TO ? (FORMAT PARQUET)", [str(partial)])
    except duckdb.Error as exc:
        partial.unlink(missing_ok=True)
        raise DedupError(f"cannot dedup events {events_path}: {exc}") from exc
    finally:
        con.close()
    os.replace(partial, out_path)
    return out_path
