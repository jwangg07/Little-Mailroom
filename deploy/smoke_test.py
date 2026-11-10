"""Unsigned HTTP checks; run through docker exec or against a public Function URL."""

import json
import sys
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit
from urllib.request import urlopen


class Assets(HTMLParser):
    def __init__(self):
        super().__init__()
        self.paths = []
        self.has_root = False

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "div" and attrs.get("id") == "root":
            self.has_root = True
        if tag == "script" and attrs.get("src"):
            self.paths.append(attrs["src"])
        if tag == "link" and attrs.get("rel") == "stylesheet":
            self.paths.append(attrs["href"])


def check(base):
    base = base.rstrip("/") + "/"
    with urlopen(urljoin(base, "health"), timeout=30) as response:
        assert response.status == 200
        assert json.load(response) == {"status": "ok"}
    with urlopen(base, timeout=30) as response:
        assert response.status == 200
        assert "text/html" in response.headers["Content-Type"]
        page = response.read().decode()
    assets = Assets()
    assets.feed(page)
    assert assets.has_root and assets.paths, "Built React app missing"
    for path in assets.paths:
        url = urljoin(base, path)
        assert urlsplit(url).netloc == urlsplit(base).netloc, "Unexpected asset origin"
        with urlopen(url, timeout=30) as response:
            assert response.status == 200 and response.read(), "Asset unavailable"
            assert "text/html" not in response.headers.get("Content-Type", "")
    with urlopen(urljoin(base, "auth/me"), timeout=30) as response:
        assert json.load(response) == {"user": None}
        assert response.headers.get("Cache-Control") == "no-store"
    print("Frontend assets, health, and unsigned authentication checks passed")


if __name__ == "__main__":
    check(sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000")
