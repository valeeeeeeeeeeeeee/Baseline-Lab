"""Update check: version comparison and the reading of GitHub's answer (no network is used);
the update: the download, checked and extracted, and the script that swaps the folders."""
import hashlib
import io
import json
import subprocess
import sys
import time
import zipfile

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
    assert updates.check("1.1.0") == ("1.2.0", page, None, None)
    assert updates.check("1.2.0") is None


def test_check_only_opens_pages_of_the_repository(monkeypatch):
    _answer(monkeypatch, {"tag_name": "v1.2.0", "html_url": "https://example.com/x.exe"})
    assert updates.check("1.1.0").page == updates.PAGE


ZIP = updates.DOWNLOADS + "v1.2.0/Baseline.Lab.zip"


def test_check_takes_the_zip_of_the_repository_only(monkeypatch):
    _answer(monkeypatch, {"tag_name": "v1.2.0", "assets": [
        {"browser_download_url": "https://example.com/Baseline.Lab.zip"},
        {"browser_download_url": ZIP, "digest": "sha256:ab"}]})
    found = updates.check("1.1.0")
    assert (found.zip, found.digest) == (ZIP, "sha256:ab")
    _answer(monkeypatch, {"tag_name": "v1.2.0", "assets": [
        {"browser_download_url": "https://example.com/Baseline.Lab.zip"}]})
    assert updates.check("1.1.0").zip is None


@pytest.mark.parametrize("payload", [OSError("offline"), {"message": "Not Found"}, [],
                                     {"tag_name": None}])
def test_check_is_silent_on_failure(monkeypatch, payload):
    _answer(monkeypatch, payload)
    assert updates.check("1.0.0") is None


def test_check_from_the_source_does_not_use_the_network(monkeypatch):
    _answer(monkeypatch, AssertionError("network used"))
    assert updates.check("dev") is None


def _release_zip() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("Baseline Lab/Baseline Lab.exe", b"new exe")
        z.writestr("Baseline Lab/_internal/lib.txt", b"new lib")
        z.writestr("../outside.txt", b"x")  # zipfile keeps it inside
    return buf.getvalue()


def _serve(monkeypatch, data: bytes):
    class Answer(io.BytesIO):
        headers = {"Content-Length": str(len(data))}
    monkeypatch.setattr(updates.urllib.request, "urlopen", lambda _req, timeout=None: Answer(data))


def test_download_extracts_beside_the_app(monkeypatch, tmp_path):
    data = _release_zip()
    _serve(monkeypatch, data)
    app = tmp_path / "Baseline Lab"
    app.mkdir()
    seen = []
    release = updates.Release("1.2.0", updates.PAGE, ZIP,
                              "sha256:" + hashlib.sha256(data).hexdigest())
    stage, new = updates.download(release, seen.append, app=app)
    assert stage.parent == tmp_path and stage.name.startswith(updates.STAGE)
    assert (new / "Baseline Lab.exe").read_bytes() == b"new exe"
    assert not (tmp_path / "outside.txt").exists()
    assert not (stage / "release.zip").exists()
    assert seen[-1] == 1.0


def test_download_that_does_not_match_leaves_nothing(monkeypatch, tmp_path):
    _serve(monkeypatch, _release_zip())
    app = tmp_path / "Baseline Lab"
    app.mkdir()
    release = updates.Release("1.2.0", updates.PAGE, ZIP, "sha256:" + "0" * 64)
    with pytest.raises(ValueError):
        updates.download(release, app=app)
    assert sorted(tmp_path.iterdir()) == [app]


@pytest.mark.skipif(sys.platform != "win32", reason="the swap is a Windows script")
@pytest.mark.parametrize("name", ["Baseline Lab", "Lab & Ç (1) 100% !x"])
def test_swap_waits_for_the_app_and_puts_the_new_folder_in_place(tmp_path, name):
    root = tmp_path / "Pasta Álvaro"
    app = root / name
    (app / "_internal").mkdir(parents=True)
    (app / "_internal" / "old.txt").write_text("old")
    stage = root / (updates.STAGE + "x")
    new = stage / "new" / "Baseline Lab"
    (new / "_internal").mkdir(parents=True)
    (new / "_internal" / "new.txt").write_text("new")
    # the app still running: a file of its folder held open for a while
    holder = subprocess.Popen([sys.executable, "-c", (
        "import sys, time; f = open(sys.argv[1]); time.sleep(3)"), str(app / "_internal" / "old.txt")])
    time.sleep(0.5)
    script = updates.swap(stage, new, "about:blank", restart=False, app=app, exe="none.exe")
    assert script.wait(timeout=60) == 0
    holder.wait()
    assert (app / "_internal" / "new.txt").read_text() == "new"
    assert not (app / "_internal" / "old.txt").exists()
    assert not stage.exists()
