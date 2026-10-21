import json
import subprocess
import sys
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import duckdb

IGN_FIXTURE = {
    "features": [
        {
            "attributes": {
                "UBIGEO": "010202",
                "NOMBDIST": "ARAMANGO",
                "NOMBPROV": "BAGUA",
                "NOMBDEP": "AMAZONAS",
            }
        },
        {
            "attributes": {
                "UBIGEO": "150101",
                "NOMBDIST": "LIMA",
                "NOMBPROV": "LIMA",
                "NOMBDEP": "LIMA",
            }
        },
    ]
}

HEADER = (
    '"ANIO","MES","DPTO_HECHO_NEW","PROV_HECHO","DIST_HECHO",'
    '"UBIGEO_HECHO","P_MODALIDADES","cantidad"'
)

CSV_ROWS = [
    '"2018","1","AMAZONAS","BAGUA","ARAMANGO","10202","Extorsión",5',
    '"2018","1","AMAZONAS","BAGUA","ARAMANGO","10202","Robo",11',
    '"2018","2","LIMA","LIMA","LIMA","150101","Extorsión",3',
]


def write_csv(path: Path, rows: list[str]) -> Path:
    path.write_text(HEADER + "\n" + "\n".join(rows) + "\n", encoding="utf-8")
    return path


def run_sidpol(*args: str) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        [sys.executable, "-m", "dep", "sidpol", *args],
        capture_output=True,
        text=True,
    )
    return result


def read_out(out_dir: Path) -> list[tuple[object, ...]]:
    return duckdb.execute(
        "SELECT ubigeo, distrito, provincia, departamento, anio, mes, denuncias "
        f"FROM read_parquet('{out_dir}/extorsion_distrito_mes.parquet') "
        "ORDER BY ubigeo, anio, mes"
    ).fetchall()


def test_sidpol_replaces_table_and_pads_ubigeo(tmp_path: Path) -> None:
    ign = tmp_path / "ign.json"
    ign.write_text(json.dumps(IGN_FIXTURE), encoding="utf-8")
    out_dir = tmp_path / "out"

    first = write_csv(tmp_path / "first.csv", CSV_ROWS)
    result = run_sidpol(
        "--csv-file", str(first), "--ign-file", str(ign), "--out-dir", str(out_dir)
    )
    assert result.returncode == 0, result.stderr
    assert read_out(out_dir) == [
        ("010202", "ARAMANGO", "BAGUA", "AMAZONAS", 2018, 1, 5),
        ("150101", "LIMA", "LIMA", "LIMA", 2018, 2, 3),
    ]

    # The publisher revises counts after publication, so a rerun replaces the
    # whole table. Here January 2018 goes from 5 to 9 and a new month appears.
    second = write_csv(
        tmp_path / "second.csv",
        [
            '"2018","1","AMAZONAS","BAGUA","ARAMANGO","10202","Extorsión",9',
            '"2018","3","AMAZONAS","BAGUA","ARAMANGO","10202","Extorsión",2',
        ],
    )
    result = run_sidpol(
        "--csv-file",
        str(second),
        "--ign-file",
        str(ign),
        "--out-dir",
        str(out_dir),
    )
    assert result.returncode == 0, result.stderr
    assert read_out(out_dir) == [
        ("010202", "ARAMANGO", "BAGUA", "AMAZONAS", 2018, 1, 9),
        ("010202", "ARAMANGO", "BAGUA", "AMAZONAS", 2018, 3, 2),
    ]


def serve(directory: Path) -> tuple[ThreadingHTTPServer, str]:
    class Handler(SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            super().__init__(*args, directory=str(directory), **kwargs)

        def log_message(self, *args):  # type: ignore[no-untyped-def]
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_port}"


def fixture_site(tmp_path: Path, robots: str) -> Path:
    site = tmp_path / "site"
    (site / "allowed").mkdir(parents=True)
    (site / "private").mkdir(parents=True)
    (site / "robots.txt").write_text(robots, encoding="utf-8")
    (site / "ign.json").write_text(json.dumps(IGN_FIXTURE), encoding="utf-8")
    write_csv(site / "allowed" / "denuncias.csv", CSV_ROWS)
    write_csv(site / "private" / "denuncias.csv", CSV_ROWS)
    return site


def test_sidpol_obeys_robots_txt(tmp_path: Path) -> None:
    site = fixture_site(tmp_path, "User-agent: *\nDisallow: /private/\n")
    server, base = serve(site)
    out_dir = tmp_path / "out"

    try:
        allowed = run_sidpol(
            "--csv-url",
            f"{base}/allowed/denuncias.csv",
            "--ign-url",
            f"{base}/ign.json",
            "--out-dir",
            str(out_dir),
        )
        assert allowed.returncode == 0, allowed.stderr
        assert read_out(out_dir) == [
            ("010202", "ARAMANGO", "BAGUA", "AMAZONAS", 2018, 1, 5),
            ("150101", "LIMA", "LIMA", "LIMA", 2018, 2, 3),
        ]

        blocked = run_sidpol(
            "--csv-url",
            f"{base}/private/denuncias.csv",
            "--ign-url",
            f"{base}/ign.json",
            "--out-dir",
            str(out_dir),
        )
        assert blocked.returncode == 1
        assert "robots.txt" in blocked.stderr
        assert "/private/denuncias.csv" in blocked.stderr
    finally:
        server.shutdown()
