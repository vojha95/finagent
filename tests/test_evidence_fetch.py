import gzip
import hashlib
import json
from datetime import date

import pytest

from finagent.config import ConfigError, load_universe
from finagent.evidence import EvidenceStore
from finagent.evidence.fetch import (
    COMPANYFACTS_ROOT,
    REPOSITORY_EVIDENCE,
    SUBMISSIONS_ROOT,
    TICKERS_URL,
    FetchError,
    ManifestCollisionError,
    SecClient,
    StagingArea,
    fetch_issuer,
    main,
    merged_submissions,
)
from finagent.evidence.store import build_filing_url

USER_AGENT = "Test Person test@example.com"
CIK = "0000999999"
PERIODS = [(date(2019 + i, 12, 31), 2019 + i) for i in range(7)]  # FY2019..FY2025


def _filing_columns(rows):
    columns = ("form", "reportDate", "filingDate", "accessionNumber", "primaryDocument")
    return {c: [r[i] for r in rows] for i, c in enumerate(columns)}


def _accession(year: int) -> str:
    return f"0000999999-{(year + 1) % 100:02d}-000001"


def _fake_sec() -> dict[str, bytes]:
    """URL -> bytes for one synthetic issuer: FY2019 lives only in an older submissions page."""
    rows = [
        ("10-K", end.isoformat(), date(end.year + 1, 2, 1).isoformat(), _accession(fy),
         f"synth-{end:%Y%m%d}.htm")
        for end, fy in PERIODS
    ]
    recent = _filing_columns(rows[1:] + [("10-Q", "2025-09-30", "2025-10-30", "q", "q.htm")])
    page = _filing_columns(rows[:1])
    responses = {
        TICKERS_URL: json.dumps({"0": {"cik_str": 999999, "ticker": "SYNT", "title": "S"}}),
        f"{SUBMISSIONS_ROOT}/CIK{CIK}.json": json.dumps(
            {"filings": {"recent": recent, "files": [{"name": "CIK0000999999-submissions-001.json"}]}}
        ),
        f"{SUBMISSIONS_ROOT}/CIK0000999999-submissions-001.json": json.dumps(page),
        f"{COMPANYFACTS_ROOT}/CIK{CIK}.json": json.dumps({"facts": {}}),
    }
    for (end, fy), row in zip(PERIODS, rows):
        responses[build_filing_url(CIK, row[3], row[4])] = (
            '<html><ix:nonNumeric name="dei:DocumentFiscalYearFocus">'
            f"{fy}</ix:nonNumeric></html>"
        )
    return {url: body.encode() for url, body in responses.items()}


class FakeTransport:
    def __init__(self, responses, failures=None):
        self.responses = responses
        self.failures = dict(failures or {})
        self.calls: list[tuple[str, dict]] = []

    def __call__(self, url, headers):
        self.calls.append((url, headers))
        if self.failures.get(url):
            status = self.failures[url].pop(0)
            raise FetchError(url, status, f"HTTP {status}")
        if url not in self.responses:
            raise FetchError(url, 404, "HTTP 404")
        return self.responses[url]


class FakeClock:
    def __init__(self):
        self.now = 0.0
        self.sleeps: list[float] = []

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


def _client(transport, clock=None):
    clock = clock or FakeClock()
    return SecClient(USER_AGENT, transport=transport, clock=clock, sleep=clock.sleep)


# ---------- client ----------

def test_user_agent_must_name_a_contact():
    with pytest.raises(ValueError, match="User-Agent"):
        SecClient("python-urllib")


def test_client_sends_user_agent_and_rate_limits():
    clock = FakeClock()
    transport = FakeTransport({"u1": b"a", "u2": b"b"})
    client = _client(transport, clock)
    assert client.get("u1") == b"a" and client.get("u2") == b"b"
    assert transport.calls[0][1]["User-Agent"] == USER_AGENT
    assert clock.sleeps == [pytest.approx(client.min_interval)]
    assert client.min_interval >= 0.1  # SEC fair access: at most 10 requests per second


def test_client_retries_throttling_then_gives_up():
    transport = FakeTransport({"u": b"ok"}, failures={"u": [429, 503]})
    assert _client(transport).get("u") == b"ok"

    hopeless = FakeTransport({}, failures={"v": [429, 429, 429, 429]})
    with pytest.raises(FetchError) as info:
        _client(hopeless).get("v")
    assert info.value.status == 429


def test_client_does_not_retry_not_found():
    transport = FakeTransport({})
    with pytest.raises(FetchError):
        _client(transport).get("missing")
    assert len(transport.calls) == 1


# ---------- staging ----------

