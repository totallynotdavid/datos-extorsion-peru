import itertools
import threading
import time
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest

from dep.http import RobotsCheckedClient


@pytest.fixture
def server() -> Iterator[tuple[str, list[float]]]:
    times: list[float] = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            times.append(time.monotonic())
            self.send_response(200)
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"ok")

        def log_message(self, format: str, *args: object) -> None:
            pass

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}", times
    httpd.shutdown()
    thread.join()


def test_requests_to_one_host_are_spaced_by_the_delay(
    server: tuple[str, list[float]],
) -> None:
    base, times = server
    with httpx.Client() as raw:
        client = RobotsCheckedClient(raw, "test-agent", 0.4)
        for n in range(3):
            client.get(f"{base}/page-{n}")

    assert len(times) == 4  # robots.txt and three pages
    assert min(b - a for a, b in itertools.pairwise(times)) >= 0.35
