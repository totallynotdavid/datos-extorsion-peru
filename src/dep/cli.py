import argparse
from datetime import date, timedelta
from pathlib import Path

from dep import (
    callao,
    dedup,
    export_web,
    extract,
    news,
    population,
    sidpol,
    sidpol_live,
)


def _month(text: str) -> date:
    try:
        year, month = text.split("-")
        if len(year) != 4 or len(month) != 2:
            raise ValueError
        return date(int(year), int(month), 1)
    except ValueError:
        raise argparse.ArgumentTypeError(f"expected YYYY-MM, got {text!r}") from None


def _month_end(first: date) -> date:
    return (first.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(
        days=1
    )


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="dep",
        description="Open data pipeline for crime and extortion events in Peru.",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    sidpol_cmd = commands.add_parser(
        "sidpol",
        help="Download the SIDPOL denuncias CSV and write the district x month "
        "extortion table to data/out/.",
    )
    sidpol_cmd.add_argument(
        "--out-dir",
        type=Path,
        default=Path("data/out"),
        help="Directory for the output Parquet file (default: data/out).",
    )
    sidpol_cmd.add_argument(
        "--csv-file",
        type=Path,
        help="Read the denuncias CSV from this path instead of downloading it.",
    )
    sidpol_cmd.add_argument(
        "--csv-url",
        help="Download the denuncias CSV from this URL instead of resolving it "
        "from the open-data catalog.",
    )
    sidpol_cmd.add_argument(
        "--ign-file",
        type=Path,
        help="Read IGN districts from this ArcGIS JSON file instead of querying "
        "the FeatureServer.",
    )
    sidpol_cmd.add_argument(
        "--ign-url",
        help="Query this ArcGIS FeatureServer URL instead of the IGN default.",
    )

    live_cmd = commands.add_parser(
        "sidpol-live",
        help="Read the live SIDPOL ArcGIS layer and write the district x month "
        "x shift and police sector x month extortion tables to data/out/.",
    )
    live_cmd.add_argument(
        "--out-dir",
        type=Path,
        default=Path("data/out"),
        help="Directory for the output Parquet files (default: data/out).",
    )
    live_cmd.add_argument(
        "--url",
        default=sidpol_live.LAYER_URL,
        help="Query this ArcGIS layer URL instead of the Mininter default.",
    )
    live_cmd.add_argument(
        "--csv-table",
        type=Path,
        help="District x month table from `dep sidpol` to compare against "
        "(default: extorsion_distrito_mes.parquet in --out-dir).",
    )
    live_cmd.add_argument(
        "--delay",
        type=float,
        default=sidpol_live.DELAY,
        help=f"Seconds between requests (default: {sidpol_live.DELAY}).",
    )

    callao_cmd = commands.add_parser(
        "callao",
        help="Download the Callao regional government's SIDPOL extract and write "
        "the sex x age band x month and occupation group x month victim-profile "
        "tables to data/out/.",
    )
    callao_cmd.add_argument(
        "--out-dir",
        type=Path,
        default=Path("data/out"),
        help="Directory for the output Parquet files (default: data/out).",
    )
    callao_cmd.add_argument(
        "--csv-file",
        type=Path,
        help="Read the extract from this path instead of downloading it.",
    )
    callao_cmd.add_argument(
        "--csv-url",
        help="Download the extract from this URL instead of resolving it from "
        "the open-data catalog.",
    )
    callao_cmd.add_argument(
        "--catalog-url",
        default=sidpol.CATALOG_URL,
        help="Resolve the CSV URL from this CKAN package_show URL "
        "(default: the open-data catalog).",
    )
    callao_cmd.add_argument(
        "--delay",
        type=float,
        default=callao.DELAY,
        help=f"Seconds between requests (default: {callao.DELAY}).",
    )

    population_cmd = commands.add_parser(
        "population",
        help="Download the INEI district population projections workbook and "
        "write the district x year population table to data/out/.",
    )
    population_cmd.add_argument(
        "--out-dir",
        type=Path,
        default=Path("data/out"),
        help="Directory for the output Parquet file (default: data/out).",
    )
    population_cmd.add_argument(
        "--xlsx-file",
        type=Path,
        help="Read the projections workbook from this path instead of downloading it.",
    )
    population_cmd.add_argument(
        "--xlsx-url",
        help="Download the workbook from this URL instead of the INEI default.",
    )

    export_cmd = commands.add_parser(
        "export-web",
        help="Build web/public/data/districts.geojson and extorsion.json from "
        "the district x month table and the IGN district polygons.",
    )
    export_cmd.add_argument(
        "--out-dir",
        type=Path,
        default=Path("web/public/data"),
        help="Directory for the output files (default: web/public/data).",
    )
    export_cmd.add_argument(
        "--parquet",
        type=Path,
        default=Path("data/out/extorsion_distrito_mes.parquet"),
        help="District x month table (default: "
        "data/out/extorsion_distrito_mes.parquet).",
    )
    export_cmd.add_argument(
        "--population",
        type=Path,
        default=Path("data/out/poblacion_distrito_anio.parquet"),
        help="District x year population table (default: "
        "data/out/poblacion_distrito_anio.parquet).",
    )
    export_cmd.add_argument(
        "--ign-file",
        type=Path,
        help="Read IGN district polygons from this GeoJSON file instead of "
        "querying the FeatureServer.",
    )
    export_cmd.add_argument(
        "--ign-url",
        help="Query this ArcGIS FeatureServer URL instead of the IGN default.",
    )

    news_cmd = commands.add_parser(
        "news",
        help="Collect candidate extortion articles from four Peruvian outlets "
        "into data/out/news_candidates.parquet.",
    )
    news_cmd.add_argument(
        "--from",
        dest="start",
        type=_month,
        required=True,
        metavar="YYYY-MM",
        help="First month to collect.",
    )
    news_cmd.add_argument(
        "--to",
        dest="end",
        type=_month,
        required=True,
        metavar="YYYY-MM",
        help="Last month to collect.",
    )
    news_cmd.add_argument(
        "--source",
        action="append",
        choices=list(news.SOURCES),
        help="Collect only this source. Repeat for several (default: all).",
    )
    news_cmd.add_argument(
        "--source-url",
        action="append",
        default=[],
        metavar="NAME=URL",
        help="Fetch source NAME from this base URL instead of its outlet.",
    )
    news_cmd.add_argument(
        "--out-dir",
        type=Path,
        default=Path("data/out"),
        help="Directory for the output Parquet file (default: data/out).",
    )
    news_cmd.add_argument(
        "--raw-dir",
        type=Path,
        default=Path("data/raw/news"),
        help="Directory for the raw article HTML (default: data/raw/news).",
    )

    extract_cmd = commands.add_parser(
        "extract",
        help="Extract extortion events from news candidates into "
        "data/out/news_events.parquet.",
    )
    extract_cmd.add_argument(
        "--candidates",
        type=Path,
        default=Path("data/out/news_candidates.parquet"),
        help="Candidate Parquet file (default: data/out/news_candidates.parquet).",
    )
    extract_cmd.add_argument(
        "--raw-dir",
        type=Path,
        default=Path("data/raw/news"),
        help="Directory for raw article HTML (default: data/raw/news).",
    )
    extract_cmd.add_argument(
        "--out-dir",
        type=Path,
        default=Path("data/out"),
        help="Directory for the output Parquet file (default: data/out).",
    )
    extract_cmd.add_argument(
        "--limit",
        type=int,
        default=10,
        help="Process at most this many articles (default: 10).",
    )
    extract_cmd.add_argument(
        "--force",
        action="store_true",
        help="Reprocess URLs that already have rows from this model.",
    )

    dedup_cmd = commands.add_parser(
        "dedup",
        help="Merge extracted events that report the same incident into "
        "data/out/news_events_dedup.parquet.",
    )
    dedup_cmd.add_argument(
        "--events",
        type=Path,
        default=Path("data/out") / dedup.INPUT_NAME,
        help="Events Parquet file from `dep extract` "
        "(default: data/out/news_events.parquet).",
    )
    dedup_cmd.add_argument(
        "--out-dir",
        type=Path,
        default=Path("data/out"),
        help="Directory for the output Parquet file (default: data/out).",
    )

    args = parser.parse_args(argv)
    if args.command == "news":
        if args.start > args.end:
            parser.error("--from is after --to")
        base_urls: dict[str, str] = {}
        for item in args.source_url:
            name, _, url = item.partition("=")
            if name not in news.SOURCES or not url:
                parser.error(f"--source-url expects NAME=URL, got {item!r}")
            base_urls[name] = url
        try:
            out = news.run(
                out_dir=args.out_dir,
                raw_dir=args.raw_dir,
                start=args.start,
                end=_month_end(args.end),
                sources=list(dict.fromkeys(args.source or news.SOURCES)),
                base_urls=base_urls,
            )
        except RuntimeError as exc:
            raise SystemExit(f"dep: error: {exc}") from exc
        print(out)
    elif args.command == "extract":
        if args.limit < 0:
            parser.error("--limit must not be negative")
        try:
            out = extract.run(
                candidates_path=args.candidates,
                raw_dir=args.raw_dir,
                out_dir=args.out_dir,
                limit=args.limit,
                force=args.force,
            )
        except RuntimeError as exc:
            raise SystemExit(f"dep: error: {exc}") from exc
        print(out)
    elif args.command == "dedup":
        try:
            out = dedup.run(events_path=args.events, out_dir=args.out_dir)
        except RuntimeError as exc:
            raise SystemExit(f"dep: error: {exc}") from exc
        print(out)
    elif args.command == "sidpol":
        try:
            out = sidpol.run(
                out_dir=args.out_dir,
                csv_file=args.csv_file,
                ign_file=args.ign_file,
                csv_url=args.csv_url,
                ign_url=args.ign_url,
            )
        except RuntimeError as exc:
            raise SystemExit(f"dep: error: {exc}") from exc
        print(out)
    elif args.command == "callao":
        if args.delay < 0:
            parser.error("--delay must not be negative")
        try:
            sex_age_path, occupation_path = callao.run(
                out_dir=args.out_dir,
                csv_file=args.csv_file,
                csv_url=args.csv_url,
                catalog_url=args.catalog_url,
                delay=args.delay,
            )
        except RuntimeError as exc:
            raise SystemExit(f"dep: error: {exc}") from exc
        print(sex_age_path)
        print(occupation_path)
    elif args.command == "sidpol-live":
        if args.delay < 0:
            parser.error("--delay must not be negative")
        try:
            district_path, sector_path = sidpol_live.run(
                out_dir=args.out_dir,
                url=args.url,
                csv_table=args.csv_table,
                delay=args.delay,
            )
        except RuntimeError as exc:
            raise SystemExit(f"dep: error: {exc}") from exc
        print(district_path)
        print(sector_path)
    elif args.command == "population":
        try:
            out = population.run(
                out_dir=args.out_dir,
                xlsx_file=args.xlsx_file,
                xlsx_url=args.xlsx_url,
            )
        except RuntimeError as exc:
            raise SystemExit(f"dep: error: {exc}") from exc
        print(out)
    elif args.command == "export-web":
        try:
            export_web.run(
                out_dir=args.out_dir,
                parquet_path=args.parquet,
                population_path=args.population,
                ign_file=args.ign_file,
                ign_url=args.ign_url,
            )
        except RuntimeError as exc:
            raise SystemExit(f"dep: error: {exc}") from exc