def test_staging_refuses_repository_evidence():
    with pytest.raises(ValueError, match="staging"):
        StagingArea(REPOSITORY_EVIDENCE)


def test_pin_writes_gzip_and_hash_of_uncompressed_bytes(tmp_path):
    staging = StagingArea(tmp_path)
    assert staging.pin("a.json", "https://x/a.json", b'{"a": 1}')
    entry = json.loads((tmp_path / "manifest.json").read_text())["a.json"]
    assert entry == {
        "url": "https://x/a.json",
        "file": "a.json.gz",
        "sha256": hashlib.sha256(b'{"a": 1}').hexdigest(),
    }
    assert gzip.decompress((tmp_path / "a.json.gz").read_bytes()) == b'{"a": 1}'


def test_pin_is_idempotent_and_never_overwrites(tmp_path):
    staging = StagingArea(tmp_path)
    staging.pin("form10-k.htm", "https://x/1/form10-k.htm", b"one")
    assert not staging.pin("form10-k.htm", "https://x/1/form10-k.htm", b"one")
    with pytest.raises(ManifestCollisionError, match="already pinned"):
        staging.pin("form10-k.htm", "https://x/2/form10-k.htm", b"two")  # another issuer
    with pytest.raises(ManifestCollisionError, match="changed"):
        staging.pin("form10-k.htm", "https://x/1/form10-k.htm", b"edited")
    assert StagingArea(tmp_path).has("form10-k.htm")  # manifest persisted


def test_merged_submissions_appends_older_pages():
    recent = {"filings": {"recent": {"form": ["10-K"], "reportDate": ["2025-12-31"]}}}
    merged = merged_submissions(recent, [{"form": ["10-K"], "reportDate": ["2019-12-31"]}])
    assert merged["filings"]["recent"]["reportDate"] == ["2025-12-31", "2019-12-31"]


# ---------- end to end ----------

def test_fetch_issuer_pins_what_the_store_needs(tmp_path):
    transport = FakeTransport(_fake_sec())
    staging = StagingArea(tmp_path)
    pinned = fetch_issuer(_client(transport), staging, "SYNT", as_of=date(2026, 12, 31))

    assert "company_tickers.json" in pinned
    assert f"submissions_{CIK}.json" in pinned and f"companyfacts_{CIK}.json" in pinned
    assert "CIK0000999999-submissions-001.json" in pinned
    documents = sorted(n for n in pinned if n.endswith(".htm"))
    assert documents == [f"synth-{end:%Y%m%d}.htm" for end, _ in PERIODS[-5:]]

    records = EvidenceStore(tmp_path).load_filings("SYNT", date(2026, 12, 31))
    assert [r.fiscal_year for r in records] == [2021, 2022, 2023, 2024, 2025]
    assert records[0].start == date(2021, 1, 1)

    again = fetch_issuer(_client(transport), staging, "SYNT", as_of=date(2026, 12, 31))
    assert again == []  # re-running downloads nothing new


def test_fetch_respects_as_of_and_older_pages(tmp_path):
    transport = FakeTransport(_fake_sec())
    pinned = fetch_issuer(
        _client(transport), StagingArea(tmp_path), "SYNT", as_of=date(2024, 6, 30), years=6
    )
    documents = sorted(n for n in pinned if n.endswith(".htm"))
    # Six 10-Ks filed by mid-2024: FY2019 (older page only) through FY2023 (filed Feb 2024).
    assert documents == [f"synth-{end:%Y%m%d}.htm" for end, _ in PERIODS[:5]]


def test_main_reports_failures_and_succeeds(tmp_path, capsys):
    ok = main(["SYNT", "--out", str(tmp_path / "s"), "--user-agent", USER_AGENT,
               "--as-of", "2026-12-31"], transport=FakeTransport(_fake_sec()))
    assert ok == 0
    failed = main(["NOPE", "--out", str(tmp_path / "s"), "--user-agent", USER_AGENT],
                  transport=FakeTransport(_fake_sec()))
    assert failed == 1
    assert "NOPE: FAILED" in capsys.readouterr().err


# ---------- universe ----------

def test_universe_has_blueprint_splits():
    universe = load_universe()
    assert {s: sum(v == s for v in universe.values()) for s in set(universe.values())} == {
        "train": 32, "validation": 8, "held_out": 8,
    }
    assert universe["AMZN"] == universe["MSFT"] == "validation"


def test_universe_rejects_ticker_in_two_splits(tmp_path):
    path = tmp_path / "u.yaml"
    path.write_text("train: [AAA]\nheld_out: [aaa]\n")
    with pytest.raises(ConfigError, match="both"):
        load_universe(path)
