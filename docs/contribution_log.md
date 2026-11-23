# Contribution log

| Date | Person | Component / phase | What was done | Evidence (commit, file, result) |
|---|---|---|---|---|
| 2026-11-23 | Vineet | Phase 0 / foundation + evidence store | Repo, py3.11 venv with blueprint dependency groups; evidence store: hash-checked snapshots, ticker→CIK, 10-K selection (up to five pinned consecutive years, walk-back), as_of filtering, DEI fiscal year, URL/manifest checks, typed gap errors, start-date classification. Design change: up to five years instead of five plus one (blueprint 8.1). Open: older submissions pages not pinned; E6 backtest needs earlier years, revisit in Phase 2 fetch. | 04fef2b, f1f9f24; finagent/evidence/; pytest 35 passed |
