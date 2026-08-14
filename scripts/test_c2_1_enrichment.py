#!/usr/bin/env python3

import urllib.error
import urllib.request

from c2_1_enrichment import JsonClient


class FakeTextResponse:
    status = 200
    headers = {"Content-Type": "text/html; charset=utf-8"}

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, _limit):
        return b"<html>ok</html>"


def main():
    calls = {"count": 0}
    original = urllib.request.urlopen

    def fail(*_args, **_kwargs):
        calls["count"] += 1
        raise urllib.error.URLError("fixed_test_disconnect")

    urllib.request.urlopen = fail
    try:
        client = JsonClient(timeout=1, sleep=lambda _seconds: None)
        first = client.request("fixed_source", "https://example.invalid")
        assert first[0] == "source_failure" and calls["count"] == 4
        second = client.request("fixed_source", "https://example.invalid")
        assert second[0] == "source_failure" and second[3][0]["attempt"] == 0 and calls["count"] == 4
    finally:
        urllib.request.urlopen = original

    calls = {"broken": 0, "healthy": 0}

    def website_response(request, **_kwargs):
        if "broken.example" in request.full_url:
            calls["broken"] += 1
            raise urllib.error.URLError("fixed_test_disconnect")
        calls["healthy"] += 1
        return FakeTextResponse()

    urllib.request.urlopen = website_response
    try:
        client = JsonClient(timeout=1, sleep=lambda _seconds: None)
        broken = client.text(
            "project_website",
            "https://broken.example",
            circuit_key="project_website:1",
        )
        healthy = client.text(
            "project_website",
            "https://healthy.example",
            circuit_key="project_website:2",
        )
        assert broken[0] == "source_failure" and calls["broken"] == 4
        assert healthy[0] == "success" and calls["healthy"] == 1
    finally:
        urllib.request.urlopen = original

    def forbidden(*_args, **_kwargs):
        raise urllib.error.HTTPError(
            "https://blocked.example", 403, "Forbidden", {}, None
        )

    urllib.request.urlopen = forbidden
    try:
        client = JsonClient(timeout=1, sleep=lambda _seconds: None)
        blocked = client.text(
            "project_website",
            "https://blocked.example",
            circuit_key="project_website:3",
        )
        assert blocked[0] == "unsupported"
    finally:
        urllib.request.urlopen = original
    print("C2.1 enrichment tests passed")


if __name__ == "__main__":
    main()
