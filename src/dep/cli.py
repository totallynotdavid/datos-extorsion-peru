import argparse
from pathlib import Path

from dep import export_web, sidpol


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
        "--ign-file",
        type=Path,
        help="Read IGN district polygons from this GeoJSON file instead of "
        "querying the FeatureServer.",
    )
    export_cmd.add_argument(
        "--ign-url",
        help="Query this ArcGIS FeatureServer URL instead of the IGN default.",
    )

    args = parser.parse_args(argv)
    if args.command == "sidpol":
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
    elif args.command == "export-web":
        try:
            export_web.run(
                out_dir=args.out_dir,
                parquet_path=args.parquet,
                ign_file=args.ign_file,
                ign_url=args.ign_url,
            )
        except RuntimeError as exc:
            raise SystemExit(f"dep: error: {exc}") from exc
