import time
import urllib.robotparser
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any
from urllib.parse import urlsplit

import httpx


class RobotsCheckedClient:
    """HTTP client that fetches each host's robots.txt once and refuses any
    request to a path it disallows. At least `delay` seconds pass between two
    requests to one host."""

    def __init__(
        self, client: httpx.Client, user_agent: str, delay: float = 0.0
    ) -> None:
        self._client = client
        self._user_agent = user_agent
        self._delay = delay
        self._parsers: dict[str, urllib.robotparser.RobotFileParser] = {}
        self._last_request: dict[str, float] = {}

    def _wait_turn(self, url: str) -> None:
        host = urlsplit(url).netloc
        last = self._last_request.get(host)
        if last is not None:
            remaining = self._delay - (time.monotonic() - last)
            if remaining > 0:
                time.sleep(remaining)
        self._last_request[host] = time.monotonic()

    def _parser(self, origin: str) -> urllib.robotparser.RobotFileParser:
        if origin not in self._parsers:
            parser = urllib.robotparser.RobotFileParser()
            robots_url = f"{origin}/robots.txt"
            self._wait_turn(robots_url)
            try:
                response = self._client.get(robots_url)
            except httpx.HTTPError as exc:
                raise RuntimeError(f"{robots_url}: {exc}") from exc
            if response.status_code == 429:
                raise RuntimeError(f"{robots_url}: HTTP 429, source stopped")
            if response.status_code == 200:
                parser.parse(response.text.splitlines())
            elif response.status_code in (401, 403):
                parser.parse(["User-agent: *", "Disallow: /"])
            else:
                parser.parse(["User-agent: *", "Disallow:"])
            self._parsers[origin] = parser
        return self._parsers[origin]

    def _require_allowed(self, url: str) -> None:
        parts = urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        if not self._parser(origin).can_fetch(self._user_agent, url):
            raise RuntimeError(f"robots.txt at {origin} disallows {url}")

    def get(self, url: str, **kwargs: Any) -> httpx.Response:
        self._require_allowed(url)
        self._wait_turn(url)
        return self._client.get(url, **kwargs)

    @contextmanager
    def stream(self, url: str, **kwargs: Any) -> Iterator[httpx.Response]:
        self._require_allowed(url)
        self._wait_turn(url)
        with self._client.stream("GET", url, **kwargs) as response:
            yield response
