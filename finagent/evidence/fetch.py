"""One-time SEC fetch into a staging directory (blueprint 8.1 method step 1, 9.1).

Downloads the ticker map, each issuer's submissions JSON (with its older pages), its Company
Facts JSON and its recent 10-K primary documents through a rate-limited client with a declared
User-Agent, and writes them gzipped with a manifest of source URL and SHA-256 of the
uncompressed bytes. It never writes to the repository's evidence/ (CLAUDE.md rule 2): a person
reviews the staging directory and copies it in. The product never calls the SEC at run time.

Usage:
    python -m finagent.evidence.fetch --user-agent "Name name@example.com" --out staging \
        --split validation
"""

import argparse
import gzip
import hashlib
import json
import os
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Iterable, Sequence
from datetime import date
from pathlib import Path
from typing import Any

from finagent.config import load_universe
from finagent.evidence.integrity import read_snapshot_json
from finagent.evidence.store import (
    MAX_FISCAL_YEARS,
    EvidenceStore,
    build_filing_url,
    merged_submissions,
    select_annual_filings,
)

TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SUBMISSIONS_ROOT = "https://data.sec.gov/submissions"
COMPANYFACTS_ROOT = "https://data.sec.gov/api/xbrl/companyfacts"

# SEC fair access allows at most 10 requests per second; stay comfortably below it.
MIN_INTERVAL_SECONDS = 0.125
RETRY_STATUSES = (429, 500, 502, 503, 504)
REPOSITORY_EVIDENCE = Path(__file__).resolve().parents[2] / "evidence"

Transport = Callable[[str, dict[str, str]], bytes]


class FetchError(RuntimeError):
    """A download failed; status is the HTTP status when there was one."""

    def __init__(self, url: str, status: int | None, message: str) -> None:
        super().__init__(f"{url}: {message}")
        self.url = url
        self.status = status


class ManifestCollisionError(RuntimeError):
    """A manifest name is already pinned with a different source or different bytes."""


def urllib_transport(url: str, headers: dict[str, str]) -> bytes:
    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            body = response.read()
            if response.headers.get("Content-Encoding") == "gzip":
                body = gzip.decompress(body)
            return body
    except urllib.error.HTTPError as exc:
        raise FetchError(url, exc.code, f"HTTP {exc.code}") from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise FetchError(url, None, str(exc)) from exc


