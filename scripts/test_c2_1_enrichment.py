#!/usr/bin/env python3

import urllib.error
import urllib.request

from c2_1_enrichment import JsonClient


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
    print("C2.1 enrichment tests passed")


if __name__ == "__main__":
    main()
