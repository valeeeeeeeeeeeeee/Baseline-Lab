"""Update check: asks GitHub for the latest release and compares it with this version.

Only the standard library is used. Any failure (no network, GitHub out of reach, an answer in
another shape) just means there is no notice.
"""
from __future__ import annotations

import json
import re
import urllib.request

from . import __version__

REPO = "valeeeeeeeeeeeeee/Baseline-Lab"
API = f"https://api.github.com/repos/{REPO}/releases/latest"
PAGE = f"https://github.com/{REPO}/releases/latest"


def parse(version: str) -> tuple[int, ...] | None:
    """"v1.2.3" or "1.2.3" as (1, 2, 3); None if it is not a release number (the source runs as
    "dev": the release workflow writes the tag into `__version__`)."""
    m = re.fullmatch(r"v?(\d+(?:\.\d+)*)", version.strip())
    return tuple(int(n) for n in m.group(1).split(".")) if m else None


def newer(latest: str, current: str = __version__) -> bool:
    a, b = parse(latest), parse(current)
    if a is None or b is None:
        return False
    n = max(len(a), len(b))  # "1.1" and "1.1.0" are the same release
    return a + (0,) * (n - len(a)) > b + (0,) * (n - len(b))


def check(current: str = __version__, timeout: float = 5.0) -> tuple[str, str] | None:
    """(version, download page) of the latest release if it is newer than `current`, else None.
    It waits for the network: call it off the interface's thread."""
    if parse(current) is None:
        return None
    try:
        req = urllib.request.Request(API, headers={"Accept": "application/vnd.github+json",
                                                   "User-Agent": "Baseline-Lab"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.load(resp)
        tag, url = data["tag_name"], data.get("html_url")
        if not newer(tag, current):
            return None
    except Exception:  # noqa: BLE001
        return None
    # the address is handed to the browser: only a page of the repository itself
    if not (isinstance(url, str) and url.startswith(f"https://github.com/{REPO}/")):
        url = PAGE
    return tag.strip().lstrip("v"), url
