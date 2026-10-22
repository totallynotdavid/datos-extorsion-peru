import os
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, cast

import anthropic
import duckdb

from dep.news import parse_fragment, parse_page, raw_path

MODEL = "claude-haiku-5-5"
OUTPUT_NAME = "news_events.parquet"
MAX_ARTICLE_CHARS = 8_000

VICTIM_TYPES = frozenset(
    {"negocio", "transporte", "vivienda", "persona", "institucion", "otro"}
)
METHODS = frozenset(
    {
        "amenaza",
        "disparo",
        "explosivo",
        "cobro_de_cupo",
        "gota_a_gota",
        "secuestro",
        "otro",
    }
)
CONFIDENCES = frozenset({"low", "medium", "high"})

TOOL = {
    "name": "extract_extortion_events",
    "description": "Extract one structured object for each extortion event in the article.",
    "input_schema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "events": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "event_date": {
                            "type": ["string", "null"],
                            "description": (
                                "ISO date after resolving relative dates against the "
                                "published date, or null when the article gives none."
                            ),
                        },
                        "district": {"type": ["string", "null"]},
                        "province": {"type": ["string", "null"]},
                        "department": {"type": ["string", "null"]},
                        "victim_type": {"type": "string", "enum": sorted(VICTIM_TYPES)},
                        "method": {"type": "string", "enum": sorted(METHODS)},
                        "gang": {"type": ["string", "null"]},
                        "confidence": {
                            "type": "string",
                            "enum": sorted(CONFIDENCES),
                        },
                    },
                    "required": [
                        "event_date",
                        "district",
                        "province",
                        "department",
                        "victim_type",
                        "method",
                        "gang",
                        "confidence",
                    ],
                },
            }
        },
        "required": ["events"],
    },
}


class ExtractionError(RuntimeError):
    pass


@dataclass(frozen=True)
class Candidate:
    url: str
    source: str
    published: date


@dataclass(frozen=True)
class EventRow:
    url: str
    source: str
    published: date
    event_date: date | None
    district: str | None
    province: str | None
    department: str | None
    victim_type: str | None
    method: str | None
    gang: str | None
    confidence: str | None
    model: str
    extracted_at: datetime
    has_event: bool


def resolve_event_date(value: str | None) -> date | None:
    """Validate the model's already-resolved ISO event date."""
    if value is None:
        return None
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise ValueError(f"event_date must be an ISO date or null, got {value!r}")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"invalid event_date {value!r}") from exc


def _article_text(candidate: Candidate, raw_dir: Path) -> str:
    path = raw_path(raw_dir, candidate.source, candidate.url)
    try:
        markup = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ExtractionError(
            f"{candidate.url}: cannot read raw article {path}"
        ) from exc
    try:
        text = parse_page(candidate.url, markup).text
    except RuntimeError as exc:
        text = parse_fragment(markup)
        if not text:
            raise ExtractionError(
                f"{candidate.url}: cannot parse raw article: {exc}"
            ) from exc
    return text[:MAX_ARTICLE_CHARS]


def _prompt(candidate: Candidate, text: str) -> str:
    return f"""You extract extortion events from a Peruvian news article.

The article was published on {candidate.published.isoformat()}. Return one event
for each distinct extortion event, or an empty events list when there is none.
Resolve Spanish relative dates such as "ayer" and "el lunes" against that
published date and return event_date as YYYY-MM-DD. Use null when the article
does not give an event date. Preserve district, province, and department names
exactly as the article names them, when present.

Use only these victim_type values: negocio, transporte, vivienda, persona,
institucion, otro. Use only these method values: amenaza, disparo, explosivo,
cobro_de_cupo, gota_a_gota, secuestro, otro. Use low, medium, or high for
confidence. Gang names are allowed. Do not return article text, quotes, or the
names of victims, suspects, police, or other people. The only name that may be
returned is a criminal organisation in gang.

Article text:
{text}"""


def _tool_input(response: Any, url: str) -> dict[str, Any]:
    blocks = [
        block
        for block in response.content
        if getattr(block, "type", None) == "tool_use"
    ]
    if len(blocks) != 1 or not isinstance(blocks[0].input, dict):
        raise ExtractionError(
            f"{url}: Anthropic reply has no single structured tool result"
        )
    value = blocks[0].input
    if "events" not in value or not isinstance(value["events"], list):
        raise ExtractionError(
            f"{url}: invalid structured reply: expected an events list"
        )
    return {"events": value["events"]}


