"""Update check: version comparison and the reading of GitHub's answer (no network is used)."""
import io
import json

import pytest

from baseline_lab import updates


def test_parse():
    assert updates.parse("v1.0.2") == (1, 0, 2)
    assert updates.parse("1.10") == (1, 10)
    assert updates.parse("dev") is None
    assert updates.parse("v1.0.2-beta") is None


def test_newer_compares_numbers_not_text():
    assert updates.newer("v1.0.10", "1.0.9")
    assert updates.newer("v2.0", "1.9.9")
    assert not updates.newer("v1.0.2", "1.0.2")
    assert not updates.newer("v1.1", "1.1.0")
    assert not updates.newer("v1.0.1", "1.0.2")
    assert not updates.newer("v9.9.9", "dev")
    assert not updates.newer("nightly", "1.0.0")


def _answer(monkeypatch, payload):
    def urlopen(_req, timeout=None):
        if isinstance(payload, Exception):
            raise payload
        return io.BytesIO(json.dumps(payload).encode())
    monkeypatch.setattr(updates.urllib.request, "urlopen", urlopen)


def test_check_finds_a_newer_release(monkeypatch):
    page = f"https://github.com/{updates.REPO}/releases/tag/v1.2.0"
    _answer(monkeypatch, {"tag_name": "v1.2.0", "html_url": page})
    assert updates.check("1.1.0") == ("1.2.0", page)
    assert updates.check("1.2.0") is None


def test_check_only_opens_pages_of_the_repository(monkeypatch):
    _answer(monkeypatch, {"tag_name": "v1.2.0", "html_url": "https://example.com/x.exe"})
    assert updates.check("1.1.0") == ("1.2.0", updates.PAGE)


@pytest.mark.parametrize("payload", [OSError("offline"), {"message": "Not Found"}, [],
                                     {"tag_name": None}])
def test_check_is_silent_on_failure(monkeypatch, payload):
    _answer(monkeypatch, payload)
    assert updates.check("1.0.0") is None


def test_check_from_the_source_does_not_use_the_network(monkeypatch):
    _answer(monkeypatch, AssertionError("network used"))
    assert updates.check("dev") is None
