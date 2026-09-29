"""Archive addressing and a cache-first fetcher.

The published layout changed inside the study window, so the loader has to know
three address families rather than one, and has to treat "this session's file is
served under a different name" as normal rather than as a missing session:

* legacy bhavcopy  ``/content/historical/EQUITIES/2023/JUL/cm13JUL2023bhav.csv.zip``
* UDiFF bhavcopy   ``/content/cm/BhavCopy_NSE_CM_0_0_0_20250915_F_0000.csv.zip``
* delivery         ``/products/content/sec_bhavdata_full_13072023.csv``
* corporate action ``/archives/equities/bhavcopy/pr/PR130723.zip`` → ``Bc130723.csv``

Everything is cached to disk on first fetch and read from disk afterwards, so a
rebuild of the raw store, an audit and the eventual study make **zero** network
calls. That is not only politeness towards the exchange: a study whose inputs are
re-downloaded is a study whose inputs can change under it, and the fingerprints
in §37 would then be meaningless.

``fetch`` never raises for an absent session. A trading holiday and a
retired-URL scheme both produce "no file", and the difference matters, so the
caller gets a status back: ``CACHED`` / ``FETCHED`` / ``NOT_PUBLISHED`` (the host
answered 404) / ``UNAVAILABLE`` (transport failure — inconclusive, retryable,
and *never* recorded as an absent session, which is the mistake the mcxhist
probe made when a throttled call read as "0 reachable").
"""
from __future__ import annotations

import time
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass
from datetime import date
from io import BytesIO
from pathlib import Path

from . import ARCHIVE_HOSTS

CACHED = "CACHED"
FETCHED = "FETCHED"
NOT_PUBLISHED = "NOT_PUBLISHED"
UNAVAILABLE = "UNAVAILABLE"

_MONTHS = (
    "JAN", "FEB", "MAR", "APR", "MAY", "JUN",
    "JUL", "AUG", "SEP", "OCT", "NOV", "DEC",
)

#: The archive host rejects requests without a browser-shaped User-Agent. No
#: credential, cookie or token is sent or stored by this module.
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0 Safari/537.36"
    ),
    "Accept": "*/*",
    "Referer": "https://www.nseindia.com/",
}

MIN_INTERVAL_SEC = 0.4
TIMEOUT_SEC = 45


def legacy_bhav_paths(day: date) -> list[str]:
    stamp = f"{day.day:02d}{_MONTHS[day.month - 1]}{day.year}"
    return [
        f"/content/historical/EQUITIES/{day.year}/{_MONTHS[day.month - 1]}/cm{stamp}bhav.csv.zip"
    ]


def udiff_bhav_paths(day: date) -> list[str]:
    stamp = day.strftime("%Y%m%d")
    return [f"/content/cm/BhavCopy_NSE_CM_0_0_0_{stamp}_F_0000.csv.zip"]


def delivery_paths(day: date) -> list[str]:
    stamp = day.strftime("%d%m%Y")
    return [f"/products/content/sec_bhavdata_full_{stamp}.csv"]


def pr_paths(day: date) -> list[str]:
    stamp = day.strftime("%d%m%y")
    return [f"/archives/equities/bhavcopy/pr/PR{stamp}.zip"]


@dataclass
class FetchResult:
    status: str
    path: Path | None
    url: str | None
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.status in (CACHED, FETCHED)


class Archive:
    """Cache-first reader over the NSE archive hosts.

    ``opener`` is injected so the whole ingest path is testable with no network:
    the smoke suite drives it with a dictionary of canned bytes and asserts the
    no-network property directly instead of trusting a comment.
    """

    def __init__(self, cache_dir: Path, opener=None, *, min_interval_sec: float = MIN_INTERVAL_SEC):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._opener = opener or self._urlopen
        self._min_interval = min_interval_sec
        self._last_call = 0.0
        self.calls = 0

    # -- transport ---------------------------------------------------------

    @staticmethod
    def _urlopen(url: str) -> bytes:
        request = urllib.request.Request(url, headers=HEADERS)
        with urllib.request.urlopen(request, timeout=TIMEOUT_SEC) as response:
            return response.read()

    def _throttle(self) -> None:
        wait = self._min_interval - (time.time() - self._last_call)
        if wait > 0:
            time.sleep(wait)
        self._last_call = time.time()

    # -- fetch -------------------------------------------------------------

    def fetch(self, paths: list[str], cache_name: str) -> FetchResult:
        """Return the bytes for the first path any host serves, cached by name."""
        target = self.cache_dir / cache_name
        if target.exists() and target.stat().st_size > 0:
            return FetchResult(CACHED, target, None)

        not_published = 0
        attempted = 0
        last_detail = ""
        for path in paths:
            for host in ARCHIVE_HOSTS:
                url = host + path
                attempted += 1
                self._throttle()
                self.calls += 1
                try:
                    payload = self._opener(url)
                except urllib.error.HTTPError as exc:  # published-or-not is decisive
                    last_detail = f"HTTP {exc.code}"
                    if exc.code in (403, 404):
                        not_published += 1
                        continue
                    return FetchResult(UNAVAILABLE, None, url, last_detail)
                except Exception as exc:  # transport: inconclusive, not absent
                    return FetchResult(UNAVAILABLE, None, url, type(exc).__name__)
                if not payload:
                    not_published += 1
                    last_detail = "empty body"
                    continue
                tmp = target.with_suffix(target.suffix + ".part")
                tmp.write_bytes(payload)
                tmp.replace(target)
                return FetchResult(FETCHED, target, url)

        if attempted and not_published == attempted:
            return FetchResult(NOT_PUBLISHED, None, None, last_detail)
        return FetchResult(UNAVAILABLE, None, None, last_detail or "no candidate url")

    # -- convenience -------------------------------------------------------

    def bhavcopy(self, day: date) -> FetchResult:
        """Either published schema, preferring the one that covers the date."""
        legacy_first = day < date(2024, 7, 8)
        paths = (
            legacy_bhav_paths(day) + udiff_bhav_paths(day)
            if legacy_first
            else udiff_bhav_paths(day) + legacy_bhav_paths(day)
        )
        return self.fetch(paths, f"bhav_{day.isoformat()}.zip")

    def delivery(self, day: date) -> FetchResult:
        return self.fetch(delivery_paths(day), f"deliv_{day.isoformat()}.csv")

    def corporate_actions(self, day: date) -> FetchResult:
        return self.fetch(pr_paths(day), f"pr_{day.isoformat()}.zip")


def read_member(payload: bytes, *, prefix: str | None = None, suffix: str = ".csv") -> tuple[str, str]:
    """Return (member name, text) for one CSV inside a zip, or the plain CSV."""
    if not payload[:2] == b"PK":
        return ("", payload.decode("utf-8", "replace"))
    with zipfile.ZipFile(BytesIO(payload)) as archive:
        names = [
            name
            for name in archive.namelist()
            if name.lower().endswith(suffix)
            and (prefix is None or Path(name).name.lower().startswith(prefix.lower()))
        ]
        if not names:
            raise KeyError(f"no {prefix or ''}*{suffix} member in archive")
        name = sorted(names)[0]
        return (name, archive.read(name).decode("utf-8", "replace"))