def _event_values(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("event is not an object")
    fields = {
        "event_date",
        "district",
        "province",
        "department",
        "victim_type",
        "method",
        "gang",
        "confidence",
    }
    missing = fields - value.keys()
    if missing:
        raise ValueError(f"event is missing {', '.join(sorted(missing))}")

    nullable = ("event_date", "district", "province", "department", "gang")
    for field in nullable:
        if value[field] is not None and not isinstance(value[field], str):
            raise ValueError(f"{field} must be a string or null")
    if value["victim_type"] not in VICTIM_TYPES:
        raise ValueError(f"invalid victim_type {value['victim_type']!r}")
    if value["method"] not in METHODS:
        raise ValueError(f"invalid method {value['method']!r}")
    if value["confidence"] not in CONFIDENCES:
        raise ValueError(f"invalid confidence {value['confidence']!r}")

    return {
        "event_date": resolve_event_date(value["event_date"]),
        "district": value["district"],
        "province": value["province"],
        "department": value["department"],
        "victim_type": value["victim_type"],
        "method": value["method"],
        "gang": value["gang"],
        "confidence": value["confidence"],
    }


def extract_article(
    client: anthropic.Anthropic, candidate: Candidate, text: str
) -> list[dict[str, Any]]:
    try:
        response = client.messages.create(
            model=MODEL,
            max_tokens=2_000,
            messages=cast(Any, [{"role": "user", "content": _prompt(candidate, text)}]),
            tools=cast(Any, [TOOL]),
            tool_choice=cast(Any, {"type": "tool", "name": TOOL["name"]}),
        )
    except anthropic.APIStatusError as exc:
        status = getattr(exc, "status_code", None)
        if status == 429 or (isinstance(status, int) and status >= 500):
            raise ExtractionError(
                f"{candidate.url}: Anthropic API HTTP {status}; extraction stopped"
            ) from exc
        raise ExtractionError(
            f"{candidate.url}: Anthropic API request failed: {exc}"
        ) from exc
    except anthropic.APIError as exc:
        raise ExtractionError(
            f"{candidate.url}: Anthropic API request failed: {exc}"
        ) from exc

    result = _tool_input(response, candidate.url)
    try:
        return [_event_values(item) for item in result["events"]]
    except (TypeError, ValueError) as exc:
        raise ExtractionError(
            f"{candidate.url}: invalid structured reply: {exc}"
        ) from exc


def _read_candidates(path: Path) -> list[Candidate]:
    try:
        rows = duckdb.execute(
            "SELECT url, source, published FROM read_parquet(?) ORDER BY published, url",
            [str(path)],
        ).fetchall()
    except duckdb.Error as exc:
        raise ExtractionError(f"cannot read candidates {path}: {exc}") from exc
    candidates: list[Candidate] = []
    for url, source, published in rows:
        if (
            not isinstance(url, str)
            or not isinstance(source, str)
            or not isinstance(published, date)
        ):
            raise ExtractionError(
                f"{path}: candidate has invalid url, source, or published"
            )
        candidates.append(Candidate(url, source, published))
    return candidates


def _done_urls(path: Path) -> set[str]:
    if not path.exists():
        return set()
    try:
        rows = duckdb.execute(
            "SELECT DISTINCT url FROM read_parquet(?) WHERE model = ?",
            [str(path), MODEL],
        ).fetchall()
    except duckdb.Error as exc:
        raise ExtractionError(f"cannot read existing events {path}: {exc}") from exc
    return {url for (url,) in rows}


def _write_table(path: Path, rows: list[EventRow], replaced_urls: set[str]) -> None:
    con = duckdb.connect()
    con.execute(
        "CREATE TABLE events ("
        "url VARCHAR, source VARCHAR, published DATE, event_date DATE, "
        "district VARCHAR, province VARCHAR, department VARCHAR, "
        "victim_type VARCHAR, method VARCHAR, gang VARCHAR, confidence VARCHAR, "
        "model VARCHAR, extracted_at TIMESTAMPTZ, has_event BOOLEAN)"
    )
    if path.exists():
        con.execute("INSERT INTO events SELECT * FROM read_parquet(?)", [str(path)])
    if replaced_urls:
        con.execute(
            "DELETE FROM events WHERE model = ? AND list_contains(?, url)",
            [MODEL, list(replaced_urls)],
        )
    if rows:
        con.executemany(
            "INSERT INTO events VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    row.url,
                    row.source,
                    row.published,
                    row.event_date,
                    row.district,
                    row.province,
                    row.department,
                    row.victim_type,
                    row.method,
                    row.gang,
                    row.confidence,
                    row.model,
                    row.extracted_at,
                    row.has_event,
                )
                for row in rows
            ],
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(path.name + ".partial")
    con.execute(
        "COPY (SELECT * FROM events ORDER BY published, url, event_date) TO ? "
        "(FORMAT PARQUET)",
        [str(partial)],
    )
    con.close()
    os.replace(partial, path)


def run(
    candidates_path: Path,
    raw_dir: Path,
    out_dir: Path,
    limit: int,
    force: bool,
) -> Path:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise ExtractionError("ANTHROPIC_API_KEY is required")
    candidates = _read_candidates(candidates_path)
    out_path = out_dir / OUTPUT_NAME
    if force:
        pending = candidates[:limit]
    else:
        done = _done_urls(out_path)
        pending = [candidate for candidate in candidates if candidate.url not in done]
        pending = pending[:limit]
    client_kwargs: dict[str, Any] = {"api_key": api_key, "max_retries": 0}
    base_url = os.environ.get("ANTHROPIC_BASE_URL")
    if base_url:
        client_kwargs["base_url"] = base_url
    client = anthropic.Anthropic(**client_kwargs)

    event_count = 0
    for candidate in pending:
        text = _article_text(candidate, raw_dir)
        values = extract_article(client, candidate, text)
        timestamp = datetime.now(UTC)
        extracted = [
            EventRow(
                url=candidate.url,
                source=candidate.source,
                published=candidate.published,
                model=MODEL,
                extracted_at=timestamp,
                has_event=True,
                **value,
            )
            for value in values
        ] or [
            EventRow(
                url=candidate.url,
                source=candidate.source,
                published=candidate.published,
                event_date=None,
                district=None,
                province=None,
                department=None,
                victim_type=None,
                method=None,
                gang=None,
                confidence=None,
                model=MODEL,
                extracted_at=timestamp,
                has_event=False,
            )
        ]
        _write_table(out_path, extracted, {candidate.url})
        event_count += len(values)
    print(f"processed {len(pending)} articles, wrote {event_count} events")
    return out_path