class SecClient:
    """Rate-limited GET with a declared User-Agent and bounded retries on throttling."""

    def __init__(
        self,
        user_agent: str,
        transport: Transport = urllib_transport,
        min_interval: float = MIN_INTERVAL_SECONDS,
        retries: int = 3,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if "@" not in user_agent or len(user_agent.split()) < 2:
            raise ValueError("SEC requires a User-Agent with a name and a contact email")
        self.headers = {"User-Agent": user_agent, "Accept-Encoding": "gzip"}
        self.transport = transport
        self.min_interval = min_interval
        self.retries = retries
        self.clock = clock
        self.sleep = sleep
        self._last: float | None = None
        self.requests = 0

    def _wait_turn(self) -> None:
        if self._last is not None:
            remaining = self.min_interval - (self.clock() - self._last)
            if remaining > 0:
                self.sleep(remaining)
        self._last = self.clock()

    def get(self, url: str) -> bytes:
        for attempt in range(self.retries + 1):
            self._wait_turn()
            self.requests += 1
            try:
                return self.transport(url, self.headers)
            except FetchError as exc:
                if exc.status not in RETRY_STATUSES or attempt == self.retries:
                    raise
                self.sleep(2.0**attempt)
        raise AssertionError("unreachable")  # pragma: no cover


class StagingArea:
    """A staging copy of the evidence layout: {name}.gz files plus manifest.json."""

    def __init__(self, directory: Path | str) -> None:
        self.directory = Path(directory)
        if self.directory.resolve() == REPOSITORY_EVIDENCE.resolve():
            raise ValueError("Fetch into a staging directory, review it, then copy into evidence/")
        self.directory.mkdir(parents=True, exist_ok=True)
        path = self.directory / "manifest.json"
        self.manifest: dict[str, dict[str, str]] = (
            json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        )

    def has(self, name: str) -> bool:
        return name in self.manifest

    def pin(self, name: str, url: str, payload: bytes) -> bool:
        """Write one snapshot. Returns False when the identical snapshot is already pinned.

        Pinned evidence is immutable: the same name with another URL (two issuers sharing a
        document name) or with different bytes raises instead of overwriting.
        """
        sha256 = hashlib.sha256(payload).hexdigest()
        existing = self.manifest.get(name)
        if existing is not None:
            if existing["url"] != url:
                raise ManifestCollisionError(
                    f"{name} is already pinned from {existing['url']}, not {url}"
                )
            if existing["sha256"] != sha256:
                raise ManifestCollisionError(f"{name} changed since it was pinned ({url})")
            return False
        file_name = f"{name}.gz"
        # mtime=0 keeps the gzip bytes deterministic for identical content.
        (self.directory / file_name).write_bytes(gzip.compress(payload, mtime=0))
        self.manifest[name] = {"url": url, "file": file_name, "sha256": sha256}
        self._save()
        return True

    def _save(self) -> None:
        path = self.directory / "manifest.json"
        ordered = dict(sorted(self.manifest.items()))
        path.write_text(json.dumps(ordered, indent=2) + "\n", encoding="utf-8")


def _get_and_pin(client: SecClient, staging: StagingArea, name: str, url: str) -> bool:
    if staging.has(name):
        return False
    return staging.pin(name, url, client.get(url))


def fetch_issuer(
    client: SecClient,
    staging: StagingArea,
    ticker: str,
    as_of: date,
    years: int = MAX_FISCAL_YEARS,
    older_pages: bool = True,
) -> list[str]:
    """Pin one issuer's submissions, older pages, Company Facts and last `years` 10-Ks.

    Returns the manifest names newly pinned. Re-running is safe: pinned names are skipped.
    """
    pinned: list[str] = []
    if _get_and_pin(client, staging, "company_tickers.json", TICKERS_URL):
        pinned.append("company_tickers.json")
    cik = EvidenceStore(staging.directory).resolve_cik(ticker)

    submissions_name = f"submissions_{cik}.json"
    if _get_and_pin(client, staging, submissions_name, f"{SUBMISSIONS_ROOT}/CIK{cik}.json"):
        pinned.append(submissions_name)
    submissions = read_snapshot_json(staging.directory, submissions_name, staging.manifest)

    pages = []
    if older_pages:
        for page in submissions.get("filings", {}).get("files", []):
            name = page["name"]
            if _get_and_pin(client, staging, name, f"{SUBMISSIONS_ROOT}/{name}"):
                pinned.append(name)
            pages.append(read_snapshot_json(staging.directory, name, staging.manifest))

    facts_name = f"companyfacts_{cik}.json"
    if _get_and_pin(client, staging, facts_name, f"{COMPANYFACTS_ROOT}/CIK{cik}.json"):
        pinned.append(facts_name)

    filings = select_annual_filings(merged_submissions(submissions, pages), as_of)
    for filing in filings[-years:]:
        url = build_filing_url(cik, filing.accession, filing.primary_document)
        if _get_and_pin(client, staging, filing.primary_document, url):
            pinned.append(filing.primary_document)
    return pinned


def _tickers(args: argparse.Namespace) -> list[str]:
    if args.tickers:
        return [t.upper() for t in args.tickers]
    universe = load_universe()
    splits = args.split or ["train", "validation", "held_out"]
    return [t for t, s in universe.items() if s in splits]


def main(argv: Sequence[str] | None = None, transport: Transport = urllib_transport) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("tickers", nargs="*", help="tickers; default: the universe in configs")
    parser.add_argument("--split", action="append", choices=["train", "validation", "held_out"])
    parser.add_argument("--user-agent", default=os.environ.get("SEC_USER_AGENT", ""))
    parser.add_argument("--out", required=True, help="staging directory (not evidence/)")
    parser.add_argument("--as-of", type=date.fromisoformat, default=date.today())
    parser.add_argument("--years", type=int, default=MAX_FISCAL_YEARS)
    parser.add_argument("--no-older-pages", action="store_true")
    args = parser.parse_args(argv)

    client = SecClient(args.user_agent, transport=transport)
    staging = StagingArea(args.out)
    failures = 0
    for ticker in _tickers(args):
        try:
            pinned = fetch_issuer(
                client, staging, ticker, args.as_of, args.years, not args.no_older_pages
            )
            print(f"{ticker}: pinned {len(pinned)} new snapshot(s)")
        except (FetchError, ManifestCollisionError, KeyError, ValueError) as exc:
            failures += 1
            print(f"{ticker}: FAILED {exc}", file=sys.stderr)
    print(f"{client.requests} request(s); manifest at {staging.directory / 'manifest.json'}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
